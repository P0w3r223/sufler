"""Bramka redakcyjna OPISÓW NARZĘDZI powierzchni agenta (ADR 0068 §4).

Prompt systemowy ma swoją bramkę od ADR 0056 (``test_prompt.py``): wersaliki nacisku, udział
zdań przeczących, powtórzenia, rusztowanie CoT. Opisy narzędzi nie miały ŻADNEGO pomiaru,
a w najbogatszej konfiguracji ważą ~7,9 KB wobec ~2,4 KB korpusu promptu — czyli trzykrotnie
więcej tekstu jedzie w każdym żądaniu bez jakiegokolwiek progu.

Sonda buduje POWIERZCHNIĘ, jaką agent dostaje w JEDNEJ turze, łącznie z narzędziami dokładanymi
per turę przez drzwi teams_graph (``ReplyWithFile``, ``SendImage``, ``SendDocument``). Pierwsza
wersja o nich zapominała i przez to mierzyła 6,4 KB tam, gdzie realnie jedzie 7,9 KB — bramka
przechodziła, a sufit był przekroczony.

Progi są tu CELOWO luźniejsze niż w prompcie i mierzą co innego:

* **sufit bajtów per narzędzie** jest twardy, bo wynika z zewnętrznego faktu: klient Claude
  Code skraca opis do ~2 KB (konwencja z CLAUDE.md). Opis dłuższy niż sufit jest po prostu
  ucinany w połowie zdania — i nikt tego nie zobaczy;
* **sufit sumaryczny** pilnuje tego, czego sufit per narzędzie nie widzi: dziesięciu opisów
  mieszczących się w limicie każdy z osobna;
* **wersaliki nacisku** dostają BUDŻET, a nie zakaz. W prompcie zakaz jest właściwy (proza
  ciągła), w opisach narzędzi wersalik niesie rozróżnienie kontraktowe (``ODCZYT``/``ZAPIS``,
  ``NOWĄ``), którego składnia nie wyraża. Budżet łapie inflację, nie użycie.

Sondy budują KAŻDE narzędzie powierzchni agenta z prawdziwych builderów; nowe narzędzie
dopisane bez opisu albo z opisem ponad sufit zrywa bramkę, zanim wejdzie niezauważone.
"""

from __future__ import annotations

import functools
import inspect
import re
from datetime import date, datetime
from typing import Annotated, Literal, get_args, get_origin

import pytest

from tests.conftest import FakeNotesRepository, FakeNotesWriter, FakeProjectsRepository
from workmate.core.agent.prompt import (
    STATIC_PROMPT,
    STATIC_PROMPT_SHELL,
    SUMMARY_SYSTEM_PROMPT,
    build_session_header,
)
from workmate.core.application.services import NotesService, NotesWriteService, ProjectsService
from workmate.core.application.tools import (
    ToolSpec,
    build_activity_catalog,
    build_agent_notes_read_catalog,
    build_file_catalog,
    build_file_reply_catalog,
    build_jira_catalog,
    build_project_catalog,
    build_schedule_catalog,
    build_shell_catalog,
    build_user_doc_push_catalog,
    build_user_image_push_catalog,
    build_workspace_catalog,
)
from workmate.core.domain.models import Project
from workmate.core.domain.workspace import WorkspaceScope
from workmate.core.ports.llm import AttachmentQueue
from workmate.core.ports.materialization import MaterializationLimits

# Sufit per narzędzie. Konwencja z CLAUDE.md mówi „~2 KB" i jest to fakt o KLIENCIE (skraca
# opis), a nie nasze upodobanie — stąd twardy 2048 B zamiast progu z marginesem.
_SUFIT_BAJTOW = 2048

# Sufit sumaryczny NAJBOGATSZEJ powierzchni agenta. Zmierzone po ADR 0068 (razem z trójką
# dostawy, którą drzwi teams_graph wstrzykują per turę): 7446 B z powłoką, 7865 B bez niej —
# wobec 9281 B i 9748 B przed zmianą.
#
# Próg 8000 B jest podniesiony ŚWIADOMIE wobec pierwszej wersji (7000 B), która mierzyła
# powierzchnię WĘŻSZĄ niż realna: pomijała ``ReplyWithFile``/``SendImage``/``SendDocument``,
# przez co realna suma 7628 B stała ponad sufitem, a bramka tego nie widziała.
#
# Zapas to ~135 B, czyli JEDNO ZDANIE — i to jest komunikat tej liczby, a nie jej wada.
# Pomiar 2026-08-20 (po dopisaniu do `File(edit)` zdania o `content`): 7716 B z powłoką,
# 7971 B bez niej, czyli zapas stopniał do ~29 B. Zdanie zostało wcześniej ODCHUDZONE z 201 B
# do 104 B właśnie po to, żeby zmieścić się w budżecie zamiast podnosić próg — pierwsza
# redakcja kładła tę bramkę (8066 B) i tak miała zostać przeczytana: jako rachunek, nie usterka.
# Najmniejsze narzędzie agenta ma 68 B (``ListProjects``), najmniejsze skonsolidowane 782 B
# (``Project``), więc kolejne narzędzie musi zostać opłacone CIĘCIEM, a nie podniesieniem
# progu. Podniesienie sufitu jest dopuszczalne, ale ma być decyzją zapisaną w ADR — dokładnie
# tak jak to podniesienie z 7000 na 8000.
_SUFIT_SUMY = 8000

# Wersaliki nacisku (poza akronimami i skrótami formatów). Budżet, nie zakaz — patrz docstring.
_WERSALIK = re.compile(r"\b[A-ZĄĆĘŁŃÓŚŹŻ]{3,}\b")
_DOZWOLONE_AKRONIMY = frozenset(
    {"ADR", "API", "JSON", "CSV", "PDF", "DOCX", "XLSX", "PPTX", "HTML", "MCP", "SQL", "URL"}
)
_BUDZET_WERSALIKOW = 12

# Nazwy narzędzi powierzchni agenta — obecne i ZNIESIONE. Te drugie są w bramce dlatego, że
# martwe odesłanie powstaje właśnie przy przemianowaniu: tekst zostaje ze starą nazwą, a sonda
# znająca wyłącznie nowe nazwy przepuszcza go jako „nic o narzędziach".
_NAZWY_NARZEDZI = frozenset(
    {
        "Bash",
        "Project",
        "Activity",
        "Jira",
        "Schedule",
        "File",
        "SearchNotes",
        "GetNote",
        "ListProjects",
        "CreateFile",
        "ReadFile",
        "ListFiles",
        "ReplyWithFile",
        "SendImage",
        "SendDocument",
    }
)
_ZNIESIONE_NAZWY = frozenset(
    {
        "Notes",
        "GitHub",
        "search_notes",
        "get_note",
        "list_projects",
        "create_file",
        "read_file",
        "list_files",
        "reply_with_file",
        "send_image_to_user",
        "send_document_to_user",
        "reply_on_thread",
    }
)

# KOMPLET narzędzi każdego z dwóch wariantów tury. Ta para jest kontrolą samego POMIARU, bo
# sufity wyżej mierzą to, co ``_powierzchnia_agenta`` im poda: narzędzie pominięte w składaniu
# nie psuje żadnej asercji o sufitach — po prostu zaniża sumę, a bramka staje się fikcją.
# Dokładnie tak przepadła pierwsza wersja tej sondy (6,4 KB zamiast 7,8 KB, bo brakowało trójki
# dostawy), a jedynym objawem było to, że bramka przechodziła.
_POWIERZCHNIA_Z_POWLOKA = frozenset(
    {"Project", "Activity", "Jira", "Schedule", "File", "Bash", "SendImage", "SendDocument"}
)
_POWIERZCHNIA_BEZ_POWLOKI = frozenset(
    {
        "Project",
        "Activity",
        "Jira",
        "Schedule",
        "File",
        "SearchNotes",
        "GetNote",
        "ListProjects",
        "CreateFile",
        "ReadFile",
        "ListFiles",
        "ReplyWithFile",
        "SendImage",
        "SendDocument",
    }
)

# Konwencja nazw powierzchni agenta (ADR 0068 §3): PascalCase, bez podkreśleń.
_PASCAL_CASE = re.compile(r"[A-Z][A-Za-z0-9]*\Z")


def _cytowanie(nazwa: str) -> re.Pattern[str]:
    """Wzorzec ODESŁANIA do narzędzia — trzy kształty, dobrane do rodzaju nazwy.

    Pierwsza wersja tej sondy znała wyłącznie ```Nazwa``` i przepuściła przez to dwa realne
    defekty naraz: ``GitHub(action='comment')`` w nagłówku sesji (cytowanie z akcją, bez
    grawisów) oraz „(z wyników search_notes)" w opisie ``GetNote`` (goła proza).

    Kształt zależy od rodzaju nazwy i to rozróżnienie jest konieczne, a nie ozdobne. Nazwy
    PascalCase pokrywają się ze zwykłymi słowami prozy — korpus promptu mówi „Notes are
    identified as…" o notatkach, nie o narzędziu — więc dla nich wymagamy kontekstu cytowania
    (grawisy albo nawias wywołania). Nazwy snake_case w prozie nie występują, więc dla nich
    wystarczy całe słowo, gdziekolwiek stoi.
    """
    ucieczka = re.escape(nazwa)
    if "_" in nazwa:
        return re.compile(rf"(?<![A-Za-z0-9_]){ucieczka}(?![A-Za-z0-9_])")
    return re.compile(rf"`{ucieczka}`|(?<![A-Za-z0-9_]){ucieczka}\s*\(")


def _wzmiankowane(tekst: str, nazwy: frozenset[str] | set[str]) -> set[str]:
    """Które z ``nazw`` są w tekście ODESŁANIEM do narzędzia (patrz ``_cytowanie``)."""
    return {n for n in nazwy if _cytowanie(n).search(tekst)}


class _FakeEvents:
    def recent(self, source=None, project=None, limit=20):  # noqa: ANN001, ANN201
        return []


class _FakeWorklog:
    def propose_worklog(self, since, until, author):  # noqa: ANN001, ANN201
        raise AssertionError("opis nie woła serwisu")


class _FakeGithubWrite:
    def create_issue(self, title, body, labels):  # noqa: ANN001, ANN201
        raise AssertionError

    def create_comment(self, number, body):  # noqa: ANN001, ANN201
        raise AssertionError


class _FakeMyJira:
    def my_open_tasks(self):  # noqa: ANN201
        return [], False

    def my_history(self, since="", until=""):  # noqa: ANN001, ANN201
        return [], False


class _FakeJiraRead:
    def member_open_tasks(self, user):  # noqa: ANN001, ANN201
        return [], False

    def member_history(self, user, since, until):  # noqa: ANN001, ANN201
        return [], False

    def task_details(self, key):  # noqa: ANN001, ANN201
        raise AssertionError

    def search_tasks(self, **kwargs):  # noqa: ANN003, ANN201
        return []


class _FakeSchedule:
    def schedule(self, **kwargs):  # noqa: ANN003, ANN201
        return {}


class _FakeRunner:
    def run(self, command, *, cwd="", timeout_s=0):  # noqa: ANN001, ANN201
        raise AssertionError


class _PustyWorkspace:
    def list(self, scope_dir):  # noqa: ANN001, ANN201
        return []

    def read(self, scope_dir, name):  # noqa: ANN001, ANN201
        return None

    def read_bytes(self, scope_dir, name):  # noqa: ANN001, ANN201
        return None

    def exists(self, relpath):  # noqa: ANN001, ANN201
        return False

    def create(self, relpath, content):  # noqa: ANN001, ANN201
        raise AssertionError

    def create_bytes(self, relpath, data):  # noqa: ANN001, ANN201
        raise AssertionError


class _PustyMaterializer:
    def materialize(self, name, data):  # noqa: ANN001, ANN201
        return None


class _FakeMutations:
    """Obecność ORAZ ``allow_delete`` decydują o wariancie opisu ``File`` — ciało nie biegnie.

    Domyślnie z kasowaniem, bo pomiar ma dotyczyć NAJBOGATSZEJ powierzchni.
    """

    def __init__(self, allow_delete: bool = True) -> None:
        self.allow_delete = allow_delete

    def edit_note(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError

    def delete_note(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError


class _FakeRenderer:
    def render(self, content, fmt):  # noqa: ANN001, ANN201
        raise AssertionError


class _FakeFileSender:
    def upload_channel_file(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError

    def post_reply_with_attachment(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError


class _FakeImageSender:
    def send_image_to_user(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError


class _FakeDocSender:
    def send_document_to_user(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise AssertionError


def _projects() -> ProjectsService:
    projekty = [Project(key="workmate", company="biap", name="WorkMate", description="asystent")]
    return ProjectsService(FakeProjectsRepository(projekty, {}), FakeNotesRepository([]))


def _powierzchnia_agenta(
    *,
    shell: bool,
    zapis_notatek: bool = True,
    zapis_github: bool = True,
    worklog: bool = True,
    mutacje: str = "z kasowaniem",
) -> list[ToolSpec]:
    """Każde narzędzie, jakie agent może dostać w JEDNEJ turze; domyślnie NAJBOGATSZA konfiguracja.

    Domyślne wartości odwzorowują wariant najbogatszy, bo tego wymagają sufity bajtów: mierzą
    powierzchnię, którą agent naprawdę może dostać. Argumenty bramek zdolności są tu dla sond
    martwych obietnic, którym potrzebna jest powierzchnia UBOŻSZA — obietnica bez pokrycia
    powstaje dokładnie tam, gdzie bramka jest zamknięta (patrz ``_PROFILE_DRZWI``).
    """
    from workmate.core.application.workspace import (
        WorkspaceLimits,
        WorkspaceService,
        WorkspaceWriteService,
    )

    projects = _projects()
    notes = NotesService(FakeNotesRepository([]))
    writer = NotesWriteService(FakeNotesWriter(), FakeProjectsRepository([], {}))
    scope = WorkspaceScope("teams_graph", "t/c/r")
    repo = _PustyWorkspace()
    limity = WorkspaceLimits(1, 1, 1, frozenset({"md"}))
    mutator = None if mutacje == "brak" else _FakeMutations(allow_delete=mutacje == "z kasowaniem")

    katalog: list[ToolSpec] = [
        *build_project_catalog(projects, write_service=writer if zapis_notatek else None),
        *build_activity_catalog(
            events=_FakeEvents(),
            worklog=_FakeWorklog() if worklog else None,
            write_service=_FakeGithubWrite() if zapis_github else None,
        ),
        *build_jira_catalog(_FakeMyJira(), _FakeJiraRead(), lambda name: "konto"),
        *build_schedule_catalog(_FakeSchedule()),
        *build_file_catalog(
            scope,
            WorkspaceService(repo),
            _PustyMaterializer(),
            AttachmentQueue(budget_bytes=1),
            MaterializationLimits(1, 1),
            # Wariant NAJBOGATSZY: z mutacjami opis ``File`` rośnie o akapit `edit`/`delete`,
            # a sufit sumaryczny ma pilnować powierzchni, którą agent naprawdę może dostać.
            mutator,
            "u-anna",
            shell_available=shell,
        ),
    ]
    if shell:
        katalog += build_shell_catalog(
            scope, _FakeRunner(), workspace_root="/home/scratchpad", outbox_enabled=True
        )
    else:
        katalog += [
            *build_agent_notes_read_catalog(notes, projects),
            *build_workspace_catalog(
                scope, WorkspaceService(repo), WorkspaceWriteService(repo, repo, limity)
            ),
            # ``ReplyWithFile`` schodzi z powierzchni RAZEM z pojawieniem się powłoki (etap 7:
            # dostawa idzie wtedy skrzynką ``outputs/``), więc stoi w gałęzi „bez powłoki".
            *build_file_reply_catalog(
                _FakeFileSender(), _FakeRenderer(), "team", "chan", "root", max_bytes=1
            ),
        ]
    # Push 1:1 jest NIEZALEŻNY od powłoki — drzwi teams_graph wstrzykują go per turę, gdy
    # bramki `..._ENABLE_USER_FILE_PUSH` / `..._ENABLE_USER_DOC_PUSH` są włączone. Pominięcie
    # tej trójki w pomiarze było powodem, dla którego sufit sumaryczny mierzył powierzchnię
    # WĘŻSZĄ niż ta, którą agent naprawdę dostaje (amendment ADR 0068).
    katalog += [
        *build_user_image_push_catalog(_FakeImageSender(), "aad-anna", max_bytes=1),
        *build_user_doc_push_catalog(_FakeDocSender(), _FakeRenderer(), "aad-anna", max_bytes=1),
    ]
    return katalog


@pytest.fixture(params=[True, False], ids=["z powłoką", "bez powłoki"])
def powierzchnia(request) -> list[ToolSpec]:  # noqa: ANN001
    return _powierzchnia_agenta(shell=request.param)


def test_kazdy_opis_miesci_sie_w_sufcie_bajtow(powierzchnia: list[ToolSpec]) -> None:
    """Opis dłuższy niż sufit klient UCINA — i to bez śladu w naszych testach."""
    za_dlugie = {
        spec.name: len(spec.description.encode("utf-8"))
        for spec in powierzchnia
        if len(spec.description.encode("utf-8")) > _SUFIT_BAJTOW
    }

    assert not za_dlugie, f"opisy ponad {_SUFIT_BAJTOW} B: {za_dlugie}"


def test_suma_opisow_powierzchni_ma_sufit(powierzchnia: list[ToolSpec]) -> None:
    """Sufit per narzędzie nie widzi dziesięciu opisów mieszczących się każdy z osobna."""
    suma = sum(len(spec.description.encode("utf-8")) for spec in powierzchnia)

    assert suma <= _SUFIT_SUMY, f"suma opisów {suma} B ponad sufit {_SUFIT_SUMY} B"


def test_kazde_narzedzie_ma_niepusty_opis(powierzchnia: list[ToolSpec]) -> None:
    """Narzędzie bez opisu jest dla modelu samą nazwą — najtańszy możliwy regres."""
    bez_opisu = [spec.name for spec in powierzchnia if not spec.description.strip()]

    assert bez_opisu == []


def test_wersaliki_nacisku_maja_budzet(powierzchnia: list[ToolSpec]) -> None:
    """Budżet, nie zakaz: w opisie wersalik niesie kontrakt (ODCZYT/ZAPIS), w prompcie — nacisk.

    Bramka łapie INFLACJĘ. Stan po ADR 0068 mieści się z zapasem; wersja sprzed cięcia opisu
    ``Jira`` miała ich w tym jednym narzędziu kilkanaście.
    """
    for spec in powierzchnia:
        wersaliki = [w for w in _WERSALIK.findall(spec.description) if w not in _DOZWOLONE_AKRONIMY]
        assert len(wersaliki) <= _BUDZET_WERSALIKOW, (
            f"{spec.name}: {len(wersaliki)} wersalików nacisku: {wersaliki}"
        )


def test_opis_nie_odsyla_do_narzedzia_spoza_tej_konfiguracji(powierzchnia: list[ToolSpec]) -> None:
    """Odesłanie do narzędzia, którego w TEJ konfiguracji nie ma, to obietnica bez pokrycia.

    Ta klasa defektu wracała pięć razy: `/mnt/user/outputs` w opisie ``Bash``, `workmate-search`
    w ``Notes`` na drzwiach bez powłoki, `search_notes` w ``File`` na drzwiach Z powłoką,
    `search_notes` w ``GetNote`` na CAŁEJ powierzchni agenta oraz `GitHub(action='comment')`
    w nagłówku sesji po przemianowaniu narzędzia.

    Dwie ostatnie przeżyły PIERWSZĄ wersję tej sondy, bo znała ona wyłącznie NOWE nazwy i tylko
    kształt ```Nazwa```. Zna więc dziś także nazwy ZNIESIONE (stare snake_case i dawne
    ``Notes``/``GitHub``) oraz kształt ``Nazwa(...)``, którym cytuje się akcję.
    """
    nieobecne = (_NAZWY_NARZEDZI | _ZNIESIONE_NAZWY) - {spec.name for spec in powierzchnia}

    for spec in powierzchnia:
        wzmianki = _wzmiankowane(spec.description, nieobecne)
        assert not wzmianki, f"{spec.name} odsyła do nieobecnych narzędzi: {sorted(wzmianki)}"


def test_prompt_i_naglowek_sesji_tez_nie_odsylaja_do_nieobecnych_narzedzi() -> None:
    """Ta sama reguła obowiązuje TEKST, który stoi WYŻEJ w hierarchii niż opis narzędzia.

    Nagłówek sesji instruował ``GitHub(action='comment', number=N)`` jeszcze długo po tym, jak
    narzędzie nazywało się ``Activity`` — a runtime odpowiada na to „Nieznane narzędzie".
    Kosztowało to całą rundę narzędziową, w tekście per turę, na który model ma reagować
    najsilniej. Pierwsza wersja bramki patrzyła wyłącznie na ``ToolSpec.description``, więc
    korpusu ani nagłówka nie widziała — a to w nich odesłanie jest najdroższe.
    """
    naglowek = build_session_header(
        datetime(2026, 8, 17),
        channel="teams_graph",
        thread="t/c/r",
        skills=(("brief", "opis"),),
        github_thread=("issue", 7),
        staged_files=("umowa.pdf",),
        trust_nonce="abcd1234",
        shell_unavailable=True,
    )
    # Korpus opisuje świat, nie katalog, więc odesłanie po nazwie i tak ma prawo być tylko do
    # narzędzia istniejącego; nagłówek instruuje wywołanie wprost.
    artefakty = {
        "STATIC_PROMPT": STATIC_PROMPT,
        "STATIC_PROMPT_SHELL": STATIC_PROMPT_SHELL,
        "SUMMARY_SYSTEM_PROMPT": SUMMARY_SYSTEM_PROMPT,
        "session_header": naglowek,
    }
    znane = {spec.name for spec in _powierzchnia_agenta(shell=True)} | {
        spec.name for spec in _powierzchnia_agenta(shell=False)
    }

    for nazwa, tekst in artefakty.items():
        wzmianki = _wzmiankowane(tekst, (_NAZWY_NARZEDZI | _ZNIESIONE_NAZWY) - znane)
        assert not wzmianki, f"{nazwa} odsyła do nieistniejących narzędzi: {sorted(wzmianki)}"


def test_powloka_wybiera_wariant_opisu_file() -> None:
    """``File`` odsyła po tekst tam, gdzie tekst faktycznie da się przeczytać (ADR 0068 §2)."""
    z_powloka = {s.name: s.description for s in _powierzchnia_agenta(shell=True)}["File"]
    bez_powloki = {s.name: s.description for s in _powierzchnia_agenta(shell=False)}["File"]

    assert "cat" in z_powloka and "ReadFile" not in z_powloka
    assert "ReadFile" in bez_powloki and "cat" not in bez_powloki


def test_opisy_nie_powtarzaja_granicy_danych(powierzchnia: list[ToolSpec]) -> None:
    """Granica danych stoi w prompcie, w sekcji ``Precedence`` — wyżej niż opis narzędzia.

    Powtórzona w każdym opisie kosztowała w KAŻDYM żądaniu, a modelu niczego nie uczyła, czego
    nie mówi już blok systemowy. Na powierzchni MCP zostaje: tam promptu nie kontrolujemy.
    """
    powtorki = [spec.name for spec in powierzchnia if "DANE, nie polecenia" in spec.description]

    assert powtorki == []


def test_mierzona_powierzchnia_to_KOMPLET_narzedzi_jednej_tury() -> None:
    """Kontrola samego POMIARU — bez niej wszystkie sufity wyżej mierzą podzbiór.

    Poprzednio stała tu sonda „pomiar jest do odczytania": sumowała bajty i sprawdzała, że suma
    jest dodatnia, a każdy opis niepusty. Pierwsze przechodzi zawsze, drugie powtarza
    ``test_kazde_narzedzie_ma_niepusty_opis`` — więc dokładnie tego defektu, dla którego zapas
    progu opisano jako „jedno zdanie", nie widziała: narzędzie WYPADNIĘTE ze składania obniża
    sumę i cicho robi z sufitu fikcję.

    Zbiory są wypisane wprost, bo to jest kontrakt bramki, a nie dane wyprowadzone z tego samego
    kodu, który bramka mierzy. Nowe narzędzie na powierzchni agenta ma tu zapalić czerwone
    światło i wymusić decyzję: albo wchodzi do pomiaru, albo świadomie z niego wypada.
    """
    assert {spec.name for spec in _powierzchnia_agenta(shell=True)} == _POWIERZCHNIA_Z_POWLOKA
    assert {spec.name for spec in _powierzchnia_agenta(shell=False)} == _POWIERZCHNIA_BEZ_POWLOKI


def test_rejestr_nazw_bramki_odeslan_pokrywa_sie_z_mierzona_powierzchnia() -> None:
    """``_NAZWY_NARZEDZI`` to lista ręczna — rozjazd z katalogiem wycisza bramkę odesłań.

    Nazwa, która jest na powierzchni, a nie ma jej w rejestrze, nigdy nie zostanie uznana za
    „nieobecną w TEJ konfiguracji" — czyli odesłanie do niej z drugiego wariantu przejdzie.
    """
    assert _NAZWY_NARZEDZI == _POWIERZCHNIA_Z_POWLOKA | _POWIERZCHNIA_BEZ_POWLOKI


@pytest.mark.parametrize("shell", [True, False], ids=["z powłoką", "bez powłoki"])
def test_kazda_nazwa_powierzchni_agenta_trzyma_konwencje(shell: bool) -> None:
    """Jedna konwencja nazw na powierzchni agenta (ADR 0068 §3) — nic tego dotąd nie pilnowało.

    ``przemianuj_na_konwencje_agenta`` ŚWIADOMIE degraduje do nazwy MCP, gdy w mapie nazw brakuje
    wpisu: lepiej drzwi z nazwą spoza konwencji niż drzwi, które się nie podniosły. Ta degradacja
    jest zaworem RUNTIME'u i nie ma prawa dojechać do wydania — czwarte narzędzie odczytu dopisane
    po stronie MCP wchodziłoby inaczej na powierzchnię agenta jako ``get_project_status``, obok
    ``SearchNotes`` i ``GetNote``, i nikt by tego nie zobaczył.
    """
    poza_konwencja = [
        spec.name for spec in _powierzchnia_agenta(shell=shell) if not _PASCAL_CASE.match(spec.name)
    ]

    assert poza_konwencja == []


def test_project_save_zostaje_pod_bramka_zapisu() -> None:
    """Pomiar buduje wariant NAJBOGATSZY — sonda pilnuje, że to naprawdę ten wariant."""
    opis = {s.name: s.description for s in _powierzchnia_agenta(shell=True)}["Project"]

    assert "`save`" in opis
    assert date(2026, 1, 1).isoformat()[:4] not in opis  # opis nie zamraża roku


# Bramki zdolności składają się na WIELE powierzchni, a martwa obietnica powstaje dokładnie tam,
# gdzie bramka jest ZAMKNIĘTA — więc wariant najbogatszy, którym mierzy się sufity, nie ma czym
# tych sond nakarmić. Profile odwzorowują złożenia, które buduje `adapters/inbound`: drzwi Teams
# mają `enable_write=False` zaszyte (ADR 0006), most GitHub i worklog bywają nieskonfigurowane
# (`wiring_bridge.py`), a mutacje notatek i kasowanie to dwie osobne bramki (ADR 0065).
_PROFILE_DRZWI: tuple[tuple[str, dict[str, object]], ...] = (
    ("Teams: powłoka, bez mutacji", {"shell": True, "zapis_notatek": False, "mutacje": "brak"}),
    (
        "Teams: powłoka, mutacje bez kasowania",
        {"shell": True, "zapis_notatek": False, "mutacje": "bez kasowania"},
    ),
    ("Teams: powłoka, mutacje z kasowaniem", {"shell": True, "zapis_notatek": False}),
    ("Teams: bez powłoki, mutacje z kasowaniem", {"shell": False, "zapis_notatek": False}),
    (
        "Teams: bez mostu GitHub i bez worklogu",
        {"shell": True, "zapis_notatek": False, "zapis_github": False, "worklog": False},
    ),
    # Złożenie, którego dziś nie buduje żadne drzwi (Teams zaszywa `enable_write=False`), ale
    # które jest budowalne: `Project` z zapisem obok `File` bez mutacji. Stoi tu, bo to jedyny
    # profil, w którym odesłanie MIĘDZY narzędziami może umrzeć — a bramka ma pilnować sprzężenia,
    # nie tylko dzisiejszej konfiguracji.
    ("CLI: zapis notatek ON, mutacje OFF", {"shell": True, "mutacje": "brak"}),
    ("najbogatsza z powłoką", {"shell": True}),
    ("najbogatsza bez powłoki", {"shell": False}),
)


def _akcje_wariantu(spec: ToolSpec) -> frozenset[str]:
    """Akcje, jakie TEN wariant narzędzia dopuszcza — wprost z ``Literal`` w sygnaturze.

    Źródłem jest sygnatura, nie tabela obok: ``Activity`` składa swój ``Literal`` dopiero
    w builderze (``activity.__annotations__["action"]`` podmieniane po definicji), więc każdy
    ręczny spis akcji rozjechałby się z kodem przy pierwszym dopisaniu akcji. ``eval_str=True``
    jest konieczne, bo moduły narzędzi mają ``from __future__ import annotations`` i reszta
    adnotacji jest napisami; wpis niebędący napisem ``inspect`` przepuszcza bez zmian.
    """
    parametr = inspect.signature(spec.fn, eval_str=True).parameters.get("action")
    if parametr is None:
        return frozenset()
    adnotacja = parametr.annotation
    if get_origin(adnotacja) is Annotated:
        adnotacja = get_args(adnotacja)[0]
    if get_origin(adnotacja) is not Literal:
        return frozenset()
    return frozenset(get_args(adnotacja))


def _cytowanie_akcji(akcja: str) -> re.Pattern[str]:
    """Wzorzec CYTOWANIA akcji — bez kontekstu ani rusz, bo nazwy akcji to zwykłe słowa.

    ``read``, ``save``, ``task``, ``search``, ``comment`` i ``summary`` w gołej prozie znaczą co
    innego niż akcja narzędzia. Wzorzec bez grawisów zapalałby się na zdaniach o czytaniu
    i zapisie, czyli na prawie każdym opisie.

    Trzy kształty, dokładnie z tego samego powodu, dla którego siostrzana ``_cytowanie`` musiała
    poznać ``Nazwa(...)``: kształt pominięty to defekt przepuszczony. Zmierzone na dzisiejszej
    powierzchni — ```save``` występuje, ``Nazwa(akcja)`` występuje (``Project`` odsyłał tak do
    ``File``), a ``action='…'`` NIE występuje w żadnym opisie. Ten trzeci jedzie tu obroną
    z wyprzedzeniem: tak cytują akcję docstringi ``File`` i nagłówek sesji, więc kształt wejdzie
    do opisu przy pierwszym przeklejeniu stamtąd.
    """
    ucieczka = re.escape(akcja)
    return re.compile(
        rf"`{ucieczka}`|action=['\"]{ucieczka}['\"]|[A-Z][A-Za-z0-9]*\(\s*{ucieczka}\s*[,)]"
    )


# Odesłanie do akcji SĄSIEDNIEGO narzędzia: ```File(edit)``` albo ```Project(action='save')```.
_ODESLANIE_Z_AKCJA = re.compile(r"`([A-Z][A-Za-z0-9]*)\(\s*(?:action=)?['\"]?([a-z_]+)['\"]?\s*\)`")

# Nagłówek akapitu akcji w opisie: „Akcja `save` — …". Niezależny od introspekcji sygnatury,
# więc widzi też narzędzie, które akcje ROZDZIELA W CIELE (`action: str`) zamiast enumem.
_AKAPIT_AKCJI = re.compile(r"Akcja `([a-z_]+)`")


@functools.cache
def _repertuar_akcji() -> dict[str, frozenset[str]]:
    """Akcje, jakie dane narzędzie ma w JAKIMKOLWIEK profilu — złożone z kodu, nie z listy.

    Lista pisana ręką rozjeżdża się z katalogiem i wycisza bramkę; ten plik przerabiał to już raz
    przy nazwach narzędzi (patrz ``test_rejestr_nazw_bramki_odeslan_pokrywa_sie_z_mierzona_
    powierzchnia``). Liczone LENIWIE, przy pierwszym teście, a nie przy imporcie: osiem profili to
    osiem wywołań prawdziwych builderów, a wyjątek w którymkolwiek z nich przy imporcie zabrałby
    także sufity bajtów i bramkę odesłań — czyli zamieniłby w fikcję sondy, które z tą nie mają
    nic wspólnego.
    """
    repertuar: dict[str, frozenset[str]] = {}
    for _, kwargi in _PROFILE_DRZWI:
        for spec in _powierzchnia_agenta(**kwargi):  # type: ignore[arg-type]
            repertuar[spec.name] = repertuar.get(spec.name, frozenset()) | _akcje_wariantu(spec)
    return repertuar


@pytest.mark.parametrize(("etykieta", "kwargi"), _PROFILE_DRZWI, ids=[e for e, _ in _PROFILE_DRZWI])
def test_opis_nie_obiecuje_akcji_spoza_tego_wariantu(etykieta: str, kwargi: dict) -> None:  # noqa: ANN001
    """Opis cytujący WŁASNĄ akcję, której ``Literal`` tego wariantu nie ma, to martwa obietnica.

    ``tools/project.py`` zapisuje tę zasadę jako komentarz („akapit zapisu wchodzi WYŁĄCZNIE razem
    z wariantem ``Literal`` zawierającym `save`"), a jej złamanie po stronie ``File`` — obietnicę
    „poprawki do nich zapisuj jako nową notatkę" przy ``Project(action='save')`` wyłączonym —
    zdjął commit ``d989a64``. Tamten commit dołożył też sondę na TO JEDNO zdanie w TYM JEDNYM
    narzędziu (``tests/core/test_file_tool.py``). Ta sonda uogólnia ją na repertuar akcji: nie
    zamraża zdania, tylko liczy akcje wariantu z sygnatury.

    Zmierzone przy dopisaniu: żaden profil nie cytuje własnej akcji spoza swojego enuma. Sonda
    stoi za regres, nie za znalezisko — inaczej niż jej międzynarzędziowa siostra niżej.
    """
    repertuar = _repertuar_akcji()
    obiecane: list[str] = []
    for spec in _powierzchnia_agenta(**kwargi):
        nieobecne = repertuar[spec.name] - _akcje_wariantu(spec)
        obiecane += [
            f"{spec.name}({a})"
            for a in sorted(nieobecne)
            if _cytowanie_akcji(a).search(spec.description)
        ]

    assert not obiecane, f"{etykieta}: opis cytuje własną akcję spoza swojego Literal: {obiecane}"


@pytest.mark.parametrize(("etykieta", "kwargi"), _PROFILE_DRZWI, ids=[e for e, _ in _PROFILE_DRZWI])
def test_opis_nie_odsyla_do_akcji_nieobecnej_u_sasiada(etykieta: str, kwargi: dict) -> None:  # noqa: ANN001
    """Odesłanie ```File(edit)``` umiera, gdy sąsiad ma tę akcję ZA BRAMKĄ, która akurat jest OFF.

    Bramka odesłań (wyżej) sprawdza, czy NAZWA jest na tych drzwiach; ta sprawdza, czy AKCJA jest
    w tamtym narzędziu. To nie jest przypadek teoretyczny: opis ``Project`` odsyłał do
    ``File(edit)``, a ``File`` przy wyłączonych mutacjach ma sam ``read``. Żadne dzisiejsze drzwi
    nie budują tego złożenia, bo Teams zaszywa ``enable_write=False`` — ale budowalne jest, a
    ``build_project_catalog`` nie ma jak zapytać sąsiada o jego bramkę.

    Rozstrzygnięcie jest SYMETRYCZNE do tego po drugiej stronie: ``File`` przestał obiecywać
    „zapisz jako nową notatkę", bo nie zna profilu zapisu ``Project``. Milczenie jest tańsze niż
    odesłanie prawdziwe w jednej konfiguracji i fałszywe w drugiej.
    """
    powierzchnia = _powierzchnia_agenta(**kwargi)
    akcje = {spec.name: _akcje_wariantu(spec) for spec in powierzchnia}
    martwe: list[str] = []
    for spec in powierzchnia:
        for nazwa, akcja in _ODESLANIE_Z_AKCJA.findall(spec.description):
            # Nieobecną NAZWĘ łapie bramka odesłań; narzędzie bez enuma akcji nie ma czym
            # zaprzeczyć, więc oba przypadki zostawiamy jej i milczymy.
            if akcje.get(nazwa) and akcja not in akcje[nazwa]:
                martwe.append(f"{spec.name} → {nazwa}({akcja})")

    assert not martwe, f"{etykieta}: odesłanie do akcji, której sąsiad nie ma: {martwe}"


def test_kazdy_akapit_akcji_ma_pokrycie_w_enumie() -> None:
    """Sonda pokrycia oparta na OPISIE, nie na introspekcji — inaczej mierzyłaby własny detektor.

    „Narzędzie akcyjne" rozpoznane jako „ma ``Literal`` na parametrze o nazwie dokładnie
    ``action``" wypuszcza wszystko, co odbiega od dzisiejszej czwórki: ``action: str``
    z dyspozytorem w ciele, ``Literal[...] | None``, parametr nazwany ``op``. Takie narzędzie
    przeszłoby obie sondy wyżej w ciszy, bo dla nich po prostu nie istnieje.

    Punktem zaczepienia jest więc kształt, którym opisy TEGO repo ogłaszają akcję — akapit
    „Akcja `x` — …". Ogłoszona akcja bez pokrycia w enumie znaczy albo martwą obietnicę, albo
    narzędzie rozdzielające akcje w ciele; oba mają się tu zapalić.
    """
    bez_pokrycia: list[str] = []
    for etykieta, kwargi in _PROFILE_DRZWI:
        for spec in _powierzchnia_agenta(**kwargi):  # type: ignore[arg-type]
            ogloszone = set(_AKAPIT_AKCJI.findall(spec.description))
            brak = sorted(ogloszone - _akcje_wariantu(spec))
            bez_pokrycia += [f"{etykieta}: {spec.name} ogłasza {a}" for a in brak]

    assert not bez_pokrycia
