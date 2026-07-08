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

## 3. Opakowanie MCP (cienkie drzwi)

W `src/workmate/adapters/inbound/mcp/tools.py`, wewnątrz `register_tools`:

```python
    @mcp.tool()
    def count_notes(project: str | None = None) -> dict[str, Any]:
        """Policz notatki (opcjonalnie w jednym projekcie)."""
        try:
            total = notes.count_notes(project=project)
        except RepositoryError as exc:
            return {"error": str(exc)}
        return {"project": project, "count": total}
```

Zasady opakowania:
- typuj parametry (FastMCP generuje z nich schemat wejścia);
- opis zwięzły, od słów kluczowych (limit ~2 KB w Claude Code);
- łap `RepositoryError` i zwracaj `{"error": ...}`; nie łap wyjątków nieznanych.

## 4. Sprawdź

```bash
uv run pytest
uv run mcp dev src/workmate/server.py   # wywołaj count_notes ręcznie
```

## 5. Udokumentuj

Dopisz narzędzie do [`../reference/tools.md`](../reference/tools.md).
