# Analiza luk wobec Roadmapy WorkMate V1 — plan na sesje

Data: 2026-07-27
Status: proposed
Autor: Patryk
Related to: [`roadmap.md`](roadmap.md), `roadmap_workmate.pdf` (Roadmap V1)

---

## Wniosek

Zdecydowana **większość celów Roadmapy V1 jest zrealizowana**, a projekt wyszedł poza V1
(most Jira, Telegram, cotygodniowe worklogi). Domknięte: **Faza 1**, **Faza 2 · M1/M2**,
**Faza 3 (GitHub)** — ta ostatnia rozszerzona o dwukierunkowy most PR/CI/review. Pozostają
pojedyncze elementy M3/M4 zależne od Azure, RAG semantyczny oraz push zdarzeń do sesji MCP.

> **Korekta 2026-07-27:** scope Graph `Files.ReadWrite.All` jest **nadany** (zgoda admina).
> Wcześniejszy zapis „zablokowane zgodą" był nieprawdziwy i został poprawiony w repo. Skutek dla
> planu: **ADR 0026/0027** (załączniki plikowe na Teams) i dostawa worklogu załącznikiem (0035/0038)
> **nie są już zablokowane** — pozostaje sam build `TeamsFileSender`. Dlatego przesunięto je z grupy
> „Azure-gated" do etapów wczesnych.

## Status celów V1

| Cel Roadmapy V1 | Status | Dowód w kodzie |
|---|---|---|
| Faza 1 — MCP read-only (4 narzędzia), schemat notatki, Bramki 1–3, wdrożenie HTTP | ✅ kod domknięty (deploy czeka) | `core/` + `adapters/inbound/mcp/`, ADR 0003/0006/0007 |
| Faza 2 · M1 — runtime agenta w rdzeniu | ✅ domknięte | `core/agent/`, ADR 0008/0010–0014 |
| Faza 2 · M2 — adapter Teams (@wzmianka → odpowiedź) | ✅ domknięte (delegowany Graph) | `adapters/inbound/teams_graph`, ADR 0015/0016 |
| Faza 2 · M3 — przepływ „nowa notatka ze spotkania" | 🟡 rdzeń zbudowany, integracja Graph = stub | `core/application/meeting_notes.py`; `GraphTranscriptSource` → `NotImplementedError`, brak podpięcia do drzwi |
| Faza 2 · M4 — async + mapowanie tożsamości Entra/AD + zapis bramkowany | 🟡 zapis ✅; async i tożsamość niezbudowane | `save_note` gated ✅; ADR 0009 §6 (świadomie odłożone) |
| Faza 3 — GitHub issues jako drzwi | ✅ domknięte i rozszerzone | `adapters/inbound/github`, ADR 0019–0024 |
| Faza 3 — lepszy retrieval / RAG (osadzenia + ranking) | 🟡 tylko leksykalny BM25 | ADR 0023; brak zależności vector/embedding |
| Faza 3 — push zdarzeń do sesji Claude Code (kanały MCP) | ✅ domknięte pull-em (push niedostępny) | `read_events_since` na drzwiach MCP (ADR 0040); research `docs/research/mcp-server-to-session-push.md` |

## Checklista luk (następne etapy)

### 🟢 Grupa A — możliwe od zaraz
- [x] **A1. RAG semantyczny** — kod gotowy (ADR-0039, Opcja A) + **bramka uruchomiona (2026-07-27): dense NIE przechodzi** (ΔnDCG@5 = −0.095 przy progu +0.03; lexical-PL na suficie — mrr/recall@10 = 1.0). Dense zostaje za flagą `enable_dense=False`; cel Fazy 3 „embeddings" **domknięty DOWODEM**. Rewizja, gdy korpus urośnie/zróżnicuje się.
- [x] **A2. Lokalny harness M3** — ZROBIONE (sesja 2, 2026-07-27): komenda `workmate-meeting`
  (`adapters/inbound/cli/meeting.py`) spina `InMemoryTranscriptSource` + `AnthropicMeetingSummarizer`
  + `MeetingNoteService` e2e na wklejonym transkrypcie. Domyślnie pisze do katalogu tymczasowego (nie
  `data/notes/`); firmę bierze z realnego rejestru; zapis create-only. Pokrywa poz. 7 live-smoke
  ([`how-to/meeting-note-harness.md`](how-to/meeting-note-harness.md)). Bramka: ruff+mypy czyste,
  pytest 1424 zielonych (+15). Realny przebieg wobec Claude = operator (🔑, poz. 7 checklisty).
- [x] **A3. Push zdarzeń do sesji Claude Code** — ZROBIONE (sesja 3, 2026-07-27) ścieżką **Research →
  ADR → build**. Ustalenie badawcze (`docs/research/mcp-server-to-session-push.md`): standardowy
  server-initiated push **nie dociera** do Claude Code (resource subscriptions #7252 „not planned";
  message/progress ignorowane), a proprietary „Channels" jest research-preview za flagą
  `--dangerously-load-development-channels`, at-most-once, non-portable — **nie fundament**. Dlatego
  **PULL**: narzędzie kursorowe `read_events_since` (bootstrap → najnowsze okno + `latest_cursor`;
  przyrostowo → `id > after_id`) wystawione WPROST na drzwi MCP przez `register_event_tools` (ADR
  0040). Read-only ⇒ bez bramki; kursor trzyma sesja. Zamrożone 4+1 nietknięte (golden-test rozbity
  na present/absent mostu, deterministyczny); baseline +1 narzędzie. Bramka: ruff+mypy czyste,
  pytest 1441 zielonych (+14).

### 🟢 Grupa A′ — odblokowane korektą Files.ReadWrite.All (2026-07-27)
- [x] **A′1. `TeamsFileSender`** — ZROBIONE (sesja 4, 2026-07-27): wspólny prymityw „wyjściowy
  załącznik Graph" — port rdzenia `core/ports/file_output.py` (`TeamsFileSender` + value object
  `UploadedFile`) + synchroniczny adapter `adapters/outbound/graph_file_sender.py`
  (`HttpxGraphFileSender`: `upload_channel_file` = `GET …/filesFolder` → `PUT …/content`;
  `post_reply_with_attachment` = POST reply z `attachments[]` typu `reference` wiązanym z treścią
  przez `<attachment id="GUID">`, GUID z `eTag`). Sync jak zapis GitHub Gate-4; polityka ponawiania
  jak `graph_teams_notifier` (429 zawsze; wysłanie odpowiedzi z plikiem NIGDY — duplikat załącznika;
  odczyt folderu i idempotentny upload — tak); 404 na root → `ThreadRootGone`. Testy na atrapie
  httpx + strukturalna atrapa portu w pamięci. **Świadomie POZA A′1** (idzie z konsumentami): scope
  `Files.ReadWrite.All` w `config.py`, bramka `enable_file_reply`, wiring w `teams_graph/app.py`,
  renderer (PDF = `fpdf2`, wybór z tej sesji). `@code-reviewer`: warunkowa akceptacja, brak
  CRITICAL/HIGH — zaadresowano 2× MEDIUM: referencja `UploadedFile` waliduje `webUrl` i GUID (z `eTag`)
  JUŻ przy wgraniu (pole `attachment_id` zamiast surowego `etag`), więc niżej nie da się zbudować
  martwego załącznika; usunięto cichy fallback GUID→`item_id`. Bramka: ruff+mypy czyste, pytest 1460
  zielonych (+19).
- [ ] **A′2. ADR 0026** — odpowiedź w wątku Teams plikiem (md/txt/pdf/docx), bramka `enable_file_reply`.
  Konsumuje `TeamsFileSender` (A′1) + nowy `DocumentRenderer` (md/txt czyste, docx→python-docx,
  pdf→`fpdf2` w extra) + `reply_with_file` przez `extra_catalog`/fabrykę per-turn + scope w config.
- [ ] **A′3. ADR 0027** — push plików/zdjęć do usera, bramka `enable_user_file_push`.
- [ ] **A′4. Dostawa worklogu załącznikiem** (0035/0038) zamiast fallbacku ścieżką.

### 🔵 Grupa B — zależne od Azure/M365 (inne scope'y niż Files.ReadWrite.All)
- [ ] **B1. M3 produkcyjnie** — `GraphTranscriptSource` (`OnlineMeetingTranscript.Read.All`) + podpięcie zapisu do drzwi Teams.
- [ ] **B2. M4 — mapowanie tożsamości** Entra/AD → jeden model uprawnień rdzenia.
- [ ] **B3. M4 — async „wrzuć-i-idź" + callback** do wątku Teams.

### 🟡 Grupa C — go-live / operacyjne (kod gotowy)
- [ ] **C1. Faza 1 — wdrożenie HTTP** (`tokens.json` z ACL + IIS + usługa Windows).
- [ ] **C2. Jira — live-smoke** (checklist poz. 11–15).
- [ ] **C3. Worklog — go-live** (potwierdzenie `WORKLOGPRO_HEADERS`, dry-run, `DRY_RUN=false`).

## Plan na sesje

| Sesja | Zakres | Złożoność | Start od |
|---|---|---|---|
| **1** | RAG semantyczny (A1) | MEDIUM/LARGE | `@architect` → ADR embeddings → zgoda → implementacja |
| **2** | Harness M3 lokalnie (A2) — ✅ zrobione 2026-07-27. Push do sesji MCP (A3) wydzielony do osobnej sesji (Research → ADR → build). | MEDIUM | A2: klocki gotowe → harness `workmate-meeting` |
| **3** | Zapis plików na Teams: `TeamsFileSender` + ADR 0026/0027 + worklog załącznikiem (A′) | MEDIUM/LARGE | odblokowane; build + live-smoke |
| **4** | Go-live operacyjne (C1 + C2 + C3) | operacyjna | konfiguracja + weryfikacja na żywo |
| **5** | Przygotowanie M4: `@architect` (tożsamość Entra/AD + async) + potwierdzenie scope `OnlineMeetingTranscript.Read.All` dla M3 | LARGE | ADR-y + lista scope'ów admina |
| **6** | M3/M4 produkcyjnie (B1 + B2 + B3) | LARGE | po odblokowaniu pozostałych scope'ów Azure |

**Uwaga:** „braki" M3-Graph/M4 to nie zaległości, lecz świadomie odłożona praca zależna od dostępu do
Azure/M365 (ADR 0009 §6). Wobec wszystkiego, co dało się zbudować bez tej infrastruktury, WorkMate jest
względem Roadmapy V1 domknięty.
