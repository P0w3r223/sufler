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

- **Krok:** podaj realny transkrypt do `AnthropicMeetingSummarizer.summarize`
  (`InMemoryTranscriptSource` wystarcza — realny fetch z Graph jest ☁️ Azure-gated i pozostaje
  odłożony). Czysta obróbka otoku JSON (`_extract_json`) jest już pokryta testem jednostkowym.
- **Oczekiwane:** model zwraca JSON walidujący się do `MeetingSummary` (`model_validate_json`),
  a jakość streszczenia (tytuł, decyzje, action items) jest sensowna.

## 8. GitHub → EventStore (ingest) — 🐙 (ADR 0019/0020)

- **Krok:** ustaw `WORKMATE_GITHUB_TOKEN`/`_OWNER`/`_REPO`, uruchom `uv run workmate-github`;
  utwórz ręcznie issue w repo. Podejrzyj `~/.workmate/events.db` (np. przez agenta narzędziem
  `read_recent_events` na drzwiach Teams).
- **Oczekiwane:** issue pojawia się jako zdarzenie `source="github", kind="issue_opened"`;
  ponowny poll go NIE dubluje (dedup), a issue utworzone kontem PAT jest pomijane (self-skip).

## 9. EventStore → Teams (notifier dual-target) — 🔑 👥 🐙 (ADR 0022)

- **Krok:** skonfiguruj `WORKMATE_TEAMS_PUSH_*` (włącz `ENABLE_CHAT` i/lub `ENABLE_CHANNEL`),
  zaloguj się raz device-code; wywołaj zdarzenie GitHub (nowe issue).
- **Oczekiwane:** powiadomienie ląduje w **czacie 1:1 ORAZ na kanale** (wg włączonych celów),
  treść zescapowana (bez żywego HTML). Restart procesu nie gubi ani nie dubluje (kursor).

## 10. Teams → GitHub (bramkowany zapis) — 🔑 👥 🐙 (ADR 0021)

- **Krok:** ustaw `WORKMATE_GITHUB_ENABLE_WRITE=true`; przez agenta na kanale Teams poproś
  „utwórz issue: …". 
- **Oczekiwane:** issue powstaje w skonfigurowanym repo (nie w cudzym); agent zwraca numer i URL;
  **potwierdź, że NIE wraca jako powiadomienie** (self-skip po loginie PAT + echo `source="teams"`).

---

## Most Jira (seria B) — live-testy do wykonania

Cały kod mostu Jira jest gotowy i zielony na atrapach; poniższe pozycje potwierdzają realne
zachowanie na **prawdziwej instancji Jira Server/Data Center**. Wspólne wymagania (NIE-sekret w
CLAUDE.md, sekrety poza repo): `WORKMATE_JIRA_BASE_URL`, `WORKMATE_JIRA_TOKEN` (PAT Bearer),
`WORKMATE_JIRA_WATCH_PROJECTS` (np. `WM`), a dla zapisu/tranzycji `WORKMATE_JIRA_WRITE_PROJECT` +
`WORKMATE_JIRA_SELF_ACCOUNT` (= login konta PAT — inaczej fail-fast). **Inwariant cross-proces
strażnika pętli:** poller `workmate-jira` i drzwi zapisu `teams_graph` MUSZĄ mieć TEN SAM
`WORKMATE_JIRA_TOKEN`/`SELF_ACCOUNT` na WSPÓLNYM `~/.workmate/events.db`.

## 11. Jira → EventStore (ingest read) — 🟪 (ADR 0030)

- **Krok:** ustaw `WORKMATE_JIRA_BASE_URL`/`_TOKEN`/`_WATCH_PROJECTS`, uruchom
  `uv run workmate-jira`. Utwórz ręcznie zgłoszenie w projekcie, zmień jego status i dodaj komentarz.
  Podejrzyj `~/.workmate/events.db` (np. przez agenta narzędziem `read_recent_events` z filtrem
  `source="jira"`).
- **Oczekiwane:** trzy zdarzenia — `jira_issue_created`, `jira_transition`, `jira_comment` — z
  atrybucją `project` z rejestru (`jira_project_key`). Ponowny poll ich NIE dubluje (dedup po
  kluczu / id wpisu changelogu / id komentarza), a zmiany autorstwa konta PAT są pomijane (self-skip).
  Watermark JQL po `updated` (minutowa precyzja) nie gubi zdarzeń po restarcie.

## 12. Jira → Teams (notifier dual-target) — 🔑 👥 🟪 (ADR 0022/0030)

- **Krok:** skonfiguruj `WORKMATE_TEAMS_PUSH_*` (włącz `ENABLE_CHAT` i/lub `ENABLE_CHANNEL`),
  zaloguj się raz device-code; wywołaj zdarzenie Jira (nowe zgłoszenie / zmiana statusu / komentarz).
- **Oczekiwane:** powiadomienie ląduje w czacie 1:1 ORAZ na kanale (wg włączonych celów) z etykietą
  źródła **`[Jira]`** i poprawnym rodzajem (`Nowe zgłoszenie`/`Zmiana statusu`/`Nowy komentarz`).
  Treść zescapowana (bez żywego HTML). Restart procesu nie gubi ani nie dubluje (kursor at-least-once).

## 13. Wątkowanie kanału Jiry — jedno zgłoszenie, jeden wątek (B2) — 🔑 👥 🟪 (ADR 0024)

- **Krok:** ustaw `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING=true` (wymaga `ENABLE_CHANNEL=true`);
  dla jednego zgłoszenia (np. `WM-5`) wywołaj po kolei: utworzenie, zmianę statusu i komentarz.
- **Oczekiwane:** wszystkie trzy powiadomienia trafiają do JEDNEGO wątku (root utworzenia; kolejne
  jako odpowiedzi), a inne zgłoszenie (`WM-6`) zaczyna NOWY wątek. Klucz `kind="jira"` nie koliduje z
  wątkami GitHuba na tym samym `events.db`. Usunięcie roota w Teams → notifier tworzy nowy i przełącza
  link (nie blokuje strumienia).

## 14. Teams → Jira: bramkowany zapis create-only (Gate 5) — 🔑 👥 🟪 (ADR 0031)

- **Krok:** ustaw `WORKMATE_JIRA_ENABLE_WRITE=true` (+ `WRITE_PROJECT` + `SELF_ACCOUNT`); przez
  agenta na kanale Teams poproś „utwórz zgłoszenie: …" oraz „dodaj komentarz do WM-5: …".
- **Oczekiwane:** zgłoszenie/komentarz powstają w SKONFIGUROWANYM projekcie (nie w cudzym); agent
  zwraca klucz i URL. Komentarz do klucza spoza projektu (`OPS-1`) lub o kształcie path-traversal
  (`WM-1/../OPS-1`) jest ODRZUCONY. **Potwierdź, że zapis NIE wraca jako powiadomienie** (self-skip
  PAT + echo `source="teams"`, którego notifier `source="jira"` nie odsyła).

## 15. Teams → Jira: tranzycja statusu (walk) — 🔑 👥 🟪 (ADR 0032)

- **Krok:** ustaw `WORKMATE_JIRA_ENABLE_TRANSITION=true` (+ `WRITE_PROJECT` + `SELF_ACCOUNT`).
  Przy domyślnym `MAX_TRANSITION_HOPS=1` poproś agenta „przenieś WM-5 do In Progress" (status będący
  bezpośrednim sąsiadem) oraz do statusu odległego/nieosiągalnego. Następnie podnieś
  `MAX_TRANSITION_HOPS` (np. 3) i powtórz dla celu o kilka kroków dalej po LINIOWYM workflow.
- **Oczekiwane:** (a) cel-sąsiad → `reached=true`, jeden hop, `[Jira] Zmiana statusu` na kanale;
  (b) cel nieosiągalny przy cap=1 → `reached=false`, `stop_reason=hop_cap`, **zgłoszenie NIE ruszone**
  (raport z `available_next`); (c) przy cap≥2 na liniowym workflow → walk przechodzi stany wymuszone
  do celu, echo per hop; na rozgałęzieniu STOP (`branch_point`, bez zgadywania). Raport zawsze
  strukturalny (`reached`/`path`/`stop_reason`); tranzycja nie wraca jako zbędne powiadomienie.

## 16. Teams → Jira: ewidencja czasu z commitów — 🔑 👥 🟪 🐙 (ADR 0034)

- **Krok (propozycja):** ustaw `WORKMATE_JIRA_ENABLE_WORKLOG=true` (+ `WRITE_PROJECT` +
  `SELF_ACCOUNT` + GitHub `TOKEN`/`OWNER`/`REPO`). Poproś agenta o propozycję ewidencji za tydzień,
  w którym realnie commitowałeś w `PIWorkmate`, i porównaj wynik z własną pamięcią.
- **Oczekiwane:** sesje pocięte przerwami i dobami, sumy per zgłoszenie wyciągnięte z kluczy `WT-*`
  w wiadomościach commitów, `confidence` niski przy pojedynczych commitach, `notes` ostrzegające
  o gałęzi domyślnej. **Nic nie zapisane w Jirze.** Sprawdź też zakres: dzień z jednym commitem na
  koniec dnia da tylko „rozbieg" (~30 min), nie osiem godzin — to znana, świadoma cecha estymacji.
- **Krok (zapis własny):** poproś o zapis konkretnej liczby godzin na istniejące `WT-*` za wczoraj.
  Powtórz TĘ SAMĄ prośbę drugi raz.
- **Oczekiwane:** pierwszy wpis widoczny w zakładce Work log zgłoszenia; drugi **odrzucony** przez
  strażnik duplikatów (bez usuwania wpisów duplikat byłby nieusuwalny z poziomu narzędzia).
- **Krok (cross-user):** ustaw dodatkowo `WORKMATE_JIRA_WORKLOG_ALLOW_ON_BEHALF=true` i poproś
  o wpis z `on_behalf_of=<accountId Mikołaja>`, `display_name=Mikołaj`.
- **Oczekiwane:** w UI Jiry kolumna autora pokazuje **konto tokenu (Piotr)**, a treść wpisu zaczyna
  się od `w imieniu: Mikołaj`; odpowiedź narzędzia niesie pole `note` o stratnej atrybucji, a agent
  je RELACJONUJE. Bez włączonej drugiej bramki ta sama prośba musi zostać odrzucona.

## 17. Cotygodniowe karty czasu → Teams — 🔑 👥 🟪 (ADR 0035)

- **Krok 0 (BRAMKA):** Apps → WorklogPRO → Import worklogs. Pobierz szablon i porównaj nagłówki
  z `WORKLOGPRO_HEADERS`. Zaimportuj 2–3 wiersze ręcznie, potem TE SAME drugi raz. Sprawdź, czy
  import działa z konta BEZ uprawnień admina.
- **Oczekiwane:** nagłówki zgodne (albo poprawione w stałej + teście); wiadomo, czy powstają
  duplikaty; wiadomo, czy nie-admin może importować. Bez tego kroku reszta jest niepotwierdzona.
- **Krok (próbny):** `WORKMATE_WORKLOGI_ENABLED=true`, `DRY_RUN=true`, `ONLY_SOURCE_IDS` = tylko Ty.
  `uv run workmate-worklogi --once`.
- **Oczekiwane:** arkusz w `OUTPUT_DIR` z Twoimi godzinami, ŻADNEJ wiadomości na Teams, pusty stan.
  Otwórz plik i zweryfikuj kolumny oraz znacznik `Start Date & Time` z offsetem.
- **Krok (bojowy, pilotaż):** `DRY_RUN=false`, `ONLY_SOURCE_IDS` = Ty + Mikołaj. Uruchom ponownie.
- **Oczekiwane:** prywatna wiadomość 1:1 z **tabelą** (nie `&lt;table&gt;` — to sprawdza
  `send_chat_html`), sumą tygodnia, ścieżką pliku i ostrzeżeniem o dublowaniu. Każdy widzi TYLKO
  swoje godziny. Osoba bez godzin nie dostaje nic.
- **Krok (idempotencja):** uruchom trzeci raz w tym samym tygodniu.
- **Oczekiwane:** zero wiadomości, raport `wysłano 0`. Następnie spróbuj uruchomić DRUGĄ instancję
  równolegle — musi odmówić z komunikatem o blokadzie.
- **Krok (import):** zaimportuj wygenerowany arkusz do WorklogPRO jako pracownik.
- **Oczekiwane:** wpisy w Jirze z **właściwym autorem** (to cała różnica wobec ADR 0034) i czasem
  zgodnym z tabelą z wiadomości.

---

> Po wykonaniu pozycji odnotuj wynik w briefie sesji (`.claude/sessions/`) i — gdy dotyczy —
> zaktualizuj powiązany ADR. Pozycje ☁️ Azure-gated (M3 fetch transkryptu, M4 mapowanie
> tożsamości) czekają na dostęp do infrastruktury i nie są tu wykonywalne.
