# Changelog

Wszystkie istotne zmiany w projekcie `powiadomienia-teams`. Format oparty na
[Keep a Changelog](https://keepachangelog.com/pl/1.1.0/); wersjonowanie
[SemVer](https://semver.org/lang/pl/). Decyzje projektowe: [`docs/adr/`](docs/adr/).

Numeracja wersji śledzi **tagi obrazu Dockera** (`powiadomienia-teams:X.Y.Z`) — to jedyne źródło
prawdy o iteracji na produkcji; metadane wewnątrz obrazu (`pyproject.toml`) bywały z nimi
rozjechane.

## [0.2.19] — 2026-08-20 (obraz produkcyjny; źródła odzyskane 2026-09-04)

Wersja DZIAŁAJĄCA u klienta. Źródła zostały odzyskane z obrazu `powiadomienia-teams:0.2.19`, bo
build powstał z drzewa roboczego, którego nigdy nie zacommitowano — ścieżki `runtime/`, `cli.py`,
`agent/tools.py`, `domain/czas.py` nie występują w żadnym commicie ani na jednym z 37 refów.

**Czego tu NIE ma:** wpisów dla 0.2.7–0.2.18. Tych obrazów nie da się zdiffować źródło-po-źródle,
bo ich źródeł nie ma — spisanie ich osobno byłoby zgadywaniem. Poniżej wyłącznie delta 0.2.6 →
0.2.19 ustalona maszynowo z odzyskanego kodu.

**Testów tej wersji nie ma.** Bramka jakości obrazu zaliczyła 792 testy (`/app/.testy-przeszly`),
ale zestawu nie zachowano. `tests/` w repo pochodzi od starszej linii: 44 testy padają, 6 modułów
się nie importuje.

### Zmienione — termin odpowiedzi przestał być liczbą godzin

- `REPLY_WINDOW_HOURS` **zniknął**. Zastąpiły go `REPLY_DEADLINE_OFFSET_H` (domyślnie 5) —
  termin kalendarzowy liczony od północy poniedziałku tygodnia DOCELOWEGO — oraz
  `REPLY_MIN_HOURS` (24), dolna granica kurtuazji od ostatniej prośby bota. Termin liczony jako
  `max(termin_kalendarzowy, teraz + min_h)` nie wypada już w przeszłości przy ujemnym przesunięciu.
- Treść prośby MÓWI o terminie. Wcześniej obietnica w wiadomości i zachowanie runtime'u mogły się
  rozjechać bez śladu, a rozjazd wychodził dopiero wtedy, gdy ktoś tracił tydzień grafiku.

### Dodane

- **Godziny ciszy** `CISZA_OD_H` (20) / `CISZA_DO_H` (7) — kiedy bot nie pisze do pracowników.
  Cisza PRZESUWA nadrabianie, a nie unieważnia go: `cisza_pomiedzy` odejmuje ciszę od okna łaski,
  bo inaczej usługa wstająca po awarii w piątek o 21:00 nie nadrobiłaby przebiegu nigdy.
  Odmowa z powodu ciszy ma własny wyjątek (`CiszaWstrzymalaPrzebieg`), bo pusty wynik był
  nierozróżnialny od „nikomu nie brakuje grafiku" i odhaczał termin jako obsłużony.
- **`PILOTAZ`** — deklaracja ZAMIARU zawężenia odbiorców, niezależna od samej listy. Pusta
  `ONLY_USER_IDS` znaczy „wszyscy", więc literówka w nazwie zmiennej zamieniała pilotaż w wysyłkę
  do całego zespołu, bezgłośnie. `PILOTAZ=true` przy pustej liście zatrzymuje start.
- **`ADMIN_USER_IDS`** — lista zamiast skalara. Podsumowanie jest dead man's switchem, a przy
  jednym odbiorcy jest martwe przez cały jego urlop. Stara `ADMIN_USER_ID` działa i SUMUJE SIĘ
  z listą (duplikaty odpadają), z ostrzeżeniem przy starcie.
- **`ALERTY_WYLACZONE`** — jawna rezygnacja z alertów przy `DRY_RUN=false`. Bez niej instalacja
  z przeoczonym webhookiem wygląda identycznie jak taka, w której zrezygnowano świadomie; teraz
  brak obu zatrzymuje start.
- **`LOGUJ_NAZWISKA`** (domyślnie `false`) — logi i alerty nazywają pracownika identyfikatorem,
  bo alert zostaje w kanale Teams bezterminowo i jest przeszukiwalny (A10).
- **`TERMINAL_RETAIN_HOURS`** (48) — jak długo trzymać wpisy zamknięte.
- **`RUN_DEADLINE_S`** (1800) — sufit czasu na JEDEN przebieg, jedyny limit obejmujący więcej niż
  jedno żądanie (`runtime/budzet.py`). `0` wyłącza.
- **`POWIADOMIENIA_AGENT_API_KEY`** jako nazwa kanoniczna klucza modelu; `ANTHROPIC_API_KEY`
  zostaje aliasem konwencji SDK, z ostrzeżeniem przy starcie.
- **Polecenia diagnostyczne:** `--stan` (raport stanu bez sieci i bez blokady instancji),
  `--proba-nasluchu` (obieg nasłuchu na żywym tenancie z odciętymi metodami zapisu i wysyłki),
  `--ignoruj-cisze` (nadrabianie po awarii w godzinach ciszy).

### Struktura

Rozbicie monolitu `app.py` na pakiet `runtime/` (`service`, `listener`, `wysylka`, `nudge`,
`domkniecia`, `przeglad`, `snapshot`, `budzet`, `cisza`, `etykiety`, `operator`) oraz wydzielenie
`cli.py`, `domain/czas.py`, `domain/powody.py`, `graph/tylko_odczyt.py`, `reminders/wzorzec.py`,
`agent/{tools,schema,kalendarz,odczyt}.py`. Z 28 plików źródłowych zrobiło się 48.

### Znane usterki tej wersji

- `--proba-nasluchu` bierze blokadę na PRODUKCYJNYM pliku stanu, mimo że pisze do osobnego.
  Przy działającej usłudze kończy się „Inna instancja już działa" — a dokumentacja stawia ją
  w sekwencji wdrożenia. Obejście: uruchomić na kopii wolumenu w osobnym kontenerze.
- Klient HTTP loguje URL żądania na poziomie INFO, więc `ALERT_WEBHOOK_URL` (bywa sekretem —
  token w ścieżce) trafia do logów kontenera.

## [Unreleased] — LINIA REPOZYTORIUM, NIEWDROŻONA

Scalone po 0.2.6 na gałęzi repozytorium i **nigdy niewydane jako obraz**. Ta linia rozwidliła się
z produkcyjną: opisane niżej okno wysyłki nie istnieje w 0.2.19, a arytmetyka sufitu jest tu
wyrażona przez `REPLY_WINDOW_HOURS` — klucz, którego produkcja nie czyta (zastąpiony terminem
kalendarzowym, patrz 0.2.19). Zachowane jako zapis zamiaru; przeniesienie na obecne źródła wymaga
przepisania, nie merge'a. Patrz ADR 0005, sekcja „Production status".

### Dodane

- **Okno wysyłki wiadomości INICJOWANYCH przez bota** (`SEND_WINDOW_START_HOUR` = 8,
  `SEND_WINDOW_END_HOUR` = 18, `SEND_WINDOW_WEEKDAYS` = `0,1,2,3,4`; godziny lokalne zespołu,
  przedział `[start, end)`). Dotyczy trzech rodzajów wiadomości, które bot zaczyna sam:
  cotygodniowej prośby, domknięcia po wygaśnięciu okna odpowiedzi i podziękowania za samodzielne
  uzupełnienie grafiku. **Odpowiedź na wiadomość pracownika oknu NIE podlega** — rozmowę zaczął on
  i czeka na reakcję. Poza oknem wiadomość CZEKA na najbliższe otwarcie; status terminalny
  utrwalany jest od razu, więc decyzja o zamknięciu tematu przeżywa restart nawet wtedy, gdy
  uprzejmość jeszcze nie wyszła. Nieudana wysyłka już odłożonego domknięcia nie jest ponawiana — to
  uprzejmość, nie zapis, a ponawianie groziłoby serią. Czekanie ma własny sufit
  (`3 × REPLY_WINDOW_HOURS` od ostatniej aktywności wpisu): po nim odłożone domknięcie jest
  PORZUCANE, bo spóźniona o kilka dni uprzejmość jest zagadką, a wpis z niewysłaną wiadomością
  zostaje poza zasięgiem `prune_terminal`. Przy domyślnych 48 h weekend sufitu nie dosięga; przy
  `REPLY_WINDOW_HOURS=8` (sufit 24 h) odłożenie z piątku wieczorem na poniedziałek rano — ~62 h —
  domknięcie kasuje. W trybie próbnym okno jest zawsze otwarte:
  nic stąd nie wychodzi, a blokowanie próby po godzinach odbierałoby operatorowi możliwość
  sprawdzenia wdrożenia o dowolnej porze. Czysta logika kalendarzowa w
  `scheduler/send_window.py` (wstrzykiwany `moment`, brak I/O). Patrz ADR 0005.
- **Trzeci wynik przebiegu — `ODLOZONY`** obok `UDANY`/`NIEUDANY`. Naprawy są przeciwstawne:
  nieudany przebieg ponawiamy szybko w granicach okna łaski, odłożony CZEKA na otwarcie okna,
  czasem przez cały weekend. Odłożenie **ZAWIESZA** odliczanie `CATCHUP_GRACE_HOURS` — cisza nie
  jest zużytą szansą. Wcześniejsze zlanie obu w jedno `False` sprawiało, że łaska licząca się od
  piątku 16:00 wygasała o 22:00, a okno wysyłki otwierało się dopiero w poniedziałek — tydzień
  przepadał bez śladu w logu. Patrz ADR 0005.

### Zmienione

- **Niespójna konfiguracja harmonogramu zatrzymuje start.** `RUN_WEEKDAY` musi należeć do
  `SEND_WINDOW_WEEKDAYS`, a `RUN_HOUR` mieścić się w `[START_HOUR, END_HOUR)`; puste
  `SEND_WINDOW_WEEKDAYS` jest odrzucane („nigdy" nie jest sposobem wyłączenia okna — pełny tydzień
  `0,1,2,3,4,5,6` jest). Bez tej kontroli `run_hour=20` przechodził bez słowa, a usługa nie
  wysyłała już nigdy nic w terminie: każdy przebieg odbijał się od godzin ciszy i przesuwał na
  następny dzień roboczy. Cisza wygląda identycznie jak sprawna praca.
- **Nierozpoznana wartość logiczna w konfiguracji to BŁĄD STARTU, nie „domyślnie fałsz".**
  Dotychczasowe „cokolwiek spoza listy prawd znaczy fałsz" było fail-open dla najważniejszej bramki
  w tym projekcie: literówka (`fasle`), cudzysłowy zostawione przez `docker run -e DRY_RUN="true"`,
  polskie `prawda` czy ucięte `tru` dawały cicho `dry_run=False`, czyli tryb NA ŻYWO — wysyłkę do
  całego zespołu i zapis do grafiku klienta. Zachowanie jak w `_int`: nieznana wartość zatrzymuje
  start jednym czytelnym zdaniem.

## [0.2.6] — 2026-08-03

Skok z 0.2.1 do 0.2.6 opisany jedną sekcją: repo miało wcześniej wyłącznie stan 0.2.1, a obrazy
pośrednie 0.2.2–0.2.5 nie zostały zdiffowane źródło-po-źródle wobec repo — nie ma tu więc
opisywanych osobno wpisów dla nich, żeby nie zgadywać szczegółów, których nie da się zweryfikować.

### Dodane
- **Wykrywanie samodzielnego uzupełnienia grafiku (self-fill detection)**. Pracownik, który
  uzupełnia Shifts bezpośrednio (bez odpowiedzi na czacie), przestaje dostawać fałszywe „Nie
  dostałem odpowiedzi" — nowy krok 1.5 w `poll_replies` sprawdza, po przekroczeniu
  `self_fill_check_min_idle_s` ciszy (domyślnie 1h, `-1` wyłącza), czy dana osoba ma już wypełniony
  docelowy tydzień w Shifts, i jeśli tak — zamyka temat statusem `SELF_FILLED` oraz wysyła
  podziękowanie zamiast dalej nagabywać. Patrz ADR 0004.
- **Świadomość znanych dni urlopowych w przypomnieniach**. Częściowy urlop (np. tylko piątek) nie
  wycisza już całej prośby o uzupełnienie grafiku — bot nadal pyta o pozostałe dni robocze, ale
  pomija dzień już objęty urlopem zarówno w propozycji „jak w zeszłym tygodniu"
  (`propose.proposal_from_last_week(..., skip_weekdays=...)`), jak i w treści przypomnienia
  (wymienia znane dni wolne), oraz nie tworzy dla niego drugiego wpisu `timeOff` przy zapisie.
  Nowe `detect.off_weekdays_by_member`/`detect.member_filled_week`. Patrz ADR 0004.
- **Pamięć konwersacji dla interpretera odpowiedzi**. Interpreter Claude dostaje teraz kontekst do
  10 ostatnich wiadomości pracownika z ostatniej godziny (`reminders/replies.advance_memory`/
  `history_for_llm`), więc rozumie odpowiedzi wieloturowe („a piątek zdalnie", „jak zwykle") bez
  konieczności powtarzania wcześniej podanych informacji. Prompt systemowy jawnie oznacza historię
  jako DANE pracownika (nie polecenia) — rozszerzenie istniejącej obrony anty-injection — i opisuje
  granice własnej pamięci (limit 10 wiadomości / 1h). Interpreter rozpoznaje też tryb pracy podany
  kolorem lub emotką (🟢 zielony = stacjonarnie, 🔵 niebieski = zdalnie), nie tylko słowem. Patrz
  ADR 0004.

## [0.2.1] — 2026-07-22

Stan bazowy repozytorium przed synchronizacją z produkcją: rdzeń cyklu tygodniowego (Etapy 0–4 z
`PLAN.md`) — wykrywanie braków w grafiku, propozycja „jak w zeszłym tygodniu", interpretacja
odpowiedzi naturalnym językiem (Claude), dwukierunkowy obieg potwierdzenia i zapisu do Shifts,
adaptacyjny listener z backoffem (ADR 0002) oraz wygaszanie okna odpowiedzi wymagające dowodu z
udanego odczytu czatu (ADR 0003).
