"""Orkiestracja: `Criteria` → API/raport → SQLite → eksport. Jedyny moduł znający sieć i bazę.

Przepływ (docs/design/phase2_core.md): `count` → estymacja → run w bazie → strony listy
(checkpoint w tej samej transakcji) → opcjonalnie szczegóły porcjami → eksport wyłącznie
z bazy, w osobnym poleceniu, bez żądań.
"""

from __future__ import annotations

import json
import math
import uuid
import zipfile
from collections.abc import Callable, Collection, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml
from pydantic import ValidationError

from . import __version__
from .apiprofile import ApiProfile, load_profile
from .assistant import Assistant
from .batching import Batch, BatchPlan, refine
from .client import CeidgClient, Cursor
from .clock import Clock, SystemClock, local_hhmm, utc_iso
from .config import DEMO_OSTRZEZENIE, HOST_ENVIRONMENT, Settings, safe_filename
from .criteria import Criteria, bledy_po_polsku
from .errors import (
    CeidgError,
    ConfigError,
    ProdWithoutConsentError,
    ProfileMismatchError,
    ResumableError,
    StoreError,
    StoreLockedError,
)
from .estimating import LARGE_COUNT_THRESHOLD, effective_spacing
from .exporter import (
    MIN_FREE_BYTES,
    check_free_space,
    write_csv,
    write_jsonl,
    write_workbook,
)
from .httpclient import build_http_client
from .logsetup import get_logger
from .normalizer import KOLUMNY_TYLKO_ZE_SZCZEGOLOW, NormalizedRecord, normalize
from .pkdmap import TablicaPkd, load_pkd_map
from .progress import Events, NullEvents
from .ratelimit import RateLimiter
from .recordid import PREFIKS_TRESCI, KanonicznyId, kanoniczne_id
from .records import ZRODLO_RAPORT, Report, RowContext
from .reports import (
    UNFILLED_COLUMNS,
    iter_report_rows,
    matches_criteria,
    pick_registered_report,
    row_to_record,
)
from .safetext import strip_control
from .store import DEFAULT_LOCK_STALE_S, Store
from .store import RunInfo as RunInfo  # jawny re-eksport: `ui` nie importuje store

log = get_logger("pipeline")

# Od ilu sekund przerwa trafia do logu. Niżej niż próg ekranowy (`console.LONG_WAIT_S`),
# bo plik logu nikomu nie miga przed oczami, a diagnostyka po fakcie zyskuje na gęstości.
LOG_WAIT_S = 5.0

RESUME_GAP_S = 180.0
REPORT_PAGE_SIZE = 1000
DETAIL_TTL_DAYS_DEFAULT = 7


# ----------------------------------------------------------------------------- zależności


@dataclass
class Deps:
    settings: Settings
    profile: ApiProfile
    store: Store
    clock: Clock
    events: Events
    client: CeidgClient | None = None
    limiter: RateLimiter | None = None
    # Asystent jest **opcjonalny** i budowany tylko, gdy jest klucz i extra `asystent`.
    # `pipeline` nie zyskuje przez to twardej zależności od SDK: konkret powstaje za importem
    # wewnątrz funkcji, a tutaj stoi protokół z warstwy czystej.
    assistant: Assistant | None = None
    # Powód nieobecności asystenta, gdy go nie ma. Bez tego `None` gubi informację, a komunikat
    # dla operatora musiał **zgadywać** przyczynę — przebieg B6 pokazał, że przy braku słownika
    # PKD mówił „brak klucza API albo pakietu anthropic", czyli wskazywał dwie rzeczy, które
    # akurat były na miejscu.
    assistant_reason: str | None = None
    # Tablica przejścia PKD 2007 → 2025 (ADR-0012). Opcjonalna z tego samego powodu co asystent:
    # brak pliku ma wyłączyć rozszerzanie, a nie całe narzędzie. Nieobecność znaczy „pytaj i
    # pobieraj jak dotąd", czyli dzisiejsze zachowanie.
    pkd_map: TablicaPkd | None = None
    warnings: list[str] = field(default_factory=list)
    # Jedno bicie serca na `Deps`, żeby detektor snu widział **wszystkie** bicia — także te
    # z plastrów limitera. Ustawiane w `build_deps`; `None` tylko w ręcznie składanych
    # atrapach, gdzie pętle same je tworzą.
    heartbeat: LockHeartbeat | None = None
    # Tryb pokazu (ADR-0014). Stoi w `Deps`, bo znacznik ma dotrzeć wszędzie tam, dokąd
    # dochodzą zależności: na pierwszy ekran kreatora i do arkusza `Metadane`. Wywodzenie
    # go z wartości tokenu albo ze ścieżki katalogu byłoby zgadywaniem, a to jest fakt,
    # który korzeń kompozycji zna wprost.
    demo: bool = False


class LockLostError(ResumableError):
    """Blokadę bazy przejął inny proces — run kończy się jako `przerwany`, checkpoint stoi."""


class LockHeartbeat:
    """Bicie serca blokady bazy — i jedyne miejsce, które zauważa, że dzierżawa przepadła.

    Dwie rzeczy, których `store.touch_lock()` sam nie zrobi. Po pierwsze, reaguje na `False`:
    próbuje odzyskać blokadę (jeśli poprzednia wygasła i nikt jej nie zajął, to się uda), a gdy
    trzyma ją naprawdę kto inny, przerywa run zamiast pisać do bazy bez dzierżawy. Przerwanie
    jest `ResumableError`, więc checkpoint zostaje i operator dostaje `wznow`.

    Po drugie, mierzy skok zegara **ściennego** między biciami. 2026-09-08 maszyna operatora
    spała 9 h 50 min w środku `aktualizuj`: zamrożony proces nie bije, więc blokada wygasła,
    a w logu została po tym dziura nie do odróżnienia od czekania. Skok większy niż próg
    wygaśnięcia jest nazywany po imieniu, bo tłumaczy naraz rozjechany czas zakończenia,
    pustą historię żądań i wygasłą blokadę.

    **Jedna instancja na `Deps`, nie po jednej na pętlę.** Detektor snu odejmuje od siebie
    dwa kolejne bicia, więc ma sens wyłącznie wtedy, gdy widzi **wszystkie** — także te
    z plastrów limitera. Pierwsza wersja wstrzykiwała limiterowi `store.touch_lock`, czyli
    odświeżała dzierżawę w bazie, nie ruszając tego znacznika; godzinny postój limitera
    wciąż meldował się wtedy jako sen maszyny, a komentarze twierdziły, że jest inaczej.
    Przy jednym obiekcie własne czekanie nie jest snem, a sen **w środku** czekania nadal
    jest wykrywany."""

    def __init__(self, store: Store, events: Events, clock: Clock) -> None:
        self._store = store
        self._events = events
        self._clock = clock
        self._ostatnie = clock.wall()

    def bij(self) -> None:
        teraz = self._clock.wall()
        przerwa = teraz - self._ostatnie
        self._ostatnie = teraz
        if przerwa > DEFAULT_LOCK_STALE_S:
            komunikat = (
                f"Przerwa w pracy programu: {przerwa / 60:.0f} min "
                "(komputer prawdopodobnie spał). Blokada bazy mogła w tym czasie wygasnąć."
            )
            # Własny wpis w logu, mimo że `_LogEvents.on_message` też go zapisze: ten jest
            # na poziomie WARNING i nie zależy od tego, czy dekorator logujący został
            # założony. Anomalia, która milknie razem z dekoratorem, jest dokładnie tym
            # kształtem, który ta faza zamyka — dwa wpisy o różnych poziomach to tania cena.
            log.warning("%s", komunikat)
            self._events.on_message(komunikat)
        if self._store.touch_lock() or not self._store.holds_lock:
            # Brak wiersza `run_lock` przy operacji, która blokady nie brała (eksport, `runy`),
            # to nie utrata dzierżawy — bez tego warunku bicie serca po cichu **brałoby**
            # blokadę, której ta operacja nie potrzebuje.
            return
        log.warning("blokada bazy przepadła — próba odzyskania")
        try:
            self._store.acquire_lock(force=False)
        except StoreLockedError as exc:
            raise LockLostError(
                "Blokadę bazy przejął w międzyczasie inny proces, więc przerywam, "
                f"żeby nie pisać do niej równolegle. {exc}"
            ) from exc
        self._events.on_message(
            "Blokada bazy wygasła (długa przerwa w pracy) i została odzyskana; pracuję dalej."
        )


class _LogEvents:
    """Zdarzenia przepisywane do pliku logu — czekanie zostawia ślad, który przeżywa sesję.

    Do 2026-09-08 log niósł wyłącznie żądania, ostrzeżenia o ponowieniach i błąd końcowy.
    `on_wait` szedł tylko na ekran, a `ConsoleEvents` tłumi zresztą przerwy krótsze niż 5 s.
    Skutek zobaczyliśmy na przebiegu z 2026-09-07/08: w logu były dwie dziury, godzinna
    i dziesięciogodzinna, **nie do odróżnienia** od siebie ani od postoju limitera, bo jedynym
    śladem był brak śladu. (Obie okazały się snem maszyny — hamulec budżetowy odpala dopiero
    przy `remaining <= 10`, a ostatnie żądanie przed przerwą meldowało 214. Ale rozstrzygnąć
    to dało się dopiero z licznika budżetu w innej linii, nie z samego kształtu przerwy —
    i o to właśnie chodzi.) A log jest tym, co zostaje po sesji.

    Stąd zapisywana jest **przewidywana** godzina wznowienia: linia „czekam do 02:14" obok
    następnej linii ostemplowanej na 12:04 czyni dziesięciogodzinną przerwę policzalną
    i przypisywalną. Sam czas trwania tego nie daje.

    Dekorator przy szwie kompozycji, obok `_LockHeartbeatEvents` — jeden szew zamiast
    logowania w `ratelimit`, `client` i `caller` osobno."""

    def __init__(self, inner: Events, clock: Clock) -> None:
        self._inner = inner
        self._clock = clock

    def on_request(self, endpoint: str, status: int, elapsed_s: float) -> None:
        self._inner.on_request(endpoint, status, elapsed_s)

    def on_wait(self, seconds: float, reason: str, resume_at_epoch: float) -> None:
        if seconds >= LOG_WAIT_S:
            log.info(
                "czekam %.0f s (%s), wznowienie planowane na %s",
                seconds,
                reason,
                local_hhmm(resume_at_epoch),
            )
        self._inner.on_wait(seconds, reason, resume_at_epoch)

    def on_page(self, page_index: int, records: int, total: int | None) -> None:
        self._inner.on_page(page_index, records, total)

    def on_details(self, done: int, total: int) -> None:
        self._inner.on_details(done, total)

    def on_export(self, done: int, total: int) -> None:
        self._inner.on_export(done, total)

    def on_download(self, done_bytes: int, total_bytes: int | None) -> None:
        self._inner.on_download(done_bytes, total_bytes)

    def on_model(self, elapsed_s: float, tokens: int) -> None:
        self._inner.on_model(elapsed_s, tokens)

    def on_message(self, text: str) -> None:
        # Tekst bywa z rejestru, a plik logu jest drugim kanałem czytanym przez terminal
        # (`type`, `cat`, `tail`) — sekwencje sterujące neutralizuje `strip_control`,
        # tak samo jak `richtext.safe` robi to dla ekranu (reguła granic 10 zna tylko `rich`).
        log.info("%s", strip_control(text))
        self._inner.on_message(text)

    def close(self) -> None:
        self._inner.close()


class _LockHeartbeatEvents:
    """Zdarzenia z biciem serca blokady przy każdym długim czekaniu.

    Odstępy limitera, pełna blokada po 429 i drabinka ponowień transportu to jedyne miejsca,
    w których proces stoi długo i nic nie robi — i do 2026-09-07 jedyne, w których nie miał
    czym odświeżyć blokady bazy. Cztery nieudane próby połączenia to 10+30+60+300 s plus
    limity czasu żądań, czyli do ~640 s, a `DEFAULT_LOCK_STALE_S` wynosi 600: blokada
    potrafiła wygasnąć pod procesem, który pracował. Postęp pobierania zasypał tę dziurę
    tylko dla transferu, w którym **płyną bajty**.

    `on_wait` pada **przed** uśpieniem (`RateLimiter.acquire`), więc bicie zawsze wyprzedza
    przerwę, a nie idzie za nią. `store.touch_lock()` jest warunkowane na PID, więc gdy ten
    proces akurat blokady nie trzyma — eksport, `runy`, sonda — to zwyczajny brak akcji.

    Dekorator, a nie linia w każdej pętli: takich pętli jest pięć, a `on_wait` zna każde
    czekanie w programie. Siedzi w `pipeline`, bo tylko ten moduł ma prawo dotknąć bazy
    (reguła granic 5) — `client` i `ratelimit` dostają zwykły protokół `Events`.
    """

    def __init__(self, inner: Events, store: Store) -> None:
        self._inner = inner
        self._store = store

    def on_request(self, endpoint: str, status: int, elapsed_s: float) -> None:
        self._inner.on_request(endpoint, status, elapsed_s)

    def on_wait(self, seconds: float, reason: str, resume_at_epoch: float) -> None:
        self._store.touch_lock()
        self._inner.on_wait(seconds, reason, resume_at_epoch)

    def on_page(self, page_index: int, records: int, total: int | None) -> None:
        self._inner.on_page(page_index, records, total)

    def on_details(self, done: int, total: int) -> None:
        self._inner.on_details(done, total)

    def on_export(self, done: int, total: int) -> None:
        self._inner.on_export(done, total)

    def on_download(self, done_bytes: int, total_bytes: int | None) -> None:
        self._inner.on_download(done_bytes, total_bytes)

    def on_model(self, elapsed_s: float, tokens: int) -> None:
        self._inner.on_model(elapsed_s, tokens)

    def on_message(self, text: str) -> None:
        self._inner.on_message(text)

    def close(self) -> None:
        self._inner.close()


def build_deps(
    settings: Settings,
    *,
    events: Events | None = None,
    clock: Clock | None = None,
    http: httpx.Client | None = None,
    online: bool = True,
) -> Deps:
    """Składa zależności; `online=False` nie tworzy klienta (eksport, listowanie runów)."""
    clock = clock or SystemClock()
    events = events or NullEvents()
    profile = load_profile(settings.environment, settings.profile_path)
    host = urlsplit(profile.base_url).hostname or ""
    if HOST_ENVIRONMENT.get(host) != settings.environment:
        raise ProdWithoutConsentError(
            f"Profil API wskazuje host {host} (środowisko {HOST_ENVIRONMENT.get(host)}), "
            f"a wybrane środowisko to {settings.environment}. Użyj --srodowisko prod "
            "--produkcja albo popraw profil."
        )
    store = Store(settings.store_path, environment=settings.environment, clock=clock)
    events = _LogEvents(_LockHeartbeatEvents(events, store), clock)
    deps = Deps(settings=settings, profile=profile, store=store, clock=clock, events=events)
    try:
        deps.pkd_map = load_pkd_map()
    except ConfigError as exc:
        # Brak tablicy wyłącza rozszerzanie o rocznik 2007, a nie narzędzie. Ale milczeć nie
        # wolno: bez niej zapytanie z filtrem PKD po cichu pomija firmy, które nie przeszły
        # jeszcze na PKD 2025 — a „po cichu" jest tu całym defektem (ADR-0012).
        deps.warnings.append(f"{exc} Wyszukiwanie po PKD obejmie tylko kody PKD 2025.")
    if store.quarantined is not None:
        deps.warnings.append(
            f"Baza była uszkodzona i została odłożona jako {store.quarantined.name}; "
            "praca zaczyna się od nowej bazy."
        )
    if store.journal_mode not in ("wal", "memory"):
        deps.warnings.append(
            f"Baza pracuje w trybie dziennika '{store.journal_mode}' zamiast WAL. "
            "Przerwanie procesu może być mniej odporne; sprawdź, czy plik bazy nie leży "
            "na dysku sieciowym."
        )
    if store.merged_duplicates or store.renamed_identifiers:
        # Migracja zmienia bazę pod operatorem, więc ma o tym powiedzieć — inaczej zastaje
        # inne liczby niż wczoraj i nie wie dlaczego (ADR-0013, decyzja 3).
        czesci = []
        if store.merged_duplicates:
            czesci.append(f"{store.merged_duplicates} zapisanych dwukrotnie (scalono w jeden)")
        if store.renamed_identifiers:
            czesci.append(f"{store.renamed_identifiers} z samą starą pisownią identyfikatora")
        komunikat = (
            "Ujednolicono identyfikatory wpisów w bazie: " + ", ".join(czesci) + ". "
            "Żadne dane nie zostały usunięte."
        )
        deps.warnings.append(komunikat)
        # Do logu też, i to jest tu istotne: `merged_duplicates` jest jednorazowe — przy każdym
        # kolejnym otwarciu wynosi zero. Gdyby pierwszym poleceniem po aktualizacji było takie,
        # które akurat nie wypisuje ostrzeżeń, meldunek przepadłby bezpowrotnie. Plik logu
        # przeżywa proces, więc przestaje to być kwestią trafienia we właściwą komendę.
        log.warning("%s", komunikat)
    # Jedno bicie serca dla całego kompletu zależności: detektor snu odejmuje od siebie dwa
    # kolejne bicia, więc musi widzieć także te z plastrów limitera.
    serce = LockHeartbeat(store, events, clock)
    deps.heartbeat = serce
    if online:
        store.trim_request_log()
        limiter = RateLimiter(
            windows=profile.rate.windows,
            min_spacing_s=profile.rate.min_spacing_s,
            cooldown_s=profile.rate.cooldown_s,
            clock=clock,
            history=store.history(settings.token_fp),
            events=events,
            # Bicie serca w środku długiego postoju. Postój budżetowy bywa przycięty do
            # najdłuższego okna (3600 s), a dzierżawa blokady gaśnie po 600 s — bez tego
            # jedno czekanie zabijało ją pod pracującym procesem. Idzie tym samym obiektem,
            # który mierzy przerwy, więc własne czekanie nie melduje się jako sen maszyny.
            heartbeat=serce.bij,
        )
        client = CeidgClient(
            # Bramka wyjścia zawężona do hosta wybranego środowiska: podczas pracy na
            # teście `links.next` wskazujący produkcję nie ma prawa opuścić procesu.
            http=http or build_http_client(allowed=frozenset({host})),
            profile=profile,
            limiter=limiter,
            token=settings.token,
            clock=clock,
            events=events,
        )
        deps.limiter = limiter
        deps.client = client
        deps.assistant, deps.assistant_reason = _build_assistant(settings, events)
    return deps


def _build_assistant(settings: Settings, events: Events) -> tuple[Assistant | None, str | None]:
    """Asystent, gdy da się go zbudować — i **powód**, gdy się nie da.

    Instrukcja wymaga, żeby kreator i CLI działały bez asystenta, więc żaden z powodów nie jest
    błędem: wszystkie kończą się `None` i pytaniami po kolei. Ale powód wraca razem z `None`,
    bo bez niego komunikat dla operatora musiał zgadywać — a zgadywał źle (przebieg B6).

    Import konkretnego wywołującego siedzi tutaj, żeby `pipeline` dał się zaimportować na
    maszynie bez `anthropic`."""
    if not settings.anthropic_key:
        return None, None  # brak klucza to stan normalny; zdanie o nim ma `texts`
    try:
        from .assistant.caller import AnthropicCaller
        from .assistant.pkd import load_pkd
    except ImportError:
        return None, None
    try:
        return AnthropicCaller(
            api_key=settings.anthropic_key, slownik=load_pkd(), events=events
        ), None
    except CeidgError as exc:
        # Najczęściej brak słownika PKD, i wtedy `load_pkd` mówi wprost, jak go zbudować.
        # Ta treść jest cenniejsza niż nasza domyślna, więc idzie dalej zamiast zniknąć.
        return None, str(exc)


def _client(deps: Deps) -> CeidgClient:
    if deps.client is None:
        raise ConfigError("Ta operacja wymaga połączenia z API (deps zbudowane offline).")
    return deps.client


# ----------------------------------------------------------------------------- kryteria z pliku


def criteria_from_yaml(path: Path) -> Criteria:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Nie można wczytać zapytania {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Plik zapytania {path} musi być mapą pole: wartość.")
    try:
        return Criteria.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"Niepoprawne kryteria w {path}:\n{exc}") from exc


# ----------------------------------------------------------------------------- pobieranie


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    records: int
    details: int
    pages: int
    count_api: int | None
    requests: int
    # Wpisy runu bez szczegółów i bez wyjaśnienia. Zero jest normą; cokolwiek innego znaczy,
    # że praca została wykonana i zgubiona, a podsumowanie ma o tym powiedzieć (ADR-0013).
    unresolved: int = 0
    # Wpisy, których szczegół pochodzi sprzed **końca okna, w którym rejestr je zgłosił** —
    # czyli takie, o których powiedziano „zmienił się", a w bazie został opis sprzed zmiany.
    # Liczba **wpisów**, nie wystąpień: przy długim zakresie ta sama firma bywa zgłoszona
    # w dwóch oknach. Tylko dla `aktualizuj`; zero jest jedyną poprawną wartością. Bez tego
    # pola złamanie gwarancji nie miało obserwatora — `unresolved` nie może zadziałać, bo
    # taki wpis jest `pobrany` (audyt 2026-09-08, A1).
    stale_details: int = 0


def count_hits(criteria: Criteria, deps: Deps) -> int:
    return _client(deps).count(criteria)


def find_resumable(criteria: Criteria, deps: Deps) -> RunInfo | None:
    return deps.store.find_resumable_run(
        criteria.fingerprint(), profile_hash=deps.profile.profile_hash()
    )


def list_resumable(deps: Deps, *, kinds: Sequence[str] = ("firmy",)) -> list[RunInfo]:
    """Niedokończone pobrania niezależnie od kryteriów — menu pokazuje je bez pytania o filtr.

    Filtr idzie do zapytania, nie za nie: odsiewanie po `LIMIT 20` sprawiało, że przerwany
    run znikał z menu, gdy tylko powstało dwadzieścia nowszych zakończonych."""
    return deps.store.list_runs(statuses=("przerwany", "w_toku"), kinds=tuple(kinds))


def _acquire_lock(deps: Deps, *, force: bool) -> None:
    """Blokada plus ostrzeżenie, gdy `--force` zabrało ją procesowi, który wciąż żył."""
    if deps.store.acquire_lock(force=force):
        deps.events.on_message(
            "Flaga --force przejęła blokadę procesu, który wciąż zapisywał dane. Jeśli on żyje, "
            "przerwij jedno z pobrań — dwa naraz zderzą się o limit API i mogą zostawić run "
            "oznaczony jako zakończony, zanim naprawdę się skończy."
        )


def run_fetch(
    criteria: Criteria,
    deps: Deps,
    *,
    resume_run_id: str | None = None,
    known_count: int | None = None,
    force_lock: bool = False,
) -> RunResult:
    """Pobiera listę (i szczegóły, gdy `criteria.szczegoly`) do bazy. Wznawia z checkpointu."""
    client = _client(deps)
    store = deps.store
    _acquire_lock(deps, force=force_lock)
    try:
        if resume_run_id:
            run = store.get_run(resume_run_id)
            if run.kind != "firmy":
                raise ConfigError(
                    f"Run {run.run_id} to pobranie typu {run.kind!r} — nie da się go wznowić "
                    "przez API. Powtórz odpowiednie polecenie (raport pobiera się jednym "
                    "żądaniem, `aktualizuj` liczy zmiany od znacznika)."
                )
            if run.profile_hash != deps.profile.profile_hash():
                raise ProfileMismatchError(
                    f"Run {run.run_id} pobierano z innym profilem API — nie można go wznowić."
                )
            criteria = Criteria.model_validate_json(run.criteria_json)
            run_id = run.run_id
            store.update_run_status(run_id, "w_toku")
            if deps.limiter is not None:
                wait = deps.limiter.enforce_resume_gap(RESUME_GAP_S)
                if wait > 0:
                    deps.events.on_message(
                        f"Wznowienie: odczekuję {wait:.0f} s od ostatniego żądania sprzed przerwy."
                    )
        else:
            run_id = store.start_run(
                run_id=str(uuid.uuid4()),
                criteria_json=criteria.canonical_json(),
                criteria_hash=criteria.fingerprint(),
                profile_hash=deps.profile.profile_hash(),
                mode="szczegoly" if criteria.szczegoly else "lista",
                tool_version=__version__,
                cursor_mode=deps.profile.paging_mode,
            )
            if known_count is not None:
                store.set_run_count(run_id, known_count)

        try:
            _fetch_list(criteria, deps, run_id)
            if criteria.szczegoly:
                _fetch_details(deps, run_id)
            store.set_stage(run_id, "gotowe")
            store.update_run_status(run_id, "zakonczony")
        except (ResumableError, KeyboardInterrupt) as exc:
            reason = (
                "przerwane przez użytkownika" if isinstance(exc, KeyboardInterrupt) else str(exc)
            )
            store.update_run_status(run_id, "przerwany", error=reason)
            log.warning("run %s przerwany: %s", run_id, reason)
            raise
        except CeidgError as exc:
            store.update_run_status(run_id, "blad", error=str(exc))
            raise
    finally:
        store.release_lock()
        # Pasek gaśnie razem z operacją, a nie dopiero na końcu sesji. Póki żył, kreator
        # rysował pod nim podsumowanie, pytanie o zapis i menu — operator widział ciszę
        # i program czekający na odpowiedź, której nie było widać (bramka 3, 2026-09-06).
        deps.events.close()

    run = store.get_run(run_id)
    return RunResult(
        run_id=run_id,
        status=run.status,
        records=store.count_run_records(run_id),
        details=store.count_run_details(run_id),
        pages=run.pages_done,
        count_api=run.count_api,
        requests=client.requests_made,
        # Tylko przy `szczegoly=True`: w trybie listy każdy wpis z definicji zostaje w stanie
        # `brak`, więc licznik byłby szumem, a nie alarmem. Przy szczegółach obowiązuje ten sam
        # niezmiennik co w `aktualizuj` — `fetch_details` gwarantuje, że `found ∪ missing`
        # pokrywa porcję — więc cokolwiek zostało w `brak` znaczy pracę wykonaną i zgubioną.
        unresolved=store.count_run_unresolved(run_id) if criteria.szczegoly else 0,
    )


def _fetch_list(criteria: Criteria, deps: Deps, run_id: str) -> None:
    serce = deps.heartbeat or LockHeartbeat(deps.store, deps.events, deps.clock)
    client = _client(deps)
    store = deps.store
    checkpoint = store.get_checkpoint(run_id)
    if checkpoint is not None and checkpoint.stage != "lista":
        return
    start: Cursor | None = None
    first_index = 0
    if checkpoint is not None and checkpoint.page_index > 0:
        if checkpoint.cursor is None:
            return  # lista skończona, etap nie został jeszcze przełączony
        start = Cursor(checkpoint.cursor_mode, checkpoint.cursor)
        first_index = checkpoint.page_index
    seen = store.count_run_records(run_id)
    for page in client.iter_pages(criteria, start=start, first_index=first_index):
        if page.count is not None and store.get_run(run_id).count_api is None:
            store.set_run_count(run_id, page.count)
        records = page.records
        next_cursor = page.next_cursor.value if page.next_cursor else None
        if criteria.max_rekordow is not None and seen + len(records) >= criteria.max_rekordow:
            records = records[: max(0, criteria.max_rekordow - seen)]
            next_cursor = None
        store.save_page(run_id, page_index=page.index, records=records, next_cursor=next_cursor)
        serce.bij()
        seen += len(records)
        if next_cursor is None:
            break
    store.set_stage(run_id, "szczegoly" if criteria.szczegoly else "gotowe")


def _fetch_details(deps: Deps, run_id: str) -> None:
    serce = deps.heartbeat or LockHeartbeat(deps.store, deps.events, deps.clock)
    client = _client(deps)
    store = deps.store
    pending = store.pending_detail_ids(run_id, ttl_days=deps.settings.cache_ttl_days)
    total = len(pending)
    done = 0
    batch = deps.profile.ids_batch_size
    if total:
        # Pasek pojawia się przed pierwszym żądaniem, nie po nim. `run_update` dostał ten
        # znak życia przy poprzedniej poprawce, zwykłe pobieranie szczegółów nie — ta sama
        # asymetria trzech bliźniaczych ścieżek, co przy obsłudze Ctrl+C.
        deps.events.on_details(0, total)
    for i in range(0, total, batch):
        chunk = pending[i : i + batch]
        found, missing = client.fetch_details(chunk)
        store.save_details(details=found, missing_ids=missing)
        serce.bij()
        done += len(chunk)
        deps.events.on_details(done, total)


# ----------------------------------------------------------------------------- raporty


def choose_report(criteria: Criteria, deps: Deps) -> Report | None:
    """Najnowszy raport CSV pokrywający zapytanie (dokładnie jedno województwo) albo `None`."""
    if len(criteria.wojewodztwo) != 1:
        return None
    reports = _client(deps).list_reports()
    return pick_registered_report(reports, criteria.wojewodztwo[0])


def run_report_fetch(
    criteria: Criteria, deps: Deps, report: Report, *, force_lock: bool = False
) -> RunResult:
    """Pobiera raport (1 żądanie), filtruje lokalnie wg kryteriów i zapisuje jako run."""
    client = _client(deps)
    store = deps.store
    serce = deps.heartbeat or LockHeartbeat(deps.store, deps.events, deps.clock)
    wojewodztwo = criteria.wojewodztwo[0] if criteria.wojewodztwo else None
    dest = (
        deps.settings.data_dir
        / "raporty"
        / safe_filename(f"{report.nazwa}_{report.utworzono[:10]}", ".zip")
    )
    _acquire_lock(deps, force=force_lock)
    try:
        run_id = store.start_run(
            run_id=str(uuid.uuid4()),
            criteria_json=criteria.canonical_json(),
            criteria_hash=criteria.fingerprint(),
            profile_hash=deps.profile.profile_hash(),
            mode="lista",
            tool_version=__version__,
            cursor_mode="numeric",
            kind="raport",
        )

        def download_progress(done_bytes: int, total_bytes: int | None) -> None:
            """Pasek i bicie serca blokady jednym ruchem, co pół megabajta transferu.

            Blokada należy do `pipeline` (reguła granic 5), więc klient dostaje wywołanie
            zwrotne, a nie `store`. Bez tego między `_acquire_lock` a pierwszym
            `touch_lock()` — tysiąc wierszy CSV dalej — leżało 21 MB transferu bez jednego
            znaku życia, przy `DEFAULT_LOCK_STALE_S` równym 600 s.

            To zasypuje wyłącznie transfer, w którym **płyną bajty**. Czekanie przed
            żądaniem i po nieudanej próbie obsługuje `_LockHeartbeatEvents.on_wait` — jedno
            i drugie jest potrzebne, bo drabinka ponowień potrafi nie dojść do nagłówków
            ani razu i wtedy to wywołanie nie padnie w ogóle."""
            serce.bij()
            deps.events.on_download(done_bytes, total_bytes)

        try:
            if not dest.exists():
                deps.events.on_message(
                    f"Pobieram raport {report.nazwa} — jeden plik ZIP, kilkadziesiąt MB."
                )
                client.download_report(report, dest, progress=download_progress)
            elif not _readable_archive(dest):
                # Plik z cache bywa uszkodzony (przerwane pobieranie, pełny dysk). Warunek
                # `not dest.exists()` widział go jako gotowy, więc każda kolejna próba kończyła
                # się tym samym błędem — bez wyjścia, bo nikt nie mówił, że trzeba go skasować.
                deps.events.on_message(
                    f"Raport w pamięci podręcznej był uszkodzony ({dest.name}) — usuwam go "
                    "i pobieram jeszcze raz."
                )
                dest.unlink(missing_ok=True)
                client.download_report(report, dest, progress=download_progress)
            deps.events.on_message(f"Raport pobrany: {report.nazwa} ({report.utworzono}).")
            matched = 0
            seen_ids: set[str] = set()
            # Dwa liczniki, bo to dwie różne wiadomości. Powtórzony NIP to rejestr
            # wymieniający jeden wpis dwa razy — zwyczajne. Powtórzony skrót treści to
            # **nasz wniosek**, że dwa wiersze opisują tę samą firmę, i może być błędny
            # (ADR-0016). Jeden licznik na oba kazałby operatorowi zgadywać, co się stało.
            duplicates = 0
            duplikaty_tresci = 0
            page_index = 0
            scanned = 0
            reported = 0  # dopasowania, które już trafiły na pasek
            buffer: list[dict[str, Any]] = []
            for row in iter_report_rows(dest):
                scanned += 1
                record = row_to_record(row, wojewodztwo=wojewodztwo)
                if matches_criteria(record, criteria):
                    rid = str(record["id"])
                    if rid in seen_ids:
                        # wygrywa późniejszy wiersz
                        if rid.startswith(PREFIKS_TRESCI):
                            duplikaty_tresci += 1
                        else:
                            duplicates += 1
                    else:
                        seen_ids.add(rid)
                        matched += 1
                    buffer.append(record)
                if scanned % REPORT_PAGE_SIZE == 0:
                    # Rytm wyznaczają wiersze **przeczytane**, nie **dopasowane**: pracą tej
                    # pętli jest zmapowanie każdego wiersza, a filtr odrzuca go dopiero po
                    # `row_to_record`. Przy wąskich kryteriach (miasto w raporcie wojewódzkim)
                    # licznik dopasowań nie dobijał do progu ani razu, więc pasek stał, a
                    # blokada nie dostawała bicia serca przez całe 287 tys. wierszy.
                    serce.bij()
                    deps.events.on_page(page_index, matched - reported, None)
                    reported = matched
                if criteria.max_rekordow is not None and matched >= criteria.max_rekordow:
                    break
                if len(buffer) >= REPORT_PAGE_SIZE:
                    store.save_page(
                        run_id,
                        page_index=page_index,
                        records=buffer,
                        next_cursor=str(page_index + 1),
                        zrodlo="CEIDG_RAPORT",
                    )
                    page_index += 1
                    buffer = []
            store.save_page(
                run_id,
                page_index=page_index,
                records=buffer,
                next_cursor=None,
                zrodlo="CEIDG_RAPORT",
            )
            deps.events.on_page(page_index, matched - reported, matched)
            store.set_run_count(run_id, matched)
            if duplicates:
                deps.events.on_message(
                    f"Raport zawierał {duplicates} wierszy o powtórzonym identyfikatorze "
                    "(NIP/REGON); zachowano po jednym rekordzie."
                )
            if duplikaty_tresci:
                # Osobne zdanie, bo osobna sytuacja i osobna pewność. Wierszy bez NIP i bez
                # REGON było w zmierzonym archiwum 315 i **żadne dwa** nie miały tych samych
                # czterech pól tożsamości. Zero zmierzone raz nie jest zerem na zawsze, więc
                # zlanie się dwóch firm w jedną musi mieć obserwatora — i musi powiedzieć, że
                # jest wnioskiem, a nie odczytem z rejestru.
                deps.events.on_message(
                    f"{duplikaty_tresci} wierszy bez NIP i bez REGON miało tę samą nazwę, "
                    "nazwisko, imię i datę rozpoczęcia, więc potraktowano je jako jeden wpis. "
                    "To wniosek narzędzia, nie dana z rejestru — warto te wpisy sprawdzić."
                )
            store.set_stage(run_id, "gotowe")
            store.update_run_status(run_id, "zakonczony")
        except (ResumableError, KeyboardInterrupt) as exc:
            # `run_fetch` obsługiwał to od początku, ścieżka raportu nie: Ctrl+C przechodził
            # na zewnątrz i zostawiał run w `w_toku` na zawsze — a run w tym statusie nie
            # schodził z retencji, więc dane osobowe z raportu zostawały w bazie bezterminowo.
            reason = (
                "przerwane przez użytkownika" if isinstance(exc, KeyboardInterrupt) else str(exc)
            )
            store.update_run_status(run_id, "przerwany", error=reason)
            raise
        except CeidgError as exc:
            store.update_run_status(run_id, "blad", error=str(exc))
            raise
    finally:
        store.release_lock()
        deps.events.close()
    run = store.get_run(run_id)
    return RunResult(
        run_id=run_id,
        status=run.status,
        records=store.count_run_records(run_id),
        details=0,
        pages=run.pages_done,
        count_api=run.count_api,
        requests=client.requests_made,
    )


# ----------------------------------------------------------------------------- zmiany


UPDATE_WINDOW_DAYS = 5
UPDATE_SCOPE = "zmiana"


def update_scope(deps: Deps) -> str:
    return f"{UPDATE_SCOPE}:{deps.settings.environment}"


def update_range(
    deps: Deps, *, since: datetime | None = None, until: datetime | None = None
) -> tuple[datetime, datetime]:
    """Zakres zmian: podany albo od znacznika, a w jego braku ostatnia doba.

    Wspólne dla wyceny i dla pobrania — gdyby każde liczyło własny zakres, tabela kosztów
    opisywałaby inną pracę niż ta, która potem rusza.

    „Teraz" bierze się z wstrzykniętego zegara, nie z `datetime.now()`. Inaczej ta funkcja
    była jedynym miejscem w ścieżce `aktualizuj`, którego test ani demo nie mogły ustawić,
    więc ten sam przebieg dwa razy znaczył co innego.

    Zakres nie wychodzi w przyszłość i nie bywa pusty — jedno i drugie **odmawia**, zamiast
    po cichu przycinać. Przycięcie `--do` zamieniłoby `--od jutro --do pojutrze` w pusty
    zakres i komunikat „nic się nie zmieniło", czyli odpowiedź na pytanie, którego nikt nie
    zadał; a `--od` w przyszłości dawało dokładnie to samo zdanie, tylko bez żadnej odmowy.

    Powód jest o utracie danych, nie o higienie wejścia. `set_watermark` zapisuje koniec
    domkniętego okna, więc znacznik w przyszłości kazałby **następnemu** przebiegowi zacząć
    od tamtego momentu i pominąć wszystko, co zmieni się w międzyczasie. A próg świeżości
    szczegółów to koniec okna, więc leżący w przyszłości znaczyłby „szczegół musi pochodzić
    z przyszłości" i każdy przebieg kupowałby wszystko od nowa.

    Kontrola stoi **tutaj**, bo to jedyne miejsce, które w ogóle widzi `since` obok `until`,
    a `run_update` jest publicznym wejściem i `cli` nie jest jego jedynym wołającym.
    `cli._koniec_zakresu_zmian` zostaje jako wcześniejszy i czytelniejszy komunikat: ta sama
    celowa dwuwarstwowość, co `client._checked_host` obok `AllowedHostsTransport` (reguła
    granic 11), gdzie jedna warstwa tłumaczy, a druga obowiązuje niezależnie od tego, czy
    ktoś o niej pamiętał. `wizard.handle_update` przyjmuje zakres z myślą o trybie demo
    (ADR-0014) i dziś nikt mu go nie podaje — to przyszły wołający, nie dzisiejszy argument
    za tą kontrolą."""
    teraz = datetime.fromtimestamp(deps.clock.wall(), tz=UTC)
    if until is not None and until > teraz:
        raise ConfigError(
            f"Koniec zakresu zmian wskazuje przyszłość ({until.date().isoformat()}). "
            "Rejestr zgłasza tylko zmiany, które już nastąpiły."
        )
    now = until if until is not None else teraz
    # Początek rozstrzyga się **przed** kontrolą, na wszystkich trzech gałęziach. Kontrola
    # tylko w gałęzi `since is not None` przepuszczała dwa realne przypadki, oba dopiero
    # z flagą `--do`: `--od 2026-09-05 --do 2026-09-05` (naturalny zapis „zmiany z 5 września",
    # bo obie flagi biorą daty, a data znaczy północ) oraz `--do` wcześniejsze niż niewidoczny
    # znacznik. Oba dawały pusty podział i komunikat „Baza jest aktualna", bez jednego żądania —
    # czyli dokładnie tę odpowiedź, której ta funkcja odmawia trzy linijki wyżej.
    #
    # Gałąź ze znacznikiem jest najważniejsza: początek pochodzi wtedy z bazy, a nie od
    # operatora, więc to jedyny przypadek, w którym nie ma on jak zobaczyć, co mu powiedziano.
    if since is not None:
        start = since
    else:
        mark = deps.store.get_watermark(update_scope(deps))
        start = (
            datetime.fromisoformat(mark.replace("Z", "+00:00")) if mark else now - timedelta(days=1)
        )
    if start >= now:
        # Godzina, nie sama data: przy zakresie w obrębie jednego dnia komunikat z samą datą
        # wypisywałby dwa razy to samo i nie tłumaczyłby niczego.
        raise ConfigError(
            f"Zakres zmian jest pusty: początek ({start:%Y-%m-%d %H:%M}) nie jest wcześniejszy "
            f"niż koniec ({now:%Y-%m-%d %H:%M} UTC)."
        )
    return start, now


def update_windows(since: datetime, until: datetime) -> list[tuple[datetime, datetime]]:
    """Podział na okna po 5 dni — tyle, ile zaleca API dla `/zmiana`."""
    windows: list[tuple[datetime, datetime]] = []
    start = since
    while start < until:
        end = min(start + timedelta(days=UPDATE_WINDOW_DAYS), until)
        windows.append((start, end))
        start = end
    return windows


@dataclass(frozen=True)
class UpdatePlan:
    """Wycena aktualizacji: co obejmuje, ile zmian, ile to zapytań i minut."""

    since: datetime
    until: datetime
    count: int
    windows: int

    def requests(self, profile: ApiProfile) -> int:
        """Górna granica: każda zmiana wymaga szczegółów, jeśli nie ma jej świeżej w cache."""
        return self.windows + math.ceil(self.count / profile.ids_batch_size)

    def seconds(self, profile: ApiProfile) -> float:
        return self.requests(profile) * effective_spacing(profile)


def plan_update(
    deps: Deps, *, since: datetime | None = None, until: datetime | None = None
) -> UpdatePlan:
    """Ile zmian czeka — jedno tanie żądanie na okno, **przed** pobraniem czegokolwiek.

    Odpowiedź `/zmiana` niesie `count` całego zakresu, więc aktualizacja może przejść tę
    samą sekwencję co pobieranie: policz, pokaż koszt, zapytaj. Wcześniej ruszała od razu
    i operator dowiadywał się o trzydziestu minutach pracy dopiero z paska postępu."""
    client = _client(deps)
    start, end = update_range(deps, since=since, until=until)
    windows = update_windows(start, end)
    total = sum(client.count_changes(od, do) for od, do in windows)
    return UpdatePlan(since=start, until=end, count=total, windows=len(windows))


def run_update(
    deps: Deps,
    *,
    until: datetime | None = None,
    since: datetime | None = None,
    force_lock: bool = False,
) -> RunResult:
    """Tryb `/zmiana`: identyfikatory zmienione od znacznika → szczegóły → cache i nowy run."""
    client = _client(deps)
    store = deps.store
    serce = deps.heartbeat or LockHeartbeat(deps.store, deps.events, deps.clock)
    scope = update_scope(deps)
    since, now = update_range(deps, since=since, until=until)
    windows = update_windows(since, now)
    if len(windows) > 1:
        deps.events.on_message(
            f"Zakres zmian dłuższy niż {UPDATE_WINDOW_DAYS} dni — API zaleca krótsze zakresy; "
            f"dzielę na {len(windows)}."
        )
    criteria_json = json.dumps({"zmiana_od": since.isoformat(), "zmiana_do": now.isoformat()})
    _acquire_lock(deps, force=force_lock)
    try:
        run_id = store.start_run(
            run_id=str(uuid.uuid4()),
            criteria_json=criteria_json,
            criteria_hash=f"zmiana:{since.isoformat()}",
            profile_hash=deps.profile.profile_hash(),
            mode="szczegoly",
            tool_version=__version__,
            cursor_mode=deps.profile.paging_mode,
            kind="zmiana",
        )
        try:
            page_index = 0
            # Zbiór, nie licznik: ta sama firma wraca w dwóch oknach długiego zakresu,
            # a zdanie dla operatora mówi o wpisach, nie o wystąpieniach.
            przestarzale: set[KanonicznyId] = set()
            seen = 0  # przetworzone identyfikatory, narastająco przez wszystkie okna
            total = 0  # suma `count` z okien, o których już wiemy
            for window_start, window_end in windows:
                for page in client.iter_changes(window_start, window_end):
                    ids = kanoniczne_id(str(r["id"]) for r in page.records if r.get("id"))
                    if page.index == 0 and page.count is not None:
                        total += page.count
                    goal = max(total, seen + len(ids))
                    # Powiązanie strony z runem **przed** pobraniem szczegółów. W trybie
                    # `/zmiana` wiersz `firma` powstaje dopiero tutaj, a `save_details`
                    # zapisuje stan `nieznaleziony` przez `UPDATE ... WHERE id = ?` — bez
                    # istniejącego wiersza ten UPDATE trafiał w nic i przepadał po cichu.
                    # Wpis zostawał w stanie `brak`, więc każdy kolejny przebieg kupował go
                    # od nowa: ta sama cicha strata, którą ADR-0013 zamyka od strony pisowni.
                    store.link_ids(run_id, page_index=page_index, ids=ids)
                    # Próg świeżości to **koniec tego okna zmian**, nie TTL cache'u.
                    # `/zmiana` jest sygnałem nieświeżości: skoro rejestr zgłosił ten wpis
                    # jako zmieniony w `[window_start, window_end]`, to szczegół pobrany
                    # przed `window_end` opisuje stan sprzed zmiany. TTL siedmiodniowy
                    # pomijał takie wpisy i zostawiał w bazie starą treść (audyt 2026-09-08,
                    # A1; na bazie operatora 742 z 2 891 identyfikatorów powtórzyło się
                    # między dwoma przebiegami odległymi o 23 godziny).
                    #
                    # Powtórzenie tego samego okna nadal kosztuje zero żądań o szczegóły,
                    # bo wtedy `detail_utc >= window_end`. W obrębie jednego przebiegu
                    # identyfikator obecny w oknie 1 i 3 też jest kupowany raz.
                    # Że `window_end` nie leży w przyszłości, pilnuje `update_range` —
                    # inaczej żaden szczegół nie byłby nigdy dość świeży i każdy przebieg
                    # kupowałby wszystko od nowa.
                    stale = store.stale_detail_ids(ids, cutoff=window_end)
                    batch = deps.profile.ids_batch_size
                    # Znak życia zanim ruszy setka żądań o szczegóły — pasek ma się pojawić
                    # od razu, a nie po pierwszej porcji.
                    deps.events.on_details(seen, goal)
                    for i in range(0, len(stale), batch):
                        f, m = client.fetch_details(stale[i : i + batch])
                        # Zapis po KAŻDEJ porcji, tak jak w `_fetch_details`. Zapis raz na
                        # stronę oznaczał, że przerwanie w środku (Ctrl+C, `LockLostError`)
                        # wyrzuca do kosza nawet 500 identyfikatorów — do stu żądań, ~6 min
                        # pracy — i nie odkłada ich w cache, więc powtórka kupuje je jeszcze raz.
                        store.save_details(details=f, missing_ids=m)
                        # Bicie serca blokady też po porcji, nie po stronie. Blokada wygasa po
                        # 10 minutach, a strona 500 identyfikatorów to około 6,25 min — jedno
                        # 429 (185 s) albo drabinka ponowień wypycha ją ponad próg i blokada
                        # gaśnie pod pracującym procesem, wpuszczając drugi na ten sam token.
                        serce.bij()
                        # Postęp po KAŻDEJ porcji, nie po stronie. Strona to 500 identyfikatorów,
                        # czyli do stu żądań po 3,75 s — ponad sześć minut ciszy, w których
                        # program wygląda na zawieszony i bywa zabijany, choć pracuje.
                        deps.events.on_details(seen + min(i + batch, len(stale)), goal)
                    # Obserwator liczony **tu**, przy oknie, które zna swój koniec i swoje
                    # identyfikatory — a nie raz na cały run. Pytanie raz na run musiałoby
                    # wziąć jeden próg dla wszystkich okien, więc wpis zgłoszony w drugim
                    # oknie ze szczegółem z pierwszego mieściłby się powyżej progu i nie
                    # byłby widziany, choć jest dokładnie tym przypadkiem, o który chodzi.
                    zostale = store.outdated_details(ids, older_than=window_end)
                    if zostale:
                        # Do logu, nie tylko na ekran. Po czterdziestu minutach bez widza
                        # podsumowanie żyje wyłącznie w przewijaniu terminala — a to jest ten
                        # sam kształt, przez który dziesięć godzin czekania nie zostawiło
                        # śladu, bo `on_wait` docierał wyłącznie na ekran.
                        deps.events.on_message(
                            f"Uwaga: {len(zostale)} wpisów z okna kończącego się "
                            f"{utc_iso(window_end.timestamp())} zostało z opisem sprzed "
                            "zgłoszonej zmiany."
                        )
                    przestarzale.update(zostale)
                    page_index += 1
                    seen += len(ids)
                    serce.bij()
                    # Licznik narastający, nie `page_index * len(ids)`: tamto mnożyło numer
                    # strony przez rozmiar **bieżącej** strony, więc na krótszej ostatniej
                    # stronie pasek cofał się (2891 zmian: … 2500, a potem 2346).
                    deps.events.on_details(seen, goal)
                # domknięte okno = trwały postęp: przerwanie nie cofa znacznika o cały zakres
                store.set_watermark(scope, utc_iso(window_end.timestamp()))
            store.set_stage(run_id, "gotowe")
            store.update_run_status(run_id, "zakonczony")
        except (ResumableError, KeyboardInterrupt) as exc:
            store.update_run_status(run_id, "przerwany", error=str(exc) or "przerwane")
            raise
        except CeidgError as exc:
            store.update_run_status(run_id, "blad", error=str(exc))
            raise
    finally:
        store.release_lock()
        deps.events.close()
    run = store.get_run(run_id)
    return RunResult(
        run_id=run_id,
        status=run.status,
        records=store.count_run_records(run_id),
        details=store.count_run_details(run_id),
        pages=run.pages_done,
        count_api=run.count_api,
        requests=client.requests_made,
        unresolved=store.count_run_unresolved(run_id),
        # Zsumowane po oknach, każde ze swoim progiem — patrz komentarz w pętli.
        stale_details=len(przestarzale),
    )


# ----------------------------------------------------------------------- pobranie w partiach


@dataclass(frozen=True)
class BatchOutcome:
    """Los jednej partii — materiał do tabeli podsumowania i do logu."""

    label: str
    status: str
    count: int
    records: int
    run_id: str | None = None


@dataclass(frozen=True)
class BatchResult:
    run_ids: tuple[str, ...]
    records: int
    counted: int
    expected: int
    outcomes: tuple[BatchOutcome, ...]
    requests: int

    @property
    def missing(self) -> int:
        """Trafienia z pierwotnego `count`, których nie objęła żadna partia.

        Do ADR-0015 ta własność miała w docstringu przyczynę („wpisy bez daty rozpoczęcia")
        i to samo zdanie szło na ekran. Przyczyną było co innego: partie wysyłały granice
        dat, których zapytanie niepodzielone nie wysyła, i traciły 2,96 % rekordów na bazie
        operatora. Po naprawie różnica bywa nadal niezerowa — rejestr zmienia się między
        zapytaniem o `count` a pobraniem partii — ale **której przyczyny dotyczy, tego ta
        liczba nie wie**, więc jej nie nazywa."""
        return max(0, self.expected - self.counted)

    @property
    def surplus(self) -> int:
        """Partie zobaczyły **więcej**, niż zapowiedział `count`.

        `max(0, …)` wyżej odrzucał ten przypadek bez śladu, więc ten sam dryf rejestru
        w drugą stronę nie miał ani jednego obserwatora (audyt 2026-09-08). Liczba jest
        nieszkodliwa i właśnie dlatego warto ją zobaczyć: rośnie razem z opóźnieniem między
        wyceną a pobraniem."""
        return max(0, self.counted - self.expected)


def run_batched_fetch(
    plan: BatchPlan,
    deps: Deps,
    *,
    threshold: int = LARGE_COUNT_THRESHOLD,
    on_batch: Callable[[BatchOutcome], None] | None = None,
    force_lock: bool = False,
) -> BatchResult:
    """Pobiera partie planu po kolei. Partia już pobrana jest pomijana bez żądania,
    przerwana wznawiana z checkpointu, a nadal zbyt duża dzielona o poziom drobniej."""
    queue: list[Batch] = list(plan.batches)
    run_ids: list[str] = []
    outcomes: list[BatchOutcome] = []
    counted = 0
    # `--force` zużywa się raz. Martwą blokadę trzeba przejąć tylko przy pierwszej partii;
    # dalej każda partia zwalnia blokadę po sobie, więc powtarzanie wymuszenia oznaczałoby
    # odbieranie jej procesowi, który w międzyczasie wziął ją uczciwie.
    force_next = force_lock

    def emit(outcome: BatchOutcome) -> None:
        outcomes.append(outcome)
        log.info("partia %s: %s (%s trafień)", outcome.label, outcome.status, outcome.count)
        if on_batch is not None:
            on_batch(outcome)

    while queue:
        batch = queue.pop(0)
        fingerprint = batch.fingerprint()
        done = deps.store.find_run(
            fingerprint, statuses=("zakonczony",), profile_hash=deps.profile.profile_hash()
        )
        # Partia o otwartej górnej krawędzi **nigdy nie jest ostateczna**: obejmuje „od daty X
        # w górę", więc rośnie o każdą nową rejestrację. Kafel zamknięty to populacja, która
        # się nie powiększa, i tam „już pobrana" jest prawdą.
        #
        # Do ADR-0015 ostatnia partia niosła `data_do = dzisiaj`, więc jej odcisk zmieniał się
        # z dnia na dzień i pomijanie jej nie groziło. Otwarcie krawędzi ustabilizowało odcisk
        # i zamieniło ten przypadkowy mechanizm w cichą pułapkę: powtórzony `pobierz --partie`
        # meldował „pominięta (już pobrana)" dla **wszystkich** partii i nie przynosił ani
        # jednego nowego rekordu, choć ścieżka niepodzielona zawsze pobiera od nowa
        # (przegląd 2026-09-09). Przerwana partia nadal wznawia się z checkpointu — niżej.
        if done is not None and not batch.otwarty_do:
            run_ids.append(done.run_id)
            counted += done.count_api or done.records_seen
            emit(
                BatchOutcome(
                    label=batch.label,
                    status="pominięta (już pobrana)",
                    count=done.count_api or done.records_seen,
                    records=done.records_seen,
                    run_id=done.run_id,
                )
            )
            continue

        unfinished = deps.store.find_run(
            fingerprint,
            statuses=("przerwany", "w_toku"),
            profile_hash=deps.profile.profile_hash(),
        )
        if unfinished is not None:
            result = run_fetch(
                batch.criteria, deps, resume_run_id=unfinished.run_id, force_lock=force_next
            )
            force_next = False
            run_ids.append(result.run_id)
            counted += result.count_api or result.records
            emit(
                BatchOutcome(
                    label=batch.label,
                    status="wznowiona",
                    count=result.count_api or result.records,
                    records=result.records,
                    run_id=result.run_id,
                )
            )
            continue

        count = count_hits(batch.criteria, deps)
        if count > threshold:
            finer = refine(batch)
            if not finer:
                trafien = f"{count:,}".replace(",", " ")
                raise ConfigError(
                    f"Partia {batch.label} ma {trafien} trafień i nie da się jej podzielić "
                    "drobniej niż na miesiące. Zawęź kryteria inaczej niż datą "
                    "(województwo, PKD, status)."
                )
            queue[0:0] = list(finer)
            emit(
                BatchOutcome(
                    label=batch.label,
                    status=f"podzielona na {len(finer)}",
                    count=count,
                    records=0,
                )
            )
            continue

        counted += count
        if count == 0:
            emit(BatchOutcome(label=batch.label, status="pusta", count=0, records=0))
            continue

        result = run_fetch(batch.criteria, deps, known_count=count, force_lock=force_next)
        force_next = False
        run_ids.append(result.run_id)
        emit(
            BatchOutcome(
                label=batch.label,
                status="pobrana",
                count=count,
                records=result.records,
                run_id=result.run_id,
            )
        )

    return BatchResult(
        run_ids=tuple(run_ids),
        records=deps.store.count_records_for_runs(run_ids),
        counted=counted,
        expected=plan.count,
        outcomes=tuple(outcomes),
        requests=_client(deps).requests_made,
    )


# ------------------------------------------------------------------- sprawdzenie po NIP


@dataclass(frozen=True)
class NipLookup:
    """Wynik sprawdzenia jednej firmy: rekord po normalizacji albo brak trafienia."""

    nip: str
    run_id: str
    record: NormalizedRecord | None
    requests: int


def lookup_nip(nip: str, deps: Deps, *, szczegoly: bool = True) -> NipLookup:
    """Jedna firma po NIP. Suma kontrolna sprawdzana lokalnie, więc literówka nie kosztuje
    żadnego żądania; trafienie kosztuje dwa (lista + szczegóły) i zostaje w bazie, dzięki
    czemu `eksportuj` zrobi z niego skoroszyt bez ponownego pobierania."""
    try:
        criteria = Criteria(nip=(nip,), szczegoly=szczegoly)
    except ValidationError as exc:
        # Zdanie, nie zrzut. Do 2026-09-09 wychodził tu surowy `ValidationError` razem z
        # `[type=value_error, input_value=('1234567890',), input_type=tuple]` i odnośnikiem do
        # errors.pydantic.dev — najgorszy możliwy komunikat akurat w tym wejściu, bo NIP
        # przepisuje się z faktury i literówka jest w nim stanem normalnym, nie awarią.
        raise ConfigError(
            f"Niepoprawny NIP:\n{bledy_po_polsku(exc)}\nPodaj 10 cyfr, na przykład 356-345-79-32."
        ) from exc
    if not criteria.nip:
        # Walidator list odsiewa puste napisy, więc pusty NIP dałby `Criteria` bez ani jednego
        # filtra — a to jest zapytanie o cały rejestr, nie o jedną firmę.
        raise ConfigError("Niepoprawny NIP: podaj 10 cyfr, na przykład 356-345-79-32.")
    result = run_fetch(criteria, deps)
    run = deps.store.get_run(result.run_id)
    ctx = RowContext(srodowisko=run.environment, pobrano_utc=run.updated_utc)
    records = [normalize(raw, ctx) for raw in deps.store.iter_run_records(result.run_id)]
    return NipLookup(
        nip=criteria.nip[0],
        run_id=result.run_id,
        record=records[0] if records else None,
        requests=result.requests,
    )


# ----------------------------------------------------------------------------- eksport


@dataclass(frozen=True)
class ExportSummary:
    paths: tuple[Path, ...]
    records: int
    by_status: dict[str, int]
    with_phone: int
    with_email: int
    sheets: tuple[str, ...]
    # Czy w tym pliku kontaktów **nie może** być — bo nie pobrano szczegółów i nie ma w nim
    # wierszy z raportu. Bez tego odsetek kontaktów odpowiadał na inne pytanie, niż czytał
    # operator: „0 (0%)" w trybie listy znaczyło „nie pytaliśmy", a brzmiało jak „żadna
    # z tych firm nie ma telefonu". Jeden predykat, bo decyduje o dwóch rzeczach naraz:
    # o ukryciu kolumn i o zdaniu na ekranie, a rozjazd między nimi byłby defektem.
    bez_kontaktow: bool = False
    # Czy operator prosił o szczegóły. Rozstrzyga wyłącznie o **radzie**: „dokończ przez
    # wznow" zamiast „powtórz ze szczegółami".
    tryb_szczegoly: bool = False
    kind: str = "firmy"
    run_ids: tuple[str, ...] = ()
    # Stan każdego runu w tej samej kolejności co `run_ids`. Podsumowanie musi umieć
    # powiedzieć, że plik powstał z pobrania niedokończonego — arkusz `Metadane` to wie,
    # ale operator bez wiedzy o API czyta przede wszystkim ekran.
    statuses: tuple[str, ...] = ()


def output_name(
    criteria: Criteria,
    environment: str,
    now: datetime,
    suffix: str = ".xlsx",
    *,
    demo: bool = False,
) -> str:
    """Nazwa pliku wyjściowego; w trybie pokazu z prefiksem `DEMO_`.

    Znacznik numer trzy z ADR-0014. Nazwa pliku jest tym, co widać w katalogu i w załączniku
    do wiadomości — czyli tam, gdzie plik trafia po pokazie i gdzie nikt już nie pamięta,
    skąd pochodzi."""
    parts = ["DEMO", "ceidg"] if demo else ["ceidg"]
    if criteria.wojewodztwo:
        parts.append("_".join(criteria.wojewodztwo))
    if criteria.miasto:
        parts.append("_".join(criteria.miasto))
    if criteria.pkd:
        parts.append("pkd_" + "_".join(criteria.pkd))
    if criteria.data_od or criteria.data_do:
        parts.append(f"{criteria.data_od or 'x'}_{criteria.data_do or 'x'}")
    parts.append(environment)
    parts.append(now.strftime("%Y%m%d_%H%M"))
    return safe_filename("_".join(parts), suffix)


def _record_source(
    deps: Deps, run_ids: Sequence[str], ctx: RowContext
) -> Callable[[], Iterator[NormalizedRecord]]:
    def source() -> Iterator[NormalizedRecord]:
        for raw in deps.store.iter_records_for_runs(run_ids):
            yield normalize(raw, ctx)

    return source


def build_metadata(
    run: RunInfo,
    deps: Deps,
    *,
    cel_pobrania: str | None,
    records: int,
    parts: Sequence[RunInfo] = (),
    hidden_columns: Collection[str] = (),
) -> list[tuple[str, Any]]:
    czesci = tuple(parts) if parts else (run,)
    criteria_text, criteria_json = _union_criteria(czesci)
    statusy = [p.status for p in czesci]
    status_txt = (
        statusy[0]
        if len(set(statusy)) == 1
        else "mieszany: " + ", ".join(f"{s}×{statusy.count(s)}" for s in sorted(set(statusy)))
    )
    # `count` bywa nieznany dla partii, która padła przed pierwszą odpowiedzią. Suma po
    # znanych z adnotacją mówi prawdę; sama suma udawałaby, że policzono wszystkie.
    znane_count = [p.count_api for p in czesci if p.count_api is not None]
    if len(znane_count) == len(czesci):
        count_txt: Any = sum(znane_count)
    elif znane_count:
        count_txt = f"{sum(znane_count)} (znany dla {len(znane_count)} z {len(czesci)} partii)"
    else:
        count_txt = ""
    bledy = [(p.run_id, p.error) for p in czesci if p.error]
    meta: list[tuple[str, Any]] = []
    if deps.demo:
        # Znacznik numer dwa z ADR-0014, i jedyny, który **podróżuje razem z plikiem**.
        # Ekran widzi tylko ten, kto siedział przy pokazie; skoroszyt trafia dalej i musi
        # sam o sobie mówić. Bez tego wiersza plik z pokazu jest nie do odróżnienia od
        # produkcyjnego — dokładnie to ryzyko audyt zapisał przy zasianej bazie demo.
        meta.append(("UWAGA", DEMO_OSTRZEZENIE))
    meta += [
        ("kryteria", criteria_text),
        ("kryteria_json", criteria_json),
        ("cel_pobrania", cel_pobrania or ""),
        ("srodowisko", run.environment),
        ("run_id", ", ".join(p.run_id for p in czesci)),
        ("tryb", ", ".join(sorted({p.mode for p in czesci}))),
        ("status_runu", status_txt),
        ("pobranie_start_utc", min(p.created_utc for p in czesci)),
        ("pobranie_koniec_utc", max(p.updated_utc for p in czesci)),
        ("eksport_utc", utc_iso(deps.clock.wall())),
        ("liczba_trafien_count", count_txt),
        ("liczba_pobranych_rekordow", records),
        ("liczba_stron", sum(p.pages_done for p in czesci)),
        # Partie pobrane przed aktualizacją i po niej mają różne wersje narzędzia; branie
        # wersji pierwszej partii opisywałoby plik, którego część powstała czym innym.
        ("wersja_narzedzia", ", ".join(sorted({p.tool_version for p in czesci}))),
        ("profil_api_hash", ", ".join(sorted({p.profile_hash for p in czesci}))),
        ("zrodlo", "CEIDG_API / CEIDG_RAPORT wg kolumny zrodlo"),
    ]
    if hidden_columns:
        # Ukrycie bez wyjaśnienia wygląda jak brakująca kolumna. Wiersz mówi, że kolumny
        # są w pliku i dlaczego są puste — inaczej operator szuka błędu tam, gdzie go nie ma.
        meta.append(
            (
                "kolumny_ukryte",
                "niedostępne w źródle CEIDG_RAPORT (pokaż je w Excelu przez Odkryj): "
                + ", ".join(sorted(hidden_columns)),
            )
        )
    if len(parts) > 1:
        # pobranie w partiach: jeden skoroszyt, ale audyt musi widzieć wszystkie runy
        meta.append(("liczba_partii", len(parts)))
        meta.append(("partie_run_id", ", ".join(p.run_id for p in parts)))
        meta.append(("partie_kryteria", " | ".join(_batch_label(p) for p in parts)))
    if bledy:
        # Błąd każdej partii z jej identyfikatorem. Samo `run.error` opisywało partię
        # pierwszą, więc partia przerwana w połowie nie zostawiała w pliku żadnego śladu.
        meta.append(("ostrzezenie", " | ".join(f"{rid}: {err}" for rid, err in bledy)))
    return meta


def _union_criteria(czesci: Sequence[RunInfo]) -> tuple[str, str]:
    """Kryteria **całego** eksportu: opis dla człowieka i JSON do powtórzenia zapytania.

    Partie różnią się wyłącznie zakresem dat — `batching` dzieli po dacie rozpoczęcia —
    więc sumą jest kryterium pierwszej partii z zakresem rozciągniętym na skrajne daty.
    Opis pierwszej partii nazywałby jedną dwunastą pliku i robił to bez ostrzeżenia."""
    try:
        criteria = [Criteria.model_validate_json(p.criteria_json) for p in czesci]
    except ValidationError:
        return czesci[0].criteria_json, czesci[0].criteria_json
    if len(criteria) == 1:
        return criteria[0].describe(), czesci[0].criteria_json
    od = [c.data_od for c in criteria]
    do = [c.data_do for c in criteria]
    suma = criteria[0].model_copy(
        update={
            "data_od": None if None in od else min(d for d in od if d is not None),
            "data_do": None if None in do else max(d for d in do if d is not None),
        }
    )
    return suma.describe(), suma.model_dump_json()


def _batch_label(run: RunInfo) -> str:
    """Etykieta partii ze **stanem**: bez niego przerwana partia wygląda jak każda inna."""
    try:
        criteria = Criteria.model_validate_json(run.criteria_json)
    except ValidationError:
        return run.run_id
    od = criteria.data_od.isoformat() if criteria.data_od else "…"
    do = criteria.data_do.isoformat() if criteria.data_do else "…"
    return f"{od}–{do}: {run.records_seen} ({run.status})"


def run_export(
    run_id: str | Sequence[str],
    dest: Path,
    deps: Deps,
    *,
    cel_pobrania: str | None = None,
    formats: Sequence[str] = ("xlsx",),
) -> ExportSummary:
    """Eksport wyłącznie z bazy — zero żądań. `formats`: xlsx, csv, jsonl.

    `run_id` może być listą runów (pobranie w partiach): powstaje jeden skoroszyt
    bez duplikatów, a arkusz `Metadane` wymienia wszystkie partie."""
    store = deps.store
    run_ids = (run_id,) if isinstance(run_id, str) else tuple(run_id)
    if not run_ids:
        raise StoreError("Eksport wymaga wskazania przynajmniej jednego pobrania.")
    runs = [store.get_run(rid) for rid in run_ids]
    for rid, run in zip(run_ids, runs, strict=True):
        if run.environment != deps.settings.environment:
            raise StoreError(f"Run {rid} pochodzi ze środowiska {run.environment}.")
    run = runs[0]
    ctx = RowContext(srodowisko=run.environment, pobrano_utc=max(r.updated_utc for r in runs))
    source = _record_source(deps, run_ids, ctx)
    records = store.count_records_for_runs(run_ids)
    # Jeden predykat na pytanie „czy to eksport z raportu", i liczony z danych, nie z etykiety
    # runu: `zrodlo` stoi przy każdym rekordzie i ma `CHECK` w schemacie, a `run.kind` bywa
    # błędny (starszy zapis oznaczył pobranie z raportu jako `firmy`). Zestaw mieszany nie
    # ukrywa niczego — te same kolumny bywają wypełnione przez ścieżkę API.
    zrodla = store.record_sources(run_ids)
    from_report = zrodla == {ZRODLO_RAPORT}
    # Dwa niepełne źródła, dwa zbiory kolumn — i do 2026-09-09 tylko jedno z nich było
    # obsłużone. Skoroszyt z trybu `lista` pokazywał kilkanaście kolumn pustych w każdym
    # wierszu i niczego nie chował, choć ścieżka raportu robiła to od początku.
    #
    # Predykat pyta o **oba** źródła kontaktów, nie o jedno: `/firma` wypełnia je przez
    # `detail_json`, a dzienny raport wprost w wierszu CSV, nie mając żadnych szczegółów.
    # Pierwsza wersja sprawdzała samo `szczegolow == 0` i przy zestawie mieszanym
    # (część z raportu, część z API bez szczegółów) ukryłaby kolumny, które raport
    # wypełnia — czyli dokładnie ten kierunek, który `KOLUMNY_TYLKO_ZE_SZCZEGOLOW` nazywa
    # defektem, i wprost wbrew komentarzowi „zestaw mieszany nie ukrywa niczego" wyżej.
    # Dziś nie ma jak takiego zestawu zbudować z CLI; warunek jest po to, żeby jutro też nie
    # było. Liczone z danych, nie z `run.mode` — etykieta opisuje zamiar, plik zawartość.
    szczegolow = store.count_details_for_runs(run_ids)
    bez_kontaktow = szczegolow == 0 and ZRODLO_RAPORT not in zrodla
    if from_report:
        hidden = UNFILLED_COLUMNS
    elif bez_kontaktow:
        hidden = KOLUMNY_TYLKO_ZE_SZCZEGOLOW
    else:
        hidden = frozenset[str]()
    metadata = build_metadata(
        run, deps, cel_pobrania=cel_pobrania, records=records, parts=runs, hidden_columns=hidden
    )

    by_status: dict[str, int] = {}
    contacts = {"telefon": 0, "email": 0}
    sheets = {"Firmy", "Slownik", "Metadane"}

    def observe(rec: NormalizedRecord) -> None:
        status = str(rec.firmy.get("status") or "brak")
        by_status[status] = by_status.get(status, 0) + 1
        for key in contacts:
            if rec.firmy.get(key):
                contacts[key] += 1
        if rec.pkd:
            sheets.add("PKD")
        if rec.spolki:
            sheets.add("Spolki")
        if rec.adresy:
            sheets.add("Adresy")

    if records:
        # Zapis bywa najdłuższym etapem bez ani jednego żądania: zmierzone 453 firmy/s,
        # czyli ponad dziesięć minut dla pełnego województwa z raportu. Zdanie pada nawet
        # tam, gdzie paska nie widać (log, tryb cichy).
        deps.events.on_message(f"Zapisuję skoroszyt: {records} firm do {dest.name}.")
    paths: list[Path] = []
    # Od tego miejsca każde wyjście gasi pasek — także przez wyjątek. Do audytu 2026-09-07
    # `close()` stało tylko na ścieżce szczęśliwej, a eksport potrafi paść na brak miejsca,
    # błąd zapisu i rekord z nadmiarem wierszy. Kreator te błędy łapie i wraca do menu, więc
    # wracał z żywym `Live` — czyli w stan, w którym pytania menu może nie być widać. To ta
    # sama lekcja, którą `run_fetch` odrobił po bramce 3 w 2026-09-06.
    try:
        if "xlsx" in formats:
            paths.extend(
                write_workbook(
                    dest,
                    source,
                    metadata=metadata,
                    observer=observe,
                    hidden_columns=hidden,
                    events=deps.events,
                )
            )
        else:
            for rec in source():
                observe(rec)
        if "csv" in formats or "jsonl" in formats:
            # §C mówi „przed eksportem", bez zawężenia do Excela. Sprawdzenie siedziało wyłącznie
            # w `write_workbook`, więc `--formaty csv` i `jsonl` pisały bez żadnego progu.
            check_free_space(dest.parent, records, min_free_bytes=MIN_FREE_BYTES)
        if "csv" in formats:
            paths.extend(write_csv(dest.with_name(dest.stem + "_csv"), source, events=deps.events))
        if "jsonl" in formats:
            paths.append(write_jsonl(dest.with_suffix(".jsonl"), source))

    finally:
        deps.events.close()  # pasek gaśnie razem z operacją, nie dopiero na jej sukcesie
    return ExportSummary(
        paths=tuple(paths),
        records=records,
        by_status=dict(sorted(by_status.items())),
        with_phone=contacts["telefon"],
        with_email=contacts["email"],
        bez_kontaktow=bez_kontaktow,
        # Zamiar, nie zawartość — i tu właśnie o zamiar chodzi. Przerwane pobranie ze
        # szczegółami ma zero szczegółów w pliku, ale rada „powtórz z opcją ze szczegółami"
        # jest dla niego nieprawdą: właściwym lekarstwem jest `wznow`, co zresztą mówi
        # obok uwaga o niedokończonym przebiegu. Dwa zdania na jednym ekranie nie mogą
        # odsyłać w dwie strony.
        tryb_szczegoly=any(r.mode == "szczegoly" for r in runs),
        sheets=tuple(
            s for s in ("Firmy", "PKD", "Spolki", "Adresy", "Slownik", "Metadane") if s in sheets
        ),
        # Ten sam predykat, co decyduje o ukryciu kolumn — inaczej podsumowanie mogłoby
        # zapowiadać pusty `link_ceidg` przy skoroszycie, w którym nic nie ukryto.
        kind="raport" if from_report else run.kind,
        run_ids=run_ids,
        statuses=tuple(r.status for r in runs),
    )


def purge_report_files(directory: Path, *, days: float, now_epoch: float) -> int:
    """Usuwa pobrane raporty ZIP starsze niż `days` (pełne zrzuty województw z danymi osobowymi)."""
    if not directory.exists():
        return 0
    cutoff = now_epoch - days * 86_400
    removed = 0
    for path in directory.glob("*.zip"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def default_export_path(deps: Deps, run: RunInfo | str, now: datetime | None = None) -> Path:
    """Nazwa pliku z kryteriów runu. Przyjmuje `run_id`, żeby warstwa `ui` nie sięgała do bazy."""
    info = deps.store.get_run(run) if isinstance(run, str) else run
    moment = now or datetime.now(tz=UTC)
    try:
        criteria = Criteria.model_validate_json(info.criteria_json)
    except ValidationError:
        criteria = Criteria()
    return deps.settings.output_dir / output_name(
        criteria, info.environment, moment, demo=deps.demo
    )


def today() -> date:
    return datetime.now(tz=UTC).date()


def _readable_archive(path: Path) -> bool:
    """Czy plik w pamięci podręcznej to wciąż otwieralne archiwum z CSV-em w środku."""
    try:
        with zipfile.ZipFile(path) as archive:
            return any(name.lower().endswith(".csv") for name in archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return False
