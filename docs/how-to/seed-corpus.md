# How-to: zasilić korpus notatek z lokalnych dokumentów (W0)

`workmate-seed-corpus` importuje lokalne markdowny (README, ADR, inne `.md`) do korpusu notatek
`data/notes/<firma>/<projekt>/`, żeby świeży bot miał czego przeszukać (zimny start — zasila
wyszukiwanie, one-pager i „co się zmieniło"). Reużywa sankcjonowanej ścieżki zapisu
`save_note` — **nie** dokłada narzędzia mutującego, więc powierzchnia MCP i `NoteMetadata`
zostają nietknięte.

## Jak to działa

- **Tytuł** notatki = pierwszy nagłówek `# H1` dokumentu (fallback: nazwa pliku).
- **Data** = nagłówek `Date: RRRR-MM-DD` (jak w ADR); bez niego `--date` (domyślnie stała).
- **Prowenansja** w tagach: `seed`, rodzaj (`adr`/`readme`/`doc`), `src:<ścieżka>`.
- **Id deterministyczny** (`<firma>/<projekt>/<data>-<slug>`): powtórny przebieg wykrywa
  istniejącą notatkę i ją **pomija** (dedup) — bez duplikatów `-2`.

## Uruchomienie

Domyślnie **dry-run** — pokazuje, co powstałoby, nic nie zapisuje:

```powershell
uv run workmate-seed-corpus --source docs/adr --project workmate
```

Zapis dopiero z `--write`:

```powershell
uv run workmate-seed-corpus --source docs/adr --project workmate --write
```

Argumenty: `--source <katalog>` (wymagany), `--project <klucz z rejestru>` (wymagany, cel importu
— zaufany argument), `--date RRRR-MM-DD` (dla plików bez nagłówka `Date:`), `--glob` (domyślnie
`*.md`), `--recursive` (przeszukaj podkatalogi).

## ⚠️ Bramka przed zapisem do żywego korpusu

Dołożenie notatek zmienia ranking wyszukiwania, a to jest bramkowane mikro-evalem
(twarda reguła 9). **Przed `--write` na `data/notes` i po nim** uruchom:

```powershell
uv run --no-sync python eval/retrieval_eval.py
```

Jeśli metryki spadną — dopisz golden queries pokrywające nowe dokumenty (`eval/golden_queries.yaml`)
albo zaimportuj do osobnej firmy/projektu, żeby nie konkurować ze zbiorami `relevant`. Sekrety i
dane imienne nie należą do korpusu — importuj tylko dokumenty przeznaczone do współdzielenia.
