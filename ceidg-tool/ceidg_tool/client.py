"""Klient HTTP API v3 CEIDG: token, limiter, ponawianie, paginacja, szczegóły, raporty, zmiany.

Każde żądanie przechodzi przez `RateLimiter.acquire()` — także ponowienia. Błędy
transportu (DNS, timeout, reset) są odróżniane od błędów API i ponawiane z odstępami
10 → 30 → 60 → 300 s do 30 minut (uzupelnienie-01.md §C). Odpowiedź 5xx jest ponawiana
z rosnącym odstępem, 429 czeka pełną blokadę limitera, 400/401/403/404 nie są ponawiane.
Klient łączy się wyłącznie z hostami z `config.ALLOWED_HOSTS`; `links.next` z obcym
hostem kończy się `UntrustedLinkError`.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx

from . import __version__
from .apiprofile import ApiProfile
from .clock import Clock
from .config import ALLOWED_HOSTS
from .criteria import Criteria
from .errors import (
    AuthError,
    BadRequestError,
    CeidgError,
    NotFoundError,
    PagingRunawayError,
    ProfileMismatchError,
    RateLimitError,
    ServerError,
    TransportError,
    UntrustedLinkError,
)
from .logsetup import get_logger
from .progress import Events, NullEvents
from .ratelimit import REASON_NO_CONNECTION, RateLimiter
from .recordid import KanonicznyId, kanoniczny_id
from .records import Report

log = get_logger("client")

CONNECTION_RETRY_DELAYS_S: tuple[float, ...] = (10.0, 30.0, 60.0, 300.0)
CONNECTION_MAX_OUTAGE_S = 30 * 60.0
MAX_CONSECUTIVE_429 = 3
USER_AGENT = f"ceidg-tool/{__version__}"
HEADER_RATE_REMAINING = "X-Rate-Limit-Remaining"
HEADER_RATE_RESET = "X-Rate-Limit-Reset"
HEADER_TRANSACTION = "X-Gravitee-Transaction-Id"
DOWNLOAD_CHUNK = 1 << 16
# Co ile bajtów pobieranie melduje postęp. 512 KiB to około 42 zdarzenia na 21 MB raportu
# i co najmniej jedno na 10 s nawet przy 50 kB/s — czyli i ruch paska, i bicie serca
# blokady bazy, której `DEFAULT_LOCK_STALE_S` wynosi 600 s.
DOWNLOAD_EVENT_BYTES = 512 * 1024

# Postęp pobierania pliku: (bajty pobrane, bajty spodziewane albo None bez `Content-Length`).
# Wywołanie zwrotne, a nie `Events`, bo niesie **także** bicie serca blokady, a blokada
# należy do `pipeline` (reguła granic 5) — klient nie ma prawa znać bazy.
DownloadProgress = Callable[[int, int | None], None]

CursorMode = str  # "links" | "numeric"


@dataclass(frozen=True)
class Cursor:
    """Nieprzezroczysty kursor strony: URL (`links`) albo numer strony (`numeric`)."""

    mode: CursorMode
    value: str


@dataclass(frozen=True)
class Page:
    records: list[dict[str, Any]]
    count: int | None
    next_cursor: Cursor | None
    index: int
    self_url: str


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: Any
    headers: httpx.Headers
    url: str


class CeidgClient:
    """Synchroniczny klient jednego środowiska (test albo prod)."""

    def __init__(
        self,
        *,
        http: httpx.Client,
        profile: ApiProfile,
        limiter: RateLimiter,
        token: str,
        clock: Clock,
        events: Events | None = None,
    ) -> None:
        self._http = http
        self._profile = profile
        self._limiter = limiter
        self._clock = clock
        self._events: Events = events or NullEvents()
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, application/octet-stream",
            "User-Agent": USER_AGENT,
        }
        self._base_host = self._checked_host(profile.base_url)
        self.rate_remaining: int | None = None
        self.rate_reset_epoch: float | None = None
        self.last_transaction_id: str | None = None
        self.requests_made = 0

    # ------------------------------------------------------------------ narzędzia

    @property
    def profile(self) -> ApiProfile:
        return self._profile

    def _checked_host(self, url: str) -> str:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme != "https" or host not in ALLOWED_HOSTS:
            raise UntrustedLinkError(
                f"Odmowa połączenia z {host or url!r}: dozwolone są tylko hosty "
                + ", ".join(sorted(ALLOWED_HOSTS))
            )
        return host

    def _url(self, path: str, params: Sequence[tuple[str, str]] | None = None) -> str:
        url = f"{self._profile.base_url}/{path.lstrip('/')}"
        if params:
            url += "?" + urlencode(list(params))
        return url

    def _read_rate_headers(self, headers: httpx.Headers) -> None:
        remaining = headers.get(HEADER_RATE_REMAINING)
        if remaining is not None and remaining.isdigit():
            self.rate_remaining = int(remaining)
        reset = headers.get(HEADER_RATE_RESET)
        if reset is not None and reset.isdigit():
            value = int(reset)
            self.rate_reset_epoch = value / 1000.0 if value > 10_000_000_000 else float(value)
        self.last_transaction_id = headers.get(HEADER_TRANSACTION) or self.last_transaction_id

    @staticmethod
    def _content_length(headers: httpx.Headers) -> int | None:
        """Spodziewany rozmiar **rozpakowanych** bajtów albo `None`, gdy go nie znamy.

        Dwa powody, dla których go nie znamy. Pierwszy: odpowiedź strumieniowa (chunked) nie
        niesie `Content-Length` w ogóle. Drugi: `httpx` sam wysyła `Accept-Encoding`, a
        `iter_bytes` oddaje bajty **po** rozpakowaniu, podczas gdy `Content-Length` opisuje
        te przed — więc przy `Content-Encoding: gzip` licznik przekroczyłby sumę i pasek
        pokazałby ponad 100 %. Archiwum ZIP jest dziś podawane jako `identity`, ale to
        ustawienie bramy, nie nasza gwarancja; „nie wiem" jest tańsze niż zły pasek."""
        encoding = (headers.get("Content-Encoding") or "identity").strip().lower()
        if encoding not in ("identity", ""):
            return None
        raw = headers.get("Content-Length")
        return int(raw) if raw is not None and raw.isdigit() else None

    @staticmethod
    def _retry_after(headers: httpx.Headers) -> float | None:
        raw = headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    @staticmethod
    def _error_text(body: Any, status: int) -> str:
        if isinstance(body, dict):
            code = body.get("code")
            message = body.get("message")
            if code or message:
                return f"{status} {code or ''}: {message or ''}".strip()
        return f"HTTP {status}"

    # ------------------------------------------------------------------ żądanie

    def _request(
        self,
        url: str,
        *,
        endpoint: str,
        download_to: Path | None = None,
        progress: DownloadProgress | None = None,
    ) -> ApiResponse:
        """GET z limiterem i ponawianiem. Zwraca odpowiedź 200/204 albo rzuca `CeidgError`."""
        self._checked_host(url)
        outage_s = 0.0
        transport_failures = 0
        server_failures = 0
        consecutive_429 = 0
        extra_delay = 0.0
        while True:
            self._limiter.acquire(endpoint, extra_delay_s=extra_delay)
            extra_delay = 0.0
            started = time.monotonic()
            try:
                response = self._perform(url, download_to, progress)
            except httpx.HTTPError as exc:
                delay = CONNECTION_RETRY_DELAYS_S[
                    min(transport_failures, len(CONNECTION_RETRY_DELAYS_S) - 1)
                ]
                transport_failures += 1
                outage_s += delay
                log.warning("%s: błąd połączenia (%s), ponowienie za %.0f s", endpoint, exc, delay)
                if outage_s > CONNECTION_MAX_OUTAGE_S:
                    raise TransportError(
                        f"Brak połączenia z API przez ponad {CONNECTION_MAX_OUTAGE_S / 60:.0f} min "
                        f"({exc}). Postęp jest zapisany — wznów poleceniem `ceidg-tool wznow`."
                    ) from exc
                self._events.on_wait(delay, REASON_NO_CONNECTION, self._clock.wall() + delay)
                extra_delay = delay
                continue

            self._zanotuj_odpowiedz(endpoint, response, time.monotonic() - started)

            status = response.status
            if status == 200 or status in self._profile.empty_result_statuses:
                return response
            if status == 429:
                consecutive_429 += 1
                if consecutive_429 >= MAX_CONSECUTIVE_429:
                    raise RateLimitError(
                        "API odrzuca żądania (429) mimo odczekania pełnej blokady. "
                        "Wznów pobieranie później poleceniem `ceidg-tool wznow`."
                    )
                continue
            if 500 <= status < 600:
                server_failures += 1
                if server_failures > self._profile.rate.max_retries_5xx:
                    raise ServerError(
                        f"Błąd serwera API ({self._error_text(response.body, status)}) po "
                        f"{server_failures} próbach. Postęp zapisany — wznów później."
                    )
                extra_delay = self._profile.rate.backoff_base_s * (2 ** (server_failures - 1))
                log.warning("%s: %s, ponowienie za %.0f s", endpoint, status, extra_delay)
                continue
            raise self._blad_nie_do_ponowienia(status, response, endpoint)

    def _zanotuj_odpowiedz(self, endpoint: str, response: ApiResponse, elapsed: float) -> None:
        """Księgowanie po KAŻDEJ odpowiedzi, także tej, po której zaraz ponowimy żądanie.

        Licznik żądań, limiter, budżet serwera, zdarzenie na ekran i wpis do logu chodzą
        razem, bo pominięcie któregokolwiek przy ponowieniu daje ten sam defekt: licznik,
        który nie zgadza się z rachunkiem po stronie API.
        """
        self.requests_made += 1
        self._limiter.note_response(response.status, self._retry_after(response.headers))
        self._read_rate_headers(response.headers)
        # Budżet zgłaszany przez serwer jest jedynym sygnałem o zużyciu tokenu poza tym
        # procesem (sonda, druga maszyna). Limiter sam z siebie widzi tylko `request_log`.
        self._limiter.note_budget(self.rate_remaining, self.rate_reset_epoch)
        self._events.on_request(endpoint, response.status, elapsed)
        log.info(
            "GET %s -> %s w %.2fs (transakcja %s, pozostało %s)",
            endpoint,
            response.status,
            elapsed,
            self.last_transaction_id,
            self.rate_remaining,
        )

    def _blad_nie_do_ponowienia(
        self, status: int, response: ApiResponse, endpoint: str
    ) -> CeidgError:
        """Wyjątek dla statusu, którego ponawianie niczego nie zmieni.

        ZWRACA wyjątek, zamiast go rzucać: dzięki temu w `_request` zostaje jedno `raise`
        i widać, że każda gałąź pętli kończy się albo zwrotem, albo ponowieniem, albo tym
        rzutem. Ostatni `else` jest tu świadomie szeroki — nieznany status ma być głośny,
        a nie ponowiony w nieskończoność.
        """
        if status in (401, 403):
            return AuthError(
                f"Token odrzucony ({status}). Sprawdź, czy token dotyczy środowiska "
                f"{self._base_host} i czy nie wygasł."
            )
        if status == 400:
            return BadRequestError(
                f"API odrzuciło zapytanie: {self._error_text(response.body, status)}"
            )
        if status == 404:
            return NotFoundError(f"Zasób nie istnieje: {endpoint}")
        return ServerError(
            f"Nieoczekiwana odpowiedź API: {self._error_text(response.body, status)}"
        )

    def _perform(
        self, url: str, download_to: Path | None, progress: DownloadProgress | None = None
    ) -> ApiResponse:
        timeout = self._profile.rate.timeout_s
        if download_to is None:
            resp = self._http.get(url, headers=self._headers, timeout=timeout)
            return ApiResponse(resp.status_code, self._parse_json(resp), resp.headers, url)

        download_to.parent.mkdir(parents=True, exist_ok=True)
        tmp = download_to.with_name(f".{download_to.name}.tmp")
        with self._http.stream("GET", url, headers=self._headers, timeout=timeout) as resp:
            if resp.status_code != 200:
                resp.read()
                return ApiResponse(resp.status_code, self._parse_json(resp), resp.headers, url)
            total = self._content_length(resp.headers)
            done = 0
            reported = 0
            # Pierwsze zgłoszenie zaraz po nagłówkach: to ono zaczyna ruch paska i pierwsze
            # bicie serca blokady, zanim jeszcze przyjdzie pół megabajta danych.
            if progress is not None:
                progress(0, total)
            try:
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_bytes(DOWNLOAD_CHUNK):
                        fh.write(chunk)
                        done += len(chunk)
                        if progress is not None and done - reported >= DOWNLOAD_EVENT_BYTES:
                            reported = done
                            progress(done, total)
                if progress is not None and done != reported:
                    progress(done, total)
                os.replace(tmp, download_to)
            finally:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
            return ApiResponse(200, {"plik": str(download_to)}, resp.headers, url)

    def _parse_json(self, resp: httpx.Response) -> Any:
        if resp.status_code in self._profile.empty_result_statuses or not resp.content:
            return None
        try:
            return json.loads(resp.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if resp.status_code == 200:
                raise ServerError(
                    "Odpowiedź API nie jest poprawnym JSON (ucięta lub HTML). "
                    "Postęp jest zapisany — spróbuj wznowić za chwilę."
                ) from exc
            return {"_nonjson": resp.content[:300].decode("utf-8", "replace")}

    # ------------------------------------------------------------------ /firmy

    def _list_params(self, criteria: Criteria, limit: int) -> list[tuple[str, str]]:
        return criteria.to_params(self._profile) + [("limit", str(limit))]

    def count(self, criteria: Criteria) -> int:
        """Jedno żądanie z `limit=1`; 204 oznacza zero trafień."""
        response = self._request(
            self._url("firmy", self._list_params(criteria, 1)), endpoint="firmy"
        )
        if response.body is None:
            return 0
        body = response.body if isinstance(response.body, dict) else {}
        if self._profile.count_semantics == "total" and isinstance(body.get("count"), int):
            return int(body["count"])
        return len(body.get(self._profile.list_root_key) or [])

    def iter_pages(
        self, criteria: Criteria, start: Cursor | None = None, *, first_index: int = 0
    ) -> Iterator[Page]:
        limit = self._profile.max_limit_firmy
        first_url = self._url("firmy", self._list_params(criteria, limit))
        yield from self._iter_paged(
            first_url,
            root_key=self._profile.list_root_key,
            endpoint="firmy",
            start=start,
            first_index=first_index,
            limit=limit,
        )

    def _iter_paged(
        self,
        first_url: str,
        *,
        root_key: str,
        endpoint: str,
        start: Cursor | None,
        first_index: int,
        limit: int,
    ) -> Iterator[Page]:
        mode = self._profile.paging_mode
        url = first_url
        page_no = self._profile.page_start
        if start is not None:
            if start.mode == "links":
                url = start.value
            else:
                try:
                    page_no = int(start.value)
                except ValueError as exc:
                    raise ProfileMismatchError(
                        f"Checkpoint ma kursor {start.value!r} niezgodny z paginacją numeryczną "
                        "— tego pobrania nie da się wznowić przez API."
                    ) from exc
                url = _with_page(first_url, page_no)
        elif mode == "numeric" and self._profile.send_page_on_first_request:
            url = _with_page(first_url, page_no)

        index = first_index
        count: int | None = None
        expected_pages: int | None = None
        while True:
            if index - first_index >= self._profile.max_pages:
                raise PagingRunawayError(
                    f"Przekroczono {self._profile.max_pages} stron dla {endpoint} — przerwano, "
                    "żeby nie zapętlić pobierania."
                )
            response = self._request(url, endpoint=endpoint)
            body = response.body if isinstance(response.body, dict) else None
            records = _records_of(body, root_key)
            if body is not None and count is None and isinstance(body.get("count"), int):
                count = int(body["count"])
                expected_pages = math.ceil(count / limit) if count else 0
            links = (body or {}).get("links") or {}
            next_url = links.get("next") if isinstance(links, dict) else None
            self_url = links.get("self") if isinstance(links, dict) else None

            next_cursor: Cursor | None = None
            if records:
                if mode == "links":
                    if isinstance(next_url, str) and next_url != url and next_url != self_url:
                        self._checked_host(next_url)
                        next_cursor = Cursor("links", next_url)
                elif len(records) >= limit:
                    next_cursor = Cursor("numeric", str(page_no + 1))
                if expected_pages is not None and index - first_index + 1 >= expected_pages + 1:
                    log.warning("%s: więcej stron niż wynika z count=%s — kończę", endpoint, count)
                    next_cursor = None

            self._events.on_page(index, len(records), count)
            yield Page(
                records=records, count=count, next_cursor=next_cursor, index=index, self_url=url
            )
            if next_cursor is None or not records:
                return
            index += 1
            if next_cursor.mode == "links":
                url = next_cursor.value
            else:
                page_no = int(next_cursor.value)
                url = _with_page(first_url, page_no)

    # ------------------------------------------------------------------ /firma

    def fetch_details(
        self, ids: Sequence[KanonicznyId]
    ) -> tuple[list[dict[str, Any]], list[KanonicznyId]]:
        """Szczegóły dla listy `id`. Zwraca (rekordy, identyfikatory nieznalezione).

        Bierze identyfikatory już kanoniczne, więc porównanie poniżej jest **równością**,
        a nie pobłażliwością. Wcześniej to jedyne `.upper()` w programie łagodziło różnicę
        pisowni akurat tam, gdzie miała się ujawnić: lista `missing` wychodziła pusta, nic
        nie krzyczało, a magazyn dostawał dwie tożsamości tego samego wpisu (ADR-0013)."""
        found: list[dict[str, Any]] = []
        missing: list[KanonicznyId] = []
        for chunk in self._chunks(ids):
            if (
                self._profile.detail_mode == "path"
                or len(chunk) == 1
                and self._profile.ids_batch_size == 1
            ):
                for rid in chunk:
                    try:
                        response = self._request(
                            self._url(f"firma/{quote(rid, safe='')}"), endpoint="firma"
                        )
                    except NotFoundError:
                        missing.append(rid)
                        continue
                    found.extend(_records_of(response.body, self._profile.detail_root_key))
                continue
            params = [("ids", rid) for rid in chunk]
            try:
                response = self._request(self._url("firma", params), endpoint="firma")
            except NotFoundError:
                missing.extend(chunk)
                continue
            records = _records_of(response.body, self._profile.detail_root_key)
            # `_records_of` kanonizuje, `chunk` przychodzi kanoniczny — więc to zwykła
            # równość zbiorów, i „nie ma tego rekordu" znaczy dokładnie tyle, ile mówi.
            got = {str(r.get("id", "")) for r in records}
            found.extend(records)
            missing.extend(rid for rid in chunk if rid not in got)
        return found, missing

    def _chunks(self, ids: Sequence[KanonicznyId]) -> Iterator[list[KanonicznyId]]:
        size = self._profile.ids_batch_size
        base_len = len(self._url("firma")) + 1
        chunk: list[KanonicznyId] = []
        length = base_len
        for rid in ids:
            piece = len("ids=") + len(rid) + 1
            if chunk and (len(chunk) >= size or length + piece > self._profile.max_url_length):
                yield chunk
                chunk, length = [], base_len
            chunk.append(rid)
            length += piece
        if chunk:
            yield chunk

    # ------------------------------------------------------------------ /raporty

    def list_reports(self, od: date | None = None, do: date | None = None) -> list[Report]:
        params: list[tuple[str, str]] = []
        if od:
            params.append(("dataod", od.strftime(self._profile.date_format)))
        if do:
            params.append(("datado", do.strftime(self._profile.date_format)))
        response = self._request(self._url("raporty", params), endpoint="raporty")
        items = _records_of(response.body, self._profile.reports_root_key)
        reports = []
        for item in items:
            url = str(item.get("raport") or "")
            if not url and item.get("id"):
                url = self._url(f"raport/{item['id']}")
            reports.append(
                Report(
                    id=str(item.get("id", "")),
                    nazwa=str(item.get("nazwa", "")),
                    format=str(item.get("format", "")),
                    url=url,
                    utworzono=str(item.get("data-utworzenia") or item.get("dataUtworzenia") or ""),
                )
            )
        return reports

    def download_report(
        self, report: Report, dest: Path, *, progress: DownloadProgress | None = None
    ) -> Path:
        """Pobiera archiwum raportu strumieniowo; `progress` dostaje bajty co pół megabajta.

        Bez `progress` to jedno żądanie było najdłuższą ciszą w programie: 21 MB archiwum
        wojewódzkiego przy jednym komunikacie na starcie i niczym do końca transferu —
        ten sam kształt defektu co strony `/zmiana` w fazie 3e, tylko o warstwę niżej."""
        url = report.url or self._url(f"raport/{report.id}")
        self._request(url, endpoint="raport", download_to=dest, progress=progress)
        return dest

    # ------------------------------------------------------------------ /zmiana

    def count_changes(self, od: datetime | date, do: datetime | date) -> int:
        """Ile zmian w zakresie — jedno żądanie z `limit=1`, jak `count` dla `/firmy`.

        Odpowiedź `/zmiana` niesie `count` całego zakresu, a nie liczbę pozycji na stronie
        (sonda: 3 dni = 38 744 przy 10 identyfikatorach na stronie), więc koszt aktualizacji
        da się pokazać **przed** pobraniem czegokolwiek."""
        response = self._request(
            self._url("zmiana", self._changes_params(od, do, limit=1)), endpoint="zmiana"
        )
        body = response.body if isinstance(response.body, dict) else {}
        if isinstance(body.get("count"), int):
            return int(body["count"])
        return len(_records_of(body, self._profile.changes_root_key))

    def _changes_params(
        self, od: datetime | date, do: datetime | date, *, limit: int
    ) -> list[tuple[str, str]]:
        fmt = "%Y-%m-%d %H:%M:%S" if isinstance(od, datetime) else self._profile.date_format
        return [
            ("dataod", od.strftime(fmt)),
            ("datado", do.strftime(fmt if isinstance(do, datetime) else self._profile.date_format)),
            ("limit", str(limit)),
        ]

    def iter_changes(
        self, od: datetime | date, do: datetime | date, start: Cursor | None = None
    ) -> Iterator[Page]:
        params = self._changes_params(od, do, limit=self._profile.max_limit_zmiana)
        yield from self._iter_paged(
            self._url("zmiana", params),
            root_key=self._profile.changes_root_key,
            endpoint="zmiana",
            start=start,
            first_index=0,
            limit=self._profile.max_limit_zmiana,
        )


def _records_of(body: Any, root_key: str) -> list[dict[str, Any]]:
    """Lista rekordów spod klucza głównego; identyfikatory (`/zmiana`) opakowuje w `{"id": …}`.

    Tu jest granica, na której identyfikator przestaje być napisem od API i staje się
    tożsamością wpisu: przez tę funkcję przechodzą **wszystkie** rekordy z `/firmy`,
    `/firma`, `/zmiana` i `/raporty`, więc kanonizacja w jednym miejscu obejmuje każdą
    ścieżkę. `kanoniczny_id` rusza wyłącznie GUID-y szesnastkowe, więc identyfikatory
    raportów (nie-hex, istotne co do wielkości liter) przechodzą nietknięte — ADR-0013."""
    if not isinstance(body, dict):
        return []
    raw = body.get(root_key)
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            rid = item.get("id")
            out.append({**item, "id": kanoniczny_id(rid)} if isinstance(rid, str) else item)
        elif isinstance(item, str):
            out.append({"id": kanoniczny_id(item)})
    return out


def _with_page(url: str, page: int) -> str:
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}page={page}"
