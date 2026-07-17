# Changelog

Wszystkie istotne zmiany w projekcie WorkMate. Format oparty na
[Keep a Changelog](https://keepachangelog.com/pl/1.1.0/); wersjonowanie
[SemVer](https://semver.org/lang/pl/). Decyzje projektowe: [`docs/adr/`](docs/adr/).

## [1.0.0] — 2026-07-17

Pierwsze wydanie produkcyjne — Fazy 1–4 domknięte, most trójstronny zweryfikowany na żywo.

### Dodane

**Faza 1 — serwer MCP (baza wiedzy)**
- Serwer MCP `workmate` z 4 narzędziami odczytu (`search_notes`, `get_note`, `list_projects`,
  `get_project_status`) nad notatkami i statusem projektów (układ firma → projekt — ADR 0005).
- Zamrożony schemat notatki i kontrakt narzędzi (Bramka 1 — ADR 0003).
- Narzędzie zapisu `save_note` z profilem uprawnień per drzwi (Bramka 2 — ADR 0006).
- Wdrożenie sieciowe `streamable-http` z uwierzytelnianiem per osoba i drzwiami read-only
  (Bramka 3 — ADR 0007).

**Faza 2 — runtime agenta i drzwi**
- Runtime agenta w rdzeniu (`workmate-agent`) nad jednoźródłowym katalogiem narzędzi (ADR 0008).
- Drzwi Teams (Bot Framework — ADR 0009 i delegowany Microsoft Graph — ADR 0015), Telegram, CLI.
- Załączniki multimodalne na Teams: obrazy, PDF, dokumenty Office (ADR 0016).
- Bezstratna, wątkowa pamięć rozmów z kompaktowaniem historii i realnym rozliczaniem kosztów
  (ADR 0010–0014); read-only command dispatcher (ADR 0017); katalog roboczy agenta (ADR 0018).

**Fazy 3–4 — most GitHub ↔ EventStore ↔ Teams**
- Wspólny magazyn zdarzeń (append-only SQLite z deduplikacją — ADR 0019).
- Drzwi GitHub `workmate-github` (delegowany polling PAT — ADR 0020); ingest issue/PR/komentarzy/
  recenzji/CI białą listą pól (ADR 0024).
- Bramkowany zapis do GitHub (create-only, Bramka 4 — ADR 0021); proaktywny push do Teams
  (kanał + czat 1:1 — ADR 0022); dwukierunkowe wątki na kanale i deterministyczny auto-komentarz
  przy porażce CI (ADR 0024).
- Dwustronny strażnik pętli (self-skip konta PAT; echo `source="teams"`).

**Retrieval**
- Lokalny retrieval leksykalny notatek: BM25 nad lematami (polski `simplemma`) z fallbackiem
  podłańcuchowym; jakość pilnowana mikro-evalem (ADR 0023).

### Zaplanowane (proposed)
- Tworzenie notatek z Teams (ADR 0025), odpowiedź w wątku plikiem (ADR 0026), wychodzące
  pliki/zdjęcia do użytkownika (ADR 0027) — bramki domyślnie OFF; ADR 0026/0027 wymagają
  zgody admina na zakres zapisu Microsoft Graph.

[1.0.0]: https://github.com/BIAP-Inteligentne-Technologie/PIWorkmate/releases/tag/v1.0.0
