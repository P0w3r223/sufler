"""Reguły granic z `docs/design/etap1_core.md`, sprawdzane skanem AST i odczytem manifestu.

Etap 1 zamyka reguły 1, 2, 3, 6 i 7. Każda ma **test samego skanu** z zaszczepionym
naruszeniem — reguła, o której nie da się pokazać, że potrafi zapłonąć, jest nieodróżnialna
od zbioru pustego, a ten projekt dziedziczy tę lekcję zamiast odkrywać ją po raz drugi.
"""

from __future__ import annotations

import ast
import socket
import tomllib
from pathlib import Path

import pytest
import yaml

from tests.support import ZakazSieciError

KORZEN = Path(__file__).resolve().parent.parent
PAKIET = KORZEN / "krs_tool"

# Reguła 1. Dopasowanie po **prefiksach z kropką**, nie po korzeniu importu. Wersja
# korzeniowa (taka stoi w `ceidg-tool`) przepuściłaby `import urllib.request`, raportując
# się przy tym jako domknięta — to dokładnie ten kształt, który wpuścił kiedyś drugi stos
# HTTP do tamtego pakietu.
ZABRONIONE_MODULY = (
    "httpx",
    "httpx2",
    "requests",
    "urllib",
    "urllib3",
    "http",
    "socket",
    "ssl",
    "ftplib",
    "smtplib",
    "telnetlib",
    "aiohttp",
    "websockets",
    "anthropic",
    "xmlrpc",
)

# Reguła 2. Nazwy pakietów, których obecność w manifeście oznacza, że coś potrafi otworzyć
# gniazdo. Sprawdzamy nazwę przed pierwszym znakiem wersji lub ekstry.
ZABRONIONE_ZALEZNOSCI = frozenset(
    {"httpx", "httpx2", "requests", "urllib3", "aiohttp", "anthropic", "websockets", "httpcore"}
)

# Reguła 6. Wywołania, których argumenty lądują na terminalu.
WYWOLANIA_Z_TRESCIA = frozenset({"print", "log", "rule", "add_row", "add_column"})
SLOWA_Z_TRESCIA = frozenset({"title", "header", "description", "label", "renderable"})
NEUTRALIZATORY = frozenset({"safe", "safe_or_none"})
FABRYKI_RYSOWALNE = frozenset({"Table", "Column", "Panel", "Group"})
PRODUCENCI_BEZPIECZNI = NEUTRALIZATORY | FABRYKI_RYSOWALNE

# Reguła 7. `cli.py` nie drukuje **niczym**: `typer.echo` jest w programie na `typer`
# odruchem pierwszym, a skan pilnujący samego `console.print` dałby fałszywe domknięcie.
WYWOLANIA_WYJSCIA = WYWOLANIA_Z_TRESCIA | {"echo", "secho", "print_json", "write"}


def _pliki_pakietu() -> list[Path]:
    return sorted(PAKIET.rglob("*.py"))


def _id_wzgledny(sciezka: Path) -> str:
    """Nazwa pliku wobec pakietu: `texts.py` jest w tym drzewie dwa razy i to nie pomyłka."""
    return sciezka.relative_to(PAKIET).as_posix()


def _wszystkie_pliki() -> list[Path]:
    return sorted(
        [
            *PAKIET.rglob("*.py"),
            *(KORZEN / "tests").rglob("*.py"),
            *(KORZEN / "scripts").rglob("*.py"),
        ]
    )


# Dwa pliki muszą znać `socket`, bo ich zadaniem jest go zamknąć: fixture zakazu i test
# tego zakazu. Lista jest zamknięta i ma **własny** test — wyjątek bez obserwatora to
# dokładnie ten kształt, w którym naruszenie się chowa.
WOLNO_ZNAC_SIEC = ("conftest.py", "test_granice.py")


def _pliki_pod_regula_1() -> list[Path]:
    return [p for p in _wszystkie_pliki() if p.name not in WOLNO_ZNAC_SIEC]


def importowane_moduly(source: str) -> set[str]:
    """Pełne, kropkowane nazwy modułów importowanych bezwzględnie."""
    tree = ast.parse(source)
    moduly: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            moduly.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            moduly.add(node.module)
    return moduly


def literaly_import_module(source: str) -> set[str]:
    """Napisy podane do `importlib.import_module` — import ukryty przed skanem importów."""
    tree = ast.parse(source)
    nazwy: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        cel = node.func
        # Obie formy zapisu: `importlib.import_module("httpx")` i — po `from importlib import
        # import_module` — samo `import_module("httpx")`. Skan widzący tylko pierwszą
        # raportowałby się jako domknięty, będąc ślepym na wersję, którą podpowiada edytor.
        wolane = (
            cel.attr
            if isinstance(cel, ast.Attribute)
            else cel.id
            if isinstance(cel, ast.Name)
            else ""
        )
        if wolane == "import_module":
            nazwy.update(
                a.value
                for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            )
    return nazwy


def zabronione_importy(source: str) -> set[str]:
    """Moduły sieciowe zaimportowane w tym źródle — po prefiksie z kropką."""
    kandydaci = importowane_moduly(source) | literaly_import_module(source)
    return {
        modul
        for modul in kandydaci
        for zly in ZABRONIONE_MODULY
        if modul == zly or modul.startswith(f"{zly}.")
    }


def _nazwa_wywolania(node: ast.Call) -> str:
    cel = node.func
    if isinstance(cel, ast.Attribute):
        return cel.attr
    if isinstance(cel, ast.Name):
        return cel.id
    return ""


def _jest_bezpieczny(node: ast.expr, bezpieczne_nazwy: set[str]) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Call):
        return _nazwa_wywolania(node) in PRODUCENCI_BEZPIECZNI
    if isinstance(node, ast.Name):
        return node.id in bezpieczne_nazwy
    if isinstance(node, ast.Starred):
        return _jest_bezpieczny(node.value, bezpieczne_nazwy)
    if isinstance(node, ast.GeneratorExp | ast.ListComp):
        return _jest_bezpieczny(node.elt, bezpieczne_nazwy)
    return False


def _nazwy_z_neutralizatora(tree: ast.AST) -> set[str]:
    """Zmienne przypisane wprost z neutralizatora albo z fabryki rysowalnej."""
    nazwy: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if _nazwa_wywolania(node.value) in PRODUCENCI_BEZPIECZNI:
                nazwy.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return nazwy


def naruszenia_neutralizatora(source: str) -> list[int]:
    """Numery linii, w których napis trafia na terminal z pominięciem neutralizatora."""
    tree = ast.parse(source)
    bezpieczne = _nazwy_z_neutralizatora(tree)
    naruszenia: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        nazwa = _nazwa_wywolania(node)
        if nazwa not in WYWOLANIA_Z_TRESCIA and nazwa not in FABRYKI_RYSOWALNE:
            continue
        podejrzane = list(node.args) if nazwa in WYWOLANIA_Z_TRESCIA else []
        podejrzane += [kw.value for kw in node.keywords if kw.arg in SLOWA_Z_TRESCIA]
        if any(not _jest_bezpieczny(arg, bezpieczne) for arg in podejrzane):
            naruszenia.append(node.lineno)
    return naruszenia


def wywolania_wyjscia(source: str) -> list[int]:
    """Numery linii z jakimkolwiek wywołaniem drukującym."""
    tree = ast.parse(source)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _nazwa_wywolania(node) in WYWOLANIA_WYJSCIA
    ]


# --------------------------------------------------------------------------------------
# Reguła 1 — graf importów
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("sciezka", _pliki_pod_regula_1(), ids=lambda p: p.name)
def test_regula_1_zaden_modul_nie_importuje_sieci(sciezka: Path) -> None:
    """Nic w tym drzewie nie zna biblioteki, która potrafi otworzyć połączenie."""
    winowajcy = zabronione_importy(sciezka.read_text(encoding="utf-8"))

    assert not winowajcy, f"{sciezka.name} importuje {sorted(winowajcy)} — patrz docs/adr/0001"


def test_lista_wyjatkow_od_reguly_1_jest_zamknieta() -> None:
    """Wyjątek wolno mieć tylko temu, kto zakaz wykonuje — i wyłącznie tym dwóm plikom.

    Bez tego testu wystarczyłoby dopisać nazwę do krotki, żeby wyłączyć regułę 1 dla
    dowolnego pliku, i nic by o tym nie powiedziało.
    """
    katalog = KORZEN / "tests"
    istniejace = {p.name for p in katalog.iterdir() if p.name in WOLNO_ZNAC_SIEC}

    assert istniejace == set(WOLNO_ZNAC_SIEC)
    assert len(WOLNO_ZNAC_SIEC) == 2


@pytest.mark.parametrize(
    ("zrodlo", "oczekiwane"),
    [
        ("import json", 0),
        ("from pathlib import Path", 0),
        # kształty, które przepuszcza skan dopasowujący sam korzeń importu
        ("import urllib.request", 1),
        ("from http.client import HTTPConnection", 1),
        ("import httpx", 1),
        ("import socket", 1),
        ("from importlib import import_module\nimport_module('httpx')", 1),
    ],
)
def test_skan_importow_naprawde_lapie(zrodlo: str, oczekiwane: int) -> None:
    """Test samego skanu reguły 1, z zaszczepionym naruszeniem."""
    assert len(zabronione_importy(zrodlo)) == oczekiwane


# --------------------------------------------------------------------------------------
# Reguła 2 — manifest zależności
# --------------------------------------------------------------------------------------


def _nazwa_zaleznosci(wpis: str) -> str:
    for separator in ("[", ">", "<", "=", "!", "~", ";", " "):
        wpis = wpis.split(separator, 1)[0]
    return wpis.strip().lower().replace("_", "-")


def test_regula_2_manifest_nie_deklaruje_zaleznosci_sieciowej() -> None:
    """Ani zależności podstawowe, ani żadna grupa dodatkowa nie wnoszą sieci."""
    manifest = tomllib.loads((KORZEN / "pyproject.toml").read_text(encoding="utf-8"))
    projekt = manifest["project"]
    wpisy = list(projekt.get("dependencies", []))
    for grupa in projekt.get("optional-dependencies", {}).values():
        wpisy.extend(grupa)

    winowajcy = {_nazwa_zaleznosci(w) for w in wpisy} & ZABRONIONE_ZALEZNOSCI

    assert not winowajcy, f"manifest wnosi {sorted(winowajcy)} — patrz docs/adr/0001, decyzja 3"


def test_skan_manifestu_naprawde_lapie() -> None:
    """Test samego skanu reguły 2."""
    assert _nazwa_zaleznosci("httpx>=0.27,<0.29") in ZABRONIONE_ZALEZNOSCI
    assert _nazwa_zaleznosci("anthropic>=1,<2") in ZABRONIONE_ZALEZNOSCI
    assert _nazwa_zaleznosci("pydantic>=2.7") not in ZABRONIONE_ZALEZNOSCI


# --------------------------------------------------------------------------------------
# Reguła 3 — zakaz gniazd w czasie wykonania
# --------------------------------------------------------------------------------------


def test_zakaz_gniazd_naprawde_gryzie() -> None:
    """Test samego zakazu: bez tego byłby fixture, o którym wierzymy, że działa."""
    with pytest.raises(ZakazSieciError):
        socket.create_connection(("example.invalid", 80))

    with pytest.raises(ZakazSieciError):
        socket.getaddrinfo("example.invalid", 80)


# --------------------------------------------------------------------------------------
# Reguła 6 — neutralizator kanału
# --------------------------------------------------------------------------------------


def test_regula_6_kazdy_napis_na_terminal_idzie_przez_neutralizator() -> None:
    """Skan po modułach, które znają `rich`."""
    winowajcy = {
        f"{sciezka.name}:{linia}"
        for sciezka in _pliki_pakietu()
        for linia in naruszenia_neutralizatora(sciezka.read_text(encoding="utf-8"))
    }

    assert winowajcy == set()


@pytest.mark.parametrize(
    ("zrodlo", "oczekiwane"),
    [
        ("console.print(safe(nazwa))", 0),
        ('console.print("Blad")', 0),
        ("table.add_row(*(safe(c) for c in row))", 0),
        ("tytul = safe_or_none(t)\ntable = Table(title=tytul)", 0),
        # kształty, które wywracały program w tamtym projekcie
        ("console.print(nazwa)", 1),
        ('console.print(f"Firma: {nazwa}")', 1),
        ("table.add_row(nazwa)", 1),
        ("Table(title=nazwa)", 1),
        ("console.print(str(exc))", 1),
    ],
)
def test_skan_neutralizatora_naprawde_lapie(zrodlo: str, oczekiwane: int) -> None:
    """Test samego skanu reguły 6."""
    assert len(naruszenia_neutralizatora(zrodlo)) == oczekiwane


def test_moduly_znajace_rich_sa_tam_gdzie_myslimy() -> None:
    """Zabezpieczenie przed cichym rozbrojeniem reguły 6 po zmianie nazwy pliku."""
    znajace = {
        sciezka.relative_to(PAKIET).as_posix()
        for sciezka in _pliki_pakietu()
        if any(
            m == "rich" or m.startswith("rich.")
            for m in importowane_moduly(sciezka.read_text(encoding="utf-8"))
        )
    }

    assert znajace == {"richtext.py", "render.py", "console.py"}


# --------------------------------------------------------------------------------------
# Reguła 7 — warstwa poleceń nie drukuje
# --------------------------------------------------------------------------------------


def test_regula_7_cli_nie_drukuje_niczym() -> None:
    """Warunek konieczny, żeby reguła 6 dała się sprawdzić skanem jednego pliku."""
    linie = wywolania_wyjscia((PAKIET / "cli.py").read_text(encoding="utf-8"))

    assert [f"cli.py:{n}" for n in linie] == []


@pytest.mark.parametrize(
    ("zrodlo", "oczekiwane"),
    [
        ("render_block(blok, konsola)", 0),
        ("typer.echo('gotowe')", 1),
        ("console.print(blok)", 1),
        ("sys.stdout.write('x')", 1),
    ],
)
def test_skan_wyjscia_naprawde_lapie(zrodlo: str, oczekiwane: int) -> None:
    """Test samego skanu reguły 7."""
    assert len(wywolania_wyjscia(zrodlo)) == oczekiwane


# --------------------------------------------------------------------------------------
# Reguły 4, 5, 10, 11 — warstwa sygnałów
#
# Doszły w kroku 3, a nie w 4 jak zapowiadał plan: dotyczą kodu i danych pisanych właśnie
# teraz, a obserwator, który przychodzi wcześniej, nigdy nie jest gorszy.
# --------------------------------------------------------------------------------------

PAKIET_SYGNALOW = PAKIET / "signals"
ZEGAROWE = ("datetime.now", "date.today", "time.time", "time.monotonic", "utcnow")
FORBIDDEN_W_CZYSTYCH = frozenset({"rich", "typer", "questionary", "sqlite3", "openpyxl"})

# Reguła 11. Zamknięty leksykon oskarżenia. Sprawdzamy WARTOŚCI — treść, która trafia do
# człowieka — a nie komentarze: plik tłumaczący, czego nie mówimy, musi móc to nazwać.
LEKSYKON_OSKARZENIA = (
    "po terminie",
    "z opóźnieniem",
    "spóźni",
    "opóźni",
    "nieterminowo",
    "narusza obowiązek",
    "uchyla się",
    "za późno",
)


def _pliki_sygnalow() -> list[Path]:
    return sorted(PAKIET_SYGNALOW.rglob("*.py"))


def odwolania_do_zegara(source: str) -> list[str]:
    """Wywołania i importy, przez które do modułu weszłoby „dziś"."""
    znalezione = [wzorzec for wzorzec in ZEGAROWE if wzorzec in source]
    if "clock" in importowane_moduly(source) or any(
        m.endswith(".clock") for m in importowane_moduly(source)
    ):
        znalezione.append("clock")
    return znalezione


def literaly_liczbowe(source: str) -> list[int]:
    """Numery linii z literałem liczbowym innym niż 0 i 1."""
    tree = ast.parse(source)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
        and node.value not in (0, 1)
    ]


def _napisy_nie_bedace_docstringiem(source: str) -> list[str]:
    tree = ast.parse(source)
    docstringi = {
        id(wezel.body[0].value)
        for wezel in ast.walk(tree)
        if isinstance(wezel, ast.Module | ast.FunctionDef | ast.ClassDef)
        and wezel.body
        and isinstance(wezel.body[0], ast.Expr)
        and isinstance(wezel.body[0].value, ast.Constant)
        and isinstance(wezel.body[0].value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstringi
    ]


def slowa_oskarzenia(teksty: list[str]) -> list[str]:
    """Które zakazane zwroty padają w podanych napisach."""
    polaczone = " ".join(teksty).lower()
    return [zwrot for zwrot in LEKSYKON_OSKARZENIA if zwrot in polaczone]


def _wartosci_yaml(wezel: object) -> list[str]:
    if isinstance(wezel, str):
        return [wezel]
    if isinstance(wezel, dict):
        return [t for v in wezel.values() for t in _wartosci_yaml(v)]
    if isinstance(wezel, list):
        return [t for element in wezel for t in _wartosci_yaml(element)]
    return []


@pytest.mark.parametrize("sciezka", _pliki_sygnalow(), ids=lambda p: p.name)
def test_regula_4_warstwa_sygnalow_nie_czyta_zegara(sciezka: Path) -> None:
    """Każda data w sygnale pochodzi z odpisu, nigdy z „dziś"."""
    winowajcy = odwolania_do_zegara(sciezka.read_text(encoding="utf-8"))

    assert not winowajcy, f"{sciezka.name} sięga po zegar: {winowajcy}"


@pytest.mark.parametrize(
    "sciezka",
    [*_pliki_sygnalow(), PAKIET / "texts.py", PAKIET / "raport" / "texts.py"],
    ids=_id_wzgledny,
)
def test_regula_5_moduly_czyste_nie_znaja_wyjscia(sciezka: Path) -> None:
    """Bez tego ekrany i reguły przestałyby dać się sprawdzić bez terminala."""
    korzenie = {m.split(".")[0] for m in importowane_moduly(sciezka.read_text(encoding="utf-8"))}

    assert not korzenie & FORBIDDEN_W_CZYSTYCH


@pytest.mark.parametrize("sciezka", _pliki_sygnalow(), ids=lambda p: p.name)
def test_regula_10_brak_literalow_liczbowych(sciezka: Path) -> None:
    """Sześć miesięcy i piętnaście dni nie mają gdzie zamieszkać poza katalogiem."""
    linie = literaly_liczbowe(sciezka.read_text(encoding="utf-8"))

    assert linie == [], f"{sciezka.name}: literał liczbowy w liniach {linie}"


def test_regula_11_katalog_nie_zawiera_slowa_oskarzenia() -> None:
    """Skan po WARTOŚCIACH reguł — komentarz tłumaczący zakaz musi móc go nazwać."""
    teksty: list[str] = []
    for plik in sorted((PAKIET_SYGNALOW / "reguly").glob("*.yaml")):
        teksty.extend(_wartosci_yaml(yaml.safe_load(plik.read_text(encoding="utf-8"))))

    assert slowa_oskarzenia(teksty) == []


@pytest.mark.parametrize("sciezka", _pliki_pakietu(), ids=_id_wzgledny)
def test_regula_11_zaden_napis_pakietu_nie_zawiera_slowa_oskarzenia(sciezka: Path) -> None:
    """Skan idzie po CAŁYM pakiecie, a nie po liście modułów piszących zdania.

    Lista była krótsza od prawdy dokładnie jeden krok: raport dostał własny moduł tekstów
    i wypadł spod tej reguły, choć to on pisze zdania, które czyta człowiek. Komunikaty
    wyjątków i zdania dziennika też są treścią dla operatora. Zbiór „moduły z prozą" rośnie,
    więc skanujemy dopełnienie: **nigdzie w pakiecie** nie ma słowa oskarżenia, a wolno je
    nazwać w komentarzu i w docstringu, bo tych skan nie czyta.
    """
    napisy = _napisy_nie_bedace_docstringiem(sciezka.read_text(encoding="utf-8"))

    assert slowa_oskarzenia(napisy) == []


@pytest.mark.parametrize(
    ("zrodlo", "oczekiwane"),
    [
        ("MIESIECY = 0", 0),
        ("FLAGA = 1", 0),
        ("MIESIECY = 6", 1),
        ("termin = dzien + 15", 1),
    ],
)
def test_skan_literalow_naprawde_lapie(zrodlo: str, oczekiwane: int) -> None:
    assert len(literaly_liczbowe(zrodlo)) == oczekiwane


@pytest.mark.parametrize(
    ("tekst", "oczekiwane"),
    [
        ("brak wpisu na dzień D", 0),
        ("Wynikiem jest obserwacja, nie zarzut.", 0),
        ("sprawozdanie złożono po terminie", 1),
        ("spółka spóźniła się ze złożeniem", 1),
        ("złożono za późno", 1),
    ],
)
def test_skan_leksykonu_naprawde_lapie(tekst: str, oczekiwane: int) -> None:
    assert len(slowa_oskarzenia([tekst])) == oczekiwane


# --------------------------------------------------------------------------------------
# Reguły 8 i 9 — jedyni producenci typów oznaczonych
#
# Mechanizmem jest mypy strict nad `NewType`, a to poniżej jest jego obserwator: typ
# oznaczony chroni przed pomyłką dopóty, dopóki powstaje w jednym miejscu. Drugi producent
# nie wywraca bramki typów — on ją opróżnia, bo od tej chwili „ten typ" znaczy tyle, co
# „ktoś to gdzieś opakował".
# --------------------------------------------------------------------------------------

PRODUCENCI_TYPOW = {
    "NumerKRS": "identity.py",
    "DzienBilansowy": "odpis/czytanie.py",
    "TerminUstawowy": "signals/terminy.py",
}


def wywolania_konstruktora(source: str, nazwa: str) -> list[int]:
    """Numery linii, w których opakowuje się wartość w typ oznaczony.

    Deklaracja (`NumerKRS = NewType("NumerKRS", str)`) nie jest wywołaniem tej nazwy, więc
    plik deklarujący typ nie liczy się przez sam fakt deklaracji — liczy się ten, kto typ
    wytwarza.
    """
    tree = ast.parse(source)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _nazwa_wywolania(node) == nazwa
    ]


@pytest.mark.parametrize(("typ", "producent"), sorted(PRODUCENCI_TYPOW.items()))
def test_reguly_8_i_9_typ_oznaczony_ma_jednego_producenta(typ: str, producent: str) -> None:
    """Granica zakresu i granica arytmetyki terminów stoją na tym, że tych miejsc jest po jednym."""
    wytworcy = {
        sciezka.relative_to(PAKIET).as_posix()
        for sciezka in _pliki_pakietu()
        if wywolania_konstruktora(sciezka.read_text(encoding="utf-8"), typ)
    }

    assert wytworcy == {producent}


@pytest.mark.parametrize(
    ("zrodlo", "nazwa", "oczekiwane"),
    [
        ('NumerKRS = NewType("NumerKRS", str)', "NumerKRS", 0),
        ("numer = numer_krs(surowy)", "NumerKRS", 0),
        ("numer = NumerKRS(cyfry)", "NumerKRS", 1),
        ("do = DzienBilansowy(data)", "DzienBilansowy", 1),
        ("termin = TerminUstawowy(dzien + przesuniecie)", "TerminUstawowy", 1),
    ],
)
def test_skan_producentow_naprawde_lapie(zrodlo: str, nazwa: str, oczekiwane: int) -> None:
    """Test samego skanu reguł 8 i 9, z zaszczepionym drugim producentem."""
    assert len(wywolania_konstruktora(zrodlo, nazwa)) == oczekiwane


# --------------------------------------------------------------------------------------
# Reguła 6, druga połowa — kanał markdown i jego własny neutralizator (krok 5)
#
# Skan sprawdza PARY kanał-neutralizator, nie samą obecność któregoś. Przepuszczenie napisu
# przez `richtext.safe` po drodze do markdownu zdejmie znaczniki `rich` i zostawi
# `](http://…)` — czyli odnośnik na obcy adres w dokumencie, który ktoś prześle dalej jako
# raport o spółce.
# --------------------------------------------------------------------------------------

NEUTRALIZATORY_MD = frozenset({"safe_md", "safe_md_or_empty"})
# Własne budowniczy modułu markdown. Każdy jest skanowany osobno, więc jego wynik wolno
# traktować jak tekst już zneutralizowany — ale lista ma własny test, bo inaczej dopisanie
# funkcji rozbroiłoby skan bez jednego słowa.
BUDOWNICZY_MD = frozenset({"_sekcja", "_wiersz"})
MODUL_MARKDOWN = "raport/markdown.py"


def _bezpieczne_md(node: ast.expr, nazwy: set[str], dozwolone: frozenset[str]) -> bool:
    """Czy to wyrażenie na pewno niesie tekst przepuszczony przez neutralizator kanału."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id in nazwy
    if isinstance(node, ast.Starred):
        return _bezpieczne_md(node.value, nazwy, dozwolone)
    if isinstance(node, ast.GeneratorExp | ast.ListComp):
        return _bezpieczne_md(node.elt, nazwy, dozwolone)
    if isinstance(node, ast.List | ast.Tuple):
        return all(_bezpieczne_md(e, nazwy, dozwolone) for e in node.elts)
    if isinstance(node, ast.BinOp):
        return _bezpieczne_md(node.left, nazwy, dozwolone) and _bezpieczne_md(
            node.right, nazwy, dozwolone
        )
    if isinstance(node, ast.JoinedStr):
        return all(
            _bezpieczne_md(c.value, nazwy, dozwolone)
            for c in node.values
            if isinstance(c, ast.FormattedValue)
        )
    if isinstance(node, ast.Call):
        if _nazwa_wywolania(node) == "join":
            return all(_bezpieczne_md(a, nazwy, dozwolone) for a in node.args)
        return _nazwa_wywolania(node) in dozwolone
    return False


def _nazwy_bezpieczne_md(tree: ast.AST, dozwolone: frozenset[str]) -> set[str]:
    """Zmienne, do których trafia wyłącznie tekst już zneutralizowany.

    Liczone do punktu stałego, bo lista linii bywa najpierw przypisana, a potem uzupełniana
    przez `append` i `extend` — a pominięcie którejkolwiek z tych dróg dałoby skan, który
    raportuje się jako domknięty, będąc ślepym na najczęstszy kształt w tym module.
    """
    wiazania: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for cel in node.targets:
                if isinstance(cel, ast.Name):
                    wiazania.setdefault(cel.id, []).append(node.value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"append", "extend"} and isinstance(node.func.value, ast.Name):
                wiazania.setdefault(node.func.value.id, []).extend(node.args)
    nazwy = set(wiazania)
    zmiana = True
    while zmiana:
        zmiana = False
        for nazwa in sorted(nazwy):
            if not all(_bezpieczne_md(w, nazwy, dozwolone) for w in wiazania[nazwa]):
                nazwy.discard(nazwa)
                zmiana = True
    return nazwy


def wstawki_bez_neutralizatora(
    source: str, neutralizatory: frozenset[str], producenci: frozenset[str] = frozenset()
) -> list[int]:
    """Numery linii, w których napis staje się markdownem z pominięciem neutralizatora kanału.

    Skan widzi **oba** kształty, którymi tekst wchodzi do dokumentu: wstawkę w f-stringu
    i złączenie komórek. Pierwsza wersja czytała tylko f-stringi, więc `" | ".join(komorki)`
    przechodziło — a to jest dokładnie ta linia, którą pisze się w tym module najczęściej.
    `producenci` to własne budowniczy modułu: każdy z nich jest skanowany osobno, więc ich
    wynik wolno traktować jako już zneutralizowany.
    """
    tree = ast.parse(source)
    dozwolone = neutralizatory | producenci
    nazwy = _nazwy_bezpieczne_md(tree, dozwolone)
    naruszenia: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            naruszenia.extend(
                node.lineno
                for c in node.values
                if isinstance(c, ast.FormattedValue)
                and not _bezpieczne_md(c.value, nazwy, dozwolone)
            )
        elif isinstance(node, ast.Call) and _nazwa_wywolania(node) == "join":
            if any(not _bezpieczne_md(a, nazwy, dozwolone) for a in node.args):
                naruszenia.append(node.lineno)
    return naruszenia


def test_regula_6_wstawki_w_markdownie_ida_przez_wlasny_neutralizator() -> None:
    zrodlo = (PAKIET / MODUL_MARKDOWN).read_text(encoding="utf-8")

    assert wstawki_bez_neutralizatora(zrodlo, NEUTRALIZATORY_MD, BUDOWNICZY_MD) == []


def test_lista_budowniczych_markdownu_jest_zamknieta() -> None:
    """Budowniczy są traktowani jak zneutralizowani, więc ich lista nie może rosnąć w ciszy."""
    drzewo = ast.parse((PAKIET / MODUL_MARKDOWN).read_text(encoding="utf-8"))
    prywatne = {
        node.name
        for node in ast.walk(drzewo)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_")
    }

    assert prywatne == set(BUDOWNICZY_MD)


def importowane_w_pakiecie(source: str) -> set[str]:
    """Nazwy modułów importowanych **względnie**, po ostatnim członie.

    `importowane_moduly` celowo widzi tylko importy bezwzględne, bo reguła 1 pyta o biblioteki.
    Pary kanał-neutralizator są importami wewnątrz pakietu, czyli względnymi, i potrzebują
    własnego skanu — czytanie ich przez `in source` łapałoby prozę docstringów, a docstring
    kanału markdown ma prawo nazwać neutralizator, którego mu nie wolno użyć.
    """
    tree = ast.parse(source)
    return {
        node.module.split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0 and node.module
    }


def test_moduly_znajace_markdown_sa_tam_gdzie_myslimy() -> None:
    """Druga połowa pary: neutralizator markdownu ma dokładnie jednego użytkownika."""
    znajace = {
        sciezka.relative_to(PAKIET).as_posix()
        for sciezka in _pliki_pakietu()
        if "marktext" in importowane_w_pakiecie(sciezka.read_text(encoding="utf-8"))
    }

    assert znajace == {MODUL_MARKDOWN}


def test_kanaly_nie_mieszaja_neutralizatorow() -> None:
    """Napis przepuszczony przez neutralizator cudzego kanału to naruszenie, nie drobiazg."""
    markdown = importowane_w_pakiecie((PAKIET / MODUL_MARKDOWN).read_text(encoding="utf-8"))
    terminal = importowane_w_pakiecie((PAKIET / "render.py").read_text(encoding="utf-8"))

    assert "marktext" in markdown and "richtext" not in markdown
    assert "richtext" in terminal and "marktext" not in terminal


@pytest.mark.parametrize(
    ("zrodlo", "oczekiwane"),
    [
        ('tytul = f"# {safe_md(raport.tytul)}"', 0),
        ('naglowek = f"## {safe_md(blok.title)}"', 0),
        # Nawet stała programu idzie przez neutralizator, gdy wchodzi wstawką: skan nie
        # rozpoznaje „to nasza zmienna", a wyjątek dla nazw wpuściłby każdą nazwę.
        ('stala = f"| {ROZDZIELACZ} |"', 1),
        # kształty, które robią z fragmentu raportu odnośnik albo rozbijają tabelę
        ('tytul = f"# {raport.tytul}"', 1),
        ('komorka = f"| {safe(nazwa)} |"', 1),
        ('wiersz = " | ".join(safe_md(k) for k in komorki)', 0),
        # kształt, który pisze się w tym module najczęściej i który pierwsza wersja skanu
        # przepuszczała, bo czytała wyłącznie f-stringi
        ('wiersz = " | ".join(komorki)', 1),
    ],
)
def test_skan_wstawek_naprawde_lapie(zrodlo: str, oczekiwane: int) -> None:
    """Test samego skanu drugiej połowy reguły 6."""
    assert len(wstawki_bez_neutralizatora(zrodlo, NEUTRALIZATORY_MD)) == oczekiwane


# --------------------------------------------------------------------------------------
# Reguła 13 — warstwa sygnałów nie widzi wnętrza działu (krok 5)
#
# `Dzial.klucze` niesie dosłowne nazwy pól z pliku, żeby raport miał co zacytować. Reguła
# przypięta do takiej nazwy byłaby nie do odróżnienia od reguły przypiętej do nazwy
# ZMIERZONEJ — a niezmierzona jest (`docs/niezmierzone.md`, wiersz 10).
# --------------------------------------------------------------------------------------

POLE_ZABRONIONE_W_SYGNALACH = "klucze"


def odczyty_pola(source: str, nazwa: str) -> list[int]:
    """Numery linii, w których czyta się pole o danej nazwie."""
    tree = ast.parse(source)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == nazwa
    ]


@pytest.mark.parametrize("sciezka", _pliki_sygnalow(), ids=lambda p: p.name)
def test_regula_13_warstwa_sygnalow_nie_zaglada_do_wnetrza_dzialu(sciezka: Path) -> None:
    linie = odczyty_pola(sciezka.read_text(encoding="utf-8"), POLE_ZABRONIONE_W_SYGNALACH)

    assert linie == [], f"{sciezka.name}: sięga po nazwy pól działu w liniach {linie}"


@pytest.mark.parametrize(
    ("zrodlo", "oczekiwane"),
    [
        ("if dzial.pusty: pass", 0),
        ("if 'zaleglosciPodatkowe' in dzial.klucze: pass", 1),
    ],
)
def test_skan_odczytow_pola_naprawde_lapie(zrodlo: str, oczekiwane: int) -> None:
    assert len(odczyty_pola(zrodlo, POLE_ZABRONIONE_W_SYGNALACH)) == oczekiwane


# --------------------------------------------------------------------------------------
# Reguła 12 — dziennik tylko rośnie (krok 6)
#
# Skasowanie linii dziennika i skasowanie ładunku wyglądają w kodzie niemal tak samo,
# a znaczą co innego: pierwsze zaciera ślad po ocenie, drugie jest higieną retencji.
# Dlatego kasuje **jeden** moduł i skan pilnuje, że to wciąż ten sam.
# --------------------------------------------------------------------------------------

PAKIET_DZIENNIKA = PAKIET / "dziennik"
MODUL_KASUJACY = "ladunki.py"
TRYBY_DOPUSZCZALNE = frozenset({"a", "r", "x"})
WYWOLANIA_NISZCZACE = frozenset(
    {
        "unlink",
        "rmtree",
        "rename",
        "replace",
        "truncate",
        "write_text",
        "write_bytes",
        "remove",
        "move",
    }
)


def _pliki_dziennika() -> list[Path]:
    return sorted(PAKIET_DZIENNIKA.rglob("*.py"))


def wywolania_niszczace(source: str) -> list[str]:
    """Nazwy wywołań, przez które plik przestaje mieć to, co miał."""
    tree = ast.parse(source)
    return [
        _nazwa_wywolania(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _nazwa_wywolania(node) in WYWOLANIA_NISZCZACE
    ]


def tryby_otwarcia(source: str) -> list[str]:
    """Tryby, w jakich ten moduł otwiera pliki — po drugim argumencie pozycyjnym."""
    tree = ast.parse(source)
    tryby: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _nazwa_wywolania(node) != "open":
            continue
        podane = [a for a in node.args if isinstance(a, ast.Constant)]
        # Tryb bywa podany po nazwie i to jest ten sam tryb. Skan czytający wyłącznie
        # argumenty pozycyjne przepuszczałby `open(mode="w")`, raportując się jako domknięty.
        podane += [
            k.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)
        ]
        tryby.extend(str(a.value) for a in podane if isinstance(a.value, str))
    return tryby


def test_regula_12_dziennik_nie_kasuje_ani_nie_nadpisuje() -> None:
    """Plik dziennika otwiera się do dopisania albo do czytania. Trzeciej możliwości nie ma."""
    zrodlo = (PAKIET_DZIENNIKA / "zapis.py").read_text(encoding="utf-8")

    assert wywolania_niszczace(zrodlo) == []
    assert set(tryby_otwarcia(zrodlo)) <= TRYBY_DOPUSZCZALNE


def test_kasuje_dokladnie_jeden_modul_dziennika() -> None:
    """Wyjątek od reguły 12 ma być widoczny i pojedynczy — inaczej przestaje być wyjątkiem."""
    kasujace = {
        sciezka.name
        for sciezka in _pliki_dziennika()
        if wywolania_niszczace(sciezka.read_text(encoding="utf-8"))
    }

    assert kasujace == {MODUL_KASUJACY}


@pytest.mark.parametrize(
    ("zrodlo", "oczekiwane"),
    [
        ('with sciezka.open("a", encoding="utf-8") as plik: plik.write(linia)', 0),
        ('tresc = sciezka.open("r", encoding="utf-8").read()', 0),
        ('with sciezka.open("w", encoding="utf-8") as plik: plik.write(linia)', 1),
        ('sciezka.open("w+")', 1),
        ('sciezka.open(mode="w")', 1),
    ],
)
def test_skan_trybow_naprawde_lapie(zrodlo: str, oczekiwane: int) -> None:
    """Test samego skanu reguły 12: `w` kasuje dziennik w chwili otwarcia, bez ostrzeżenia."""
    assert len([t for t in tryby_otwarcia(zrodlo) if t not in TRYBY_DOPUSZCZALNE]) == oczekiwane


@pytest.mark.parametrize(
    ("zrodlo", "oczekiwane"),
    [
        ("pliki = katalog.glob('*.json')", 0),
        ("sciezka.unlink()", 1),
        ("sciezka.write_text(tresc)", 1),
        ("shutil.rmtree(katalog)", 1),
        ("os.remove(sciezka)", 1),
        ("sciezka.write_bytes(dane)", 1),
    ],
)
def test_skan_wywolan_niszczacych_naprawde_lapie(zrodlo: str, oczekiwane: int) -> None:
    assert len(wywolania_niszczace(zrodlo)) == oczekiwane
