"""Transport HTTP z fixtures sondy: `httpx.MockTransport` odpowiadający zapisanymi próbkami.

Tu mieszka też `criteria()` — pisany po ludzku konstruktor `Criteria` dla testów
(ADR-0008, decyzja 6). Produkcyjne pola są krotkami, a walidator `mode="before"`
dopuszcza pojedynczy napis; mypy tego nie widzi, więc zamiast rozluźniać typy produkcji
albo sypać `type: ignore` po testach, konwersję robi jedna otypowana funkcja.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qsl, urlsplit

import httpx

from ceidg_tool.criteria import Criteria
from ceidg_tool.httpclient import build_http_client
from ceidg_tool.pkdmap import TablicaPkd
from ceidg_tool.ui.texts import Block

FIXTURES = Path(__file__).parent / "fixtures"

StrOrSeq = str | Sequence[str]


def criteria(
    *,
    nazwa: StrOrSeq = (),
    nip: StrOrSeq = (),
    regon: StrOrSeq = (),
    imie: StrOrSeq = (),
    nazwisko: StrOrSeq = (),
    wojewodztwo: StrOrSeq = (),
    powiat: StrOrSeq = (),
    gmina: StrOrSeq = (),
    miasto: StrOrSeq = (),
    ulica: StrOrSeq = (),
    kod: StrOrSeq = (),
    pkd: StrOrSeq = (),
    pkd_2007: StrOrSeq = (),
    status: StrOrSeq = (),
    data_od: str | date | None = None,
    data_do: str | date | None = None,
    szczegoly: bool = False,
    max_rekordow: int | None = None,
) -> Criteria:
    """`criteria(wojewodztwo="podlaskie")` zamiast `Criteria(wojewodztwo=("podlaskie",))`.

    Przechodzi przez `model_validate`, więc obowiązuje dokładnie ta sama walidacja
    co w produkcji — helper skraca zapis, nie omija kontraktu.
    """
    values: dict[str, Any] = {
        "nazwa": nazwa,
        "nip": nip,
        "regon": regon,
        "imie": imie,
        "nazwisko": nazwisko,
        "wojewodztwo": wojewodztwo,
        "powiat": powiat,
        "gmina": gmina,
        "miasto": miasto,
        "ulica": ulica,
        "kod": kod,
        "pkd": pkd,
        "pkd_2007": pkd_2007,
        "status": status,
        "data_od": data_od,
        "data_do": data_do,
        "szczegoly": szczegoly,
        "max_rekordow": max_rekordow,
    }
    return Criteria.model_validate(values)


# ------------------------------------------------------------- tablica przejścia PKD (ADR-0012)

# Trzy pozycje **przepisane** z wygenerowanej tablicy `ceidg_tool/data/pkd2007_2025.yaml` —
# kody, nazwy i rozgałęzienie, nic wymyślonego. Atrapa, która zaprzecza danym, jest gorsza niż
# jej brak: to dokładnie ten sam powód, dla którego `rokPkd` w `tests/conftest.py` mówi „2025".
# `tests/test_pkdmap_data.py` pilnuje, żeby pełna tablica nadal potwierdzała te trzy pozycje —
# inaczej atrapa mogłaby po cichu zacząć opisywać klasyfikację, której już nie ma.
#
# `9602Z` prowadzi i do fryzjerstwa, i do kosmetyki, więc jest rozszerzeniem **niejednoznacznym**
# (przejście gate-3 idzie właśnie po fryzjerach). `1412Z` prowadzi wyłącznie do `1423Z`,
# więc jest rozszerzeniem **czystym**.
POPRZEDNICY_TESTOWI: dict[str, tuple[str, ...]] = {
    "9621Z": ("9602Z",),
    "9622Z": ("9602Z",),
    "1423Z": ("1412Z",),
    # Czwarta pozycja niesie **drugą postać** niejednoznaczności: `8551Z` jest poprzednikiem
    # klubów fitness i jednocześnie **żywym kodem PKD 2025** o innym znaczeniu. Do audytu
    # 2026-09-07 atrapa nie miała ani jednego takiego kodu, więc gałąź `dzis` w `pkdmap`
    # i zdanie o dzisiejszym znaczeniu w `texts` nie były wykonywane przez żaden test.
    "9313Z": ("8551Z",),
}
# Kody 2007 będące zarazem żywymi kodami 2025 — dokładając taki kod, bierzemy też jego
# dzisiejszą branżę. Prawdziwa para z tablicy, przypięta przez `test_pkdmap_data.py`.
ZYWE_2025_TESTOWE: tuple[str, ...] = ("8551Z",)
NAZWY_2007_TESTOWE: dict[str, str] = {
    "9602Z": "Fryzjerstwo i pozostałe zabiegi kosmetyczne",
    "1412Z": "Produkcja odzieży roboczej",
    "8551Z": "Pozaszkolne formy edukacji sportowej oraz zajęć sportowych i rekreacyjnych",
}
NAZWY_2025_TESTOWE: dict[str, str] = {
    "9621Z": "Fryzjerstwo",
    "9622Z": "Działalność w zakresie pielęgnacji urody i pozostała działalność kosmetyczna",
    "1423Z": "Produkcja odzieży roboczej",
    "9313Z": "Działalność klubów fitness",
    "8551Z": "Pozostałe formy edukacji sportowej oraz zajęć sportowych i rekreacyjnych",
}


def pkd_map() -> TablicaPkd:
    """Miniaturowa tablica przejścia: jedno rozszerzenie czyste, jedno niejednoznaczne.

    Trzy pozycje zamiast 264, z tego samego powodu, dla którego testy asystenta dostają
    słownik pięcioelementowy: przepływ ma się dać sprawdzić bez wczytywania całej tablicy,
    a zgodność tablicy z klasyfikacją jest osobnym pytaniem i osobnym plikiem.
    """
    return TablicaPkd(
        POPRZEDNICY_TESTOWI, NAZWY_2007_TESTOWE, NAZWY_2025_TESTOWE, ZYWE_2025_TESTOWE
    )


def load_fixture(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def registry_id(rid: str) -> str:
    """Identyfikator w pisowni, którą rejestr **zwraca**, a nie w tej, o którą go pytano.

    Zmierzone na produkcji (`probe_out/samples/`, plus baza operatora z 2026-09-08):
    `/zmiana` oddaje identyfikatory małymi literami, `/firmy` i `/firma` wielkimi,
    a `ids=` dopasowuje bez względu na wielkość liter — zapytanie pisane małymi wraca
    rekordem pisanym wielkimi.

    Atrapy `/firma` odbijały identyfikator z zapytania, więc zgadzały się same ze sobą
    i żadna nie mogła pokazać defektu, przez który każdy `aktualizuj` gubił wszystkie
    szczegóły (ADR-0013). Każda atrapa szczegółów przepuszcza identyfikator przez tę
    funkcję — to jest ta jedna linia, która odróżnia atrapę od rejestru."""
    return rid.upper()


def fixture_response(name: str) -> httpx.Response:
    """Odpowiedź zbudowana 1:1 z próbki sondy (status, nagłówki limitów, treść)."""
    sample = load_fixture(name)
    headers = {
        k: v
        for k, v in sample.get("headers", {}).items()
        if k.lower().startswith("x-rate-limit") or k.lower() == "content-type"
    }
    body = sample.get("body")
    if body is None:
        return httpx.Response(sample["status"], headers=headers)
    return httpx.Response(sample["status"], headers=headers, json=body)


def _key(url: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    parts = urlsplit(url)
    return parts.path, tuple(sorted(parse_qsl(parts.query, keep_blank_values=True)))


@dataclass
class FakeApi:
    """Router: (ścieżka, posortowane parametry) → odpowiedź; liczy żądania."""

    routes: dict[tuple[str, tuple[tuple[str, str], ...]], Callable[[], httpx.Response]] = field(
        default_factory=dict
    )
    fallback: Callable[[httpx.Request], httpx.Response] | None = None
    requests: list[str] = field(default_factory=list)

    def add(self, url: str, response: httpx.Response | Callable[[], httpx.Response]) -> None:
        if callable(response):
            self.routes[_key(url)] = response
        else:
            fixed: httpx.Response = response
            self.routes[_key(url)] = lambda: fixed

    def add_fixture(self, name: str) -> None:
        """Rejestruje próbkę pod adresem żądania i pod `links.self` (serwer dopisuje
        statusy do linków, więc `links.next` poprzedniej strony ma inny query)."""
        sample = load_fixture(name)
        self.add(sample["url"], lambda: fixture_response(name))
        body = sample.get("body")
        links = body.get("links") if isinstance(body, dict) else None
        if isinstance(links, dict) and isinstance(links.get("self"), str):
            self.add(links["self"], lambda: fixture_response(name))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        route = self.routes.get(_key(str(request.url)))
        if route is not None:
            return route()
        if self.fallback is not None:
            return self.fallback(request)
        return httpx.Response(404, json={"code": "BRAK", "message": f"brak trasy {request.url}"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def client(self) -> httpx.Client:
        """Ten sam konstruktor, którego używa produkcja (reguła 11).

        Atrapa budowała wcześniej `httpx.Client` sama, więc jedyna linia tworząca klienta
        w programie nie miała żadnego pokrycia — a ponieważ httpx pomija proxy ze środowiska,
        gdy transport jest podany, to właśnie ten szew ukrywał domyślne `trust_env=True`.
        """
        return build_http_client(transport=self.transport())


@dataclass
class RecordingView:
    """Widok dla testów: zapamiętuje bloki i komunikaty zamiast rysować je przez `rich`.

    Implementuje protokół `ui.flow.View`. Dzięki temu przepływ da się sprawdzić bez
    terminala, a asercje dotyczą treści bloku, nie jego wyglądu na ekranie.
    """

    blocks: list[Block] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def block(self, block: Block) -> None:
        self.blocks.append(block)

    def message(self, text: str) -> None:
        self.messages.append(text)

    def warning(self, text: str) -> None:
        self.warnings.append(text)

    def error(self, text: str) -> None:
        self.errors.append(text)

    def titles(self) -> list[str]:
        return [b.title for b in self.blocks]

    def text(self) -> str:
        """Wszystko, co zobaczyłby użytkownik, jako jeden tekst — do asercji o treści."""
        parts = [b.as_text() for b in self.blocks]
        parts.extend(self.messages)
        parts.extend(self.warnings)
        parts.extend(self.errors)
        return "\n".join(parts)

    def block_titled(self, fragment: str) -> Block:
        for block in self.blocks:
            if fragment in block.title:
                return block
        raise AssertionError(f"brak bloku z {fragment!r} w tytule; są: {self.titles()}")
