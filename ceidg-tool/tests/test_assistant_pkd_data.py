"""Sprawdzenia **zawartości** dostarczonego słownika PKD (ADR-0011, decyzja 4).

`tests/test_assistant.py` pyta „czy kod działa" na słowniku pięcioelementowym. Tu pytanie brzmi
inaczej: „czy to, co leży w pakiecie, zgadza się z klasyfikacją". Trzy z czterech sprawdzeń
z ADR są tutaj; czwartego — porównania kilkunastu kodów z wyszukiwarką GUS — nie da się
zautomatyzować i należy do właściciela, bo tylko ono łapie błąd **u źródła**. Test
samozgodny go nie zobaczy.

Dopóki słownika nie ma, cały plik jest pomijany z komunikatem mówiącym, jak go zbudować.
Pominięcie jest tu uczciwsze niż zieleń: nie udaje, że coś sprawdziliśmy.
"""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from ceidg_tool.assistant.pkd import DEFAULT_PKD_PATH, load_pkd
from ceidg_tool.criteria import normalize_pkd

pytestmark = pytest.mark.skipif(
    not DEFAULT_PKD_PATH.is_file(),
    reason=(
        f"brak {DEFAULT_PKD_PATH.name} — zbuduj go z oficjalnego pliku GUS: "
        "PYTHONUTF8=1 python scripts/build_pkd.py <plik.csv>"
    ),
)

# 728 podklas — policzone przez `scripts/build_pkd.py` z `StrukturaPKD2025.xls` pobranego przez
# właściciela z GUS 2026-09-07 — plik BIFF8 przekonwertowany do `StrukturaPKD2025.xlsx`,
# i tę nazwę niesie nagłówek słownika (kolumna „Podklasa" arkusza „PKD 2025", 87 działów, kody od
# 0111Z do 9900Z). Wartość pochodzi **z pliku źródłowego**, nie z pamięci ani z publikacji.
#
# Uwaga na pokusę: gdy ten test się zaczerwieni, naturalnym odruchem jest zmienić stałą na to,
# co wyszło z pliku. Wtedy sprawdzenie staje się samozgodne i przestaje cokolwiek znaczyć —
# jego zadaniem jest być **głośne** przy cichej utracie albo duplikacji wierszy.
OCZEKIWANE_PODKLASY: int | None = 728

# Kody z `tests/conftest.py`: nazwy syntetyczne, kody prawdziwe. Nadają się do sprawdzenia
# rozwiązywalności, nie do porównania nazw.
KODY_Z_CONFTEST = ("3031Z", "6210B", "0111Z", "4711Z")


def kody_z_rejestru() -> dict[str, str]:
    """Pary kod+nazwa z prawdziwych odpowiedzi API zapisanych jako fixtures.

    To jest ten sam ruch, co wyprowadzenie zbioru ukrywanych kolumn z `row_to_record` po
    przeglądzie 2026-09-06: lista pisana z pamięci łapie tylko dryf, który ktoś zapamiętał.
    """
    pary: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            kod, nazwa = node.get("kod"), node.get("nazwa")
            if isinstance(kod, str) and isinstance(nazwa, str) and len(kod) in (5, 6):
                with suppress(ValueError):
                    pary[normalize_pkd(kod)] = nazwa
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    # Ścieżka względem tego pliku, nie względem katalogu roboczego: `glob("tests/fixtures/…")`
    # uruchomiony spoza katalogu głównego zwracał pusty zbiór, a wtedy porównanie nazw
    # przechodziło **pusto** — w pliku, którego docstring dowodzi, że pominięcie jest
    # uczciwsze od zieleni.
    for path in sorted((Path(__file__).resolve().parent / "fixtures").glob("*.json")):
        try:
            walk(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return pary


def test_every_key_is_already_canonical() -> None:
    """Klucz w innej postaci niż kanoniczna nigdy nie zostałby odnaleziony."""
    slownik = load_pkd()

    zle = [kod for kod in slownik if normalize_pkd(kod) != kod]

    assert zle == [], f"klucze poza postacią kanoniczną: {zle[:5]}"


@pytest.mark.skipif(
    OCZEKIWANE_PODKLASY is None, reason="liczba podklas jeszcze nieustalona u źródła"
)
def test_the_entry_count_matches_the_classification() -> None:
    """Liczba wpisów jest kotwicą przeciw cichej utracie albo duplikacji wierszy."""
    assert len(load_pkd()) == OCZEKIWANE_PODKLASY


def test_every_code_the_register_actually_returned_is_in_the_dictionary() -> None:
    """Kody wzięte z prawdziwych odpowiedzi API muszą się w słowniku odnaleźć.

    To jedyne sprawdzenie w tym pliku, którego źródłem nie jest sam słownik — i jedyne, które
    zauważy dryf między klasyfikacją a tym, co rejestr faktycznie zwraca.
    """
    slownik = load_pkd()
    z_rejestru = kody_z_rejestru()

    assert z_rejestru, "brak par kod+nazwa w fixtures — to sprawdzenie przestałoby cokolwiek robić"
    do_sprawdzenia = set(z_rejestru) | set(KODY_Z_CONFTEST)
    brakujace = sorted(kod for kod in do_sprawdzenia if kod not in slownik)

    assert brakujace == [], f"rejestr zwrócił kody spoza słownika: {brakujace}"


def test_names_do_not_silently_disagree_with_the_register() -> None:
    """Rozjazd nazw jest ostrzeżeniem, nie błędem — rejestr bywa skrótowy.

    Nie porównujemy nazw dosłownie: CEIDG podaje je czasem skrócone albo z inną interpunkcją.
    Sprawdzamy wspólny rdzeń, żeby wychwycić pomyłkę **kategorii** (kod budowlany opisany jako
    fryzjerstwo), nie różnicę stylistyczną.
    """
    slownik = load_pkd()
    z_rejestru = kody_z_rejestru()

    assert z_rejestru, "brak par kod+nazwa — porównanie nazw przeszłoby pusto"
    rozjazdy: list[str] = []
    for kod, nazwa in z_rejestru.items():
        nasza = slownik.get(kod, "")
        wspolne = {w.lower() for w in nazwa.split() if len(w) > 5} & {
            w.lower() for w in nasza.split() if len(w) > 5
        }
        # Dwa wspólne słowa, nie jedno: polskie nazwy PKD dzielą „działalność" i „pozostała"
        # hojnie, więc próg jednego słowa przechodziłby przy pomyłce kategorii. Na dziewięciu
        # prawdziwych parach wspólnych słów jest od trzech do dziewięciu, więc próg nie uwiera.
        if len(wspolne) < 2:
            rozjazdy.append(f"{kod}: rejestr {nazwa!r} vs słownik {nasza!r}")

    assert rozjazdy == [], "nazwy rozjeżdżają się z rejestrem:\n  " + "\n  ".join(rozjazdy)


def test_the_assistant_test_dictionary_agrees_with_the_generated_one() -> None:
    """Atrapa `SLOWNIK` musi być prawdziwym wycinkiem PKD 2025 — kodem **i** nazwą.

    Do audytu 2026-09-07 była wycinkiem PKD **2007** podanym jako 2025: trzy z pięciu kodów
    (`3030Z`, `4120Z`, `6201Z`) w roczniku 2025 nie istnieją, a nazwy były przepisane dosłownie
    z tablicy `nazwy_2007`. Testowany moduł ma przy tym `PKD_VINTAGE = "PKD 2025"` na sztywno
    i wstrzykuje ten napis do promptu, a `test_the_interpretation_names_the_industry` pokazywał
    na tej atrapie ekran potwierdzenia — czyli jedyną kontrolę operatora nad branżą.

    To ta sama awaria, przez którą wybrano zły rocznik: ręcznie napisana atrapa opisująca
    rzeczywistość, której nie ma, i nic, co by ją z rzeczywistością zderzyło. Bliźniacza atrapa
    tablicy przejścia (`tests/support.py`) była przypięta od początku; ta nie.

    Porównujemy **nazwy**, nie samą obecność kodów: kod obecny z nazwą z innego rocznika jest
    dokładnie tym, co ten test ma łapać.
    """
    from tests.test_assistant import SLOWNIK

    slownik = load_pkd()

    rozjazd = {
        kod: (nazwa, slownik.get(kod))
        for kod, nazwa in SLOWNIK.items()
        if slownik.get(kod) != nazwa
    }

    assert rozjazd == {}, f"atrapa rozjechała się z PKD 2025: {rozjazd}"
