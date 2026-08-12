# Roadmapa WorkMate — „jesteśmy tu"

Ten plik streszcza fazy przedsięwzięcia i pokazuje, gdzie znajduje się kod w tym
repozytorium. Decyzje szczegółowe: [`adr/`](adr/) (0001–0065).

## Status faz

| Faza | Zakres | Status | Gdzie w kodzie |
|------|--------|--------|----------------|
| **Faza 1** | Serwer MCP tylko do odczytu dla Claude Code (4 narzędzia nad notatkami i statusem projektów). | 🟢 **Kod domknięty (3/3 bramki)** — pozostaje wdrożenie HTTP | `core/` + `adapters/inbound/mcp/` |
| **Faza 2** | Drzwi Teams + **runtime agenta** w rdzeniu (model w pętli). Te same narzędzia. | 🟢 **Domknięta** — M1 runtime (`core/agent/`, `workmate-agent`) + jednoźródłowy katalog narzędzi ([ADR 0008](adr/0008-agent-runtime-and-tool-catalog.md)); drzwi Teams/CLI na runtime agenta read-only; Teams delegowany przez polling Microsoft Graph z załącznikami multimodalnymi ([ADR 0015](adr/0015-teams-delegated-graph-polling.md)/[0016](adr/0016-user-multimodal-attachments.md)). Echo pozostaje fallbackiem transportu. | `core/agent/`, `adapters/inbound/{teams,teams_graph,cli}`, `adapters/outbound/anthropic_llm.py` |
| **Faza 3** | Dwukierunkowy most GitHub ↔ Teams przez wspólny magazyn zdarzeń (baza lokalna). Drzwi GitHub (polling PAT) → zdarzenia; notifier zdarzenie → Teams (1:1 + kanał); bramkowany zapis Teams → GitHub. Dalej: RAG. | 🟢 **Domknięta (Fazy 3–4)** — EventStore ([ADR 0019](adr/0019-shared-event-store.md)), drzwi GitHub polling ([ADR 0020](adr/0020-github-delegated-polling-door.md)), zapis GitHub Gate 4 ([ADR 0021](adr/0021-github-write-capability-gate-4.md)), push do Teams ([ADR 0022](adr/0022-proactive-dual-target-teams-push.md)); most PR/CI/review + dwukierunkowe wątki ([ADR 0024](adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md)); retrieval leksykalny notatek — BM25 nad lematami ([ADR 0023](adr/0023-hybrid-local-retrieval.md)). | `adapters/inbound/github/`, `adapters/outbound/{github_api,sqlite_events,graph_teams_notifier}.py`, `core/{domain,ports,application}` (events/github/notifier) |
| **Planowane** | Domknięcie luk multimodalnych/zapisu na drzwiach Teams: tworzenie notatek z Teams, odpowiedź w wątku plikiem (md/txt/pdf/docx), wychodzące pliki/zdjęcia do użytkownika. | 🟢 **Odpowiedź plikiem i push do usera domknięte** (`TeamsFileSender`/`graph_file_sender.py`, ADR 0026/0027) — **notatki z Teams pozostają proposed** (bramka `enable_notes_write` zaprojektowana w ADR 0025, niezaimplementowana; drzwi `teams_graph` mają dziś `enable_write=False` na sztywno) | [ADR 0025](adr/0025-teams-notes-write-gate.md) (notatki z Teams — proposed), [ADR 0026](adr/0026-agent-file-reply-in-thread.md) (odpowiedź plikiem w wątku — accepted), [ADR 0027](adr/0027-agent-outbound-file-push-to-user.md) (push plików/zdjęć do usera — accepted) |
| **Faza B** | Jira: **jedna, wyłącznie odczytowa** zdolność — "moje zadania" (lista otwartych zadań pytającego, zawężona server-side do jego konta). | 🟢 **Domknięta w kodzie** — fundament projekt↔repo↔Jira ([ADR 0028](adr/0028-project-repo-jira-mapping-and-event-dimension.md), zmodyfikowany), read port ([ADR 0030](adr/0030-jira-server-read-door.md), zmodyfikowany), "moje zadania" ([ADR 0054](adr/0054-reduce-jira-to-read-only-my-tasks.md)). **Zakres świadomie zredukowany (2026-07-30):** most push/ingest do EventStore, dwukierunkowy zapis (create/comment) i tranzycja statusu ([ADR 0031](adr/0031-jira-write-capability-gate-5.md)/[0032](adr/0032-jira-status-transition-capability.md)) USUNIĘTE z kodu — superseded przez ADR 0054. Powierzchnia: komenda Teams `/moje-zadania` + narzędzie MCP `get_my_jira_tasks` (serwer stdio, principal skonfigurowany). | `core/domain/jira_tasks.py`, `core/application/my_jira_tasks.py`, `adapters/outbound/{jira_api,jira_cloud_api}.py`, `core/ports/jira.py` |
| **Planowane — harness plików + baza mutowalna** | Narzędzie `File(read)` materializujące plik użytkownika do kontekstu (obraz/PDF/**HTML z anty-maskowaniem** — dziś nieobsługiwane) + komenda `workmate-extract`; **mutacja bazy wiedzy oceniana przez sędziego-Sonnet** (obrona w głębi na wierzchu autoryzacji AAD, backupu i schematu) zamiast twardej deny-listy — odwrócenie create-only ([ADR 0003](adr/0003-note-schema.md)) i „security by lack" ([ADR 0057](adr/0057-shell-executor-container-without-network.md)). | 🟠 **Projekt (proposed, ADR-first)** — decyzje właściciela 2026-08-12; ADR 0064/0065 `proposed`; **kod dopiero po akceptacji** i za wdrożeniem wykonawcy per rozmowa (§2, infra ADR 0012) + wspólnym wolumenem brudnopisu. | [ADR 0064](adr/0064-file-tool-and-model-initiated-materialization.md) (File + materializacja — proposed), [ADR 0065](adr/0065-mutable-knowledge-base-and-model-judged-writes.md) (baza mutowalna + sędzia — proposed) |

> 🧪 Weryfikacje wymagające żywego klucza / konta / infrastruktury (poza pakietem `pytest`)
> zebrane są w [`how-to/live-smoke-checklist.md`](how-to/live-smoke-checklist.md).

## Faza 1 — kamienie milowe (tyg. 1–4)

1. **Tydz. 1** — pierwsza pętla „Claude woła moje narzędzie".
2. **Tydz. 2 — Bramka 1** — kontrakt narzędzi + schemat notatki (→ [`reference/note-schema.md`](reference/note-schema.md), [ADR 0003](adr/0003-note-schema.md)).
3. **Tydz. 3 — Bramka 2** — granica uprawnień, sekrety, treść niezaufana; podstawowe testy.
4. **Tydz. 4 — Bramka 3** — wdrożenie HTTP (`streamable-http`), uwierzytelnianie per osoba, onboarding zespołu (→ [ADR 0007](adr/0007-gate-3-http-auth-deployment.md), zastępuje [ADR 0004](adr/0004-transport-stdio-then-http.md)).

## Trzy bramki decyzyjne (własność zespołu)

Bramki to punkty, w których zespół zatwierdza decyzje **zanim** kod ruszy dalej.
Struktura repo celowo zostawia na nie miejsce:

- **Bramka 1** — kontrakt narzędzi + schemat notatki. *(domknięta w danych i modelach — patrz ADR 0003)*
- **Bramka 2** — granica uprawnień, sekrety, treść niezaufana. *(otwarta: pierwsze narzędzie zapisu `save_note` z profilem uprawnień per drzwi — [ADR 0006](adr/0006-write-capability-gate-2.md); układ firma → projekt — [ADR 0005](adr/0005-company-project-note-layout.md))*
- **Bramka 3** — wdrożenie i dostęp. *(decyzja podjęta i kod domknięty — `streamable-http` na serwerze Windows za IIS, uwierzytelnianie per osoba tokenami self-managed, drzwi HTTP tylko do odczytu — [ADR 0007](adr/0007-gate-3-http-auth-deployment.md); `auth.py` + gałąź HTTP w `server.py`, testy zielone. Pozostaje samo wdrożenie: `tokens.json` z ACL, IIS (buffering off) + usługa Windows — patrz [how-to/deploy-http.md](how-to/deploy-http.md))*

## Zasady przekrojowe (obowiązują we wszystkich fazach)

- Wąskie, typowane narzędzia — żadnego „czytaj dowolny plik" ani powłoki.
- Sekrety poza zasięgiem rdzenia (menedżer sekretów, nie folder indeksowany).
- Treść niezaufana = dane, nie polecenia.
- Struktura przy zapisie, nie przy odczycie (stały schemat notatki).
- Profil uprawnień per drzwi (mniej zaufane drzwi = mocniejsze bramkowanie).
