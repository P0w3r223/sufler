# How-to: lokalny harness „notatka ze spotkania" (M3)

Harness `workmate-meeting` uruchamia CAŁY przepływ M3 (Faza 2 / [ADR 0009](../adr/0009-meeting-note-flow-and-write-surface.md))
end-to-end LOKALNIE, bez Azure/Graph: **wklejony transkrypt → streszczenie przez Claude
(`AnthropicMeetingSummarizer`) → złożenie `NoteMetadata` (zamrożony schemat) → zapis przez
bramkowany, dopisujący `NotesWriteService`**. Źródłem transkryptu jest `InMemoryTranscriptSource`;
realny `GraphTranscriptSource` jest odłożony do dostępu Azure/M365 (świadomy stub). To domyka
follow-up z ADR 0009 — możliwość przejścia M3 wobec Claude na wklejonym transkrypcie.

## Wymagania

- 🔑 klucz Claude API w środowisku/`.env`: `ANTHROPIC_API_KEY` (lub `WORKMATE_AGENT_API_KEY`).
- extra `agent`: `uv sync --extra agent` (import `anthropic` jest leniwy — bez extra harness kończy
  czytelnym komunikatem, nie tracebackiem).
- Projekt istnieje w rejestrze (`data/projects/registry.yaml`) — z niego brana jest firma do ścieżki.

## Uruchomienie

Transkrypt z pliku:

```
uv run workmate-meeting --project scada-integration --date 2026-07-20 --transcript spotkanie.txt
```

Transkrypt z potoku (stdin):

```
echo "…treść transkryptu…" | uv run workmate-meeting --project scada-integration --date 2026-07-20
```

Flagi:

| Flaga | Znaczenie |
|-------|-----------|
| `--project` (wymagana) | Klucz projektu z rejestru — **wyznacza miejsce zapisu** (firma z rejestru). |
| `--date` (wymagana) | Data spotkania `YYYY-MM-DD` — **decyduje wywołujący**, nie transkrypt. |
| `--transcript PATH` | Plik z transkryptem (UTF-8). Bez tej flagi transkrypt czytany z stdin. |
| `--meeting-ref` | Etykieta spotkania (domyślnie `harness-meeting`). |
| `--out DIR` | Katalog docelowy. Domyślnie katalog **tymczasowy** harnessu. |

## Bezpieczeństwo i konwencje (dlaczego tak)

- **O miejscu zapisu decyduje wywołujący, nie treść transkryptu** (ADR 0009 §3). `project`/`date`
  pochodzą z flag; transkrypt to dane niezaufane i nie może przekierować notatki do cudzego projektu.
- **Domyślnie NIE zapisuje do `data/notes/`.** Wynik ląduje w katalogu tymczasowym harnessu, więc
  powtarzane przebiegi nie zaśmiecają wspólnej bazy wiedzy. Firmę projektu rozwiązuje jednak **realny
  rejestr** (semantyka produkcyjna). Zapis do bazy = świadome `--out data/notes`.
- **Zapis create-only** (jak każdy `save_note`, [ADR 0006](../adr/0006-write-capability-gate-2.md)):
  kolizja tytuł/data/projekt → sufiks `-2`, `-3`… Nic nie jest nadpisywane.

## Wynik

Harness kończy zwięzłym raportem:

```
✓ Notatka M3 złożona i zapisana (harness lokalny, ADR 0009)
  id:          mpwik/scada-integration/2026-07-20-<slug tytułu>
  plik:        <katalog>/<id>.md  (katalog tymczasowy harnessu)
  tytuł:       …
  uczestnicy:  …
  decyzje:     N · action items: M · pytania: K · tagi: …
```

Ten harness pokrywa pozycję **7** z [`live-smoke-checklist.md`](live-smoke-checklist.md)
(summarizer M3 — jakość i poprawność JSON). Odnotuj wynik przebiegu w briefie sesji.
