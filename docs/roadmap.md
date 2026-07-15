# Roadmapa WorkMate — „jesteśmy tu"

Pełna mapa drogowa przedsięwzięcia to `roadmap_workmate.pdf` (poziom pionu).
Ten plik streszcza fazy i pokazuje, gdzie znajduje się kod w tym repozytorium.

## Status faz

| Faza | Zakres | Status | Gdzie w kodzie |
|------|--------|--------|----------------|
| **Faza 1** | Serwer MCP tylko do odczytu dla Claude Code (4 narzędzia nad notatkami i statusem projektów). | 🟢 **Kod domknięty (3/3 bramki)** — pozostaje wdrożenie HTTP | `core/` + `adapters/inbound/mcp/` |
| **Faza 2** | Drzwi Teams + **runtime agenta** w rdzeniu (model w pętli). Te same narzędzia. | 🔄 **W toku** — M1 runtime (`core/agent/`, `workmate-agent`) + jednoźródłowy katalog narzędzi ([ADR 0008](adr/0008-agent-runtime-and-tool-catalog.md)); drzwi Teams/Telegram/CLI na runtime agenta read-only; Teams delegowany przez polling Microsoft Graph z załącznikami multimodalnymi ([ADR 0015](adr/0015-teams-delegated-graph-polling.md)/[0016](adr/0016-user-multimodal-attachments.md)). Echo pozostaje fallbackiem transportu. | `core/agent/`, `adapters/inbound/{teams,teams_graph,telegram,cli}`, `adapters/outbound/anthropic_llm.py` |
| **Faza 3** | Drzwi GitHub Issues, lepszy retrieval / RAG, push zdarzeń. | ⏳ Później | `adapters/github/` (stub) |

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
