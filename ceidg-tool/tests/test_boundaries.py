"""Wszystkie reguły granic z `docs/design/phase2_core.md` sprawdzane skanem AST.

Te reguły są tym, co utrzymuje `texts` i `batching` testowalnymi bez terminala: gdy do
modułu czystego wejdzie `rich` albo `sqlite3`, testy ekranów zaczną wymagać atrap i cała
korzyść z podziału zniknie. Naruszenie jest wtedy trudne do zauważenia w przeglądzie kodu,
a trywialne do wychwycenia skanem — dlatego reguły stoją tu, a nie w dokumentacji.

Podział pliku: najpierw reguły 6-8 (warstwa użytkownika), potem 9-10 (wyjście na ekran,
z własnym testem samego skanu), na końcu 1-5 (rdzeń). Reguły 1-5 dołączyły ostatnie i były
jedynymi, które do fazy 3b opierały się wyłącznie na przeglądzie kodu.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "ceidg_tool"

# Reguła 6: moduły czyste nie znają żadnej biblioteki wejścia/wyjścia.
PURE_MODULES = (
    "ui/texts.py",
    "batching.py",
    "estimating.py",
    "criteria.py",
    "safetext.py",
    # Tablica przejścia PKD (ADR-0012): decyduje, które kody dołożyć do zapytania, więc musi
    # dać się sprawdzić bez sieci i bez bazy — tak samo jak `criteria.py`, którego rozszerza.
    "pkdmap.py",
    # Warstwa czysta asystenta (ADR-0011): schemat, słownik, prompt i tłumaczenie na `Criteria`
    # muszą dać się sprawdzić bez sieci, bez klucza i bez zainstalowanego SDK. Cała faza 4
    # opiera się na tym, że jedyny moduł znający `anthropic` to `assistant/caller.py`.
    "assistant/schema.py",
    "assistant/pkd.py",
    "assistant/prompt.py",
    "assistant/translate.py",
)
FORBIDDEN_IN_PURE = frozenset(
    {
        "rich",
        "questionary",
        "typer",
        "httpx",
        # Drugi stos HTTP i SDK modelu: moduł czysty, który je zaimportuje, przestaje dać się
        # uruchomić bez opcjonalnej zależności — a „kreator i CLI działają bez asystenta"
        # ma być prawdą także przy instalacji bez extry `asystent`.
        "httpx2",
        "anthropic",
        "sqlite3",
        "openpyxl",
    }
)

# Reguła 10: `rich` wolno znać trzem modułom i tylko im.
RICH_MODULES = frozenset({"richtext.py", "ui/render.py", "console.py"})

# Wywołania, których argumenty lądują na ekranie i mogą zostać potraktowane jak znaczniki.
# `add_row` i `Column` są tu nie mniej ważne niż `print`: to one niosą komórki z rejestru.
TEXT_BEARING_CALLS = frozenset(
    {
        "print",
        "print_json",
        "log",
        "rule",
        "out",
        "add_task",
        "add_row",
        "add_column",
        "Table",
        "Column",
        "Columns",
        "TextColumn",
        "Panel",
        "Group",
    }
)

# Wywołania, w których sprawdzamy wyłącznie słowa kluczowe: `progress.update(task, advance=n)`
# ma pierwszy argument techniczny (`TaskID`), a treść wnosi dopiero `description=`.
KEYWORD_ONLY_CALLS = frozenset({"update"})

# Słowa kluczowe tych wywołań, które niosą treść — w odróżnieniu od `style=` czy `total=`.
TEXT_BEARING_KEYWORDS = frozenset({"title", "header", "description", "label", "renderable", "data"})

# Jedyne funkcje, które robią z obcego napisu coś, co wolno wydrukować. `strip_control` tu
# **nie** należy: usuwa znaki sterujące, ale nie maskuje tokenu, a token niesie PESEL.
NEUTRALISERS = frozenset({"safe", "safe_or_none"})

# Obiekty `rich`, które rysują się same; napisy trafiły do nich wcześniej przez `safe`.
# Bez `Text`: `safe` zwraca `Text`, ale `Text(surowy)` blokuje tylko znaczniki i przepuszcza ESC.
RENDERABLE_FACTORIES = frozenset({"Table", "Column", "Columns", "Panel", "Group"})

SAFE_PRODUCERS = NEUTRALISERS | RENDERABLE_FACTORIES

# `cli.py` nie drukuje **niczym**. `typer.echo` jest w programie na `typer` odruchem
# pierwszym, a `console.log` odruchem przy szukaniu błędu — skan pilnujący samego
# `console.print` dałby fałszywe poczucie domknięcia reguły 9.
OUTPUT_CALLS = TEXT_BEARING_CALLS | {"echo", "secho"}

# Korzenie, po których poznajemy zapis wprost do strumienia — także po `from sys import stdout`.
STREAM_ROOTS = frozenset({"sys", "stdout", "stderr"})

# Pytania też drukują, ale ich zakazać nie można: `typer.prompt(hide_input=True)` musi zostać
# w `cli.py`, bo protokół `Prompter` nie umie ukryć wpisywanego tokenu (ADR-0008, decyzja 2).
# Reguła 9 zabrania więc nie samego pytania, lecz **ułożenia jego treści** na miejscu.
PROMPTING_CALLS = frozenset({"prompt", "confirm"})


def imported_roots(path: Path) -> set[str]:
    """Nazwy pakietów najwyższego poziomu importowanych w module (bez importów względnych)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def package_targets(path: Path) -> set[str]:
    """Moduły pakietu importowane przez ten plik — do reguł 2, 5 i 8.

    Liczą się obie formy zapisu. Pakiet pisze dziś wyłącznie względnie (`from ..store import …`),
    ale `from ceidg_tool.store import Store` znaczy dokładnie to samo, a podpowiadacz w edytorze
    wstawia właśnie tę wersję. Skan patrzący tylko na `level > 0` przestawałby wtedy cokolwiek
    znaczyć — bez jednego czerwonego testu, czyli w sposób nie do zauważenia w przeglądzie.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.level > 0:
                targets.add(node.module.split(".")[0])
            elif node.module and node.module.startswith(f"{PACKAGE.name}."):
                targets.add(node.module.split(".")[1])
            elif (node.module is None and node.level > 0) or node.module == PACKAGE.name:
                # `from .. import store` (bez `node.module`) i `from ceidg_tool import store`
                # (`node.module` bez kropki) niosą nazwę modułu dopiero w `names`. Obie formy
                # zwracały wcześniej zbiór pusty, więc reguły 2, 5, 8 i 13 przestawały
                # obowiązywać bez jednego czerwonego testu — dokładnie ten kształt, który ten
                # plik już raz naprawiał dla importów bezwzględnych.
                targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            targets.update(
                alias.name.split(".")[1]
                for alias in node.names
                if alias.name.startswith(f"{PACKAGE.name}.")
            )
    return targets


@pytest.mark.parametrize("module", PURE_MODULES, ids=[m.replace("/", ".") for m in PURE_MODULES])
def test_pure_modules_import_no_io_library(module: str) -> None:
    """Reguła 6: `texts`, `batching`, `estimating` i `criteria` są czyste."""
    offenders = imported_roots(PACKAGE / module) & FORBIDDEN_IN_PURE

    assert not offenders, f"{module} importuje {sorted(offenders)} — moduł ma pozostać czysty"


def test_only_prompts_knows_questionary() -> None:
    """Reguła 7: wymiana pytającego ma sens tylko wtedy, gdy `questionary` siedzi
    w jednym module."""
    users = {
        path.relative_to(PACKAGE).as_posix()
        for path in PACKAGE.rglob("*.py")
        if "questionary" in imported_roots(path)
    }

    assert users == {"ui/prompts.py"}


def test_only_render_and_console_know_rich() -> None:
    """Reguła 7: `rich` mieszka tam, gdzie rysowanie — reszta operuje na modelach widoku."""
    users = {
        path.relative_to(PACKAGE).as_posix()
        for path in PACKAGE.rglob("*.py")
        if "rich" in imported_roots(path)
    }

    assert users == RICH_MODULES


def test_the_ui_layer_never_reaches_for_the_client_or_the_store() -> None:
    """Reguła 8: `ui/*` chodzi przez `pipeline`, nigdy wprost do sieci ani do bazy."""
    offenders: dict[str, set[str]] = {}
    for path in (PACKAGE / "ui").rglob("*.py"):
        forbidden = package_targets(path) & {"client", "store"}
        if forbidden:
            offenders[path.name] = forbidden

    assert offenders == {}


def test_the_pure_modules_do_not_import_the_ui_layer() -> None:
    """Kierunek zależności: `texts` może zależeć od `batching`, nigdy odwrotnie."""
    assert "ui" not in package_targets(PACKAGE / "batching.py")
    assert "ui" not in package_targets(PACKAGE / "estimating.py")


def test_every_pure_module_actually_exists() -> None:
    """Zabezpieczenie przed cichym rozbrojeniem testu po zmianie nazwy pliku."""
    for module in PURE_MODULES:
        assert (PACKAGE / module).is_file(), module


# ----------------------------------------------------------------- reguła 10: wyjście na ekran


def unwrap(argument: ast.expr) -> ast.expr:
    """Zdejmuje `*` i komprehensje, żeby dojść do wyrażenia, które naprawdę niesie tekst.

    `table.add_row(*(safe(cell) for cell in row))` jest bezpieczne, a bez tego kroku skan
    widziałby tylko `Starred` i nie miałby czego sprawdzić.
    """
    while True:
        if isinstance(argument, ast.Starred):
            argument = argument.value
        elif isinstance(argument, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
            argument = argument.elt
        else:
            return argument


def called_name(node: ast.expr) -> str:
    """Nazwa wywoływanej funkcji: `safe(...)` i `x.safe(...)` dają to samo."""
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def receiver_root(node: ast.Attribute) -> str:
    """Korzeń łańcucha `sys.stdout.write` — po to, żeby nie mylić go z `path.write_text`.

    Sam `stdout` też jest korzeniem: po `from sys import stdout` łańcuch nie zaczyna się od `sys`.
    """
    value: ast.expr = node.value
    while isinstance(value, ast.Attribute):
        value = value.value
    return value.id if isinstance(value, ast.Name) else ""


def safe_names(tree: ast.Module) -> set[str]:
    """Nazwy związane z bezpiecznym wyrażeniem: `title = safe_or_none(...)`, `table = Table(...)`.

    Zakres jest modułowy, nie funkcyjny — celowo. Skan ma łapać przeoczenia, a nazwa
    użyta w jednej funkcji i związana w innej to już kod, którego nikt nie napisze przypadkiem.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and called_name(unwrap(node.value)) in SAFE_PRODUCERS:
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def text_bearing_arguments(tree: ast.Module) -> list[tuple[ast.expr, int]]:
    """Argumenty, które trafią na ekran: pozycyjne i te słowa kluczowe, które niosą treść.

    `style=` i `total=` nie niosą, `title=` i `description=` niosą — i to właśnie przez
    `Table(title=…)` oraz `add_task(description=…)` przechodzi tekst z rejestru.
    """
    found: list[tuple[ast.expr, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = called_name(node)
        if name in TEXT_BEARING_CALLS:
            found.extend((argument, argument.lineno) for argument in node.args)
        elif name not in KEYWORD_ONLY_CALLS:
            continue
        found.extend(
            (kw.value, kw.value.lineno) for kw in node.keywords if kw.arg in TEXT_BEARING_KEYWORDS
        )
    return found


def is_safe_argument(argument: ast.expr, names: set[str]) -> bool:
    """Bezpieczne jest **całe** wyrażenie, nie wyrażenie z neutralizatorem gdzieś w środku.

    `f"{safe(a)} {raw}"` zawiera `safe`, a mimo to przepuszcza `raw` — dlatego liczy się
    korzeń wyrażenia, a nie to, co da się w nim znaleźć obchodem drzewa.
    """
    node = unwrap(argument)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True  # napis programu, nie z zewnątrz — wolno mu być znacznikiem
    if isinstance(node, ast.Name) and node.id in names:
        return True
    return called_name(node) in SAFE_PRODUCERS


def output_calls(tree: ast.Module) -> list[int]:
    """Linie, w których moduł drukuje czymkolwiek: `rich`, `print`, `typer.echo`, `sys.stdout`."""
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = called_name(node)
        if name == "write":
            if isinstance(node.func, ast.Attribute) and receiver_root(node.func) in STREAM_ROOTS:
                lines.append(node.lineno)
        elif name in OUTPUT_CALLS:
            lines.append(node.lineno)
    return lines


def test_only_neutralised_text_reaches_rich() -> None:
    """Reguła 10: do `rich` trafia albo napis programu, albo wynik `safe`.

    Jedna zależność, której ten skan nie widzi: `add_task(safe(x))` jest bezpieczne tylko
    dlatego, że `console.py` buduje `TextColumn` z `markup=False`. Pilnuje tego test
    zachowania w `tests/test_console.py` — nie „upraszczaj" tamtej kolumny bez przeczytania go.

    Skan jest składniowy i taki ma być. Odrzuci też `console.print(f"…{liczba}")`, choć
    liczba nie jest groźna — ale takie zdanie łamie regułę 9 i należy do `ui/texts.py`,
    więc lekarstwem jest przeniesienie zdania, nie rozluźnienie skanu. Aliasowanie
    (`p = console.print`) skan omija: jego zadaniem jest łapanie przeoczeń, nie napastnika.
    """
    offenders: list[str] = []
    for module in sorted(RICH_MODULES):
        tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"), filename=module)
        names = safe_names(tree)
        offenders.extend(
            f"{module}:{line}"
            for argument, line in text_bearing_arguments(tree)
            if not is_safe_argument(argument, names)
        )

    assert offenders == [], "napis z zewnątrz idzie do rich z pominięciem richtext.safe"


def test_the_cli_prints_nothing_on_its_own() -> None:
    """Reguła 9: `cli.py` nie ma własnego kanału wyjścia — zdania idą przez `ui`.

    To jest warunek, który czyni regułę 10 sprawdzalną skanem: dopóki `cli.py` drukował
    sam, „każdy napis z zewnątrz przechodzi przez `safe`" wymagałoby analizy przepływu
    danych przez cały pakiet. Sprawdzamy wszystkie kanały, nie tylko `rich`: w programie
    na `typer` pierwszym odruchem jest `typer.echo`, a nie `console.print`.
    """
    tree = ast.parse((PACKAGE / "cli.py").read_text(encoding="utf-8"), filename="cli.py")

    assert [f"cli.py:{line}" for line in output_calls(tree)] == []


def test_every_rich_module_actually_exists() -> None:
    """Zabezpieczenie przed cichym rozbrojeniem reguły 10 po zmianie nazwy pliku."""
    for module in RICH_MODULES:
        assert (PACKAGE / module).is_file(), module


@pytest.mark.parametrize(
    ("source", "expected_offenders"),
    [
        # kształty, które program naprawdę stosuje
        ("console.print(safe(name))", 0),
        ('console.print("[red]Blad:[/red]", safe(text))', 0),
        ("table = Table()\nconsole.print(table)", 0),
        ("table.add_row(*(safe(cell) for cell in row))", 0),
        ("headers = [Column(header=safe(h)) for h in hs]\ntable = Table(*headers)", 0),
        ("title = safe_or_none(t)\ntable = Table(title=title)", 0),
        ('progress.add_task("Lista firm", total=n)', 0),
        # nazwa firmy wprost, w f-stringu, z rekordu, sklejona — to wywracało program w fazie 3
        ("console.print(name)", 1),
        ('console.print(f"Firma: {name}")', 1),
        ('console.print(record["nazwa"])', 1),
        ('console.print("a" + name)', 1),
        ("console.print(str(exc))", 1),
        # kształty, które przepuszczała pierwsza wersja skanu
        ("table.add_row(name)", 1),
        ("table.add_column(report.nazwa)", 1),
        ("Table(title=name)", 1),
        ("progress.add_task(description=report.nazwa)", 1),
        ("t = Text(name)\nconsole.print(t)", 1),
        ('console.print(f"{safe(a)} {raw}")', 1),
        ("console.print(safe(a) + raw)", 1),
        ("console.print(strip_control(x))", 1),
        # znalezione przy weryfikacji poprawek: fabryka, która uświęca nazwę, sama nie była badana
        ("console.print(Columns(nazwy))", 1),
        ("c = Columns([r.nazwa for r in reports])\nconsole.print(c)", 1),
        ("progress.update(task, description=report.nazwa)", 1),
        ("progress.update(task, advance=n)", 0),
        ("console.rule(report.nazwa)", 1),
        ("console.print_json(data=name)", 1),
    ],
)
def test_the_rule_10_scan_would_notice_a_violation(source: str, expected_offenders: int) -> None:
    """Skan bez tej próby byłby nie do odróżnienia od testu, który zawsze przechodzi.

    Druga połowa listy to kształty, które przepuszczała pierwsza wersja skanu — każdy
    znaleziony w przeglądzie kodu, nie wymyślony. Dopisanie kształtu tutaj jest tańsze
    niż odkrycie go na ekranie operatora.
    """
    tree = ast.parse(source)
    names = safe_names(tree)

    offenders = [
        argument
        for argument, _ in text_bearing_arguments(tree)
        if not is_safe_argument(argument, names)
    ]

    assert len(offenders) == expected_offenders


@pytest.mark.parametrize(
    ("source", "expected_offenders"),
    [
        ("view.message(texts.NO_RUNS)", 0),
        ('log.error("%s", exc)', 0),
        ("path.write_text(data, encoding='utf-8')", 0),
        ("typer.confirm(texts.CONFIRM_PROD, default=False)", 0),
        ('print("cokolwiek")', 1),
        ("typer.echo(record)", 1),
        ('typer.secho(f"Firma: {name}")', 1),
        ("sys.stdout.write(name)", 1),
        ("stdout.write(name)", 1),  # po `from sys import stdout` łańcuch nie zaczyna się od `sys`
        ("console.log(record)", 1),  # odruch przy szukaniu błędu, też wyjście na ekran
        ("console.print(safe(name))", 1),
    ],
)
def test_the_cli_output_scan_covers_every_channel(source: str, expected_offenders: int) -> None:
    """`cli.py` ma nie drukować niczym — także `print`, `typer.echo` i `sys.stdout`.

    Ostatni przypadek jest z rozmysłem naruszeniem: w `cli.py` nawet zneutralizowany
    `console.print` jest zdaniem napisanym poza `ui/texts.py`, czyli złamaniem reguły 9.
    """
    tree = ast.parse(source)

    assert len(output_calls(tree)) == expected_offenders


def test_a_factory_that_makes_names_safe_is_itself_scanned() -> None:
    """Niezmiennik, który zamyka całą klasę dziur zamiast kolejnej pojedynczej.

    Wpisanie obiektu do `RENDERABLE_FACTORIES` mówi „temu wolno wydrukować to, co w nim
    siedzi". Jeśli jego własne argumenty nie są sprawdzane, to zdanie jest obietnicą bez
    pokrycia: `console.print(Columns(nazwy))` przechodziłoby bez śladu. Dwa razy z rzędu
    właśnie tak wyglądało znalezisko z przeglądu — najpierw `Table` i `Column`, potem
    `Columns` i `Group` — więc reguła stoi tu jako test, a nie jako pamięć o niej.
    """
    assert RENDERABLE_FACTORIES <= TEXT_BEARING_CALLS


def test_every_screen_channel_is_also_forbidden_in_the_cli() -> None:
    """Kanał, który liczy się w regule 10, ma się liczyć i w regule 9.

    `cli.py` wciąż trzyma żywą `Console` (podaje ją `ConsoleEvents`), więc każda metoda
    drukująca `rich` jest tam działającym wyjściem. Rozjazd między zbiorami oznaczałby,
    że jedna reguła łapie `console.log`, a druga nie.
    """
    assert TEXT_BEARING_CALLS <= OUTPUT_CALLS


# ------------------------------------------------------------------- reguły 1-5: rdzeń pakietu

# Reguły 1-4 z `docs/design/phase2_core.md`: czego dany moduł rdzenia nie ma prawa importować.
# Do fazy 3b opierały się wyłącznie na przeglądzie, a są to dokładnie te zależności, które
# zamieniają moduł czysty w taki, do którego testu trzeba atrapy sieci albo pliku bazy.
CORE_FORBIDDEN: dict[str, frozenset[str]] = {
    # Reguła 1: `criteria` i `normalizer` są czyste — bez wejścia/wyjścia i bez systemu.
    "criteria.py": frozenset({"httpx", "sqlite3", "openpyxl", "rich", "os"}),
    "normalizer.py": frozenset({"httpx", "sqlite3", "openpyxl", "rich", "os"}),
    # Reguły 2 i 4: klient dostaje historię żądań jako protokół, a postęp jako zdarzenia.
    "client.py": frozenset({"sqlite3", "rich"}),
    # Reguły 3 i 4: baza nie chodzi do sieci i nie rysuje.
    "store.py": frozenset({"httpx", "rich"}),
}


@pytest.mark.parametrize("module", sorted(CORE_FORBIDDEN), ids=sorted(CORE_FORBIDDEN))
def test_the_core_modules_import_no_foreign_library(module: str) -> None:
    """Reguły 1-4: rdzeń trzyma się swojej warstwy."""
    offenders = imported_roots(PACKAGE / module) & CORE_FORBIDDEN[module]

    assert not offenders, f"{module} importuje {sorted(offenders)} — to nie jego warstwa"


def test_the_client_does_not_reach_into_the_database() -> None:
    """Reguła 2 od drugiej strony: `client` nie zna `store`, tylko protokół `RequestHistory`.

    Import bezpieczny (`sqlite3`) to jedno, import modułu `store` to drugie — gdyby klient
    sięgnął po bazę, limiter przestałby dać się testować z podstawionym zegarem i historią.
    """
    assert "store" not in package_targets(PACKAGE / "client.py")


def test_only_the_pipeline_knows_both_the_network_and_the_database() -> None:
    """Reguła 5 — ta, na której stoi cała reszta.

    Dopóki jeden moduł łączy sieć z bazą, wznowienie ma jedno miejsce, w którym może być
    poprawne. Drugi taki moduł to druga ścieżka od żądania do zapisu i drugi checkpoint
    do pogodzenia — a niezmiennik „rekordy strony i checkpoint jedną transakcją" żyje
    tylko dopóki wszystko idzie tędy.
    """
    both = {
        path.relative_to(PACKAGE).as_posix()
        for path in PACKAGE.rglob("*.py")
        if {"client", "store"} <= package_targets(path)
    }

    assert both == {"pipeline.py"}


def test_every_core_module_actually_exists() -> None:
    """Zmiana nazwy pliku nie może po cichu wyłączyć reguły — pusta tabela też przechodzi."""
    assert CORE_FORBIDDEN, "tabela reguł 1-4 nie może być pusta"
    assert all(CORE_FORBIDDEN.values()), "reguła z pustym zbiorem zakazów niczego nie sprawdza"
    for module in CORE_FORBIDDEN:
        assert (PACKAGE / module).is_file(), module


def test_the_core_scan_would_notice_a_planted_import(tmp_path: Path) -> None:
    """Odpowiednik testu własnego skanu reguły 10, dla reguł 1-4.

    Sprawdzenie mutacyjne (podrzucony `import httpx` w `store.py`) zostało zrobione ręcznie
    i tak zapisane w `docs/status.md`. Ręczne sprawdzenie nie powtarza się samo, więc stoi
    tu jego wersja, która powtarza się przy każdym uruchomieniu.
    """
    planted = tmp_path / "store.py"
    planted.write_text("import httpx\nimport sqlite3\n", encoding="utf-8")

    assert imported_roots(planted) & CORE_FORBIDDEN["store.py"] == {"httpx"}


@pytest.mark.parametrize(
    "source",
    [
        "from ..store import Store",
        "from ceidg_tool.store import Store",
        "import ceidg_tool.store",
        "import ceidg_tool.store as store",
        "from .. import store",
        "from ceidg_tool import store",
    ],
    ids=[
        "wzgledny",
        "bezwzgledny_from",
        "bezwzgledny_import",
        "bezwzgledny_alias",
        "wzgledny_paczkowy",
        "bezwzgledny_paczkowy",
    ],
)
def test_the_scan_sees_both_ways_of_writing_the_same_import(tmp_path: Path, source: str) -> None:
    """Reguły 2, 5 i 8 mówią o zależności, nie o składni, którą ktoś wybrał.

    Pakiet pisze dziś wyłącznie względnie, więc skan patrzący tylko na `level > 0` wyglądałby
    na działający dokładnie do dnia, w którym edytor podpowie formę bezwzględną — a wtedy
    reguły przestałyby obowiązywać bez jednego czerwonego testu.
    """
    module = tmp_path / "probny.py"
    module.write_text(source, encoding="utf-8")

    assert "store" in package_targets(module)


def composed_arguments(tree: ast.Module) -> list[int]:
    """Argumenty pytań, których treść powstaje na miejscu: f-string, sklejenie, `format`, `%`.

    Przekazanie `texts.CONFIRM_PROD` dalej jest w porządku — to `ui` jest autorem zdania.
    Dopisanie do niego czegokolwiek w `cli.py` czyni autorem `cli.py`, czyli łamie regułę 9.
    """
    built_here = composed_names(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or called_name(node) not in PROMPTING_CALLS:
            continue
        # Także słowa kluczowe: pierwszy parametr `typer.prompt` nazywa się `text`, więc
        # `typer.prompt(text=f"…")` omijało skan patrzący wyłącznie na argumenty pozycyjne.
        candidates = [*node.args, *(kw.value for kw in node.keywords)]
        lines.extend(
            argument.lineno
            for argument in candidates
            if is_composed(argument)
            or (isinstance(argument, ast.Name) and argument.id in built_here)
        )
    return lines


def is_composed(node: ast.expr) -> bool:
    """Czy wyrażenie układa napis: f-string, sklejenie, `%` (oba to `BinOp`) albo `.format`."""
    return isinstance(node, (ast.JoinedStr, ast.BinOp)) or (
        isinstance(node, ast.Call) and called_name(node) == "format"
    )


def composed_names(tree: ast.Module) -> set[str]:
    """Nazwy związane z ułożonym napisem — `line = f"…"` i dopiero potem `prompt(line)`.

    Ten sam obchód, którym reguła 10 rozpoznaje nazwy bezpieczne, tylko w drugą stronę:
    tam szukamy neutralizatora, tu autora zdania.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and is_composed(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def test_the_cli_does_not_compose_the_text_of_its_own_prompts() -> None:
    """Reguła 9 obejmuje też pytania, nie tylko komunikaty.

    Skan przez chwilę tego nie widział, a `_confirm` doklejał w `cli.py` klamrę `[t/N]` do
    zdania z `texts` — czyli dokładnie to, czego reguła zabrania. Klamra mieszka teraz
    w `prompts.confirm_line`, wspólna z kreatorem.
    """
    tree = ast.parse((PACKAGE / "cli.py").read_text(encoding="utf-8"), filename="cli.py")

    assert [f"cli.py:{line}" for line in composed_arguments(tree)] == []


@pytest.mark.parametrize(
    ("source", "expected_offenders"),
    [
        ("typer.prompt(texts.TOKEN_PROMPT, hide_input=True)", 0),
        ("typer.confirm(texts.CONFIRM_PROD, default=False)", 0),
        ("typer.prompt(confirm_line(question, default=default))", 0),
        ('typer.prompt(f"{question} [t/N]")', 1),
        ('typer.confirm(texts.CONFIRM_PROD + " (t/n)")', 1),
        ('typer.prompt("{} [t/N]".format(question))', 1),
        # kształty znalezione przy weryfikacji poprawek
        ('typer.prompt(text=f"{question} [t/N]")', 1),
        ("line = f'{q} [t/N]'" + chr(10) + "typer.prompt(line)", 1),
        ('typer.prompt(texts.TOKEN_PROMPT, hide_input=True, default="")', 0),
    ],
)
def test_the_prompt_scan_tells_passing_a_sentence_from_composing_one(
    source: str, expected_offenders: int
) -> None:
    """Granica przebiega między „przekazać zdanie z `ui`" a „ułożyć je tutaj"."""
    assert len(composed_arguments(ast.parse(source))) == expected_offenders


# ------------------------------------------------- reguła 11: jeden konstruktor klienta HTTP

# Reguła 11 z `docs/design/phase2_core.md`: `httpx.Client` powstaje wyłącznie w `httpclient.py`.
# Dołączyła 2026-09-07 razem z polityką wyjścia — bez niej §B trzymało się na tym, że nikt nie
# napisał drugiego `httpx.Client(...)`, a jeden taki zapis wystarczał, żeby wróciło domyślne
# `trust_env=True` i token pojechał przez proxy ze środowiska.
# Reguła jest o **kliencie HTTP**, nie o bibliotece `httpx`. Do 2026-09-07 skan dopasowywał
# dosłowną nazwę modułu `httpx`, więc drugi stos przechodził przez regułę raportującą się jako
# domknięta: `anthropic` 1.x stoi na `httpx2` (inna dystrybucja niż przypięty `httpx==0.28.1`),
# a `httpx2.Client(...)` i `anthropic.DefaultHttpxClient(...)` nie pasowały do niczego. Ten sam
# kształt co znalezisko ADR-0009: skan `cli.py` widział wyłącznie `rich`, a `typer.echo` szedł
# obok. Zbiory są tu po to, żeby trzecia biblioteka dopisywała się do reguły, a nie obok niej.
HTTP_CLIENT_MODULES = frozenset({"httpx", "httpx2", "anthropic"})
HTTP_CLIENT_FACTORIES = frozenset(
    {"Client", "AsyncClient", "DefaultHttpxClient", "DefaultAsyncHttpxClient"}
)
# `Anthropic(...)` / `AsyncAnthropic(...)` **celowo** nie są tutaj, choć też budują klienta HTTP
# w środku. Należą do reguły 12 z ADR-0011 (decyzja 2), która stawia inne wymaganie — jeden
# właściciel *plus* jawne `api_key=` i `http_client=` w każdym wywołaniu — i której nie da się
# sensownie sprawdzać, zanim istnieje moduł-właściciel. Dopisanie ich tu zamieniłoby własny
# projekt fazy 4 w naruszenie reguły 11. Do czasu wdrożenia reguły 12 ten kształt nie jest
# pilnowany przez nic i ADR wymienia go w sekcji „Gate".
HTTP_CLIENT_OWNER = "ceidg_tool/httpclient.py"
ROOT = PACKAGE.parent
TESTS = ROOT / "tests"
# Sondy niosą ten sam token produkcyjny co narzędzie, więc reguła obowiązuje i je. Dziś żadna
# nie zna httpx (idą przez `urllib` z `probe_support.no_proxy_opener`), ale sonda, która po
# httpx sięgnie, ma odbić się o regułę, a nie o czyjąś uwagę w przeglądzie.
SCANNED_FOR_HTTP_CLIENTS = (PACKAGE, ROOT / "scripts")


def builds_http_client(path: Path) -> bool:
    """Czy w tym pliku **powstaje** klient HTTP — po wywołaniu, nie po adnotacji typu.

    Adnotacja (`http: httpx.Client | None`) jest niegroźna: przyjąć gotowego klienta wolno
    każdemu, kto go dostaje. Groźne jest utworzenie go z pominięciem polityki wyjścia. Liczą się
    wszystkie formy zapisu — `httpx.Client(…)`, `httpx2.Client(…)`, `Client(…)` po
    `from httpx import Client`, `hx.Client(…)` po `import httpx as hx` — z tego samego powodu,
    dla którego skan reguł 2, 5 i 8 czyta oba warianty importu: reguła mówi o zależności,
    a nie o składni, którą ktoś wybrał.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_here = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module in HTTP_CLIENT_MODULES
        for alias in node.names
        if alias.name in HTTP_CLIENT_FACTORIES
    }
    # Nazwy, pod którymi w tym pliku widać sam moduł. `import httpx as hx` jest tu z tego samego
    # powodu, dla którego wyżej stoi `from httpx import Client as C`: skan pilnujący jednej formy
    # zapisu wygląda na działający dokładnie do dnia, w którym ktoś napisze drugą.
    module_names = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name in HTTP_CLIENT_MODULES
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Węzeł jest ALBO atrybutem (`httpx.Client(...)`), ALBO nazwą (`Client(...)`), nigdy
        # obojgiem — więc spłaszczenie `elif` do drugiego `if` niczego nie zmienia w przebiegu.
        if (
            isinstance(func, ast.Attribute)
            and func.attr in HTTP_CLIENT_FACTORIES
            and isinstance(func.value, ast.Name)
            and func.value.id in module_names
        ):
            return True
        if isinstance(func, ast.Name) and func.id in imported_here:
            return True
    return False


def test_only_one_module_builds_the_http_client() -> None:
    """Reguła 11 — warunek konieczny dla §B, tak jak reguła 9 była nim dla reguły 10.

    `httpx` z `trust_env=True` i bez podanego transportu bierze `HTTPS_PROXY` ze środowiska
    (0.28.1, `_client.py`: `allow_env_proxies = trust_env and transport is None`), więc token —
    a token niesie PESEL — wychodzi przez host, którego nikt nie porównał z `ALLOWED_HOSTS`.
    `client._checked_host` tego nie widzi, bo sprawdza adres, a nie gniazdo. „Żadne połączenie
    nie idzie poza listę" da się sprawdzić przeczytaniem jednego modułu dopóty, dopóki klient
    powstaje w jednym miejscu; drugi konstruktor zamienia to zdanie w analizę całego pakietu.

    Testy są skanowane razem z pakietem celowo: to `tests/support.py` budował własnego klienta
    i dlatego jedyna produkcyjna linia tworząca klienta nie miała pokrycia — a że httpx pomija
    proxy ze środowiska, gdy transport jest podany, ten właśnie szew ukrywał defekt.
    """
    scanned = [
        *(p for root in SCANNED_FOR_HTTP_CLIENTS for p in root.rglob("*.py")),
        *TESTS.rglob("*.py"),
        ROOT / "ceidg_probe.py",  # dostarczony oryginał sondy leży w katalogu głównym
    ]
    builders = {
        path.relative_to(ROOT).as_posix()
        for path in scanned
        if path.is_file() and builds_http_client(path)
    }

    assert builders == {HTTP_CLIENT_OWNER}


def test_the_module_that_owns_the_http_client_actually_exists() -> None:
    """Zmiana nazwy pliku nie może po cichu zamienić reguły 11 w zbiór pusty."""
    assert (ROOT / HTTP_CLIENT_OWNER).is_file(), HTTP_CLIENT_OWNER
    assert builds_http_client(ROOT / HTTP_CLIENT_OWNER), "właściciel reguły nie tworzy klienta"


@pytest.mark.parametrize(
    ("source", "builds"),
    [
        ("import httpx\nc = httpx.Client()\n", True),
        ("from httpx import Client\nc = Client()\n", True),
        ("from httpx import Client as C\nc = C()\n", True),
        ("import httpx as hx\nc = hx.Client()\n", True),
        # drugi stos HTTP: `anthropic` 1.x stoi na `httpx2` (znalezisko F2 z ADR-0011)
        ("import httpx2\nc = httpx2.Client()\n", True),
        ("from httpx2 import Client\nc = Client()\n", True),
        ("import anthropic\nc = anthropic.DefaultHttpxClient()\n", True),
        ("import httpx\nc = httpx.AsyncClient()\n", True),
        # adnotacja i przekazanie gotowego klienta to nie utworzenie go
        ("import httpx\ndef f(c: httpx.Client) -> None: ...\n", False),
        ("import httpx\nt = httpx.MockTransport(handler)\n", False),
        ("from ceidg_tool.httpclient import build_http_client\nc = build_http_client()\n", False),
        # cudza klasa o tej samej nazwie nie jest klientem httpx
        ("from zewnetrzne import Client\nc = Client()\n", False),
        ("import zewnetrzne\nc = zewnetrzne.Client()\n", False),
    ],
    ids=[
        "httpx.Client",
        "from_import",
        "alias_from_import",
        "alias_modulu",
        "httpx2_modul",
        "httpx2_from_import",
        "sdk_fabryka",
        "async",
        "adnotacja",
        "transport",
        "fabryka",
        "obca_klasa",
        "obcy_modul",
    ],
)
def test_the_rule_11_scan_tells_building_a_client_from_naming_one(
    tmp_path: Path, source: str, builds: bool
) -> None:
    """Skan, który zawsze przechodzi, jest nie do odróżnienia od działającego.

    Przypadek „obca_klasa" jest tu, bo szeroki skan po samej nazwie `Client` uznałby za
    naruszenie każdą klasę o tej nazwie i zmusiłby do wyjątków — a lista wyjątków to miejsce,
    w którym reguła cicho przestaje obowiązywać.
    """
    module = tmp_path / "probny.py"
    module.write_text(source, encoding="utf-8")

    assert builds_http_client(module) is builds


# ---------------------------------------------- reguła 13: asystent nie widzi danych z rejestru

# Reguła 13 z ADR-0011 (decyzja 2). §B uzupelnienie-01.md mówi o fazie 4: „do modelu językowego
# trafia treść pytania użytkownika i słownik PKD; pobrane rekordy nigdy". To zdanie da się
# sprawdzić na dwa sposoby: przeglądem tego, co składa prompt, albo brakiem krawędzi w grafie
# importów. Drugi sposób nie wymaga niczyjej uwagi — rekordy nie mają którędy przejść.
ASSISTANT_PACKAGE = "assistant"
ASSISTANT_FORBIDDEN_TARGETS = frozenset({"client", "store", "pipeline"})


def test_the_assistant_cannot_reach_fetched_records() -> None:
    """Reguła 13 — strukturalna postać zdania z §B o fazie 4.

    Uzupełnia, a nie zastępuje, test treści żądania w `tests/test_assistant.py`: tamten pilnuje,
    co faktycznie idzie w turze użytkownika, ten — że nie ma skąd wziąć niczego więcej.
    """
    offenders: dict[str, set[str]] = {}
    for path in (PACKAGE / ASSISTANT_PACKAGE).rglob("*.py"):
        forbidden = package_targets(path) & ASSISTANT_FORBIDDEN_TARGETS
        if forbidden:
            offenders[path.relative_to(PACKAGE).as_posix()] = forbidden

    assert not offenders, f"asystent sięga po dane z rejestru: {offenders}"


def test_the_assistant_package_actually_exists() -> None:
    """Zmiana nazwy katalogu nie może po cichu zamienić reguły 13 w pętlę po zbiorze pustym."""
    modules = list((PACKAGE / ASSISTANT_PACKAGE).rglob("*.py"))

    assert modules, f"brak modułów w {ASSISTANT_PACKAGE}/ — reguła 13 nic nie sprawdza"
    assert ASSISTANT_FORBIDDEN_TARGETS, "pusty zbiór zakazów przechodzi zawsze"


def test_the_rule_13_scan_would_notice_a_planted_import(tmp_path: Path) -> None:
    """Odpowiednik kontroli mutacyjnej dla reguł 1-4, tym razem dla asystenta."""
    planted = tmp_path / "caller.py"
    planted.write_text("from ..store import Store\nfrom ..criteria import Criteria\n", "utf-8")

    assert package_targets(planted) & ASSISTANT_FORBIDDEN_TARGETS == {"store"}


# ------------------------------------- reguła 12: jeden właściciel klienta SDK (jeszcze pusty)

# Reguła 12 z ADR-0011 (decyzja 2) zacznie obowiązywać, gdy powstanie `assistant/caller.py`.
# Do tego czasu ten test jest **wyzwalaczem**, a nie regułą: pozycja w sekcji „Gate" ADR-a jest
# czyjąś pamięcią, a ten test zapala się sam w dniu, w którym ktoś zaimportuje SDK. To ten sam
# kształt, co `test_only_prompts_knows_questionary`.
ANTHROPIC_OWNER = "assistant/caller.py"


SDK_FACTORIES = frozenset({"Anthropic", "AsyncAnthropic"})
SDK_REQUIRED_KEYWORDS = frozenset({"api_key", "http_client"})


def sdk_client_calls(path: Path) -> list[set[str]]:
    """Słowa kluczowe każdego wywołania `Anthropic(...)` w pliku — po jednym zbiorze na wywołanie.

    Skan jest składniowy i **odrzuci `Anthropic(**kwargs)`**, bo taki zapis daje pusty zbiór słów
    kluczowych. To nie jest fałszywy alarm: klient, którego poświadczenie i transport składa się
    gdzie indziej, jest dokładnie tym, czemu ta reguła ma zapobiegać.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    z_importu = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "anthropic"
        for alias in node.names
        if alias.name in SDK_FACTORIES
    }
    moduly = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "anthropic"
    }
    wywolania: list[set[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        trafione = (
            isinstance(func, ast.Attribute)
            and func.attr in SDK_FACTORIES
            and isinstance(func.value, ast.Name)
            and func.value.id in moduly
        ) or (isinstance(func, ast.Name) and func.id in z_importu)
        if trafione:
            wywolania.append({kw.arg for kw in node.keywords if kw.arg})
    return wywolania


def test_only_one_module_may_know_the_model_sdk() -> None:
    """Reguła 12, połowa pierwsza: **kto**."""
    # Ten sam zakres co reguła 11, w tym `tests/` — w `.env` leży żywy klucz, więc test
    # tworzący gołego `anthropic.Anthropic()` uwierzytelniłby się naprawdę i wydał pieniądze.
    skanowane = [
        *(p for root in SCANNED_FOR_HTTP_CLIENTS for p in root.rglob("*.py")),
        *TESTS.rglob("*.py"),
    ]
    users = {
        path.relative_to(ROOT).as_posix()
        for path in skanowane
        if path.is_file() and "anthropic" in imported_roots(path)
    }
    dozwolone = {f"{PACKAGE.name}/{ANTHROPIC_OWNER}", "tests/test_assistant_caller.py"}

    assert users <= dozwolone, (
        f"SDK modelu importują: {sorted(users)}. Reguła 12 dopuszcza wyłącznie "
        f"{sorted(dozwolone)} — test wolno, bo podstawia transport i nigdy nie wychodzi."
    )


def test_the_sdk_client_is_never_built_on_ambient_credentials() -> None:
    """Reguła 12, połowa druga: **z czym** — i to jest ta ważniejsza.

    Bez jawnego `api_key=` SDK sięga po własny łańcuch poświadczeń (`ANTHROPIC_API_KEY`,
    `ANTHROPIC_AUTH_TOKEN`, profil `ant auth login` z dysku, zmienne federacyjne), więc narzędzie
    wydałoby cudze poświadczenie, o którym `sprawdz-token` nie umie nic powiedzieć. Bez jawnego
    `http_client=` zbuduje własny transport z `trust_env` na wartości domyślnej — czyli poza
    bramką z `httpclient.py`, dokładnie tak, jak `pipeline` robił to dla CEIDG do 2026-09-07.
    """
    wywolania = sdk_client_calls(PACKAGE / ANTHROPIC_OWNER)

    assert wywolania, f"{ANTHROPIC_OWNER} nie tworzy klienta SDK — reguła 12 nic nie sprawdza"
    for slowa in wywolania:
        brak = SDK_REQUIRED_KEYWORDS - slowa
        assert not brak, f"wywołanie `Anthropic(...)` bez {sorted(brak)}"


@pytest.mark.parametrize(
    ("source", "oczekiwane"),
    [
        (
            "import anthropic\nc = anthropic.Anthropic(api_key=k, http_client=h)\n",
            [{"api_key", "http_client"}],
        ),
        ("import anthropic\nc = anthropic.Anthropic(api_key=k)\n", [{"api_key"}]),
        ("import anthropic\nc = anthropic.Anthropic()\n", [set()]),
        ("import anthropic\nc = anthropic.Anthropic(**kwargs)\n", [set()]),
        (
            "from anthropic import AsyncAnthropic\nc = AsyncAnthropic(api_key=k, http_client=h)\n",
            [{"api_key", "http_client"}],
        ),
        ("from zewnetrzne import Anthropic\nc = Anthropic()\n", []),
    ],
    ids=["komplet", "bez_transportu", "goly", "kwargs", "async_z_importu", "obca_klasa"],
)
def test_the_rule_12_scan_sees_what_each_call_actually_passes(
    tmp_path: Path, source: str, oczekiwane: list[set[str]]
) -> None:
    """Skan, który zawsze przechodzi, jest nie do odróżnienia od działającego.

    Przypadek `kwargs` jest tu celowo jako **naruszenie**: reguła pilnuje tego, co widać
    w wywołaniu, a nie tego, co być może siedzi w słowniku złożonym trzy linie wyżej.
    """
    module = tmp_path / "caller.py"
    module.write_text(source, encoding="utf-8")

    assert sdk_client_calls(module) == oczekiwane
