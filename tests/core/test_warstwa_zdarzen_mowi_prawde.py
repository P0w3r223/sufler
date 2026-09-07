"""Bramka wymuszająca: to, co warstwa zdarzeń o sobie MÓWI, ma się zgadzać z tym, co most ZAPISUJE.

Poprzednia wersja tej bramki (`test_notatka_warstwy_klamie_gdy_zamkniecia_juz_sa`) pytała
o obecność ``issue_closed`` w ``notifier._KIND_LABELS`` — i **mierzyła nie to, co deklarowała**.
Trzy pomiary, każdy osobno wystarczający, żeby ją odrzucić:

* ``default_event_render`` robi ``_KIND_LABELS.get(kind, kind)``, więc rodzaj BEZ etykiety
  działa i nic nie pęka — sprzężenie „każdy rodzaj ma etykietę" nie obowiązuje;
* rodzaje emitowane dziś bez etykiety istnieją (m.in. ``github_issue_created``
  i ``github_comment_created`` z drzwi zapisu);
* w drugą stronę: etykiety istnieją dla rodzajów, których w ``src`` nie emituje nikt.

Etykiety są tabelą RENDEROWANIA. Rzeczą chronioną jest **repertuar rodzajów, jakie potrafi
wyemitować mapper mostu** — i po nim ta bramka iteruje (ADR 0073). Etap 1 ADR 0071 dokłada
zamknięcia właśnie tam, więc zrywa ją w tym samym commicie, w którym zmienia zdolność.

**Rejestr, nie dopasowanie nazwy.** Sonda nie szuka napisu ``issue_closed``: pyta o KAŻDY rodzaj
z repertuaru, czy jest zamknięciem zgłoszenia, i wymaga wpisu w rejestrze niżej. Dzięki temu
etap 1 zrywa bramkę niezależnie od tego, jak nazwie rodzaj — a dopasowanie do nazwy przepuściłoby
``issue_state_closed`` w ciszy.

**Granica roszczenia, warta wypowiedzenia:** notka mówi o zamknięciach ZGŁOSZEŃ (issue).
``pr_closed`` jest zamknięciem, ale PR-a — jego obecność w repertuarze nie czyni notki fałszywą.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "workmate"
_MAPPER = _SRC / "adapters" / "inbound" / "github" / "selection.py"

# ROSZCZENIE — sprawdzane na WARTOŚCI, nie na źródle: w ``spec.py`` napis jest sklejony z dwóch
# literałów łamanych na granicy wiersza, więc w pliku nie występuje w całości ani razu. Sonda
# czytająca źródło byłaby ślepa na to, o co pyta, i wyglądałaby identycznie jak działająca.
# Bez względu na wielkość liter: na jednej powierzchni zdanie zaczyna akapit, na drugiej stoi
# w środku — to różnica redakcyjna, nie różnica w roszczeniu.
_FRAZA_ROSZCZENIA = "zamknięć zgłoszeń nie zapisuje".casefold()

# KOTWICA — do wyszukania powierzchni, które o zamknięciach zgłoszeń w ogóle mówią. Krótsza od
# roszczenia, bo ma przetrwać łamanie wiersza, i szersza od niego, bo szukamy także zdań
# twierdzących przeciwnie. Miejsce, które o tym mówi bez bramki, jest tym, czego szukamy.
_KOTWICA = "zamknięć zgłoszeń".casefold()

# Rodzaj zdarzenia mappera → czy jest ZAMKNIĘCIEM ZGŁOSZENIA. Rejestr jest jawny, bo to jedyne
# miejsce, w którym ktoś odpowiada na to pytanie świadomie; brak wpisu zrywa bramkę razem
# z nazwą rodzaju, którego zabrakło.
_CZY_ZAMKNIECIE_ZGLOSZENIA = {
    "issue_opened": False,
    "issue_comment": False,
    "pr_opened": False,
    "pr_comment": False,
    "pr_review": False,
    "pr_merged": False,  # zamknięcie, ale PR-a — patrz „granica roszczenia" w docstringu modułu
    "pr_closed": False,  # jak wyżej
    "ci_success": False,
    "ci_failure": False,
    "branch_pushed": False,
    "branch_deleted": False,
}


def _literal(wezel: ast.AST) -> list[str]:
    """Napisy, jakimi ten węzeł MOŻE być: stała → jeden, wyrażenie warunkowe → oba ramiona."""
    if isinstance(wezel, ast.Constant) and isinstance(wezel.value, str):
        return [wezel.value]
    if isinstance(wezel, ast.IfExp):
        return _literal(wezel.body) + _literal(wezel.orelse)
    return []


def _pozycje_parametru_kind(drzewo: ast.Module) -> dict[str, int]:
    """Funkcje tego modułu, które przyjmują ``kind``, wraz z POZYCJĄ tego parametru.

    Potrzebne do kształtu 5 (argument pozycyjny): ``_branch_event("branch_pushed", …)`` nie ma
    przy napisie ani słowa ``kind``, więc bez tej mapy dwa rodzaje wypadłyby z repertuaru.
    """
    pozycje: dict[str, int] = {}
    for wezel in ast.walk(drzewo):
        if isinstance(wezel, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nazwy = [a.arg for a in wezel.args.args]
            if "kind" in nazwy:
                pozycje[wezel.name] = nazwy.index("kind")
    return pozycje


def _z_wywolania(wezel: ast.Call, pozycje: dict[str, int]) -> list[str]:
    """Kształt 1 (``kind=`` ze stałą) i kształt 5 (argument pozycyjny funkcji z ``kind``)."""
    znalezione: list[str] = []
    for kw in wezel.keywords:
        if kw.arg == "kind":
            znalezione += _literal(kw.value)
    wolany = wezel.func
    if isinstance(wolany, ast.Name) and wolany.id in pozycje:
        i = pozycje[wolany.id]
        if i < len(wezel.args):
            znalezione += _literal(wezel.args[i])
    return znalezione


def _z_przypisania(wezel: ast.Assign) -> list[str]:
    """Kształty 2-4: proste przypisanie, wyrażenie warunkowe i przypisanie krotką."""
    znalezione: list[str] = []
    for cel in wezel.targets:
        if isinstance(cel, ast.Name) and cel.id == "kind":
            znalezione += _literal(wezel.value)
        elif isinstance(cel, ast.Tuple) and isinstance(wezel.value, ast.Tuple):
            for i, element in enumerate(cel.elts):
                if (
                    isinstance(element, ast.Name)
                    and element.id == "kind"
                    and i < len(wezel.value.elts)
                ):
                    znalezione += _literal(wezel.value.elts[i])
    return znalezione


def _rodzaje_mappera() -> set[str]:
    """Repertuar rodzajów, jakie mapper mostu potrafi wyemitować — z AST, nie z listy.

    Pięć kształtów, wszystkie obecne dziś w pliku; szósty (przekazanie zmiennej ``kind=kind``)
    jest tylko przeniesieniem jednego z nich, więc nie wnosi nowego napisu:

    1. ``NewEvent(kind="literał")`` — słowo kluczowe ze stałą;
    2. ``kind = "a" if warunek else "b"`` — oba ramiona;
    3. ``kind = "literał"`` — proste przypisanie;
    4. ``kind, suffix, ... = "literał", ...`` — przypisanie krotką, po POZYCJI nazwy ``kind``;
    5. ``_branch_event("literał", ...)`` — argument POZYCYJNY funkcji, której parametr nazywa się
       ``kind``; pozycję bierzemy z definicji tej funkcji w tym samym module.

    Piąty kształt jest tu najważniejszy, choć wygląda na szczegół: to jedyne dwa rodzaje, których
    napis NIE stoi obok słowa ``kind``. Ekstraktor pomijający go byłby ślepy dokładnie tam, gdzie
    autor nie miał złych intencji, tylko wydzielił funkcję pomocniczą.
    """
    drzewo = ast.parse(_MAPPER.read_text(encoding="utf-8"), filename=str(_MAPPER))
    pozycje = _pozycje_parametru_kind(drzewo)

    rodzaje: set[str] = set()
    for wezel in ast.walk(drzewo):
        if isinstance(wezel, ast.Call):
            rodzaje.update(_z_wywolania(wezel, pozycje))
        elif isinstance(wezel, ast.Assign):
            rodzaje.update(_z_przypisania(wezel))
    return rodzaje


def _zamkniecia_sa_zapisywane() -> bool:
    """Czy most potrafi dziś zapisać zamknięcie zgłoszenia — wprost z repertuaru mappera.

    Rodzaj bez wpisu kończy się CZYTELNĄ odmową, nie ``KeyError``: kontrola mutacyjna pokazała,
    że wtedy trzy sondy naraz wywracają się na wyjątku, a komunikat nie mówi tego, co trzeba
    zrobić. Bramka ma prowadzić autora etapu 1 do odpowiedzi, a nie tylko zapalić się na czerwono.
    """
    rodzaje = _rodzaje_mappera()
    bez_odpowiedzi = sorted(rodzaje - set(_CZY_ZAMKNIECIE_ZGLOSZENIA))
    if bez_odpowiedzi:
        pytest.fail(
            f"mapper emituje rodzaj bez wpisu w rejestrze: {bez_odpowiedzi}. "
            "Dopisz go do _CZY_ZAMKNIECIE_ZGLOSZENIA z odpowiedzią, czy jest ZAMKNIĘCIEM "
            "ZGŁOSZENIA — a jeśli jest, popraw notkę warstwy i docstring `read_events_since` "
            "w tym samym commicie (ADR 0071 decyzja 1 i 10)."
        )
    return any(_CZY_ZAMKNIECIE_ZGLOSZENIA[k] for k in rodzaje)


def _mowi_ze_nie_zapisuje(tekst: str) -> bool:
    return _FRAZA_ROSZCZENIA in tekst.casefold()


def test_ekstraktor_widzi_caly_repertuar_mappera() -> None:
    """Kontrola pozytywna SONDY: ekstraktor, który przestał widzieć, przechodzi każdą asercję niżej.

    To nie jest ostrożność teoretyczna — przegląd 2026-09-07 złapał sondę preflightu paczki, która
    była konstrukcyjnie ślepa na to, o co pytała, i wyglądała identycznie jak działająca.
    Wymieniamy tu kształty, nie liczbę: rodzaj dołożony kształtem, którego ekstraktor nie zna,
    wypada z repertuaru w ciszy — więc pilnujemy po JEDNYM przedstawicielu każdego kształtu.
    """
    rodzaje = _rodzaje_mappera()

    assert "issue_opened" in rodzaje  # kształt 1: kind= ze stałą
    assert {"pr_comment", "issue_comment"} <= rodzaje  # kształt 2: oba ramiona warunku
    assert {"ci_success", "ci_failure"} <= rodzaje  # kształt 3: proste przypisanie
    assert {"pr_merged", "pr_closed"} <= rodzaje  # kształt 4: przypisanie krotką
    assert {"branch_pushed", "branch_deleted"} <= rodzaje  # kształt 5: argument pozycyjny
    assert len(rodzaje) >= 11


def test_kazdy_rodzaj_mappera_ma_odpowiedz_czy_jest_zamknieciem_zgloszenia() -> None:
    """Komplet w obie strony. Nowy rodzaj bez wpisu zrywa bramkę — i o to chodzi: etap 1 ADR 0071
    ma tu ODPOWIEDZIEĆ, a nie prześlizgnąć się nazwą, której sonda nie zna."""
    assert _rodzaje_mappera() == set(_CZY_ZAMKNIECIE_ZGLOSZENIA)


def test_notka_warstwy_mowi_prawde_o_zamknieciach() -> None:
    """RÓWNOWAŻNOŚĆ, nie warunek — zrywa się w obie strony, więc nie jest pusta ani dziś, ani po
    dołożeniu zamknięć. Notka zawyżająca zdolność i notka ją zaniżająca to ta sama klasa wady."""
    from workmate.core.application.tools.spec import _EVENTS_LAYER_NOTE

    assert _mowi_ze_nie_zapisuje(_EVENTS_LAYER_NOTE) != _zamkniecia_sa_zapisywane()


def test_drzwi_MCP_niosa_to_samo_roszczenie_co_drzwi_agenta() -> None:
    """Zdanie stoi na OBU powierzchniach, a bramkę miała dotąd tylko jedna.

    Sekwencja, którą to domyka, jest zdradliwa: etap 1 wywraca bramkę notki, ktoś poprawia notkę,
    bramka wraca do zieleni — a docstring MCP i zamrożony baseline dalej niosą stare zdanie,
    ZGODNE ZE SOBĄ, więc golden przechodzi. Drzwi, na których o stan repozytorium pyta się
    najczęściej, zaczęłyby zaniżać zdolność, którą etap 1 właśnie dostarczył.
    (Zgodność docstringa z baseline pilnuje osobno golden ``test_mcp_tool_surface`` — tu pytamy
    o PRAWDZIWOŚĆ, tam o zamrożenie.)
    """
    from workmate.core.application.tools.events import build_events_since_catalog

    class _PusteZdarzenia:
        def read_since(self, after_id, *, source=None, limit=50):
            return []

        def recent(self, *, source=None, limit=20):
            return []

    opis = build_events_since_catalog(_PusteZdarzenia())[0].description  # type: ignore[arg-type]

    assert _mowi_ze_nie_zapisuje(opis) != _zamkniecia_sa_zapisywane()


def test_o_zamknieciach_zgloszen_mowia_WYLACZNIE_dwie_bramkowane_powierzchnie() -> None:
    """Trzecia kopia zdania wymknęłaby się obu asercjom wyżej — więc szukamy jej w CAŁYM ``src``.

    Kierunek jest ten sam co reszty tej bramki: nie pytamy dwóch znanych miejsc, czy się zgadzają,
    tylko całej powierzchni, gdzie ten temat w ogóle stoi. Zbiór jest przypięty w OBIE strony
    i nie zależy od tego, czy zamknięcia są dziś zapisywane: po etapie 1 te same dwa miejsca będą
    mówiły rzecz przeciwną, a bramką prawdziwości są dwie sondy wyżej. Tutaj pilnujemy tylko tego,
    żeby nie przybyło miejsca, którego żadna z nich nie ogląda.
    """
    nosiciele = {
        p.relative_to(_SRC).as_posix()
        for p in _SRC.rglob("*.py")
        if _KOTWICA in p.read_text(encoding="utf-8").casefold()
    }

    assert nosiciele == {
        "core/application/tools/spec.py",  # notka koperty (powierzchnia agenta)
        "core/application/tools/events.py",  # docstring `read_events_since` (MCP)
    }


@pytest.mark.parametrize("rodzaj", sorted(_CZY_ZAMKNIECIE_ZGLOSZENIA))
def test_rejestr_opisuje_rodzaje_realnie_emitowane(rodzaj: str) -> None:
    """Wpis bez rodzaju to rejestr, który przestał opisywać kod — ta sama wada,
    co rodzaj bez wpisu."""
    assert rodzaj in _rodzaje_mappera()
