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


@pytest.mark.parametrize("sciezka", [*_pliki_sygnalow(), PAKIET / "texts.py"], ids=lambda p: p.name)
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


def test_regula_11_teksty_nie_zawieraja_slowa_oskarzenia() -> None:
    napisy = _napisy_nie_bedace_docstringiem((PAKIET / "texts.py").read_text(encoding="utf-8"))

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
