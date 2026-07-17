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

## 3. Wpis w jednoźródłowym katalogu narzędzi (ADR 0008)

Narzędzia definiuje się **raz** w `src/workmate/core/application/tools.py` (funkcja
`build_tool_catalog`) — drzwi MCP ORAZ runtime agenta dostają je z tego samego miejsca
([ADR 0008](../adr/0008-agent-runtime-and-tool-catalog.md)). Adapter MCP jest cienki i sam się nie
zmienia (`mcp/tools.py` tylko rejestruje `spec.fn` na FastMCP).

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
- **nie ruszaj powierzchni MCP 4+1** — narzędzia mostu/agenta wchodzą osobnymi builderami przez
  `extra_catalog` (patrz `build_workspace_catalog`, `build_events_catalog`), nie przez `build_tool_catalog`.

## 4. Sprawdź

```bash
uv run pytest
uv run mcp dev src/workmate/server.py   # wywołaj count_notes ręcznie
```

## 5. Udokumentuj

Dopisz narzędzie do [`../reference/tools.md`](../reference/tools.md).
