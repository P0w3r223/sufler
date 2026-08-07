# How-to: dodaj nowe narzędzie MCP

Cel: dołożyć narzędzie *tylko do odczytu* zgodnie z architekturą.
Przykład: `count_notes` — zwraca liczbę notatek w projekcie.

> ⚠️ Read-only jest **domyślną postawą**. Pierwsze narzędzie zapisu (`save_note`)
> przeszło Bramkę 2 — patrz [ADR 0006](../adr/0006-write-capability-gate-2.md).
> Kolejne narzędzie, które **zapisuje lub mutuje** stan (edycja, usuwanie),
> nadal wymaga własnego ADR i zgody zespołu; wystawiaj je przez osobny port
> zapisu (`NotesWriter`) i bramkuj per drzwi (`enable_write`).

## 1. Logika w rdzeniu (przypadek użycia)

W `src/workmate/core/application/services.py` dodaj metodę do właściwego serwisu:

```python
class NotesService:
    ...
    def count_notes(self, project: str | None = None) -> int:
        notes = self._filtered(project=project, participant=None)
        return len(notes)
```

Logika zależy tylko od portu (`NotesRepository`) — bez MCP, bez dysku wprost.

## 2. Test (najpierw albo zaraz po)

W `tests/core/test_services.py`, na atrapie z `tests/conftest.py`:

```python
def test_count_notes_filters_by_project(sample_notes):
    service = NotesService(FakeNotesRepository(sample_notes))
    assert service.count_notes(project="scada-integration") == 2
```

## 3. Wpis w katalogu narzędzi

> **Najpierw rozstrzygnij, dla KTÓRYCH drzwi piszesz — style są dwa i nie wolno ich mylić.**
>
> | Drzwi | Builder | Styl |
> |---|---|---|
> | MCP (sesja Claude Code) | `build_tool_catalog` | 1:1, opis = docstring — **ta instrukcja** |
> | runtime agenta (Teams) | `build_notes_catalog`, `build_github_catalog`, `build_jira_catalog`, `build_schedule_catalog` | `action=…`, opis w `Field(description=…)` — patrz §3b |
>
> Powierzchnia MCP jest **zamrożona** golden-testem i plikiem
> `tests/adapters/tool_surface_baseline.json`. Nowe narzędzie MCP wolno dołożyć wyłącznie
> ADDYTYWNIE. Powierzchnia agenta idzie w drugą stronę — konsoliduje się do pięciu narzędzi
> (ADR 0009 paczki wdrożeniowej), więc **nowe narzędzie agenta to prawie zawsze nowa AKCJA
> w istniejącym**, nie nowy `ToolSpec`.

Narzędzia MCP definiuje się w `src/workmate/core/application/tools.py`
(funkcja `build_tool_catalog`, [ADR 0008](../adr/0008-agent-runtime-and-tool-catalog.md)).
Adapter MCP jest cienki i sam się nie zmienia (`mcp/tools.py` tylko rejestruje `spec.fn`
na FastMCP).

Wewnątrz `build_tool_catalog` dodaj funkcję narzędzia i dopisz ją do listy `catalog`:

```python
    def count_notes(project: str | None = None) -> dict[str, Any]:
        """Policz notatki (opcjonalnie w jednym projekcie)."""

        def build() -> dict[str, Any]:
            return {"project": project, "count": notes.count_notes(project=project)}

        return _envelope(build)

    catalog.append(ToolSpec("count_notes", count_notes.__doc__ or "", count_notes))
```

Zasady:
- typuj parametry (schemat wejścia — FastMCP i adapter agenta — wywodzi się z sygnatury);
- **opis to docstring**, zwięzły, od słów kluczowych (Claude Code skraca do ~2 KB);
- owijaj ciało w `_envelope(build)` — łapie `RepositoryError` → `{"error": ...}`; nie łap wyjątków nieznanych;
- **zaktualizuj baseline** (`tests/adapters/tool_surface_baseline.json`) — wyłącznie o nowy wpis;
  zmiana albo usunięcie istniejącego to złamanie zamrożonego kontraktu, nie aktualizacja.

## 3b. Narzędzie dla runtime'u agenta — wzorzec `action=…`

Powierzchnia agenta jest budżetem wyboru, nie katalogiem zdolności: każde narzędzie kosztuje
schemat, opis do rozróżnienia i pozycję do rozważenia w każdej turze. Dlatego zdolności grupują
się w jedno narzędzie z polem `action`.

**Zanim dołożysz cokolwiek — sprawdź, czy powłoka `Bash` tego nie robi.** Kryterium z ADR 0009
to *bariera*, nie *temat*: narzędzie typowane powstaje tylko tam, gdzie powłoka w kontenerze
wykonawcy **nie może** dosięgnąć — brak sieci (Jira, GitHub, Shifts), brak wolumenu `workmate-state`
(`events.db`), brak drogi do kontekstu modelu (binaria), skutek poza kontenerem (dostawa, zapis
notatki). Opakowanie prymitywu kosztuje, a nie dodaje zdolności.

Pięć reguł wzorca — każda pochodzi ze znaleziska w przeglądzie, nie z upodobania:

1. **Bramka wchodzi do `Literal`, nie do ciała funkcji.** Przy wyłączonej zdolności wartość akcji
   ma NIE ISTNIEĆ w schemacie. Bramka sprawdzana dopiero w ciele wygląda w schemacie identycznie
   jak jej brak.
2. **Pola znikają razem z akcjami.** Domknięcie samego `enum` zostawia w schemacie pola odsyłające
   do akcji, których model nie ma — zmierzone: przeciekło 7 z 11. Przycinasz `__signature__`?
   `inspect.signature` potrzebuje `eval_str=True` (moduł ma `from __future__ import annotations`).
3. **Każde pole ma niepusty opis** (`Annotated[..., Field(description=…)]`). To jedyny powód, dla
   którego jedno grube narzędzie niesie tyle informacji, co kilka wąskich, które zastąpiło.
4. **Bramka i opis muszą mówić to samo.** Akapit opisu obiecujący akcję wchodzi WYŁĄCZNIE razem
   z jej wartością w `Literal`.
5. **Sprawdź, czy builder nie ma drugiego konsumenta.** Trzy razy w etapie 5 wspólny builder
   wyglądał jak miejsce do konsolidacji, a sprzęgał powierzchnię agenta z zamrożoną powierzchnią
   MCP. Właściwym ruchem było odcięcie agenta WŁASNYM builderem, nie zmiana wspólnego.

Walidacja „przy akcji X pole Y jest wymagane" nie da się wyrazić w JSON Schema — robi ją
dispatcher i zwraca `_brakuje_pol(...)`: `{status, error, tool, action, missing, hint}`.
Kształt jest strukturalny, żeby model poprawił wywołanie bez czytania schematu drugi raz.

Sondy obowiązkowe dla każdego nowego narzędzia agenta (wzór: `tests/core/test_jira_catalog.py`):
zamrożenie `input_schema` z niepustymi opisami pól, **sonda negatywna na bramkę** (przy wyłączonej
zdolności wartość akcji nie istnieje) i walidacja per akcja. Asercje idą przez WYRENDEROWANY
schemat (`_to_tool_def`), nie przez `__annotations__`.

## 4. Sprawdź

```bash
uv run pytest
uv run mcp dev src/workmate/server.py   # wywołaj count_notes ręcznie
```

## 5. Udokumentuj

Dopisz narzędzie do [`../reference/tools.md`](../reference/tools.md).
