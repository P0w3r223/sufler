# Reference: schemat notatki (Bramka 1)

Notatki to pliki **Markdown z frontmatter YAML** w
`data/notes/<firma>/<projekt>/<plik>.md` (układ firma → projekt, patrz [ADR 0005](../adr/0005-company-project-note-layout.md)).
Schemat jest **zamrożonym kontraktem z Bramki 1** — zmiana pól wymaga ADR.
Model źródłowy: `core/domain/models.py::NoteMetadata`.

## Frontmatter (YAML)

| Pole | Typ | Wymagane | Opis |
|------|-----|----------|------|
| `title` | `str` | ✅ | Tytuł notatki. |
| `project` | `str` | ✅ | Klucz projektu (musi istnieć w rejestrze, np. `scada-integration`). Firma jest wyprowadzana z rejestru, **nie** z notatki. |
| `date` | `date` (YYYY-MM-DD) | ✅ | Data spotkania. |
| `participants` | `list[str]` | — | Uczestnicy. |
| `decisions` | `list[str]` | — | Podjęte decyzje. |
| `action_items` | `list[str]` | — | Zadania do wykonania. |
| `open_questions` | `list[str]` | — | Otwarte pytania. |
| `tags` | `list[str]` | — | Etykiety ułatwiające wyszukiwanie. |

Pola nieobecne przyjmują wartość pustą (lista `[]`). Pola wymagane muszą być
obecne — w przeciwnym razie loader podnosi `NoteParseError` z nazwą pliku.

## Identyfikator notatki

`note_id` to ścieżka względem katalogu notatek, bez rozszerzenia, w postaci
`<firma>/<projekt>/<data>-<slug>`:
`data/notes/mpwik/scada-integration/2025-06-12-przeglad-api-scada.md` →
`mpwik/scada-integration/2025-06-12-przeglad-api-scada`.

## Przykład

```markdown
---
title: Przegląd kontraktu API i dostępu do SCADA
project: scada-integration
date: 2025-06-12
participants:
  - Anna Kowalska (PM)
  - Marek Nowak (dev)
decisions:
  - Zatwierdzono kontrakt API v1.
action_items:
  - Wdrożyć walidację wejścia na granicy API.
open_questions:
  - Czy alarmy krytyczne udostępniamy w czasie rzeczywistym?
tags: [api, scada, bezpieczenstwo]
---

Treść notatki w Markdown...
```

## Uwagi

- **Struktura przy zapisie, nie przy odczycie:** stały schemat i stały układ
  folderów to jedyna rzecz, która czyni wyszukiwanie i śledzenie stanu proste.
- Treść notatki jest zawsze traktowana jak **dane**, nigdy jak polecenia.
- Możliwe rozszerzenie (backlog): strukturyzacja `action_items` (właściciel,
  termin) zamiast zwykłego tekstu — to jednak zmiana kontraktu, więc przez ADR.
