"""Atrapa rejestru dla trybu demo: odpowiada na żądania, nie odtwarza nagrania (ADR-0014).

Router, a nie playback, i to jest różnica, na której stoi całe demo: operator może zapytać
o co chce, a nie tylko powtórzyć ścieżkę, którą ktoś wcześniej nagrał. Cena jest taka, że
atrapa musi **zachowywać się** jak rejestr — a to jest twierdzenie, które da się sprawdzić:
`tests/fixtures/api_traits.yaml` mówi, co zmierzono na produkcji, i ten moduł podlega temu
samemu testowi co fixtures.

Własności odtworzone świadomie, bo każda z nich ma za sobą zamknięty defekt:

* `/firmy` i `/firma` zwracają identyfikator **wielkimi** literami, `/zmiana` **małymi**;
  `ids=` dopasowuje bez względu na wielkość liter, ale odpowiedź niesie pisownię wielką.
  To jest ADR-0013 — atrapa odbijająca pisownię z zapytania zgadza się sama ze sobą i nie
  może pokazać niczego o tożsamości wpisu.
* pusty wynik to **204**, nie `200` z pustą listą (`empty_result_statuses` w profilu);
* `count` opisuje **cały** zakres, nie stronę — inaczej tabela kosztów kłamałaby;
* `links.next == links.self` na ostatniej stronie, bo tak `client._iter_paged` rozpoznaje
  koniec paginacji;
* `/firma?ids=` powyżej `ids_batch_size` odpowiada **400**, jak produkcja.

Czego atrapa **nie** udaje i co trzeba powiedzieć na głos: nie ma tu 6 316 121 wpisów
(tyle zmierzono na produkcji), więc liczby w tabeli kosztów są prawdziwe dla tego korpusu,
a nie dla rejestru.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .korpus import Korpus, WpisDemo, zmienione_w_oknie

LIMIT_FIRMY = 25
LIMIT_ZMIANA = 500
BATCH_IDS = 5


def _params(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlsplit(url).query, keep_blank_values=True)


def _wartosci(params: list[tuple[str, str]], nazwa: str) -> list[str]:
    return [v for k, v in params if k == nazwa]


def _jeden(params: list[tuple[str, str]], nazwa: str, domyslnie: int) -> int:
    for k, v in params:
        if k == nazwa:
            try:
                return int(v)
            except ValueError:
                return domyslnie
    return domyslnie


def _data(tekst: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(tekst, fmt)
        except ValueError:
            continue
    return None


def _pasuje(wpis: WpisDemo, params: list[tuple[str, str]]) -> bool:
    """Filtr w dialekcie API: powtórzony parametr znaczy OR, różne parametry znaczą AND.

    `nazwa` dopasowuje się **fragmentem** — zmierzone na produkcji; reszta dokładnie.
    `pkd` porównuje się z **wszystkimi** kodami wpisu, nie tylko głównym: to jest ta
    własność, przez którą luka pokrycia PKD 2025 wynosi 8,6 %, a nie 25,2 % (audyt
    2026-09-08). Atrapa filtrująca po samym kodzie głównym uczyłaby nieprawdy o rejestrze.
    """
    proste: list[tuple[str, str]] = [
        ("wojewodztwo", wpis.wojewodztwo.upper()),
        ("miasto", wpis.miasto),
        ("powiat", wpis.powiat),
        ("gmina", wpis.miasto),
        ("kod", wpis.kod_pocztowy),
        ("nip", wpis.nip),
        ("regon", wpis.regon),
        ("imie", wpis.imie),
        ("nazwisko", wpis.nazwisko),
        ("status", wpis.status),
    ]
    for nazwa_param, wartosc in proste:
        # Bez względu na wielkość liter — zmierzone na produkcji (`docs/decisions.md`:
        # ten sam `count` dla `podlaskie` i `PODLASKIE`). Atrapa porównująca dokładnie
        # działała tylko dlatego, że profil podnosi województwo do wielkich liter, więc
        # pierwszy operator, który wpisze `Poznań` małą literą, dostanie w demie 204.
        oczekiwane = [w.casefold() for w in _wartosci(params, nazwa_param)]
        if oczekiwane and wartosc.casefold() not in oczekiwane:
            return False
    fragmenty = _wartosci(params, "nazwa")
    if fragmenty and not any(f.casefold() in wpis.nazwa.casefold() for f in fragmenty):
        return False
    ulice = _wartosci(params, "ulica")
    if ulice and not any(u.casefold() in wpis.ulica.casefold() for u in ulice):
        return False
    kody = {k.upper() for k in _wartosci(params, "pkd")}
    if kody and not (kody & {k.upper() for k in wpis.pkd}):
        return False
    od = _wartosci(params, "dataod")
    do = _wartosci(params, "datado")
    if od and (granica := _data(od[0])) and wpis.data_rozpoczecia < granica.date():
        return False
    if do and (granica := _data(do[0])) and wpis.data_rozpoczecia > granica.date():
        return False
    return True


def _z_page(url: str, numer: int) -> str:
    parts = urlsplit(url)
    pary = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "page"]
    pary.append(("page", str(numer)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(sorted(pary)), ""))


def _strona(
    url: str, wybrane: Sequence[Any], *, klucz: str, limit: int, numer: int
) -> httpx.Response:
    """Jedna strona odpowiedzi z `links`, `count` i pisownią identyfikatorów jak w rejestrze."""
    start = numer * limit
    kawalek = wybrane[start : start + limit]
    if not kawalek:
        # 204, nie pusta lista — profil zna `empty_result_statuses: [204]`.
        return httpx.Response(204)
    self_url = _z_page(url, numer)
    ostatnia = start + limit >= len(wybrane)
    next_url = self_url if ostatnia else _z_page(url, numer + 1)
    return httpx.Response(
        200,
        json={klucz: kawalek, "count": len(wybrane), "links": {"self": self_url, "next": next_url}},
    )


class RejestrDemo:
    """Obsługa żądań HTTP nad korpusem. Liczy żądania, żeby demo mogło je pokazać."""

    def __init__(self, korpus: Korpus) -> None:
        self._korpus = korpus
        self._wg_id = korpus.wedlug_id()
        self.zadania: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.zadania.append(url)
        sciezka = urlsplit(url).path
        params = _params(url)
        if sciezka.endswith("/firmy"):
            return self._firmy(url, params)
        if sciezka.endswith("/firma"):
            return self._firma(params)
        if sciezka.endswith("/zmiana"):
            return self._zmiana(url, params)
        if sciezka.endswith("/raporty"):
            # Pusta lista, a nie 404: „na dziś nie ma gotowego raportu" to normalny stan
            # rejestru, a `--zrodlo auto` ma się wtedy cofnąć do ścieżki API. Odpowiedź
            # błędem robiłaby z braku raportu awarię i zamykała pokaz zanim się zacznie.
            return httpx.Response(200, json={"raporty": []})
        return httpx.Response(
            404, json={"code": "NIEZNANY_ZASOB", "message": f"demo nie obsługuje {sciezka}"}
        )

    def _firmy(self, url: str, params: list[tuple[str, str]]) -> httpx.Response:
        wybrane = [w for w in self._korpus.wpisy if _pasuje(w, params)]
        limit = min(_jeden(params, "limit", LIMIT_FIRMY), LIMIT_FIRMY)
        numer = _jeden(params, "page", 0)
        return _strona(
            url, [w.jako_lista() for w in wybrane], klucz="firmy", limit=limit, numer=numer
        )

    def _firma(self, params: list[tuple[str, str]]) -> httpx.Response:
        pytane = _wartosci(params, "ids")
        if not pytane:
            return httpx.Response(400, json={"code": "BRAK_IDENTYFIKATOROW", "message": "ids"})
        if len(pytane) > BATCH_IDS:
            # Produkcja odpowiada tak na 25 i 50 identyfikatorów (sonda fazy 1).
            return httpx.Response(
                400,
                json={
                    "code": "NIEPOPRAWNA_ILOSC_IDENTYFIKATOROW",
                    "message": f"maksymalnie {BATCH_IDS} identyfikatorów",
                },
            )
        # Dopasowanie **bez względu na wielkość liter**, odpowiedź zawsze wielkimi — to jest
        # ta jedna linia, która odróżnia atrapę od rejestru (ADR-0013).
        znalezione = [self._wg_id[k] for r in pytane if (k := r.upper()) in self._wg_id]
        if not znalezione:
            return httpx.Response(404, json={"code": "BRAK", "message": "nie znaleziono wpisów"})
        return httpx.Response(
            200,
            json={"firma": [w.jako_szczegoly(self._korpus.nazwy(w.rok_pkd)) for w in znalezione]},
        )

    def _zmiana(self, url: str, params: list[tuple[str, str]]) -> httpx.Response:
        od, do = _wartosci(params, "dataod"), _wartosci(params, "datado")
        if not od or not do:
            return httpx.Response(400, json={"code": "BRAK_ZAKRESU", "message": "dataod/datado"})
        poczatek, koniec = _data(od[0]), _data(do[0])
        if poczatek is None or koniec is None:
            return httpx.Response(400, json={"code": "ZLA_DATA", "message": f"{od[0]}..{do[0]}"})
        ids = zmienione_w_oknie(self._korpus, poczatek, koniec)
        limit = min(_jeden(params, "limit", LIMIT_ZMIANA), LIMIT_ZMIANA)
        numer = _jeden(params, "page", 0)
        # `/zmiana` oddaje gołe identyfikatory, a `client._records_of` opakowuje je w `{"id": …}`.
        return _strona(url, list(ids), klucz="identyfikatoryWpisow", limit=limit, numer=numer)
