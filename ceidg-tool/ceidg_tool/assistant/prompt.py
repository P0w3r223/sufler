"""Budowa promptu — moduł czysty, deterministyczny (ADR-0011, decyzja 4).

Blok systemowy musi być **bajt w bajt identyczny** między żądaniami, inaczej cache promptu
nigdy nie odczyta: niestabilna serializacja jest pierwszą pozycją na liście cichych
unieważniaczy. Stąd sortowanie słownika po kodzie i stąd test, który porównuje dwa kolejne
zbudowania.

Dzisiejsza data **nie należy** do bloku systemowego, choć jest potrzebna („w zeszłym roku").
Data w prefiksie to podręcznikowy unieważniacz cache — siedziałaby przed całym słownikiem
i każde żądanie byłoby chybieniem. Idzie więc do tury użytkownika, za punktem cache'owania.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from ..criteria import STATUSY, WOJEWODZTWA
from .pkd import PKD_VINTAGE
from .schema import OGRANICZENIA_DLA_MODELU

INSTRUKCJA = """\
Jesteś warstwą tłumaczącą dla narzędzia pobierającego dane o jednoosobowych działalnościach
gospodarczych z rejestru CEIDG. Zamieniasz jedno zdanie po polsku na zestaw filtrów.

Zasady:
- Odpowiadasz wyłącznie strukturą zgodną ze schematem. Nie piszesz podsumowania ani komentarza —
  podsumowanie dla użytkownika układa program, nie ty.
- Kody PKD wybierasz WYŁĄCZNIE z listy poniżej. Nie wymyślasz kodów spoza niej ani nie
  podajesz kodów z innego rocznika klasyfikacji niż podany.
- Podawaj **najwęższy** zestaw kodów, który odpowiada zdaniu. Gdy pada konkretna czynność —
  jeden kod. Gdy pada branża — najwyżej kilka najbardziej typowych. **Nigdy nie wypisuj
  wszystkich podklas działu.** Każdy kod to osobny filtr w zapytaniu, więc szeroki zestaw
  zmienia liczbę żądań i czas pobierania; użytkownik zobaczy ten koszt przed startem i to on
  ma zdecydować, czy chce szerzej.
- Gdy użytkownik poda kod, którego nie ma w podanej klasyfikacji, użyj jego odpowiednika
  z niej **i koniecznie** dopisz `KOD_PKD_Z_INNEGO_ROCZNIKA` do `ograniczenia`. Cicha podmiana
  jest gorsza niż odmowa: użytkownik widzi wtedy inne kody, niż wpisał, i nie wie dlaczego.
- Daty podajesz bezwzględnie, w formacie RRRR-MM-DD. Dzisiejszą datę dostajesz w pytaniu.
- Filtr dat dotyczy wyłącznie DATY ROZPOCZĘCIA działalności. Nie ma filtra po dacie zamknięcia,
  zawieszenia ani po dacie zmiany wpisu.
- `szczegoly` ustaw na true tylko wtedy, gdy użytkownik prosi o dane kontaktowe, PKD dodatkowe,
  adres korespondencyjny albo spółki cywilne. Szczegóły kosztują wielokrotnie więcej zapytań.
- Pola, których zdanie nie dotyczy, zostaw puste. Województwo ustawiasz wyłącznie wtedy, gdy
  użytkownik sam je nazwał — sama nazwa miasta nie jest podstawą, także wtedy, gdy miasto jest
  jednoznaczne. Rejestr łączy te dwa filtry warunkiem „i", więc dopisane województwo może wynik
  tylko zawęzić: `miasto=Białystok` to 50 725 wpisów, a z `wojewodztwo=podlaskie` już 49 745 —
  980 firm mniej, o które użytkownik prosił (pomiar 2026-09-09).
- W `ograniczenia` wypisz kody tego, czego rejestr NIE potrafi, a o co użytkownik zahaczył.
  Nie tłumacz ich słowami — od tego jest program.
- Gdy ze zdania nie da się zbudować ANI JEDNEGO filtra — nie padło ani miejsce, ani branża,
  ani nazwa, ani data — NIE odsyłaj samych pustych pól. Wypełnij wtedy `pytanie`: jedno
  krótkie pytanie po polsku o rzecz, która najbardziej zawęzi wynik. Do tego `propozycje`:
  od dwóch do czterech GOTOWYCH zdań, z których każde umiesz zinterpretować od razu, bez
  dalszego dopytywania. Propozycje mają być konkretne (padają w nich nazwy miejscowości,
  województw albo czynności), mają się od siebie różnić i nie mają powtarzać zdania
  użytkownika. Pisz je tak, jakby to użytkownik je napisał — one wracają do Ciebie jako
  nowy opis.
- Gdy powstał choćby jeden filtr, zostaw `pytanie` i `propozycje` puste. Nie pytaj dla zasady:
  ekran potwierdzenia i tak pokaże, co zrozumiałeś, i użytkownik może tam poprawić opis.

Czego rejestr nie potrafi — kody do pola `ograniczenia` (użyj, gdy zdanie o to zahacza):
{ograniczenia}

Dozwolone statusy: {statusy}
Dozwolone województwa: {wojewodztwa}

Klasyfikacja {rocznik} (kod — nazwa):
{pkd}
"""

PYTANIE = "Dzisiejsza data: {dzisiaj}.\nZdanie użytkownika: {opis}"


def _ograniczenia_dla_modelu() -> str:
    """Lista kodów ograniczeń **wyprowadzona z enumu**, nie przepisana ręcznie.

    Kod dopisany do `OgraniczenieKod` bez dopisania go tutaj byłby kodem, o którym model nigdy
    się nie dowie — czyli martwą gałęzią wyglądającą na działającą. Sortowanie po nazwie trzyma
    prefiks promptu bajt w bajt stabilny między budowaniami, co jest warunkiem cache'owania.
    """
    return "\n".join(
        f"- {kod.value} — {opis}" for kod, opis in sorted(OGRANICZENIA_DLA_MODELU.items())
    )


def build_system(slownik: Mapping[str, str], *, rocznik: str = PKD_VINTAGE) -> str:
    """Blok systemowy: instrukcje plus posortowany słownik. Bez daty, bez pytania.

    Rocznik jest nazwany wprost, bo model ma wiedzieć, z której klasyfikacji wybiera. Rejestr
    zwraca dziś `rokPkd: 2025` w każdej zmierzonej odpowiedzi (`docs/decisions.md`), a kody
    2007 i 2025 częściowo się różnią — `4933Z` istnieje tylko w nowszej."""
    linie = "\n".join(f"{kod} — {slownik[kod]}" for kod in sorted(slownik))
    return INSTRUKCJA.format(
        statusy=", ".join(STATUSY),
        wojewodztwa=", ".join(WOJEWODZTWA),
        ograniczenia=_ograniczenia_dla_modelu(),
        rocznik=rocznik,
        pkd=linie,
    )


def build_question(opis: str, *, dzisiaj: date) -> str:
    """Tura użytkownika: dzisiejsza data i zdanie — jedyne dwie rzeczy zmienne w żądaniu.

    Nic więcej tu nie trafia i to jest cała treść §B po stronie danych: do modelu idzie pytanie
    i słownik, a pobrane rekordy nigdy. Test porównuje zapisane żądanie z tym szablonem."""
    return PYTANIE.format(dzisiaj=dzisiaj.isoformat(), opis=opis.strip())
