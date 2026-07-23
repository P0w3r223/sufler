# claude_summary — plan i status

Samodzielny pod-projekt uv wewnątrz WorkMate. Cel: dostarczyć większemu agentowi
(ścieżka worklog/Jira WorkMate — ADR 0034/0035) dzienny materiał o pracy osoby, łącząc
historię promptów Claude Code z historią commitów.

## Status (v0.1.0 — pierwsza działająca wersja)

Zrobione:
- Rdzeń (czysty, testowalny): modele, dyskryminator promptów, grupowanie po dniu (UTC→lokalna),
  render JSON + Markdown.
- Adaptery: odczyt transkryptów `~/.claude/projects` z korelacją repo↔sesja (nazwa folderu +
  filtr `cwd`), `git log` (filtr autora + zakres), opcjonalna warstwa LLM (Claude API, leniwy import).
- Redakcja treści wrażliwej (zawsze włączona, na granicy parsowania — ADR 0003): sekrety/konta/IP/
  ID/ścieżki w miejscu, przycięcie wklejek, pełne pominięcie czystych zrzutów; audyt w JSON/Markdown.
- CLI `claude-summary` z twardą bramką zgody (fail-closed), zakresem dat i formatami wyjścia.
- Testy jednostkowe rdzenia i adapterów + integracja bramki zgody i pełnego biegu bez repo.

## Decyzje

- **Tryb podsumowania:** dane strukturalne + opcjonalna warstwa LLM (`--llm`).
- **Źródło promptów:** auto z repo (folder o pasującej nazwie + prompty z `cwd` w repo),
  fallback bez repo → wszystkie foldery.
- **Commity:** filtr po autorze (domyślnie `git config user.email`).
- **Zgoda:** twarda bramka `--consent` / `CLAUDE_SUMMARY_CONSENT=1`.

## Następne kroki (poza v0.1.0)

- Integracja wyniku z drzwiami `workmate-worklogi` / propozycją czasu (`propose_worklog`).
- Wsparcie wielu osób i mapa tożsamości (git author ↔ konto Jira) — dziś jedna osoba.
- Ewentualne źródło `~/.claude/history.jsonl` jako uzupełnienie (dziś: transkrypty projektowe).
