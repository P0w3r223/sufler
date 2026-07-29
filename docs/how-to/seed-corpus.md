# How-to: zasilić korpus notatek z lokalnych dokumentów (W0)

`workmate-seed-corpus` importuje lokalne dokumenty do korpusu notatek
`data/notes/<firma>/<projekt>/`, żeby świeży bot miał czego przeszukać (zimny start — zasila
wyszukiwanie, one-pager i „co się zmieniło"). Reużywa sankcjonowanej ścieżki zapisu
`save_note` — **nie** dokłada narzędzia mutującego, więc powierzchnia MCP i `NoteMetadata`
zostają nietknięte.

## Formaty i instalacja

Obsługiwane rozszerzenia: `.md`, `.txt`, `.csv`, `.log`, `.json`, `.xml`, `.yaml`/`.yml`
(czytane jako tekst) oraz `.docx`, `.xlsx`, `.pptx`, `.pdf` (tekst **ekstrahowany** — te same
biblioteki co materializer załączników Teams, ADR 0016, patrz [ADR 0050](../adr/0050_seed_corpus_document_extraction.md)).
Pliki o innym rozszerzeniu i nieczytelne (uszkodzone/zaszyfrowane) są **pomijane z podaniem
powodu**, nie wywracają partii.

Ekstrakcja Office/PDF wymaga extra `seed`:

```powershell
uv sync --extra seed
```

### Źródło z SharePointa

Importer **nie sięga sam do sieci**. Żeby zasilić korpus z SharePointa: zsynchronizuj bibliotekę
lokalnie (OneDrive → „Synchronizuj") albo pobierz folder, po czym wskaż ten katalog przez
`--source`. Import robi człowiek (operator), świadomie, po bramce mikro-eval (niżej).

## Jak to działa

- **Tytuł** notatki = pierwszy nagłówek `# H1` dokumentu (fallback: nazwa pliku — dla docx/pdf
  bez H1 tytuł bierze się z nazwy pliku).
- **Data** = nagłówek `Date: RRRR-MM-DD` (jak w ADR); bez niego `--date` (domyślnie stała).
- **Prowenansja** w tagach: `seed`, rodzaj (`adr`/`readme`/`doc`), `src:<ścieżka>`.
- **Id deterministyczny** (`<firma>/<projekt>/<data>-<slug>`): powtórny przebieg wykrywa
  istniejącą notatkę i ją **pomija** (dedup) — bez duplikatów `-2`.

## Uruchomienie

Domyślnie **dry-run** — pokazuje, co powstałoby (i co pominięto), nic nie zapisuje:

```powershell
uv run workmate-seed-corpus --source "C:/Users/…/SharePoint-sync/Notatki" --project workmate --recursive
```

Zapis dopiero z `--write`:

```powershell
uv run workmate-seed-corpus --source docs/adr --project workmate --write
```

Argumenty: `--source <katalog>` (wymagany), `--project <klucz z rejestru>` (wymagany, cel importu
— zaufany argument), `--date RRRR-MM-DD` (dla plików bez nagłówka `Date:`), `--glob` (domyślnie
`*` — filtrowane po obsługiwanych rozszerzeniach), `--recursive` (przeszukaj podkatalogi).

## ⚠️ Bramka przed zapisem do żywego korpusu

Dołożenie notatek zmienia ranking wyszukiwania, a to jest bramkowane mikro-evalem
(twarda reguła 9). **Przed `--write` na `data/notes` i po nim** uruchom:

```powershell
uv run --no-sync python eval/retrieval_eval.py
```

Jeśli metryki spadną — dopisz golden queries pokrywające nowe dokumenty (`eval/golden_queries.yaml`)
albo zaimportuj do osobnej firmy/projektu, żeby nie konkurować ze zbiorami `relevant`. Sekrety i
dane imienne nie należą do korpusu — importuj tylko dokumenty przeznaczone do współdzielenia.
