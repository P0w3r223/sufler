# Changelog

Wszystkie istotne zmiany w projekcie WorkMate. Format oparty na
[Keep a Changelog](https://keepachangelog.com/pl/1.1.0/); wersjonowanie
[SemVer](https://semver.org/lang/pl/). Decyzje projektowe: [`docs/adr/`](docs/adr/).

## [Unreleased]

Scalone od 1.6.0, jeszcze bez podbicia `__version__` (nadal 1.6.0 — dług release'u).

### Added
- **Autoryzacja odczytu notatek** na drzwiach Teams — fail-closed po mapie tożsamości ([ADR 0062](docs/adr/0062-note-read-authorization.md)).
- **Bramka członkostwa na powłoce** (`Bash`) — nie-członek pionu dostaje pustą listę narzędzi powłoki, symetrycznie do odczytu notatek ([ADR 0063](docs/adr/0063-shell-membership-gate-and-conversation-isolation.md)).
- **Wykonawca powłoki per rozmowa** — menedżer `exec-manager` (entrypoint `workmate-exec-manager`) stawia wykonawcę on-demand z montażem TYLKO podkatalogu brudnopisu, domykając cross-read między członkami (ADR infra 0012).
- **Dziennik audytu wywołań narzędzi + dead-letter notifiera** — obserwowalność Fazy 0, OFF-by-default (`WORKMATE_AUDIT_DB`); audyt rejestruje akcje/ścieżki, nigdy treści; dead-letter zachowuje at-least-once ([ADR 0067](docs/adr/0067-observability-audit-journal-and-notifier-dead-letter.md)).

- **Narzędzie `File(action='read')` + odkładanie załączników na dysk rozmowy** (ADR 0064, druga
  część): model może podać sobie plik z katalogu roboczego DO WGLĄDU — obraz jako obraz, PDF jako
  dokument, resztę jako wyciągnięty tekst. Plik jedzie osobnym blokiem obok wyniku narzędzia (bo
  `tool_result` nie unosi bloku `document`, a jego treść bywa czyszczona przez edycję kontekstu),
  w tej samej turze, i przeżywa zapis do pamięci rozmowy. Załączniki użytkownika są od teraz
  ODKŁADANE na dysk katalogu rozmowy — dotąd żyły wyłącznie w blokach rozmowy, na wolumenie,
  którego wykonawca świadomie nie montuje, więc ani powłoka, ani model nie miały jak do nich
  wrócić po kompaktowaniu. Nazwy odłożonych plików trafiają do nagłówka sesji (na dysku są
  slugiem oryginalnej nazwy). Budżet materiałów tury jest WSPÓLNY z materializerem drzwi — jedno
  żądanie API, jeden sufit. Narzędzie jest agent-only (golden powierzchni MCP pilnuje tego wprost).
- **Ekstrakcja HTML + komenda `workmate-extract`** (ADR 0064, pierwsza część): plik `.html`/`.htm`
  przestaje odbijać się od drzwi jako „nieobsługiwany typ" — idzie ekstraktorem (`html.parser` ze
  stdlib, bez nowej zależności), który pomija skrypty i style, wciąga `alt` obrazów i raportuje
  liczbę grafik bez opisu, więc strona zdominowana przez baner nadal oddaje swoją treść. Powłoka
  dostaje `workmate-extract plik.pdf` na pdf/docx/xlsx/pptx/html — ten sam `document_text` co drzwi.
  `pypdf` dołożony do extra `teams-graph`, bo obraz floty nie instaluje `seed`, w którym mieszkał.

### Design (ADR-y `accepted` 2026-08-14, kod jeszcze nienapisany)
- **0064** — narzędzie `File(read|write|edit|delete)` + materializacja do następnej tury użytkownika, ekstrakcja HTML z budżetem anty-maskującym, `workmate-extract`. Pomiar ruchu (31 lip–14 sie: 4 załączniki, 0 kompaktowań) **nie** potwierdził potrzeby — narzędzie powstaje decyzją właściciela, z ponownym pomiarem po miesiącu powłoki jako warunkiem utrzymania.
- **0065** — mutowalna baza wiedzy: kanał generyczny `File(write/edit/delete)` przez walidator notatek, sędzia-Sonnet jako obrona w głębi, werdykt `confirm` = potwierdzenie w wątku od tego samego zmapowanego nadawcy, snapshot przed każdą operacją + nocna kopia wolumenu.
- **0066** — klasy zaufania T0–T3: etykiety T3 domyślnie ON, rozszczepienie T1/T2 opt-in za flagą ADR 0062; lepka skaza rozmowy eskaluje (sędzia + audyt), nie blokuje.

## [1.6.0] — 2026-08-07

Wydanie konsolidacji: powierzchnia narzędziowa schodzi z **22 rejestracji w 13 builderach do
pięciu narzędzi** — `Bash` · `Notes` · `GitHub` · `Jira` · `Schedule` — a model dostaje drogę
dostarczenia pliku rozmówcy i katalog procedur do powtarzalnej pracy. Prompt przestaje opisywać
świat sprzed tej zmiany.

Kryterium konsolidacji jest **bariera, nie temat**: narzędzie typowane powstaje wyłącznie tam,
gdzie powłoka w wykonawcy nie może dosięgnąć — brak sieci (Jira, GitHub, Shifts), brak wolumenu
stanu (`events.db`), brak drogi do kontekstu, skutek poza kontenerem (dostawa pliku, zapis
notatki). `Skill(name)` i `File(write/list)` z tego powodu **nie powstają**: to przypadki użycia
`Bash`. Zysk przychodzi z wchłaniania narzędzi o WSPÓLNEJ prozie (`Jira` −1803 znaki przy
sześciu), a nie z przekształcania pojedynczych — te powierzchnię powiększają.

Zmierzone składaniem katalogu z żywych builderów, w układach uruchamianych naprawdę (bramki wg
`config/env.example`): **7 narzędzi** dziś na produkcji (wszystko wyłączone), **5** w architekturze
docelowej (powłoka ON, GitHub write ON), **6** z dostawą plikiem, **8** przy wszystkim włączonym.

### Dodane

- **Skrzynka nadawcza rozmowy — dostawa plików przez `outputs/`.** Plik zapisany przez powłokę
  w podkatalogu `outputs/` katalogu roboczego rozmowy jedzie do rozmówcy po zakończeniu tury
  i znika ze skrzynki. Ścieżka jest WZGLĘDNA celowo: opis narzędzia siedzi w cache'owanym
  prefiksie promptu, więc ścieżka bezwzględna per rozmowa unieważniałaby go przy każdej nowej
  rozmowie. Limity per tura: `WORKMATE_TEAMS_GRAPH_OUTBOX_MAX_FILES` (5),
  `WORKMATE_TEAMS_GRAPH_OUTBOX_MAX_SECONDS` (20), `MAX_FILE_REPLY_KB`; pozycje `*.tmp` są
  pomijane, więc plik w budowie nie wyjedzie. Okno limitu przy trwającej awarii wysyłki oddaje
  ponowieniom najwyżej `limit − 1` miejsc, dopóki jest co świeżego wysłać — bezwzględny priorytet
  ponowień zamieniał jedną stratę na drugą.
- **Katalog procedur `/mnt/skills`** (ADR 0005 paczki wdrożeniowej). Układ `<korzeń>/<nazwa>/SKILL.md`;
  nazwa procedury to nazwa katalogu, opis to pierwsza niepusta linia spoza nagłówków. **Bez parsera
  frontmattera i to jest decyzja**: najcięższe udokumentowane ataki na katalogi procedur są
  własnością preprocesora, nie czytania plików — dopóki treść trafia do kontekstu zwykłym `cat`-em,
  cała ta klasa nas nie dotyczy. Znaki niewidoczne (zero-width, znaczniki kierunku pisma) są
  odsiewane na wejściu. Lista wchodzi do nagłówka sesji, `WORKMATE_SKILLS_DIR` i
  `WORKMATE_SKILLS_MAX_IN_HEADER` sterują źródłem i długością; ucięcie listy jest GŁOŚNE.
- **`Schedule()`** zamiast `get_team_schedule` — nic nie wchłania, ale cztery pola dostały opisy.

### Zmienione

- **`GitHub(action=…)`, `Jira(action=…)`, `Notes(action=…)`** wchłaniają odpowiednio pięć, sześć
  i dwa narzędzia. Wzorzec opiera się na `Annotated[Literal[…], Field(description=…)]`, bo pomiar
  pokazał, że `func_metadata` przenosi opisy pól, a `Literal` staje się `enum`. **Bramka zapisu
  wchodzi do `Literal`, nie do ciała funkcji**: przy wyłączonej bramce wartość akcji NIE ISTNIEJE
  w schemacie, więc model jej nie zaproponuje — bramka sprawdzana dopiero w ciele wyglądałaby
  w schemacie identycznie jak jej brak.
- **Powierzchnia MCP zostaje osobna i zamrożona.** Sesja Claude Code nie ma dostępu do naszego
  wykonawcy, więc `workmate-search` jest dla niej nieosiągalny — konsolidacja tam nie przeniosłaby
  zdolności, tylko ją skasowała. Agent dostał WŁASNE buildery; baseline objął całą powierzchnię
  (dotąd zamrażał 6 nazw z 8), a golden biega w czterech konfiguracjach.
- **Trzy narzędzia odczytu notatek i trzy narzędzia plikowe wchodzą tylko BEZ powłoki.** Cięcie
  jest warunkowe, nie bezwarunkowe: `WORKMATE_ENABLE_SHELL` jest domyślnie wyłączona, a bez
  powłoki te narzędzia są jedyną drogą do bazy wiedzy i jedynym sposobem, w jaki model odzyskuje
  własny szkic po kompaktowaniu kontekstu. Warunek liczy się z FABRYKI powłoki, nie z ustawienia
  operatora — ustawienie mówi, czego operator chciał, fabryka mówi, co agent dostanie.
- **`reply_on_thread` zniesione, powiązanie wątku idzie do nagłówka sesji.** Wołało tę samą metodę
  serwisu co `GitHub(action='comment')`, za tą samą bramką i obok niej — nie zawężało niczego,
  wypełniało jeden argument. Wypełnienie argumentu należy do treści promptu, a nie do katalogu
  narzędzi; nagłówek składa się per turę, więc leży POZA cache'owanym prefiksem.
- **Sekcja `ENVIRONMENT` promptu opisuje montaże, a nie świat narzędzi.** Warianty są dwa i wybiera
  je ta sama flaga co katalog narzędzi: bez powłoki baza wiedzy nadal „lives behind tools",
  z powłoką korpus wymienia `/mnt/system/notes/`, `/mnt/system/projects/`, `/mnt/skills/`
  i `/home/scratchpad/` wraz z granicą zapisu. Do tej zmiany blok STATYCZNY — najbardziej
  autorytatywny i cache'owany — zaprzeczał zdolności, którą agent z powłoką ma, a sprostowanie
  żyło niżej w hierarchii. Mapa montaży wyprowadziła się przy okazji z opisu narzędzia `Bash`:
  układ świata jest własnością promptu, a trzymany w obu miejscach dawałby dwa źródła do
  synchronizacji przy następnym montażu.
- **`build_agent_runtime` wyprowadza korpus z `shell_available`**, gdy `system_prompt` jest `None`.
  Stała jako domyślna wiązała drzwi z powłoką z opisem świata BEZ powłoki, cicho i przez
  przeoczenie jednego argumentu.

### Naprawione

- **Lista procedur wchodzi do nagłówka sesji dopiero razem z powłoką.** Nagłówek mówi „read the
  one that fits before starting", a jedyną drogą do TREŚCI procedury jest `cat` w wykonawcy:
  narzędzia plikowe są domknięte w scope'ie rozmowy i `/mnt/skills` nie widzą. Martwa obietnica
  tej samej klasy co dawne `/mnt/user/outputs`.
- **Bramka spójności wersji obejmuje badge w `README.md`.** Badge mówił **1.3.2**, czyli trzy
  wydania wstecz — porównanie szło pakiet ↔ `__version__` ↔ Dockerfile ↔ compose deweloperski,
  więc badge nie miał gdzie się zapalić, a paczka wdrożeniowa sprawdza WŁASNĄ kopię README.
- **Dispatcher `GitHub` ma jawną ostatnią gałąź** — dotąd domyślną był ZAPIS, więc przyszła akcja
  bez własnej gałęzi wpadłaby w zapis.
- **Migawka skrzynki zamyka wstrzykiwanie załączników między rozmowami**, `killpg` jest
  bezwarunkowy, wysyłka ma sufit prób, a retry HTTP przestało się dublować.
- **Komenda `/moje-zadania` odzyskana** po konsolidacji Jiry.

### Uwaga wdrożeniowa

Nowe zdolności stoją za bramkami domyślnie WYŁĄCZONYMI (`WORKMATE_ENABLE_SHELL`,
`WORKMATE_TEAMS_GRAPH_ENABLE_FILE_REPLY`), więc bez zmiany `.env` wdrożenie 1.6.0 nie zmienia
powierzchni widzianej przez użytkowników. Powłoki nie wolno włączać na kanałach, których
uczestnicy nie ufają sobie wzajemnie — rozmowy dzielą wolumen brudnopisu, a izolacja jest dziś
zakresowa, nie techniczna (ADR 0010 paczki wdrożeniowej). `preflight.sh` paczki odmawia startu
przy włączonej powłoce na obrazie starszym niż 1.6.0.

## [1.5.0] — 2026-08-05

Wydanie harnessu agenta: model dostaje powłokę w kontenerze bez sieci, wyszukiwarkę notatek
jako komendę tej powłoki, prompt systemowy rozdzielony na część cache'owaną i część per turę
oraz gospodarkę kontekstem, która zdejmuje stare wyniki narzędzi, zanim rozmowa urośnie do
kompaktowania. Wersja pakietu i wersja obrazu, rozjechane od 1.3.x, są tu z powrotem tą samą
liczbą.

### Dodane
- **Prompt systemowy jako DWA bloki** (ADR 0056). Statyczny korpus (niezmienny między turami,
  na nim siada breakpoint cache'u prefiksu `tools+system`) i nagłówek sesji składany PER TURĘ —
  data i tożsamość rozmowy. Dotąd agent nie znał bieżącej daty, więc „w zeszłym tygodniu"
  nie miało punktu odniesienia. Korpus przepisany po angielsku, bez negacji i nacisku
  wersalikami; reguły redakcyjne są bramką w `tests/core/test_prompt.py`, sprawdzaną na
  wszystkich czterech artefaktach promptu (korpus, wariant multimodalny, prompt kompaktowania,
  nagłówek sesji).
- **Kontener-wykonawca bez sieci i narzędzie `Bash`** (ADR 0057). Kod napisany przez model biegnie
  w OSOBNYM kontenerze (`network_mode: none`, `read_only`, nie-root), rozmawiającym z aplikacją
  przez gniazdo unix — uprawnienia pliku gniazda są jedyną kontrolą dostępu do powłoki.
  Bezpieczeństwo bierze się z tego, czego w tamtym kontenerze NIE MA, a nie z oceniania treści
  polecenia: baza wiedzy zamontowana `ro`, brak trasy do sieci, `curl` poza obrazem. Każde
  polecenie startuje w katalogu TEJ rozmowy pod `/home/scratchpad`, zakładanym przez aplikację
  (leniwe zakładanie dawało wszystkim rozmowom wspólny katalog). Limity: `WORKMATE_SHELL_TIMEOUT_S`
  (domyślnie 60 s, sufit 300 s po stronie wykonawcy), wyjście przycinane do 64 KiB ze
  znacznikiem `truncated`. Bramka `WORKMATE_ENABLE_SHELL` (domyślnie `false`) jest OSOBNA od
  `WORKMATE_ENABLE_WORKSPACE` — tam model tworzy pliki narzędziem typowanym, tu uruchamia
  dowolny kod, więc wspólna bramka włączałaby powłokę po cichu.
- **`workmate-search` — wyszukiwarka notatek dla powłoki agenta.** Ten sam ranker BM25 nad
  lematami polskimi, co narzędzie agenta i komenda `/szukaj` (jedno źródło budowy w
  `build_notes_service`). Dopasowanie wzorca po polskim korpusie fleksyjnym gubi trafienia —
  `grep -rl "migracji"` nie znajduje notatki o „migracja" — więc powłoka dostaje ranker, nie grep.
  Flagi: `--project`, `--participant`, `--limit`, `--paths` (same ścieżki, do `xargs cat`),
  `--json`. Wymaga `WORKMATE_NOTES_DIR`; przy braku katalogu kończy kodem 1 i komunikatem,
  zamiast udawać brak wyników.
- **Opcjonalna polityka odpowiadania na kanale — `reply_policy`** (ADR 0060, SZKIELET pod
  wielokanałowe wdrożenie WorkMate). Nowa bramka „czy w ogóle odpowiadać", niezależna od
  dotychczasowej logiki wyboru wiadomości: `WORKMATE_TEAMS_GRAPH_REPLY_POLICY` = `all` (domyślnie —
  zachowanie identyczne jak przed tą zmianą, odpowiedź na każdą wiadomość od innego człowieka) albo
  `mention` (odpowiedź tylko po @wzmiance bota ALBO gdy bot już wcześniej odezwał się w danym
  wątku — „wątek przyklejony"). `WORKMATE_TEAMS_GRAPH_ALWAYS_REPLY` (format jak `WATCH`) dokłada
  kanały, które ZAWSZE zachowują się jak `all`, niezależnie od globalnej polityki. Nowa klasa
  `selection.ReplyPolicy` (`mode`, `always_reply`, `from_settings`, `should_engage`) i
  `_thread_engaged` (sygnał przyklejenia liczony z historii odpowiedzi Graph, przeżywa restart);
  `plan_channel`/`ChannelPoller` dostają opcjonalne `policy`/`channel`, domyślnie `None` — bez
  podania bramki zachowanie jest BITOWO identyczne jak przed ADR 0060 (zero zmiany dla obecnej
  produkcji, opt-in per wdrożenie). **Uwaga wdrożeniowa**: ta funkcja i rozszerzony odczyt
  Jiry/grafik Shifts (ADR 0059, patrz sekcja `[1.3.2]` poniżej) powstawały na dwóch różnych,
  równolegle uruchomionych kontenerach i do tej pory nie jechały razem w żadnym pojedynczym
  obrazie. **To wydanie jest pierwszym, które wysyła obie zdolności naraz** — a że obie ruszały
  `adapters/inbound/teams_graph/app.py` i `config.py`, zachowanie złożenia jest tu weryfikowane
  po raz pierwszy (patrz ADR 0060 §Consequences).

### Zmienione
- **Gospodarka kontekstem rozmowy** (ADR 0058). Stare wyniki narzędzi czyści Claude API
  (`clear_tool_uses_20250919`, beta `context-management-2025-06-27`) — czyszczony jest sam
  wynik, `tool_use` zostaje, więc model wie, że już pytał. Nowe zmienne:
  `WORKMATE_CONTEXT_EDITING_ENABLED` (domyślnie `true`), `..._TRIGGER_TOKENS` (100 000),
  `..._KEEP_TOOL_USES` (8) i `..._CLEAR_AT_LEAST_TOKENS` (40 000). `KEEP_TOOL_USES` musi być
  >= `WORKMATE_AGENT_MAX_TOOL_ITERATIONS` — start jest odrzucany przy mniejszej wartości, bo
  czyszczenie potrafi odpalić w środku tury i sięgnąć wyników zamówionych przed chwilą.
  Równolegle próg kompaktowania spada z efektywnych 700 000 (0,70 × okna 1M) do 150 000: dotąd
  mechanizm praktycznie nie odpalał, teraz będzie wołał model podsumowujący. Wyłączenie
  `WORKMATE_CONTEXT_EDITING_ENABLED=false` przywraca dawny kształt żądania co do bajtu.
  Wymaga `anthropic>=0.116` (extra `agent`) — na starszym SDK tura wywala się `TypeError`.
  Progi 100 000/150 000 pochodzą z literatury, nie z pomiaru na naszym ruchu — pierwsze
  strojenie po tym wydaniu, na podstawie logu `_log_applied_edits`.
- **Wersja pakietu i wersja obrazu z powrotem tą samą liczbą.** Rozjazd narósł do trzech
  wartości w sześciu miejscach (pakiet 1.3.2, obraz 1.4.0, compose deweloperski 1.3.0),
  bo podbicia były osobnymi czynnościami bez wspólnej bramki.

### Usunięte
- **`WORKMATE_CONTEXT_WINDOW_TOKENS` i `WORKMATE_COMPACTION_THRESHOLD_FRACTION`** (ADR 0058,
  amends ADR 0014). Próg kompaktowania przestał być ułamkiem okna modelu; zastępuje je
  `WORKMATE_COMPACTION_THRESHOLD_TOKENS`. **Uwaga wdrożeniowa:** nieznana zmienna nie jest
  błędem, więc wdrożenie, które którąkolwiek z usuniętych ustawiało, straci nadpisanie po
  cichu — sprawdzić `.env` na serwerze przed rolloutem.

## [1.3.2] — 2026-08-04

### Dodane
- **Rozszerzony ODCZYT Jiry** (ADR 0059, kontynuacja ADR 0054): oprócz „moich zadań" dochodzą
  `get_my_jira_history` (moja historia zakończonych zadań, opcjonalne okno dat), `get_jira_task`
  (szczegóły JEDNEGO zgłoszenia po kluczu + do 5 ostatnich komentarzy), `search_jira_tasks`
  (wyszukiwanie po tekście/projekcie/kategorii statusu) oraz `get_member_jira_tasks`/
  `get_member_jira_history` (otwarte/zakończone zadania INNEGO członka pionu — konto Jira
  rozwiązywane WYŁĄCZNIE przez zaufaną mapę tożsamości, `resolve_by_display_name`; nieznana albo
  niejednoznaczna osoba dostaje czytelną odmowę, nie zgadywanie). Zero nowej mutacji — wszystko
  nadal czysty odczyt (gwarancja fail-closed z ADR 0054 zachowana: tożsamość wołającego nigdy nie
  jest parametrem narzędzia). Nowe `core/application/jira_read.py` (`JiraReadService`),
  `core/domain/names.py` (`normalize_name`/`match_name` — dopasowanie WYŁĄCZNIE na zaufanym
  zbiorze kandydatów), rozszerzenia `core/domain/jira_tasks.py` (`JiraComment`, `JiraTaskDetails`,
  `escape_jql_string`, `build_search_jql`, `build_history_jql`, `split_by_assignment`).
- **Grafik Teams Shifts — odczyt zmian i nieobecności zespołu** (ADR 0059). Nowe narzędzie
  `get_team_schedule` (tydzień bieżący/poprzedni/następny albo jawny zakres dat, opcjonalnie
  zawężone do jednej osoby po nazwisku; forma pracy stacjonarnie/zdalnie wywnioskowana z koloru
  zmiany). Autoryzacja jest cichym tokenem MSAL POŻYCZONYM z cudzego, tylko-do-odczytu cache
  tokenu bota powiadomienia-teams (`adapters/outbound/msal_silent_token.py`) — workmate nie loguje
  się osobno i nigdy nie zapisuje tego cache. Nowe `core/domain/schedule.py`,
  `core/application/team_schedule.py`, `core/ports/schedule.py`,
  `adapters/outbound/graph_schedule_api.py`, `config.ScheduleSettings` (`enabled="auto"` — cichy
  no-op tam, gdzie cudzy cache tokenu nie jest zamontowany).

## [1.3.1] — 2026-08-03

### Usunięte
- **Jira zredukowana do jednej, wyłącznie odczytowej zdolności „moje zadania"** (ADR 0054,
  supersedes ADR 0031 [zapis], ADR 0032 [tranzycja]; amends ADR 0028, ADR 0030). Usunięte w
  całości: poller Jira→`EventStore` (proces `workmate-jira`), push zdarzeń Jira→Teams, most
  Teams↔Jira, narzędzia zapisu `create_jira_issue`/`comment_jira_issue`/`transition_jira_issue`
  oraz cały pakiet `adapters/inbound/jira/`. Zostały: `JiraReadPort` (`authenticated_account`,
  `search_issues`), klienci `HttpxJiraClient`/`HttpxJiraCloudClient` (metody zapisu/tranzycji
  usunięte) i `JiraSettings` przycięty do `base_url`/`token`/`deployment`/`email`/`my_account`
  (zniknęły `watch_projects`, `poll_interval_s`, `per_page`, `state_path`, `self_account`,
  `enable_jira_write`, `write_project`, `default_issue_type`, `enable_jira_transition`,
  `max_transition_hops`). Stary preflight pollera (`deploy/jira/preflight.py`) zastąpiony
  READ-ONLY wersją (auth + próbne `search_issues` + opcjonalna weryfikacja tożsamości AAD).
- **Karty czasu WorklogPRO wycofane z projektu w całości** (ADR 0055, supersedes ADR 0035/0036/
  0037/0038; NIE dotyczy ADR 0034 — `propose_worklog` zostaje bez zmian). To decyzja trwała, nie
  pauza. Usunięte: generowanie cotygodniowych arkuszy WorklogPRO i wysyłka DM, self-service na
  żądanie, cała domena (`timesheet.py`, `timesheet_sheet.py`, `timesheet_message.py`,
  `shift_hours.py`, `issue_attribution.py`, `submitted_summary.py`, `day_comment.py`) i warstwa
  aplikacji/adapterów (`weekly_timesheets.py`, `selfservice_worklog.py`, `shift_hours_source.py`,
  `graph_shift_source.py`, `github_commit_source.py`, `claude_summary_store.py`,
  `json_hours_source.py`, `openpyxl_sheet_writer.py`), drzwi `adapters/inbound/worklogi/` i
  `adapters/inbound/worklog_selfservice/`, `WorklogiSettings`, extra `worklogi` oraz entrypointy
  `workmate-worklogi`/`workmate-worklog-selfservice`.

### Dodane
- **Narzędzie `get_my_jira_tasks`** (ADR 0054) — zero parametrów, zwraca TYLKO otwarte zadania
  Jira przypisane PYTAJĄCEMU (nigdy zadania kogoś innego). Dwie powierzchnie: komenda Teams
  `/moje-zadania` (alias `/zadania`, tożsamość z mapy AAD→Jira `WORKMATE_TEAMS_GRAPH_IDENTITIES`,
  pole `jira_user`) oraz narzędzie MCP na drzwiach stdio (Claude Code/CLI), wchodzące TYLKO gdy
  ustawiono `WORKMATE_JIRA_MY_ACCOUNT` — jeden, z góry skonfigurowany principal, nienadający się na
  współdzielony serwer HTTP z wieloma osobami.

### Zmienione
- **BREAKING: `WORKMATE_ENABLE_WRITE` domyślnie `false` wszędzie** (amendment ADR 0006,
  2026-07-31) — dotąd lokalne drzwi stdio (Claude Code/`workmate-agent`) miały to domyślnie
  `true`, jedyny udokumentowany wyjątek od „każda zdolność mutująca domyślnie OFF". Po pullu
  `save_note` znika z lokalnego MCP, dopóki nie ustawisz jawnie `WORKMATE_ENABLE_WRITE=true`
  w `.env` (patrz `.env.example`).
- **Tożsamość skonsolidowana**: `Person` i mapa AAD→Jira przeniesione z `core/domain/timesheet.py`
  do nowego `core/domain/identity.py` (bez pola `git_email`, specyficznego dla worklogu); port
  `AadIdentityLookup` przeniesiony z `core/ports/timesheets.py` do nowego `core/ports/identity.py`.
  Katalog tożsamości `adapters/outbound/graph_identity_directory.py` przycięty do samego
  `YamlIdentityDirectory` (usunięte `GraphIdentityDirectory`, `fetch_team_members`,
  `resolve_by_git_email` — używane wyłącznie przez usunięte drzwi worklogu).
- `graph_teams_notifier.py` — usunięta metoda `send_chat_html` (używana wyłącznie przez
  wiadomości WorklogPRO); reszta notifiera bez zmian.

### Naprawione
- **`get_my_jira_tasks` na drzwiach HTTP: gwarancja konstrukcyjna, nie tylko konwencja.**
  `_build_http_server` wymusza teraz `transport="streamable-http"` (obok istniejącego
  `enable_write=False`), więc `build_server` blokuje rejestrację "moich zadań" na tym transporcie
  NIEZALEŻNIE od `WORKMATE_JIRA_MY_ACCOUNT` w env — jeden principal na proces nie może bezpiecznie
  obsłużyć wielu osób na współdzielonym serwerze HTTP (ta sama klasa gwarancji co `save_note`,
  ADR 0007).

## [1.3.0] — 2026-07-21

### Dodane
- **Bramka potwierdzenia nagłówków WorklogPRO** — `WORKMATE_WORKLOGI_HEADERS_CONFIRMED` (domyślnie
  `false`). `WORKLOGPRO_HEADERS` pochodzi z dokumentacji producenta, nie z kreatora importu tej
  instancji, a WorklogPRO dopasowuje kolumny PO NAZWIE — jedna literówka unieważnia KAŻDY plik.
  Dotąd ta hipoteza żyła w komentarzu, więc pierwszy przebieg bojowy mógł rozesłać kilkanaście
  bezużytecznych arkuszy. Teraz `DRY_RUN=false` bez potwierdzenia = twardy błąd startu; tryb
  próbny działa bez zmian, bo to on generuje plik do porównania z szablonem.
- **Ponawianie błędów przejściowych na Graphie — TYLKO tam, gdzie powtórzenie jest bezpieczne.**
  5xx i timeout dostają krótki, rosnący backoff przy odczytach (`GET`) oraz przy tworzeniu czatu
  1:1 (Graph oddaje dla tej samej pary czat ISTNIEJĄCY, więc żądanie jest idempotentne). Przebieg
  jest COTYGODNIOWY, więc jedno 503 kosztowało człowieka cały tydzień: „następna próba" oznaczała
  następny piątek. **Wysyłka wiadomości i post na kanale ponawiane NIE są** — timeout znaczy „nie
  wiadomo, czy usługa przyjęła", a powtórzenie dołożyłoby drugą wiadomość (drugi arkusz do
  zaimportowania, wpisy w Jirze nieusuwalne) albo drugi root wątku z osieroconym pierwszym.
  429 ponawiamy wszędzie: oznacza odrzucenie PRZED przetworzeniem.
- **Niekompletna lista członków zespołu = twardy błąd**, nie ciche ucięcie na capie stron.
  Katalog tożsamości jest fail-closed, więc osoba, która wypadła z paginacji, wygląda w raporcie
  identycznie jak ktoś, kto nie pracował. Przy zespole kilkunastu osób wyczerpanie dziesięciu
  stron jest anomalią — lepiej nie wysłać nic i zostawić głośny ślad niż pominąć kogoś po cichu.

### Naprawione
- **Karty czasu raportują tydzień ZAMKNIĘTY, nie bieżący** (ADR 0035 § Consequences — korekta
  nieprawdziwej tezy w dokumentacji). Twierdziliśmy, że godziny po piątkowym terminie „wpadają do
  raportu za tydzień". Nie wpadały: kolejny przebieg raportował własny tydzień bieżący, więc
  sobota, niedziela i piątkowe popołudnie nie trafiały do ŻADNEGO arkusza NIGDY. Okno zamknięte
  jest kompletne z definicji — całe leży w przeszłości. Koszt: zestawienie sprzed 3–12 dni.
- **Wstrzyknięcie formuły do arkusza (`=cmd|'/c calc'!A0`).** openpyxl wnioskuje typ z treści, więc
  komentarz zaczynający się od `=` stawał się FORMUŁĄ w pliku, który JAWNIE każemy człowiekowi
  otworzyć — a źródła godzin jeszcze nie znamy. `SheetWriter` ma teraz kontraktowy obowiązek zapisu
  KAŻDEJ komórki jako tekstu (adapter wymusza typ po przypisaniu wartości), a rdzeń wycina znaki
  sterujące — czyli tę sanityzację, którą docstring `WorkEntry` deklarował i której nikt nie robił.
  Treść zostaje NIETKNIĘTA: żadnego apostrofu ani obcięcia, bo komentarz ma dojechać do Jiry taki,
  jaki był.
- **`OUTPUT_DIR` chroniony też przed repozytorium**, nie tylko przed `data/`. Dokumentacja
  obiecywała „poza `data/` I poza repo" od początku, kontrola sprawdzała pierwszą połowę — więc
  `OUTPUT_DIR=arkusze` kładł imienne godziny w drzewie roboczym, gotowe do `git add .`.
  Dołożone `*.xlsx` w `.gitignore` jako druga linia obrony.
- **Kolizja nazw arkuszy między imiennikami.** Nazwa pliku brała się z `display_name`, który nie
  jest różnowartościowy — dwie osoby o tej samej nazwie dostawały tę samą ścieżkę, więc drugi
  arkusz NADPISYWAŁ pierwszy, a wiadomość pierwszej osoby wskazywała cudze godziny. `_slug` zwężał
  przestrzeń jeszcze bardziej (nazwa bez ASCII → stałe `bez-nazwy`). Nazwa niesie teraz `source_id`.
- **Wiadomość rozjeżdżała się z arkuszem.** Tabela pokazywała wpisy zerowe (urlop), których arkusz
  nie zawiera, a czas per wiersz szedł jako zaokrąglone godziny dziesiętne przy sumie liczonej
  z MINUT — kolumna nie sumowała się do „Razem" (3 × 50 min = 2.49 ≠ 2.5). Oba dokumenty mają teraz
  te same wiersze i tę samą notację czasu.

### Usunięte
- **Ścieżka zapisu ewidencji czasu do Jiry** (ADR 0034 →
  [`superseded in part by 0035`](docs/adr/0034-jira-worklog-from-github-commits.md)). Cała
  konstrukcja istniała, żeby obejść jeden mur: Jira przypisuje worklog kontu tokenu i ignoruje pole
  `author`, więc czas „w imieniu" innej osoby wchodził do raportów jako czas konta usługowego.
  ADR 0035 obalił mur — arkusz WorklogPRO importuje sam pracownik, więc autor jest prawdziwy.
  Obejście straciło przedmiot i było w całości bez konsumenta (~870 linii rdzenia).
  Zniknęły: narzędzie `log_jira_worklog`, moduł `core/application/worklog_author.py` (trzy
  strategie autorstwa, z czego dwie były slotami `NotImplementedError`), port `JiraWorklogPort`
  wraz z `add_worklog`/`read_worklogs` w obu adapterach Jiry, strażnik duplikatów oraz echo
  zdarzenia `jira_worklog`.
- **Zmienne `WORKMATE_JIRA_WORKLOG_*` i bramki `WORKMATE_JIRA_ENABLE_WORKLOG` /
  `..._WORKLOG_ALLOW_ON_BEHALF`** — ZMIANA ŁAMIĄCA konfigurację (stąd MINOR, nie PATCH). Bez
  zamiennika znikają `MAX_HOURS`, `MAX_BACKDATE_DAYS`, `AUTHOR_STRATEGY`, `DUPLICATE_GUARD`
  i obie bramki; pokrętła estymacji przeniosły się (patrz *Zmienione*).

### Zmienione
- **`propose_worklog` przeniesione pod GitHuba i BEZ bramki.** Po cięciu narzędzie nie dotyka Jiry
  (klucze zgłoszeń wyłuskuje regexem z treści commitów), a niczego nie mutuje — więc wchodzi, gdy
  tylko skonfigurowany jest GitHub, zgodnie z zasadą repo „odczyt domyślny, bramkujemy zapis"
  (ADR 0006). Pokrętła estymacji migrują `JiraSettings` → `GithubSettings` jako
  `WORKMATE_GITHUB_WORKLOG_IDLE_GAP_MINUTES`, `..._RAMP_UP_MINUTES`, `..._ROUND_MINUTES`,
  `..._MAX_SESSION_HOURS` (dawne `MAX_HOURS` — to sufit JEDNEJ sesji, mimo nazwy nigdy nie należał
  do zapisu), `..._MAX_RANGE_DAYS` i `..._TZ`. Nowe `GithubSettings.validate_worklog_limits()`
  wołane z OBU stron (poller i drzwi Teams) — sufity muszą działać tam, gdzie liczy się estymację.
- **Strefa czasowa estymacji: stały offset → `ZoneInfo`** (`SessionPolicy.tz`, domyślnie
  `Europe/Warsaw`). Uzasadnienie z ADR 0034 („czystość domeny") upadło, gdy ADR 0035 wprowadził
  `week.py` liczący granice przez `ZoneInfo` i dodał `tzdata` do zależności rdzenia. Ze stałym
  offsetem granica doby przez pół roku wypadała o godzinę obok, więc commity z okolic północy
  trafiały do sąsiedniego dnia. Literówka w nazwie strefy = twardy błąd startu, nie awaria przy
  pierwszym użyciu narzędzia.
- **Ucięcie historii commitów przestaje być ciche.** `list_commits` oddaje najwyżej
  `MAX_COMMITS_PER_FETCH` = 500 pozycji (sufit jest teraz CZĘŚCIĄ KONTRAKTU portu, nie szczegółem
  adaptera) i liczy OD NAJNOWSZYCH, więc szerokie okno na aktywnym repo gubiło najstarsze dni,
  a propozycja prezentowała zaniżony wynik jako kompletny. Pełne wiadro daje jawną notę w `notes`.
- **`WorkSession.shas` to PRÓBKA (5), nie pełna lista.** Przy pełnym wiadrze odpowiedź niosła
  kilkanaście kilobajtów SHA-ów do kontekstu modelu przy każdym wywołaniu; pełną liczbę i tak
  niesie `commit_count`.
- **Zestawienie nie zawęża się już do jednego projektu Jiry.** Filtr brał prefiks z
  `WORKMATE_JIRA_WRITE_PROJECT`, czyli z celu ZAPISU — bez zapisu nie ma dokąd kierować
  zestawienia, a `OPS-9` wspomniane w commicie opisuje pracę, która naprawdę się odbyła.
  `by_issue` niesie teraz każdy klucz znaleziony w wiadomościach, `unattributed_hours` maleje.
- **`propose_worklog` odrzuca też okno KOŃCZĄCE się w przyszłości** (dotąd tylko początek) —
  literówka w roku dawała pustą końcówkę udającą brak pracy. Walidacja wejścia na ścieżce
  odczytu rzuca nowy `InvalidRequestError` zamiast `WriteError` (ta sama koperta, uczciwa nazwa).
- **Klienty HTTP drzwi Teams są domykane** (`atexit`) i **współdzielone**: jeden klient GitHuba
  obsługuje odczyt commitów i zapis, zamiast dwóch pul do tego samego hosta.

## [1.2.0] — 2026-07-21

### Dodane
- **Cotygodniowe karty czasu → arkusz WorklogPRO + prywatna wiadomość na Teams**
  ([ADR 0035](docs/adr/0035-weekly-per-person-worklogpro-sheets-and-teams-dm.md)): nowe drzwi
  `workmate-worklogi` (extra `worklogi`, bramka OFF, domyślnie tryb PRÓBNY). W piątek biorą godziny
  za mijający tydzień, generują KAŻDEJ osobie arkusz importu WorklogPRO i wysyłają jej prywatną
  wiadomość 1:1 z tabelą godzin i ścieżką pliku; wiadomość dostają tylko osoby, które pracowały.
  Zmiana kierunku wobec ADR 0034: skoro import wykonuje sam pracownik, worklog ma **prawdziwego
  autora** — obejście „w imieniu" przestaje być potrzebne.
  Nowy czysty rdzeń: `core/domain/week.py` (termin piątkowy i okno tygodnia przez `ZoneInfo`,
  odporne na DST), `timesheet.py` (agregacja w minutach, predykat `worked()`), `timesheet_sheet.py`
  (projekcja na kolumny WorklogPRO, notacja czasu bez `1d`, pełne ISO z offsetem),
  `timesheet_message.py` (tabela HTML z `html.escape` na każdej wartości),
  `application/weekly_timesheets.py` (izolacja per osoba, arkusz PRZED wiadomością, `RunReport`).
  Nowe porty `HoursSource`/`SheetWriter`/`IdentityDirectory` oraz adaptery: zapis xlsx (openpyxl —
  pierwszy ZAPIS Excela w repo), atrapa źródła na JSON, katalog tożsamości Graph + YAML.
  Strażnik `assert_single_person` woływany DWA razy (po agregacji i na granicy zapisu).
- **`TeamsNotifier.send_chat_html`** — wysyłka gotowego HTML 1:1 z pominięciem renderera Markdown.
  Konieczna, bo `to_teams_html` escapuje surowy HTML i nie włącza tabel. Bezpieczna wyłącznie dzięki
  kontraktowi: treść składa czysta funkcja rdzenia. `send_chat` bez zmian (test regresyjny pilnuje,
  że nadal escapuje).
- **`TeamMember.Read.All`** w `_DEFAULT_TEAMS_PUSH_SCOPES` — lista członków zespołu z Graph.
  Bez nowej zgody admina: ta sama rejestracja aplikacji i ten sam cache MSAL co `Powiadomienia_teams`.
- **`tzdata`** jako zależność rdzenia z markerem `sys_platform == 'win32'` (Windows nie ma
  systemowej bazy stref, Linux ma).
- **Ewidencja czasu w Jirze z historii commitów GitHub — SZKIELET**
  ([ADR 0034](docs/adr/0034-jira-worklog-from-github-commits.md)): rdzeń kompletny i pokryty testami,
  adaptery oraz wpięcie w drzwi obecne, bramka domyślnie OFF. Nowa czysta domena
  `core/domain/worklog.py` (commity → sesje pracy; cięcie po przerwie **lub** dobie kalendarzowej,
  rozbieg, zaokrąglanie w górę, `confidence`, rachunki w MINUTACH — sumy dzienne i per zgłoszenie
  zgadzają się co do minuty). Nowy port `JiraWorklogPort` (trzeci obok read/write) i
  `GithubReadPort.list_commits`. Serwis `WorklogService` daje **DWA KROKI**: `propose_worklog`
  (odczyt, zero mutacji) i `log_jira_worklog` (jeden wpis, godziny i dzień podane wprost) — sklejenie
  ich zamieniłoby wiadomości commitów w polecenia zapisu (ADR 0006). Nowy szew
  `core/application/worklog_author.py`: `SelfAuthorStrategy` (zaimplementowana) plus udokumentowane
  sloty `PerUserTokenStrategy`/`TempoWorklogStrategy`. **Jira nie pozwala ustawić autora worklogu** —
  `on_behalf_of` jest adnotacją w treści, nie atrybucją; adaptery świadomie nie wysyłają pola
  `author`, a wynik niesie jawne `note`. Konfiguracja: `WORKMATE_JIRA_ENABLE_WORKLOG` (OFF),
  osobna, węższa `..._WORKLOG_ALLOW_ON_BEHALF` (OFF) i dziewięć pokręteł estymacji/sufitów
  z walidacją fail-fast. Narzędzia `propose_worklog`/`log_jira_worklog` wchodzą przez
  `extra_catalog` — zamrożona powierzchnia MCP (4+1) bez zmian.

### Zmienione
- Wspólne strażniki zapisu do Jiry (`bounded`, `require_jira_key` — pełny kształt `PROJ-123`,
  blokada path-traversal) wydzielone do `core/domain/guards.py`, a parsowanie znaczników Jiry do
  `core/domain/jira_time.py`. Powód: zdolności mutujące urosły do dwóch serwisów, a duplikowanie
  kontroli bezpieczeństwa to dryf. Zachowanie i komunikaty błędów bez zmian.
- **Wsparcie Jira Cloud (dual-provider)** ([ADR 0033](docs/adr/0033-jira-cloud-support.md)): most Jira
  obsługuje teraz OBA warianty za przełącznikiem `WORKMATE_JIRA_DEPLOYMENT` (`server` domyślnie —
  wstecznie zgodne; `cloud`). Nowy `HttpxJiraCloudClient` (REST v3): Basic auth (`WORKMATE_JIRA_EMAIL`
  + API token), `POST /search/jql` z paginacją kursorową (`nextPageToken`/`isLast`, bez `total`, obrona
  przed zapętleniem), tożsamość po `accountId`, treść jako **ADF** (kodowana przy zapisie, spłaszczana do
  tekstu przy odczycie — NA GRANICY adaptera, więc `selection`/poller/serwisy bez zmian). Nowy czysty
  moduł `core/domain/adf.py` (`text_to_adf`/`adf_to_text`). Fabryka `build_jira_client` wybiera
  implementację wg `deployment` (jedno źródło; oba wpięcia ją wołają). Ścieżka Server/DC, jej klient i
  testy — nietknięte. Znane ograniczenie: bulk `/search/jql` ucina inline changelog/komentarze do 20/20
  (dla pollera inkrementalnego wystarcza; fallback per-issue jako follow-up).

## [1.1.0] — 2026-07-18

### Dodane
- **Fundament projekt↔repo↔Jira** ([ADR 0028](docs/adr/0028-project-repo-jira-mapping-and-event-dimension.md)):
  rejestr z `github_repos`/`jira_project_key` + reverse-lookup; wymiar `project`/`repo` w zdarzeniu
  (migracja `ADD COLUMN`); filtr `project` w `read_recent_events`; dedup multi-repo (`composite_external_id`).
- **Realny stan branchy/PR** ([ADR 0029](docs/adr/0029-branch-pr-state-transitions-and-project-activity.md)):
  endpointy `list_pulls`/`list_branches`; atrybucja zdarzeń do projektu; tranzycje `pr_merged`/`pr_closed`;
  zdarzenia `branch_pushed`/`branch_deleted` (SHA-diff); narzędzie `get_project_activity`; wzbogacony
  `get_project_status` (aktywność GitHub, porażki CI). Nowe watch-kindy `pull_state`, `branches` (opt-in).
- **Drzwi Jira read (Server/Data Center)** ([ADR 0030](docs/adr/0030-jira-server-read-door.md)): delegowany
  polling REST v2 tokenem PAT (`workmate-jira`, extra `jira`) → wspólny `EventStore` → Teams. `JiraSettings`
  + `JiraReadPort`/`HttpxJiraClient` (B1.1); `jira/selection` mapuje issue na zdarzenia `jira_issue_created`
  /`jira_transition`/`jira_comment` (dedup tranzycji po `id` wpisu changelogu, watermark JQL po `updated`,
  self-skip konta PAT), poller + entry point + notifier (B1.2). Atrybucja `project` per issue z rejestru
  (`jira_project_key`). Notifier zgeneralizowany: etykieta źródła z `event.source` (`[Jira]`/`[GitHub]`),
  źródło konsumpcji konfigurowalne. Read-only (bez nowej bramki zapisu); wątkowanie kanału OFF w B1.
- **Zapis do Jira (Gate 5, create-only)** ([ADR 0031](docs/adr/0031-jira-write-capability-gate-5.md)):
  bramkowana zdolność mutująca `create_jira_issue` + `comment_jira_issue` (odpowiednik Gate 4 GitHuba).
  Osobny `JiraWritePort`/`JiraWriteService` (rdzeń), bramka `enable_jira_write` per drzwi (domyślnie OFF,
  wystawiana na drzwiach agenta `teams_graph` przez `extra_catalog` — powierzchnia MCP nietknięta). Projekt
  tworzenia z konfiguracji (`WORKMATE_JIRA_WRITE_PROJECT`), nie z treści; komentarz waliduje PEŁNY kształt
  klucza (`PROJ-123`) i zgodność projektu — blokuje obejście cross-project (także path-traversal `WM-1/../X`).
  Strażnik pętli: echo zapisu jako `source="teams"` (notifier `source="jira"` go nie odsyła) + self-skip PAT
  w pollerze; fail-fast walidacji sprzecznej konfiguracji. Tranzycja statusu odłożona do ADR 0032.
- **Tranzycja statusu Jira** ([ADR 0032](docs/adr/0032-jira-status-transition-capability.md)): bramkowane
  narzędzie `transition_jira_issue` — best-effort „walk" po workflow. Model podaje status/akcję docelową →
  serwis dopasowuje ją do widocznej tranzycji (akcja > status, case-insensitive) → `POST /transitions`; id
  tranzycji nigdy nie pochodzi od modelu. NIEZALEŻNA bramka `enable_jira_transition` (domyślnie OFF; profil
  „tylko-tranzycja" bez zapisu) współdzieli szew Gate 5 (`JiraWritePort`/`JiraWriteService`). Wielo-hop za
  `WORKMATE_JIRA_MAX_TRANSITION_HOPS` (domyślnie 1 = single-hop, bezpieczny pilotaż; sufit 10): forced-advance
  tylko przez stany WYMUSZONE, STOP na rozgałęzieniu (bez zgadywania), detekcja cyklu, limit hopów. **Brak
  rollbacku** — zawsze strukturalny raport (`reached`/`path`/`stop_reason`/`available_next`), echo `source=
  "teams"` per hop (`external_id=f"{key}:{updated}"`). Ten sam strażnik klucza/projektu i pętli co zapis.
- **Wątkowanie kanału dla Jiry (B2)** ([ADR 0024](docs/adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md),
  domknięcie odłożenia z [ADR 0030](docs/adr/0030-jira-server-read-door.md)): resolver wątków
  (`core/domain/threads.py`) rozpoznaje teraz URL-e Jira `/browse/{KEY}` → cel `("jira", KEY)`, więc
  utworzenie/tranzycja/komentarz tego samego zgłoszenia trafiają do JEDNEGO wątku na kanale. Wpięcie
  `SqliteThreadLinkStore` w drzwi `workmate-jira` (`_build_thread_links`, wzorzec GitHuba), za tą samą
  flagą `WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING` (domyślnie OFF). Klucz `kind="jira"` nie koliduje
  z `pr`/`issue` na wspólnym `events.db`; samowystarczalne w drzwiach Jiry (notifier wypełnia mapę).

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

[Unreleased]: https://github.com/BIAP-Inteligentne-Technologie/PIWorkmate/compare/v1.3.2...HEAD
[1.3.2]: https://github.com/BIAP-Inteligentne-Technologie/PIWorkmate/releases/tag/v1.3.2
[1.3.1]: https://github.com/BIAP-Inteligentne-Technologie/PIWorkmate/releases/tag/v1.3.1
[1.3.0]: https://github.com/BIAP-Inteligentne-Technologie/PIWorkmate/releases/tag/v1.3.0
[1.2.0]: https://github.com/BIAP-Inteligentne-Technologie/PIWorkmate/releases/tag/v1.2.0
[1.1.0]: https://github.com/BIAP-Inteligentne-Technologie/PIWorkmate/releases/tag/v1.1.0
[1.0.0]: https://github.com/BIAP-Inteligentne-Technologie/PIWorkmate/releases/tag/v1.0.0
