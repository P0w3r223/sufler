# Analiza luk wobec Roadmapy Sufler V1 — plan na sesje

Data: 2026-07-27
Status: proposed
Autor: P0w3r223
Related to: [`roadmap.md`](roadmap.md), `roadmap_sufler.pdf` (Roadmap V1)

---

## Wniosek

Zdecydowana **większość celów Roadmapy V1 jest zrealizowana**, a projekt wyszedł poza V1
(Telegram; historycznie też most Jira i cotygodniowe worklogi — oba **wycofane 2026-07-30**,
patrz Update D1/D2 niżej). Domknięte: **Faza 1**, **Faza 2 · M1/M2**,
**Faza 3 (GitHub)** — ta ostatnia rozszerzona o dwukierunkowy most PR/CI/review. Pozostają
pojedyncze elementy M3/M4 zależne od Azure, RAG semantyczny oraz push zdarzeń do sesji MCP.

> **Korekta 2026-07-27:** scope Graph `Files.ReadWrite.All` jest **nadany** (zgoda admina).
> Wcześniejszy zapis „zablokowane zgodą" był nieprawdziwy i został poprawiony w repo. Skutek dla
> planu: **ADR 0026/0027** (załączniki plikowe na Teams) i dostawa worklogu załącznikiem (0035/0038)
> **nie są już zablokowane** — pozostaje sam build `TeamsFileSender`. Dlatego przesunięto je z grupy
> „Azure-gated" do etapów wczesnych.

> **Update (2026-07-30, D1/D2 scope change):** dwie wiążące decyzje zredukowały zakres opisany
> niżej. **D1 (ADR 0054):** Jira zredukowana do JEDNEJ, wyłącznie odczytowej zdolności „moje
> zadania" — poller, push Jira→Teams, most Teams↔Jira i całe pisanie (create/comment/transition)
> **usunięte w całości** (supersedes ADR 0031/0032). **D2 (ADR 0055):** karty czasu WorklogPRO
> (cotygodniowe arkusze + self-service) **wycofane z projektu w całości** — decyzja trwała, nie
> pauza (supersedes ADR 0035/0036/0037/0038; nie dotyczy ADR 0034 — `propose_worklog` zostaje bez
> zmian). Pozycje **C2** i **C3** niżej opisują sesje SPRZED tej decyzji (2026-07-28) — dokumentują,
> co zostało zbudowane i zwalidowane na żywo PRZED wycofaniem; nie traktuj ich jako otwartych zadań
> do dokończenia. Adnotacje „SUPERSEDED (D1/D2)" przy poszczególnych pozycjach wskazują, co
> dokładnie straciło aktualność.

## Status celów V1

| Cel Roadmapy V1 | Status | Dowód w kodzie |
|---|---|---|
| Faza 1 — MCP read-only (4 narzędzia), schemat notatki, Bramki 1–3, wdrożenie HTTP | ✅ kod domknięty (deploy czeka) | `core/` + `adapters/inbound/mcp/`, ADR 0003/0006/0007 |
| Faza 2 · M1 — runtime agenta w rdzeniu | ✅ domknięte | `core/agent/`, ADR 0008/0010–0014 |
| Faza 2 · M2 — adapter Teams (@wzmianka → odpowiedź) | ✅ domknięte (delegowany Graph) | `adapters/inbound/teams_graph`, ADR 0015/0016 |
| Faza 2 · M3 — przepływ „nowa notatka ze spotkania" | ✅ kod kompletny (2026-07-28), gated OFF; live-smoke zaparkowany (brak realnego id spotkania) | `core/application/meeting_notes.py`; realny `HttpxGraphTranscriptSource` + `/notatka` (ADR 0041), bramki OFF |
| Faza 2 · M4 — async + mapowanie tożsamości Entra/AD + zapis bramkowany | ✅ kod kompletny (2026-07-28), gated OFF: zapis + autoryzacja nadawcy (ADR 0042) + async (ADR 0043) | `save_note` gated ✅; tożsamość — `core/domain/identity.py` (fail-closed; skonsolidowana z timesheet.py 2026-07-30, ADR 0055) |
| Faza 3 — GitHub issues jako drzwi | ✅ domknięte i rozszerzone | `adapters/inbound/github`, ADR 0019–0024 |
| Faza 3 — lepszy retrieval / RAG (osadzenia + ranking) | 🟡 tylko leksykalny BM25 | ADR 0023; brak zależności vector/embedding |
| Faza 3 — push zdarzeń do sesji Claude Code (kanały MCP) | ✅ domknięte pull-em (push niedostępny) | `read_events_since` na drzwiach MCP (ADR 0040); research `docs/research/mcp-server-to-session-push.md` |

## Checklista luk (następne etapy)

### 🟢 Grupa A — możliwe od zaraz
- [x] **A1. RAG semantyczny** — kod gotowy (ADR-0039, Opcja A) + **bramka uruchomiona (2026-07-27): dense NIE przechodzi** (ΔnDCG@5 = −0.095 przy progu +0.03; lexical-PL na suficie — mrr/recall@10 = 1.0). Dense zostaje za flagą `enable_dense=False`; cel Fazy 3 „embeddings" **domknięty DOWODEM**. Rewizja, gdy korpus urośnie/zróżnicuje się.
- [x] **A2. Lokalny harness M3** — ZROBIONE (sesja 2, 2026-07-27): komenda `sufler-meeting`
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
- [x] **A′2. ADR 0026** — ZROBIONE (2026-07-27): odpowiedź w wątku Teams plikiem (md/txt/pdf/docx),
  bramka `enable_file_reply` (OFF). Konsumuje `TeamsFileSender` (A′1) + nowy `DocumentRenderer`
  (`core/ports/document.py` + `DefaultDocumentRenderer`: md/txt czyste, docx→python-docx,
  pdf→`fpdf2` z osadzonym fontem Unicode DejaVu — wbudowana Helvetica koduje tylko latin-1, wywróciłaby
  się na polskich znakach). Narzędzie `reply_with_file(content, file_format, filename)` przez
  per-turową fabrykę złożoną z fabryką GitHub (`_compose_thread_factories`); cel `team/channel/root`
  PRE-ZWIĄZANY z `external_id` wątku (nie od modelu); strażniki formatu/rozmiaru/pustej treści →
  `{"error": ...}` (degradacja do tekstu); podpis HTML składany i escapowany w rdzeniu. Scope
  `Files.ReadWrite.All` walidowany fail-fast przy włączonej bramce + limit `max_file_reply_kb`. Nowy
  extra `file-reply` (`fpdf2`); nazwy plików adresowane treścią (idempotencja retry bez nadpisywania
  cudzych). Bramka: ruff+mypy czyste, pytest 1492 (+32); `@code-reviewer` bez CRIT/HIGH (2×MEDIUM
  naprawione). Realny upload = operator (ponowna zgoda device-code na zakres zapisu).
- [x] **A′3. ADR 0027 (wariant obrazowy)** — ZROBIONE (2026-07-27): agent Teams odsyła OBRAZ
  rozmówcy 1:1, narzędzie `send_image_to_user`, bramka `enable_user_file_push` (OFF). Obraz idzie
  INLINE przez `hostedContents` czatu (bez dysku SharePoint → bez `Files.*`), sync port
  `UserImageSender` (`core/ports/user_push.py`) + adapter `HttpxGraphUserImagePush` (spójnie z A′2,
  bo narzędzia agenta biegną synchronicznie — ODCHYLENIE od litery ADR, która mówiła o async
  notifierze). Cel PRE-ZWIĄZANY z `sender_id` bieżącej wiadomości (osobna fabryka per turę, klucz =
  nadawca; szew: `InboundMessage.sender_id` addytywne, wypełniane w `handler.py`) — model nie podaje
  odbiorcy (anty-eksfiltracja/spam). **Korekta ADR:** „obrazy = bez nowego zakresu" prawdziwe tylko
  dla mechanizmu hostedContents; dostawa 1:1 wymaga zakresów czatu (`Chat.Create`/`ChatMessage.Send`)
  — JUŻ skonsentowanych przez admina (Powiadomienia_teams), brak tylko na tokenie tych drzwi ⇒
  walidacja fail-fast + ponowna zgoda device-code (bez nowej zgody admina). Golden MCP nietknięty
  (narzędzie per-turową fabryką). Bramka: ruff+mypy czyste, pytest zielony. Realny push = operator.
- [x] **A′3-plik. Wariant PLIKOWY** — ZROBIONE (2026-07-27): agent odsyła rozmówcy 1:1 DOKUMENT
  (md/txt/pdf/docx), narzędzie `send_document_to_user`, OSOBNA bramka `enable_user_doc_push` (OFF).
  Czat 1:1 nie ma dysku kanału → plik ląduje na OneDrive bota: nowy sync port `UserDocSender`
  (`core/ports/user_doc_push.py`) + adapter `HttpxGraphUserDocPush` (`PUT /me/drive/root:/…:/content`
  → `POST …/invite` UDZIELA odbiorcy dostępu — bez tego karta pliku nieotwieralna → `POST /chats` →
  `POST …/messages` z załącznikiem `reference`). To NIE `TeamsFileSender` (tamten wgrywa na dysk
  KANAŁU) — korekta „reuse TeamsFileSender" z ADR 0027. Renderowanie reużywa `DocumentRenderer` (A′2),
  więc narzędzie = lustro `reply_with_file` dla dostawy 1:1. Bramka SZERSZA niż obrazowa: chat scopes
  + `Files.ReadWrite.All` (osobno, by least-privilege — obraz idzie inline bez `Files.*`). Fabryki
  push-u (obraz+dokument) kluczowane `sender_id`, złożone w jedną. Golden MCP nietknięty. Research:
  `docs/research/graph-1to1-chat-file-attachment.md`. Bramka: ruff+mypy czyste, pytest 226 (zakres
  celowo zawężony) — +~45. ADR 0027 → **accepted (images + file delivered)**. Realny push = operator
  (device-code na czat + upload; live-smoke: `webUrl` vs `webDavUrl`, org-link vs `invite`).
- [x] **A′4. Dostawa worklogu załącznikiem** (0035/0038) — **SUPERSEDED (D2, ADR 0055, 2026-07-30):**
  oba drzwi worklogów opisane niżej ZOSTAŁY WYCOFANE z projektu w całości — pozycja dokumentuje
  historyczny build (2026-07-28), nie stan bieżący. ZROBIONE (2026-07-28): oba drzwi worklogów
  (wsadowe ADR 0035 + self-service ADR 0038) odsyłają arkusz `.xlsx` realnym ZAŁĄCZNIKIEM zamiast
  ścieżki tekstem, bramka `SUFLER_WORKLOGI_ENABLE_ATTACHMENT` (OFF). Reużywa `UserDocSender`
  (ADR 0027, wariant plikowy — NIE `TeamsFileSender`, bo czat 1:1 nie ma dysku kanału → OneDrive
  bota); port dostał opcjonalny `caption_html`, więc bogata treść `render_timesheet_message` jedzie
  RAZEM z kartą pliku. Rdzeń bez I/O (wstrzykiwany `send_document`/`deliver_as_attachment`; bajta
  czyta adapter). Bezpośrednie wgranie xlsx (openpyxl już daje bajta) — bez rozszerzania
  `FILE_REPLY_FORMATS`. Least-privilege: `Files.ReadWrite[.All]` w scope'ach push, fail-fast w
  wiringu. `@code-reviewer` ✅ approve (brak CRIT/HIGH): MEDIUM (duplikacja dostawy) → wspólny moduł
  `attachment_delivery.py`; LOW (spójność trybu self-service) naprawiony. Bramka: ruff+mypy czyste,
  pytest 291 (3 pliki testowe etapu aktywowane w conftest). Realny push = operator (device-code na
  scope plików; live-smoke: `Files.ReadWrite` vs `invite`). **Grupa A′ domknięta.**

### 🔵 Grupa B — zależne od Azure/M365 (inne scope'y niż Files.ReadWrite.All)
- [~] **B1. M3 produkcyjnie — KOD KOMPLETNY (2026-07-28), gated OFF. Zgoda admina NADANA; live-smoke ZAPARKOWANY (brak realnego id spotkania z transkryptem).**
  - **Odczyt transkryptu:** realny `HttpxGraphTranscriptSource` (`adapters/outbound/transcript_sources.py`)
    zastąpił stub: `joinWebUrl` (`GET /me/onlineMeetings?$filter=JoinWebUrl eq …`) → najnowszy
    transkrypt → WebVTT (`…/content?$format=text/vtt`) → czysty tekst (`vtt_to_text`, zachowuje
    mówców). Bramka `enable_meeting_transcript` (OFF) + fail-fast walidacja zakresów.
  - **Podpięcie zapisu do drzwi Teams (decyzja Gate-2, ADR 0041 proposed):** komenda `/notatka <ref>
    | <projekt> | <RRRR-MM-DD>` przez NOWY `MeetingNoteRouter` (`adapters/inbound/meeting_command.py`),
    konsultowany przez respondera OBOK read-only `CommandRouter` (ADR 0017 zostaje read-only). Zapis
    przez create-only `save_note` do `data/notes/`; `project`/`data`/`ref` z ZAUFANYCH argumentów, nie
    z transkryptu (ADR 0009 §3). OSOBNA bramka `enable_meeting_note_write` (OFF), wymaga transkryptu.
  - Weryfikacja operatorska = jedno polecenie `sufler-meeting --source graph --meeting <ref>` (CLI)
    albo komenda `/notatka` na kanale (po włączeniu bramek). Testy: `httpx.MockTransport` + router na
    atrapach + gating (pytest **323**). ruff/mypy czyste; golden MCP nietknięty.
  - **Zakresy `OnlineMeetingTranscript.Read.All` + `OnlineMeetings.Read` NADANE przez admina (2026-07-28).**
    Blok `SUFLER_TEAMS_GRAPH_*` przygotowany w `.env` (flaga OFF), device-code przeszło (token niesie nowe
    zakresy). Live-smoke NIEDOKOŃCZONY: brak realnego joinWebUrl/id spotkania z transkryptem — próba na
    placeholderze dała Graph HTTP 400 (NIE dowód braku uprawnień). Procedura:
    [`how-to/meeting-transcript-live-smoke.md`](how-to/meeting-transcript-live-smoke.md).
    **ADR 0041 pozostaje `proposed`** dopóki operator/zespół nie zaakceptuje włączenia zapisu z drzwi.
- [x] **B2. M4 — autoryzacja nadawcy `/notatka`** (bramka członkostwa, ADR 0042) — **KOD KOMPLETNY, gated OFF.**
  Za `@architect` (rekomendacja B2-A). Zamyka ryzyko KRYTYCZNE: przy włączonym zapisie każdy nadawca
  pisał do dowolnego projektu. Teraz AAD `sender_id` → członek pionu (port `AadIdentityLookup`, reużycie
  katalogu tożsamości worklogów, fail-closed) autoryzowany PRZED pobraniem transkryptu; nieznany → odmowa.
  Czysta decyzja w rdzeniu (`core/domain/authorization.py`), serwis `MeetingNoteAuthorizer`, `Actor`
  z szwem `project` pod B2-B. **Autoryzacja wbudowana w bramkę zapisu:** `enable_meeting_note_write=true`
  wymaga `SUFLER_TEAMS_GRAPH_IDENTITIES` (fail-fast). Testy: authz rdzenia + router + gating (pytest **332**).
- [x] **B3. M4 — async + idempotencja** (ADR 0043 `proposed`) — **KOMPLETNE w kodzie, gated OFF.** **Część 1 (idempotencja) zielona:**
  deterministyczny `note_id` = `<firma>/<projekt>/<data>-mtg-<hash(meeting_ref)>` (`core/domain/paths.py`),
  `NotesWriteService.save_meeting_note` (create-only na stałym id) + `meeting_note_id` (pre-check bez
  kosztu Claude); `note_from_meeting` zwraca `MeetingNoteOutcome` (utworzona / już była). Zamyka ryzyko
  KRYTYCZNE duplikatu przy crash-retry (create-only FS → kolizja zamiast `-2`). Testy: paths + write-service
  + meeting_notes + CLI + router (pytest gate **335**; pliki denylist idempotencji **51**).
  **Część 2 (async fire-and-forget + callback) KOMPLETNA, zielona:** router z bounded
  `ThreadPoolExecutor` → ACK natychmiast → łańcuch w tle → sync poster (`HttpxGraphThreadReplyPoster`)
  wyniku do `team/channel/root` (bez pętli asyncio). Autoryzacja zostaje SYNC przed zleceniem; błędy w
  tle zawsze logowane/odsyłane (nie giną). Bramka `enable_meeting_note_async` (OFF) wymaga bramki zapisu
  + `workers≥1` (fail-fast). Testy: router async + poster (MockTransport) + config + wiring (gate **348**).
  Zamyka ryzyko HIGH (stall pollera). **B3 KOMPLETNE w kodzie, gated OFF — ADR 0043 proposed.**

### 🟡 Grupa C — go-live / operacyjne (kod gotowy)
- [~] **C1. Faza 1 — wdrożenie HTTP — SZKIELET GOTOWY (2026-07-28), czeka na serwer Windows.**
  Stos serwujący był już domknięty (`config.py` + `adapters/inbound/mcp/auth.py` + gałąź uvicorn/TLS w
  `server.py` + [`deploy-http.md`](how-to/deploy-http.md)); dołożono **działający szkielet wdrożeniowy**
  `deploy/http/`: `manage_tokens.py` (CLI `issue/revoke/list/verify` — hash IDENTYCZNY jak weryfikator
  drzwi, atomowy zapis, rewalidacja przez `TokenVerifier.from_file`, guard „poza data/" PRZED zapisem,
  rotacja po prefiksie hasha), `tokens.example.json`, `web.config.sample` (IIS ARR + `responseBufferLimit=0`
  na SSE), `install-service.ps1` (NSSM, dry-run), `smoke-transport.ps1` (401/401/nie-401/421 z
  `Accept: text/event-stream`), `README.md`. Testy `tests/deploy/test_manage_tokens.py` (9): round-trip
  token→weryfikator. Bramka: ruff+mypy czyste, pytest **358**. `@code-reviewer`: brak CRIT/HIGH, 5×MEDIUM
  + 3×LOW naprawione. **Część tokenowa uruchamialna offline już teraz;** do zrobienia na serwerze: IIS +
  usługa Windows + ACL NTFS na `tokens.json` + live smoke transportu (M4/M5 = jawne TODO w README).
- [~] **C2. Jira — live-smoke — SUPERSEDED (D1, ADR 0054, 2026-07-30) w części poz. 12–15.**
  Poller ingest (poz. 11), push do Teams (poz. 12–13), zapis i tranzycja (poz. 14–15) opisane niżej
  zostały USUNIĘTE W CAŁOŚCI — pozostaje wyłącznie odczyt „moje zadania" (nowa poz. 11,
  [`live-smoke-checklist.md`](how-to/live-smoke-checklist.md)). „Do zrobienia (operator)" niżej jest
  NIEAKTUALNE — nie ma już czego dokańczać, poller i push nie istnieją. Reszta akapitu (do
  2026-07-28) dokumentuje historyczny build/walidację PRZED wycofaniem.
  Żywa instancja `example.atlassian.net`, projekt **WT** („Sufler Test"), wariant `cloud` (Basic
  `email:api_token`, REST v3/ADF, `search/jql`). Potwierdzone read-only wobec Jiry (do TYMCZASOWEGO
  `events.db`, bez zapisów): (a) łączność + auth; (b) **fail-fast strażnika pętli** — `SELF_ACCOUNT`
  = realny `accountId` tokenu (`712020:…`); (c) pełny pipeline `search → ADF-flatten → select_events
  → atrybucja → ingest`: 5 zgłoszeń WT dało 10 zdarzeń (5× `jira_issue_created` + 5× `jira_transition`)
  z atrybucją `project=workmate`; (d) **self-skip** — wszystkie 10 autorstwa konta tokenu → 0 przyjętych
  (poprawnie); (e) **dedup** — drugi poll 0. Przy okazji naprawiono lukę atrybucji: `registry.yaml`
  miał placeholder `jira_project_key: WM`, a `.env` nasłuchuje `WT` → ustawiono `WT` na projekcie
  `sufler` (bramka zielona, pytest **358**). **Blokada początkowa (rozwiązana):** stary token dawał
  `401 AUTHENTICATED_FAILED` — operator odświeżył API token w `.env`. **Do zrobienia (operator):**
  niepominięty ingest (zmiana w WT z INNEGO konta Jira niż token) oraz push do Teams — OBA
  usunięte razem z poller-em/mostem (D1, patrz adnotacja SUPERSEDED wyżej); nowe „moje zadania" to
  poz. 11 w [`live-smoke-checklist.md`](how-to/live-smoke-checklist.md).
  **Narzędzie (historyczne):** promowano skrypty smoke do repo — dawny `deploy/jira/preflight.py`
  (read-only preflight poz. 11 SPRZED D1: łączność+auth+`SELF_ACCOUNT`+pipeline do temp-db) +
  `deploy/jira/README.md`; oba zastąpione nową, czysto odczytową wersją (ADR 0054). Config push do
  Teams wpisany do nadrzędnego `.env` (`SUFLER_TEAMS_PUSH_*`: client/tenant/team z Powiadomienia,
  **cele OFF** — nic nie wychodzi). Blokada poz. 13: `CHANNEL_ID` nieznany (Powiadomienia używa
  Shifts+DM, nie kanału) — do odkrycia `GET /teams/{team}/channels`. Bramka: ruff+mypy czyste, pytest **358**.
  **Poz. 12 (zewnętrzna noga push) ZWALIDOWANA NA ŻYWO (2026-07-28):** jednorazowy kontrolowany send
  przeszedł całą ścieżkę Graph — MSAL **silent-refresh z cache** (`teams_token_cache.bin`, konto
  `piotr.czastkiewicz@…onmicrosoft.com`, BEZ device-code) → `/me` → `create_or_get_chat` (1:1) →
  `send_chat` → **DM do Mikołaja** (`1b3fa85a…`), treść jednoznacznie testowa. Cel czatu uzbrojony w
  `.env` (`ENABLE_CHAT=true`). Nadawca = Piotr (nie bot Virtual Sufler — to inny cache). **Pozostała
  luka do w pełni organicznej poz. 12:** niepominięte zdarzenie WT (zmiana z 2. konta Jira) → poller →
  auto-DM; formatowanie `[Jira]`/escaping jest pokryte testem `test_notifier.py`.
  **Nadawca przełączony na głos bota** (`SUFLER_TEAMS_PUSH_TOKEN_CACHE` → `virtual_sufler`,
  `Virtual.Sufler@…onmicrosoft.com`) — silent-refresh pobiera token push ze scope'ami czatu, więc
  uzbrojony DM zadziała od bota bez device-code. **Cel kanałowy poz. 13 odkryty:** zespół
  „Workmate-Teams" (`27fefc2f…`) / kanał „ogólny" (`19:7xExC…`), przez `/me/joinedTeams` +
  `/teams/{team}/channels`; wpisany do `.env`, `ENABLE_CHANNEL=false` (odkryty, nie włączony).
- [~] **C3. Worklog — go-live — SUPERSEDED (D2, ADR 0055, 2026-07-30).** Karty czasu WorklogPRO
  (cotygodniowe arkusze + self-service) **wycofane z projektu w całości** — decyzja trwała, nie
  pauza. „Krok 0 / przebieg próbny / pilotaż bojowy czekają na operatora" niżej NIGDY się nie
  odbędą — nie brakuje operatora, brakuje przedmiotu (drzwi usunięte). Akapit niżej (do 2026-07-28)
  jest zapisem historycznym tego, co zbudowano i zwalidowano PRZED wycofaniem.
  NARZĘDZIE + BRAMKI ZWALIDOWANE NA ŻYWO (2026-07-28); Krok 0 (szablon
  WorklogPRO), przebieg próbny i pilotaż bojowy czekają na operatora. Wypromowano
  `deploy/worklogi/preflight.py` + README (mirror `deploy/jira/`): read-only raport configu,
  realna `WorklogiSettings.validate` wobec `.env`, DOWÓD na żywo, że `DRY_RUN=false` bez
  `HEADERS_CONFIRMED=true` jest blokowane (strażnik z `config.py`), oraz generator PRZYKŁADOWEGO
  arkusza WorklogPRO z czystego rdzenia (nagłówki + `.xlsx` przez `--sample`) — artefakt do
  porównania z szablonem z kreatora w Kroku 0. Preflight na żywo: config spójny, bramka trzyma,
  arkusz `worklog_pilot-przykladowy_emp-017_2026-w29.xlsx` (kolumny `Issue Key/ID·User·Time Spent·
  Start Date & Time·Comment`, czas `3h 30m`, offset ISO `+0200`). `.env` dostał INERTNY,
  zakomentowany szkielet worklogów (reużywa tożsamości push). **Genuinnie operatorskie:** Krok 0
  w UI Jiry (pobór szablonu, ręczny import, test duplikatów/admin → `HEADERS_CONFIRMED=true`);
  przebieg próbny `uv run sufler-worklogi --login/--once` (dotyka Graph `fetch_team_members`,
  device-code); pilotaż bojowy `DRY_RUN=false` (prywatne DM). Bramka: ruff ✓ · mypy ✓ · pytest 358.

## Plan na sesje

| Sesja | Zakres | Złożoność | Start od |
|---|---|---|---|
| **1** | RAG semantyczny (A1) | MEDIUM/LARGE | `@architect` → ADR embeddings → zgoda → implementacja |
| **2** | Harness M3 lokalnie (A2) — ✅ zrobione 2026-07-27. Push do sesji MCP (A3) wydzielony do osobnej sesji (Research → ADR → build). | MEDIUM | A2: klocki gotowe → harness `sufler-meeting` |
| **3** | Zapis plików na Teams: `TeamsFileSender` + ADR 0026/0027 (A′). ~~Worklog załącznikiem~~ SUPERSEDED (D2, ADR 0055, 2026-07-30) | MEDIUM/LARGE | odblokowane; build + live-smoke |
| **4** | Go-live operacyjne (C1 + C2). ~~C3~~ SUPERSEDED (D2, ADR 0055, 2026-07-30) | operacyjna | konfiguracja + weryfikacja na żywo |
| **5** | Przygotowanie M4: `@architect` (tożsamość Entra/AD + async) + potwierdzenie scope `OnlineMeetingTranscript.Read.All` dla M3 | LARGE | ADR-y + lista scope'ów admina |
| **6** | M3/M4 produkcyjnie (B1 + B2 + B3) | LARGE | po odblokowaniu pozostałych scope'ów Azure |

**Uwaga:** „braki" M3-Graph/M4 to nie zaległości, lecz świadomie odłożona praca zależna od dostępu do
Azure/M365 (ADR 0009 §6). Wobec wszystkiego, co dało się zbudować bez tej infrastruktury, Sufler jest
względem Roadmapy V1 domknięty.
