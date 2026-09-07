# How-to: checklist smoke-testów na żywo (klucz / Azure / 2. konto)

Ta lista zbiera w jednym miejscu weryfikacje, których **pakiet `pytest` świadomie nie
wykonuje** — cała automatyka działa na atrapach/mockach, bez sieci. Realne zachowanie
(faktyczne wywołania Claude API, Microsoft Graph, przebieg kosztów) potwierdza się
osobnym „smoke na kluczu / na żywym koncie". Poniższe pozycje pochodzą z ADR-ów i briefów
sesji, w których zapisano „live-smoke do zrobienia".

Legenda warunku: 🔑 wymaga `ANTHROPIC_API_KEY` · 👥 wymaga 2. konta w kanale Teams ·
☁️ wymaga infrastruktury (Azure/M365 lub serwer Windows/IIS) · 🐙 wymaga PAT GitHub + repo ·
🟪 wymaga PAT Jira + instancji Jira Server/Data Center (`WORKMATE_JIRA_*`).

---

## 1. Realne `usage` / koszty — 🔑 (ADR 0013)

- **Krok:** zadaj agentowi realne pytanie przez harness CLI:
  `uv run workmate-agent "Jaki jest status projektu scada-integration w MPWiK?"`
- **Oczekiwane:** zwrócone przez API liczby `usage` (input/output/thinking tokens) są realne
  (atrapy ich nie generują), a koszt USD z `core/domain/pricing.py::cost_usd` zgadza się z
  cennikiem użytego modelu.

## 1b. Prompt caching — 🔑 (#10, przegląd kodu 2026-07-31)

- **Krok:** prowadź DWIE tury w tej samej rozmowie przez harness CLI (`uv run workmate-agent
  --history`, albo drzwi Teams) — pierwsza tura buduje cache (breakpoint na system+tools),
  druga powinna go trafić.
- **Oczekiwane:** w `conversations.db` (tabela `messages`, `sqlite_conversations.py`, kolumny
  już zapisywane) `cache_read_input_tokens > 0` dla DRUGIEJ tury asystenta: `sqlite3
  ~/.workmate/conversations.db "select role, cache_read_input_tokens,
  cache_creation_input_tokens from messages where role='assistant' order by id desc limit 2;"`.
  Pierwsza tura ma `cache_creation_input_tokens > 0` (zapis do cache), zero odczytu.
- **Uwaga:** skład `extra_tools` może się zmienić między turami (kanał, różni nadawcy —
  fabryki per-turowe w `responder/conversational.py`), co unieważnia breakpoint historii i daje cache-miss na
  DRUGIM breakpoincie — to oczekiwana, łagodna degradacja kosztowa, nie błąd. Breakpoint
  system+tools (pierwszy) powinien trafiać niezależnie od tego.

## 2. Kompaktowanie rozmowy — 🔑 (ADR 0014)

- **Krok:** prowadź wieloturową rozmowę (CLI `--history` lub drzwi Teams) aż przekroczy próg
  kompaktowania z `AgentSettings`/`ConversationSettings`; obserwuj wywołanie podsumowujące.
- **Oczekiwane:** podsumowanie generuje się na granicy, kontekst pozostaje ciągły w kolejnych
  turach (agent „pamięta" wcześniejsze ustalenia), a koszt wywołania podsumowującego jest
  rozsądny.

## 3. Wdrożenie HTTP / Bramka 3 — ☁️ (ADR 0007)

- **Krok:** wg [`deploy-http.md`](deploy-http.md) — wygeneruj `tokens.json` z ACL, skonfiguruj
  IIS (response buffering **off**) + usługę Windows, potwierdź ekspansję `${WORKMATE_TOKEN}`.
  Smoke transportu: żądanie bez tokenu, ze złym tokenem, z dobrym tokenem, z obcym `Host`.
- **Oczekiwane:** `401` bez/na zły token (z nagłówkiem `WWW-Authenticate: Bearer`), `200` na
  dobry token, `421` na obcy `Host`; drzwi HTTP tylko do odczytu (`enable_write=False`).

## 4. Teams — wielotura, realne odpowiedzi po naprawie — 🔑 👥 (ADR 0015)

- **Krok:** w istniejącym wątku kanału wpisz wiadomość z **drugiego konta** (tura 2+).
- **Oczekiwane:** tura 2+ daje REALNĄ odpowiedź agenta (a nie komunikat zastępczy) — potwierdza
  naprawę bloków wyjściowych z ADR 0015.

## 5. Teams — obrazy inline i Word `.docx` — 🔑 👥 (ADR 0016)

- **Krok:** wyślij w wątku obraz (wklejka), GIF oraz emoji, a osobno plik `.docx`.
- **Oczekiwane:** bloki obrazów przyjęte przez Claude API (wklejka przez listowanie-fallback,
  GIF/emoji przez publiczny URL), a treść `.docx` odczytana poprawnie. (PDF i `.pptx` już
  potwierdzone — patrz brief 2026-07-13.)

## 6. Rozumowanie — `summarized`, streaming, poufność — 🔑 (ADR 0011)

- **Krok:** ustaw `thinking` na tryb z `display=summarized` i `max_tokens=128000`; zadaj
  pytanie wymagające rozumowania.
- **Oczekiwane:** realny tekst `summarized` w odpowiedzi, poprawny streaming przy dużym
  `max_tokens`, brak wycieku surowego rozumowania tam, gdzie nie powinno go być (sondy
  poufności).

## 7. Summarizer M3 — jakość i poprawność JSON — 🔑 (ADR 0009)

- **Krok:** uruchom **lokalny harness M3** na realnym transkrypcie (patrz
  [`meeting-note-harness.md`](meeting-note-harness.md)):
  `echo "…transkrypt…" | uv run workmate-meeting --project scada-integration --date 2026-07-20`
  (albo `--transcript spotkanie.txt`). Harness spina cały przepływ M3 wobec Claude:
  `InMemoryTranscriptSource` → `AnthropicMeetingSummarizer` → złożenie `NoteMetadata` → zapis. Realny
  fetch z Graph jest ☁️ Azure-gated i pozostaje odłożony; czysta obróbka otoku JSON (`_extract_json`)
  jest pokryta testem jednostkowym.
- **Oczekiwane:** model zwraca JSON walidujący się do `MeetingSummary` (`model_validate_json`) —
  harness kończy raportem `✓ Notatka M3 złożona…` z tytułem, uczestnikami i licznikami
  decyzji/action items; jakość streszczenia jest sensowna. Domyślnie plik ląduje w katalogu
  tymczasowym harnessu (NIE w `data/notes/`).

## 8. GitHub → EventStore (ingest) — 🐙 (ADR 0019/0020)

- **Krok:** ustaw `WORKMATE_GITHUB_TOKEN`/`_OWNER`/`_REPO`, uruchom `uv run workmate-github`;
  utwórz ręcznie issue w repo. Podejrzyj `~/.workmate/events.db` (np. przez agenta akcją
  `Activity(action='events')` na drzwiach Teams).
- **Oczekiwane:** issue pojawia się jako zdarzenie `source="github", kind="issue_opened"`;
  ponowny poll go NIE dubluje (dedup). Issue utworzone **kontem PAT, ale poza narzędziem**
  (`gh`, WWW) też się pojawia — od ADR 0071 decyzja 6 strażnik pyta o echo naszych drzwi,
  nie o konto autora. Zamknięte issue daje DRUGIE zdarzenie `kind="issue_closed"` (etap 1).

## 9. EventStore → Teams (notifier dual-target) — 🔑 👥 🐙 (ADR 0022)

- **Krok:** skonfiguruj `WORKMATE_TEAMS_PUSH_*` (włącz `ENABLE_CHAT` i/lub `ENABLE_CHANNEL`),
  zaloguj się raz device-code; wywołaj zdarzenie GitHub (nowe issue).
- **Oczekiwane:** powiadomienie ląduje w **czacie 1:1 ORAZ na kanale** (wg włączonych celów),
  treść zescapowana (bez żywego HTML). Restart procesu nie gubi ani nie dubluje (kursor).

## 10. Teams → GitHub (bramkowany zapis) — 🔑 👥 🐙 (ADR 0021)

- **Krok:** ustaw `WORKMATE_GITHUB_ENABLE_WRITE=true`; przez agenta na kanale Teams poproś
  „utwórz issue: …". 
- **Oczekiwane:** issue powstaje w skonfigurowanym repo (nie w cudzym); agent zwraca numer i URL;
  **potwierdź, że NIE wraca jako powiadomienie** — dwoma niezależnymi drogami: poller pomija je,
  bo znajduje echo `('teams', numer, 'github_issue_created')`, a notifier nie odsyła zdarzeń
  `source="teams"` (ADR 0071 decyzja 6; do 2026-09-07 pierwszą drogą był login PAT).

---

## Jira: moje zadania (odczyt) — live-test

Jira jest zredukowana do JEDNEJ, wyłącznie odczytowej zdolności ([ADR 0054](../adr/0054-reduce-jira-to-read-only-my-tasks.md)) —
nie ma już pollera, pushu do Teams, mostu Teams↔Jira ani żadnego narzędzia zapisu/tranzycji.
Procedura pełna: [`jira-my-tasks.md`](jira-my-tasks.md).

## 11. Jira → „moje zadania" (preflight + realne pytanie) — 🟪 (ADR 0054)

- **Krok (preflight):** ustaw `WORKMATE_JIRA_BASE_URL`/`_TOKEN` (Cloud: + `_EMAIL`), uruchom
  `uv run --no-sync python deploy/jira/preflight.py`.
- **Oczekiwane (preflight):** auth OK (`authenticated_account`), próbne `search_issues` zwraca listę
  bez błędu. Kod wyjścia `0`.
- **Krok (tożsamość) — OSOBNE uruchomienie, bo bez `--aad` sprawdzenia NIE MA:**
  `uv run --no-sync python deploy/jira/preflight.py --aad <aad-user-id>`.
  Wyzwalaczem jest flaga, nie zmienna `WORKMATE_TEAMS_GRAPH_IDENTITIES` (`preflight.py`:
  `if aad_user_id:`). Komenda bez `--aad` kończy się `Preflight OK` i kodem `0`, **nie zajrzawszy
  do mapy ani razu** — a operator odczyta to jako „tożsamość zweryfikowana".
- **Oczekiwane (tożsamość):** AAD id rozwiązuje się w mapie do osoby z niepustym `jira_user`,
  kod wyjścia `0`.
- **Trzeci wynik, i to POPRAWNY:** dla członka pionu bez konta Jira (wpis „tylko Teams",
  [ADR 0070](../adr/0070-teams-only-identity-and-what-a-map-entry-grants.md)) preflight kończy się
  kodem `1` i mówi, dlaczego. To nie jest usterka konfiguracji i **nie wolno tego „naprawiać"
  dopisaniem `jira_user`** — cudze konto pokazałoby tej osobie cudze zadania. Osoba pozostaje
  pełnym członkiem pionu; traci wyłącznie narzędzie `Jira`.
- **Krok (realne pytanie, Teams):** ustaw `WORKMATE_TEAMS_GRAPH_IDENTITIES` z wpisem nadawcy
  (`jira_user`), na kanale/w czacie napisz `/moje-zadania`.
- **Oczekiwane:** lista TYLKO otwartych zadań PYTAJĄCEGO (nie kolegi); nadawca spoza mapy tożsamości
  dostaje odmowę, nie pustą listę cudzych zadań. Zero parametrów — nie da się poprosić o `assignee`
  innej osoby.
- **Krok (realne pytanie, MCP stdio):** ustaw `WORKMATE_JIRA_MY_ACCOUNT`, w Claude Code/CLI zapytaj
  o własne zadania Jira.
- **Oczekiwane:** narzędzie `get_my_jira_tasks` wchodzi na katalog TYLKO gdy zmienna ustawiona;
  zwraca zadania skonfigurowanego principala. Na drzwiach `streamable-http` (wielu osób) narzędzie
  NIE jest wystawiane — sprawdź, że nie pojawia się na liście narzędzi floty.

## 12. Teams: propozycja czasu z commitów — 👥 🐙 (ADR 0034, część odczytowa)

- **Krok:** wystarczy skonfigurowany GitHub (`TOKEN`/`OWNER`/`REPO`) — narzędzie nie ma bramki, bo
  po wycięciu ścieżki zapisu (2026-07-21) niczego nie mutuje. Poproś agenta o propozycję ewidencji
  za tydzień, w którym realnie commitowałeś w `PIWorkmate`, i porównaj wynik z własną pamięcią.
- **Oczekiwane:** sesje pocięte przerwami i dobami, sumy per zgłoszenie wyciągnięte z kluczy `WT-*`
  w wiadomościach commitów, `confidence` niski przy pojedynczych commitach, `notes` ostrzegające
  o gałęzi domyślnej. **Nic nie zapisane w Jirze — i nie ma czym zapisać.** Sprawdź też zakres:
  dzień z jednym commitem na koniec dnia da tylko „rozbieg" (~30 min), nie osiem godzin — to znana,
  świadoma cecha estymacji.
- **Krok (ucięcie historii):** poproś o okno 31 dni bez filtra `author` na aktywnym repo.
- **Oczekiwane:** jeśli commitów było ≥500, w `notes` pojawia się ostrzeżenie o UCIĘTEJ historii
  (wypadają NAJSTARSZE dni, więc godziny są zaniżone) — a nie cicha, kompletnie wyglądająca suma.
- **Krok (strefa):** propozycja obejmująca commity z okolic północy przy aktywnej zmianie czasu.
- **Oczekiwane:** doba liczona wg `WORKMATE_GITHUB_WORKLOG_TZ` (`ZoneInfo`), więc commit z 23:30
  lokalnego czasu zostaje w swoim dniu po obu stronach przejścia DST.

---

> **Karty czasu (WorklogPRO) — wycofane w całości** ([ADR 0055](../adr/0055-withdraw-worklogpro-timesheets.md),
> supersedes 0035/0036/0037/0038). To decyzja trwała, nie pauza: nie ma już cotygodniowego arkusza,
> wysyłki DM ani self-service na żądanie — pozycja live-smoke usunięta z tej listy bez zamiennika.

## Opcjonalna weryfikacja powdrożeniowa (nie blokuje niczego)

M3 (komenda `/notatka` — nowa notatka ze spotkania — i przechwytywanie wątku „zapisz to") ma kod i
testy KOMPLETNE, bramki włączone w `deploy/docker/env`/`.example` (ADR 0041/0042/0043/0047/0048
zaakceptowane). Live-smoke na **realnym** transkrypcie/wątku spotkania nie jest tu wykonywalny — wymaga
realnego identyfikatora spotkania Teams, dostępnego dopiero po uruchomieniu floty na serwerze
docelowym. To NIE jest pozycja otwarta ani blokująca; wykonaj ją dopiero POWDROŻENIOWO, wg
[`meeting-transcript-live-smoke.md`](meeting-transcript-live-smoke.md). Harness lokalny (pozycja 7
wyżej) pokrywa jakość podsumowania już teraz, bez czekania na wdrożenie.

---

> Po wykonaniu pozycji odnotuj wynik w briefie sesji (`.claude/sessions/`) i — gdy dotyczy —
> zaktualizuj powiązany ADR.
