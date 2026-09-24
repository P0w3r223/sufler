"""Wyszukiwanie kodów PKD w lokalnych słownikach — moduł czysty (ADR-0026).

Odpowiada na pytanie, które operator naprawdę ma: nie „jak nazywa się ten kod", tylko **„jeśli
podam `--pkd X`, co dostanę i co dołoży `--pkd-2007`"**. Oba słowniki są w pakiecie, więc
odpowiedź kosztuje zero żądań i nie potrzebuje żadnego poświadczenia — w przeciwieństwie do
asystenta, który był do 2026-09-23 jedyną drogą do znalezienia kodu.

Zero wejścia-wyjścia: dane wchodzą jako argumenty, wychodzi model widoku. Wczytywaniem zajmują
się `pkddict.load_pkd` i `pkdmap.load_pkd_map`.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from .criteria import normalize_pkd
from .pkdmap import Poprzednik, TablicaPkd

# Sufit wierszy na ekranie. Sto trafień w tabeli `rich` wypycha to, czego operator szukał, poza
# górną krawędź terminala — ta sama klasa defektu co zawijana ścieżka pliku w podsumowaniu
# (ADR-0024). Zdejmowany flagą `--wszystkie`.
DOMYSLNY_LIMIT = 40

Odczyt = Literal["kod", "fraza"]


@dataclass(frozen=True)
class Trafienie:
    """Jeden kod razem z tym, co o nim wiadomo z drugiego rocznika.

    `poprzednicy` wypełnia się dla kodu 2025 (co dołoży `--pkd-2007`), `nastepcy` dla kodu 2007
    (dokąd ten kod dziś prowadzi). Nigdy oba naraz, bo to są odpowiedzi na dwa różne pytania.
    """

    kod: str
    nazwa: str
    rocznik: int
    poprzednicy: tuple[Poprzednik, ...] = ()
    nastepcy: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class WynikSzukania:
    """Trafienia plus to, czego ekran i koperta potrzebują o samym szukaniu."""

    zapytanie: str
    odczytano_jako: Odczyt
    trafienia: tuple[Trafienie, ...]
    wszystkich: int

    @property
    def obciete(self) -> bool:
        return len(self.trafienia) < self.wszystkich


# Polskie `ł` i `Ł` **nie mają** rozkładu kanonicznego, więc NFKD ze zdjęciem znaków łączących
# zostawia je nietknięte: zmierzone 2026-09-23, `normalize("NFKD", "Łódź")` daje `"Łodz"`.
# Wyszukiwanie po „lodz" mijałoby wtedy „Łódź" w ciszy — czyli dokładnie ta klasa usterki, która
# przechodzi każde automatyczne sprawdzenie. Para jest wypisana z ręki, bo nie ma jej skąd wziąć.
_BEZ_ROZKLADU = str.maketrans({"ł": "l", "Ł": "L"})


def zloz(tekst: str) -> str:
    """Postać porównawcza: bez wielkości liter, bez ogonków, bez `ł`.

    Nie ma tu tematowania i to jest decyzja, nie brak (ADR-0026, decyzja 2). „Budowlane" kontra
    „budownictwo" jest problemem, którego nie da się rozwiązać dopasowaniem napisów, a udawanie,
    że się da, dawałoby operatorowi fałszywą pewność przy pustym wyniku. Ekran mówi wprost, że
    szukanie idzie po nazwach, nie po znaczeniach.
    """
    zlozone = unicodedata.normalize("NFKD", tekst.translate(_BEZ_ROZKLADU))
    return "".join(z for z in zlozone if not unicodedata.combining(z)).casefold()


def _jako_kod(zapytanie: str) -> str | None:
    """Kanoniczny kod PKD albo `None`, gdy argument nie ma tego kształtu.

    Ta sama normalizacja co w `--pkd`, więc `62.01.Z`, `6201z` i `6201Z` zachowują się tak samo
    tutaj i w zapytaniu. Kanoniczny kod nigdy nie jest polskim zdaniem, więc kształt rozstrzyga
    bez dodatkowej flagi.
    """
    try:
        return normalize_pkd(zapytanie)
    except ValueError:
        return None


def _trafienie_2025(kod: str, nazwa: str, tablica: TablicaPkd | None) -> Trafienie:
    poprzednicy: tuple[Poprzednik, ...] = ()
    if tablica is not None:
        rozszerzenie = tablica.rozszerz([kod])
        poprzednicy = rozszerzenie.czyste + rozszerzenie.niejednoznaczne
    return Trafienie(kod=kod, nazwa=nazwa, rocznik=2025, poprzednicy=poprzednicy)


def _trafienie_2007(kod: str, tablica: TablicaPkd) -> Trafienie | None:
    nazwa = tablica.nazwa_2007(kod)
    if nazwa is None:
        return None
    nastepcy = tuple(
        (nowy, tablica.nazwa_2025(nowy) or "") for nowy in sorted(tablica.nastepcy(kod))
    )
    return Trafienie(kod=kod, nazwa=nazwa, rocznik=2007, nastepcy=nastepcy)


def szukaj(
    zapytanie: str,
    slownik: Mapping[str, str],
    tablica: TablicaPkd | None = None,
    *,
    limit: int | None = DOMYSLNY_LIMIT,
) -> WynikSzukania:
    """Kod albo fraza — kształt argumentu rozstrzyga, a wynik mówi, jak został odczytany.

    Kod szukany jest w **obu** rocznikach: najpierw w PKD 2025, a gdy go tam nie ma — w tablicy
    przejścia. To jest ta ścieżka, po której operator sprawdza `6201Z`: kodu nie ma w 2025 wcale,
    a rejestr zwraca na niego 234 605 rekordów, więc odpowiedź „nie istnieje" byłaby prawdziwa
    o klasyfikacji i myląca o zapytaniu.

    Fraza szuka wyłącznie w nazwach PKD 2025; rocznik 2007 pokazuje się jako poprzednicy
    znalezionych kodów. Nazwa z 2007 bywa identyczna z dzisiejszą, więc szukanie w obu listach
    dawałoby ten sam wiersz dwa razy pod dwoma kodami.
    """
    kod = _jako_kod(zapytanie)
    if kod is not None:
        nazwa = slownik.get(kod)
        trafienia: tuple[Trafienie, ...] = ()
        if nazwa is not None:
            trafienia = (_trafienie_2025(kod, nazwa, tablica),)
        elif tablica is not None and (t := _trafienie_2007(kod, tablica)) is not None:
            trafienia = (t,)
        return WynikSzukania(zapytanie, "kod", trafienia, len(trafienia))

    igla = zloz(zapytanie.strip())
    if not igla:
        return WynikSzukania(zapytanie, "fraza", (), 0)
    pasujace = sorted(k for k, nazwa in slownik.items() if igla in zloz(nazwa))
    widoczne = pasujace if limit is None else pasujace[:limit]
    return WynikSzukania(
        zapytanie,
        "fraza",
        tuple(_trafienie_2025(k, slownik[k], tablica) for k in widoczne),
        len(pasujace),
    )


def jako_dane(wynik: WynikSzukania) -> dict[str, object]:
    """Wynik szukania w postaci, którą niesie koperta (ADR-0024, pola własne `szukaj-pkd`).

    Mieszka tutaj, a nie w `ui/wynik.py`, bo to ten moduł jest właścicielem `Trafienie`
    i `Poprzednik` — a koperta ogólna, do której siedem poleceń dokłada własne pola, szybko
    stałaby się miejscem, gdzie mieszka wiedza o wszystkich siedmiu.

    `odczytano_jako` jest tu nośne, a nie ozdobne: to jedyne pole mówiące wołającemu, czy jego
    argument został przeczytany jako kod, czy jako fraza. Kształt rozstrzyga o tym bez flagi,
    więc bez tego pola literówka w kodzie (`621OB` z literą O) wyglądałaby jak fraza bez
    trafień — czyli jak prawdziwa odpowiedź.
    """
    return {
        "odczytano_jako": wynik.odczytano_jako,
        "wszystkich": wynik.wszystkich,
        "obciete": wynik.obciete,
        "trafienia": [_trafienie_jako_dane(t) for t in wynik.trafienia],
    }


def _trafienie_jako_dane(trafienie: Trafienie) -> dict[str, object]:
    return {
        "kod": trafienie.kod,
        "nazwa": trafienie.nazwa,
        "rocznik": trafienie.rocznik,
        "poprzednicy": [
            {
                "kod": p.kod,
                "nazwa": p.nazwa,
                # Obie postacie niejednoznaczności osobno, tak samo jak na ekranie: „prowadzi
                # też do innego kodu" i „ten kod nadal istnieje w 2025" czyta się inaczej,
                # a zlanie ich w jedną listę pokazywało ten sam kod dwa razy (`pkdmap`).
                "rowniez": [{"kod": k, "nazwa": n} for k, n in p.rowniez],
                "dzis": p.dzis,
            }
            for p in trafienie.poprzednicy
        ],
        "nastepcy": [{"kod": k, "nazwa": n} for k, n in trafienie.nastepcy],
    }
