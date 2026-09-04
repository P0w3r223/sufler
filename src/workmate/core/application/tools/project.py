"""Katalog narzędzia ``Project`` — rejestr projektów, notatki, jedyna droga TWORZENIA."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, get_args

from pydantic import Field, ValidationError

from workmate.core.application.services import (
    NotesWriteService,
    ProjectsService,
)
from workmate.core.application.tools.spec import (
    ToolSpec,
    _brakuje_pol,
    _DateField,
    _envelope,
    _nie_znaleziono,
    _puste,
    _zla_akcja,
)
from workmate.core.domain.notes import build_note_metadata
from workmate.core.errors import WorkMateError

# Jedno źródło zestawu akcji `Project` — dwa warianty, bo zapis jest bramkowany w ``Literal``
# (ADR 0006). Aliasy idą do sygnatur, ``get_args`` do komunikatów odmownych; ręczna kopia listy
# w komunikacie rozjechałaby się przy pierwszej nowej akcji, tak jak groziło to Jirze.
_ProjectAkcja = Literal["status"]
_ProjectAkcjaRW = Literal["status", "save"]
_PROJECT_AKCJE: tuple[str, ...] = get_args(_ProjectAkcja)
_PROJECT_AKCJE_RW: tuple[str, ...] = get_args(_ProjectAkcjaRW)

# Nazwa narzędzia = jego zawartość (ADR 0068). Dawne ``Notes`` obiecywało notatki, więc opis
# musiał zużywać 44% siebie na prostowanie, czego narzędzie NIE robi. Nazwa oddająca zawartość
# kasuje potrzebę prostowania: nikt nie szuka wyszukiwarki notatek pod `Project`.
_PROJECT_HEAD = """\
Stan projektu pionu: deklaracja z rejestru plus synteza z notatek i aktywności.

Akcja `status` — kondycja projektu jako całości. Wymaga: `project` (klucz z rejestru,
np. 'workmate'). Użyj, gdy pytanie brzmi „jak stoi projekt X" albo dotyczy jego stanu,
zdrowia czy fazy."""

# Akapit zapisu wchodzi WYŁĄCZNIE razem z wariantem ``Literal`` zawierającym `save`. Opis
# obiecujący zapis przy nieczynnej akcji byłby tym samym defektem co dawna obietnica
# ``/mnt/user/outputs``: model dostaje instrukcję, po którą nie ma jak sięgnąć.
#
# Ta sama zasada w drugą stronę i MIĘDZY narzędziami: akapit nie odsyła do `File(edit)`, choć
# odesłanie było trafne. ``build_project_catalog`` nie zna profilu mutacji sąsiedniego narzędzia
# (bramka `File` siedzi w ustawieniach drzwi), a złożenie „zapis notatek ON + mutacje OFF" jest
# budowalne — wtedy `File` ma sam `read` i odesłanie stawało się martwe. Symetryczne do decyzji
# po drugiej stronie: `File` przestał obiecywać „zapisz jako nową notatkę" dokładnie dlatego, że
# nie zna profilu zapisu `Project` (`tests/core/test_file_tool.py`). Milczenie jest tu tańsze niż
# odesłanie prawdziwe w jednej konfiguracji i fałszywe w drugiej.
_PROJECT_SAVE = """

Akcja `save` — dopisz NOWĄ notatkę ze spotkania do tego projektu (ZAPIS). Wymaga: `project`,
`title`, `date` (YYYY-MM-DD), `body`. Opcjonalnie: `participants`, `decisions`, `action_items`,
`open_questions`, `tags`. Miejsce zapisu wylicza się z metadanych (firma z rejestru →
projekt → data-slug); ta akcja TWORZY nową notatkę i nie zmienia istniejącej.
Użyj wyłącznie na wprost wyrażoną prośbę człowieka."""

# Podpowiedź przy braku klucza jest BEZ ścieżek i bez nazw narzędzi. Układ ścieżek mieszka
# w sekcji ``ENVIRONMENT`` promptu (etap 6), a nazwa narzędzia odczytu zależy od tego, czy te
# drzwi mają powłokę — pomiar tego per turę nie dociera tutaj (katalog powstaje raz przy
# składaniu drzwi), więc podpowiedź zależna od powłoki bywałaby fałszywa dokładnie w turze,
# w której powłoki nie ma (ADR 0063 + ADR 0068 §2).
_PROJECT_HINT = (
    "`project` to klucz z rejestru pionu, np. 'workmate' — wypisz rejestr, gdy go nie znasz"
)


def build_project_catalog(
    projects: ProjectsService,
    *,
    write_service: NotesWriteService | None = None,
) -> list[ToolSpec]:
    """Zbuduj skonsolidowane narzędzie ``Project`` dla runtime'u agenta (ADR 0009 / ADR 0068).

    Wchłania ``get_project_status`` i ``save_note``. Odczyt notatek NIE wchodzi: powłoka
    w wykonawcy widzi bazę wiedzy zamontowaną ``ro`` i ma ranker jako komendę, więc
    ``search_notes``/``get_note``/``list_projects`` nie mają bariery uzasadniającej narzędzie
    (kryterium ADR 0009 — bariera, nie temat).

    **Osobne od ``build_tool_catalog``, i to jest istota kroku.** Tamten katalog jest WSPÓLNY
    z drzwiami MCP i zamrożony golden-testem; sesja Claude Code nie ma dostępu do naszego
    wykonawcy, więc narzędzia, które tutaj zastępuje powłoka, tam są jedyną drogą do bazy
    wiedzy. Konsolidacja przeprowadzona na wspólnym builderze nie przeniosłaby zdolności,
    tylko skasowała ją po stronie MCP.

    Nazwa i akcja niosą ZAWARTOŚĆ, nie historię (ADR 0068). Dawne ``Notes(project_status)``
    zapowiadało bazę notatek, a dawało stan jednego projektu — i musiało to prostować akapitem
    „to narzędzie do tego nie służy", zależnym od obecności powłoki. Nazwa zgodna z zawartością
    kasuje i akapit, i jego zależność od stanu drzwi.

    Bramka zapisu wchodzi do ``Literal``, nie do ciała funkcji: przy ``write_service=None``
    wartość ``save`` NIE ISTNIEJE w enumie, więc model jej nie zaproponuje. Bramka sprawdzana
    dopiero w ciele wyglądałaby w schemacie identycznie jak jej brak.
    """

    def _status(project: str | None) -> dict[str, Any]:
        missing = _puste(project=project)
        if missing:
            return _brakuje_pol("Project", "status", missing, _PROJECT_HINT)

        def build() -> dict[str, Any]:
            status = projects.get_project_status(str(project))
            if status is None:
                return _nie_znaleziono(
                    "Project",
                    "status",
                    f"Projekt nie istnieje w rejestrze: {project}",
                    _PROJECT_HINT,
                )
            return status.model_dump(mode="json")

        return _envelope(build)

    def _save(
        writer: NotesWriteService,
        project: str | None,
        title: str | None,
        meeting_date: date | None,
        body: str | None,
        participants: list[str] | None,
        decisions: list[str] | None,
        action_items: list[str] | None,
        open_questions: list[str] | None,
        tags: list[str] | None,
    ) -> dict[str, Any]:
        missing = _puste(project=project, title=title, date=meeting_date, body=body)
        # Rozbicie warunku jest dla typów, nie dla logiki: ``_puste`` odsiewa te same pola,
        # ale zwraca nazwy, a nie zawężenie — więc każde użycie niżej byłoby ``| None``.
        if missing or meeting_date is None:
            return _brakuje_pol(
                "Project", "save", missing, "`date` w formacie YYYY-MM-DD, `project` z rejestru"
            )

        def build() -> dict[str, Any]:
            metadata = build_note_metadata(
                title=str(title),
                project=str(project),
                date=meeting_date,
                participants=participants,
                decisions=decisions,
                action_items=action_items,
                open_questions=open_questions,
                tags=tags,
            )
            note = writer.save_note(metadata, str(body))
            return {"saved": True, "id": note.id, "path": f"{note.id}.md"}

        return _envelope(build, errors=(WorkMateError, ValidationError))

    if write_service is None:

        def project_tool(
            action: Annotated[
                _ProjectAkcja,
                Field(description="Co zrobić: `status` — stan projektu."),
            ],
            project: Annotated[
                str | None, Field(description="Klucz projektu z rejestru (wymagany).")
            ] = None,
        ) -> dict[str, Any]:
            # Bramka jest tu z tego samego powodu co w wariancie z zapisem niżej, i musi być
            # SYMETRYCZNA. Zapis i tak by się nie wydarzył (nie ma czym — ``write_service`` jest
            # ``None``), więc bramka uprawnień trzymała bez niej. Psuje się co innego: jeden
            # wariant tej samej funkcji z bramką, a drugi bez, czyta się jak reguła opcjonalna,
            # i następna osoba powiela wariant bez niej.
            if action != "status":
                return _zla_akcja("Project", action, _PROJECT_AKCJE)
            return _status(project)

        return [ToolSpec("Project", _PROJECT_HEAD, project_tool)]

    def project_rw(
        action: Annotated[
            _ProjectAkcjaRW,
            Field(
                description=(
                    "Co zrobić: `status` — stan projektu; `save` — dopisanie NOWEJ notatki."
                )
            ),
        ],
        project: Annotated[
            str | None, Field(description="Klucz projektu z rejestru (obie akcje).")
        ] = None,
        title: Annotated[str | None, Field(description="Tytuł notatki (`save`).")] = None,
        date: Annotated[
            _DateField | None, Field(description="Data spotkania, YYYY-MM-DD (`save`).")
        ] = None,
        body: Annotated[str | None, Field(description="Treść notatki, Markdown (`save`).")] = None,
        participants: Annotated[
            list[str] | None, Field(description="Uczestnicy spotkania (`save`).")
        ] = None,
        decisions: Annotated[
            list[str] | None, Field(description="Podjęte decyzje (`save`).")
        ] = None,
        action_items: Annotated[
            list[str] | None, Field(description="Zadania do wykonania (`save`).")
        ] = None,
        open_questions: Annotated[
            list[str] | None, Field(description="Pytania bez odpowiedzi (`save`).")
        ] = None,
        tags: Annotated[
            list[str] | None, Field(description="Etykiety tematyczne (`save`).")
        ] = None,
    ) -> dict[str, Any]:
        if action == "save":
            return _save(
                write_service,
                project,
                title,
                date,
                body,
                participants,
                decisions,
                action_items,
                open_questions,
                tags,
            )
        if action != "status":
            # Jak w ``Jira``/``Activity``: bez tego nieznana akcja po cichu oddaje stan projektu.
            return _zla_akcja("Project", action, _PROJECT_AKCJE_RW)
        return _status(project)

    return [ToolSpec("Project", f"{_PROJECT_HEAD}{_PROJECT_SAVE}", project_rw)]
