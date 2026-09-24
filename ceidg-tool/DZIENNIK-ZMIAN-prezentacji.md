# Dziennik zmian prezentacji `prezentacja-ceidg.html`

Data: 2026-09-13
Status: wykonane
Author: P0w3r223
Related to: prezentacja-ceidg.html, PROMPT_PREZENTACJA_PRODUKTU.md, docs/status.md, docs/decisions.md

---

Dwadzieścia pięć żółtych znaczników: **dwadzieścia dwa domknięte**, dwa zostają dla autora
(T06, T22), jeden zostaje jako niepotwierdzony w repozytorium (T13, część o dostawcy modelu).

Siedem zapytań o samą liczbę trafień poszło na produkcję za zgodą właściciela udzieloną w tej
sesji, wszystkie z odpowiedzią HTTP 200, zero pobranych rekordów i zero plików. Reszta pomiarów
kosztowała zero żądań: liczone są na archiwum `probe_out/raport_sample.zip`, na próbkach
w `probe_out/samples/` i na plikach wygenerowanych trybem pokazowym.

## Tabela znaczników

| Nr | Odpowiedź wpisana do dokumentu | Dowód | Status |
|---|---|---|---|
| T01 | Podział 6 320 889 wpisów na statusy, pomiar 2026-09-13: 2 649 475 aktywnych, 2 745 867 wykreślonych, 824 828 zawieszonych, 97 157 wyłącznie w formie spółki cywilnej, 3 562 oczekujących. Kafel „Wpisów w rejestrze" przestawiony na sumę pięciu statusów z tego dnia i `w tym aktywnych 2 649 475`. | Pięć zapytań `GET /firmy?status=…&limit=1` na produkcji, wzorowanych na `scripts/ceidg_probe_pkd_or.py` (ten sam limiter i ta sama historia żądań przez `probe_support.shared_gate`). Narzędzie nie ma osobnej komendy liczącej: `ui/flow.prepare_fetch` wydaje jedno zapytanie o `count`, pokazuje tabelę i czeka. | domknięte |
| T02 | Odświeżenie idzie endpointem zmian, dzieli przerwę na okna po pięć dni i mówi o tym na ekranie. Dopisany koszt: rejestr zgłasza zmiany z całego kraju, więc płaci się za ruch w rejestrze, nie za długość listy — około 13 tys. zmian na dobę (2026-09-05), a przebieg z 2026-09-08 odświeżył 13 401 wpisów za 2 681 żądań w 3 h 02 min. Wykrywanie wykreśleń opisane jako niezmierzone. | `pipeline.update_windows` (`UPDATE_WINDOW_DAYS = 5`), `pipeline.plan_update`, `pipeline.run_update`, `pipeline.update_scope` (znacznik jest jeden na środowisko, nie na kryteria), `UpdatePlan.requests` = okna + `ceil(count/5)`; `docs/decisions.md` „`/zmiana` works on production: 3 days = count 38 744"; `docs/status.md` faza 5. | domknięte |
| T03 | Lista dziewiętnastu pól zgodna z flagami. Podpis poprawiony: powtórzyć można siedemnaście pól, obie daty mają po jednej fladze, bo tworzą jeden zakres. | `cli.pobierz` — dziewiętnaście opcji filtrujących, siedemnaście typu `list[str]`, `--od` i `--do` typu `str | None`; `criteria._LIST_FIELDS`. | domknięte |
| T04 | `miasto` pochodzi z adresu działalności, nigdy z adresu do doręczeń, który ma własną kolumnę. Pusta bywa, bo hurtownia zwraca dla części wpisów pusty adres działalności: 76 z 196 prawdziwych rekordów (2026-09-09). Powód nie jest podawany przez rejestr i dokument go nie zgaduje. | `normalizer.FIRMY_FIELDS` — `FieldSpec("miasto", "adresDzialalnosci.miasto", …)` i osobne `adres_korespondencyjny`; `docs/audit-2026-09-09.md` punkt 3 („complete address (empty in 76 of them)"); policzone ponownie na `probe_out/samples/`: 53 z 105 rekordów mają `adresDzialalnosci: {}`. | domknięte |
| T05 | Etykieta `test` to nazwa profilu API, a nie ślad połączenia: pokaz wybiera profil testowy, żeby nie sięgnąć produkcyjnego, i odmawia startu z flagą produkcyjną. Pokaz rozpoznaje się po czterech pozostałych znacznikach. | `cli._settings_demo` — `load_settings(..., environment="test", ...)`; `exporter` zapisuje `srodowisko` z `settings.environment`; arkusz `Metadane` odczytany ze skoroszytu pokazowego. Propozycja zmiany w kodzie niżej. | domknięte |
| T06 | Bez zmian. | — | dla autora |
| T07 | Karta w sekcji 5 przepisana: nazwa firmy i miejscowość dopasowują się fragmentem (zmierzone), imię i nazwisko dokładnie (zmierzone 2026-09-10, cztery zapytania), powiat, gmina i ulica bez pomiaru. Dopisane, że narzędzie nie dokłada symboli wieloznacznych. Punkt w sekcji 11 zgodzony słowo w słowo: pięć pól zmieniło się na trzy. | `docs/adr/0018_one_matching_semantics.md` z dwoma aneksami; `docs/decisions.md` „`imie` and `nazwisko` match exactly"; `criteria.to_params` wysyła wartość bez żadnego przekształcenia. | domknięte |
| T08 | Zrzut obejmuje trzy statusy: 219 798 aktywnych, 58 370 zawieszonych, 9 088 wyłącznie w formie spółki cywilnej. Wykreślonych nie ma w nim wcale, więc nie zawyżają udziału. Dla samych aktywnych rocznik 2007 to 124 307 z 217 575, czyli 57,1 %. | `docs/decisions.md` „What statuses the daily report actually contains"; przeliczenie `probe_out/raport_sample.zip` wiersz po wierszu 2026-09-13 (zero żądań), odtwarzające 58,64 % i 24 494 co do sztuki. | domknięte |
| T09 | Obie definicje poprawione. Pierwszy kafel liczy wpisy, na których żaden zapisany kod, ani główny, ani dodatkowy, nie występuje w słowniku PKD 2025. Drugi kafel przemianowany na „Kody 2025 wymagające kodu z 2007", 357 z 728, `small` mówi „w tym 306 niejednoznacznych" — poprzednia etykieta nazywała 357 niejednoznacznymi, a tyle jest wszystkich rozszerzeń. | Przeliczenie `pkdmap.TablicaPkd.rozszerz` dla każdego z 728 kodów: 357 z poprzednikiem, 51 czystych, 306 niejednoznacznych; zgodne z `docs/decisions.md` „The GUS transition key, measured". | domknięte |
| T10 | Data i metoda: trzy zapytania o liczbę na produkcji 2026-09-07, bez filtra statusu — `9621Z` 38 201, `9602Z` 187 149, oba naraz 225 350. | `docs/decisions.md` „Repeated `pkd=` is OR"; `scripts/ceidg_probe_pkd_or.py`; `docs/api_notes.md` („when no explicit `status` is given the server appends all five statuses"). | domknięte |
| T11 | Dopisane, że pomiar 2026-09-10 nie ograniczał statusu, więc obejmuje wykreślone. Dla samych aktywnych, pomiar 2026-09-13: `6210B` w Poznaniu 2 536, `6201Z` 3 150, czyli dzisiejszym kodem widać 45 % aktywnych. | `docs/demo-presentation.md` „The numbers this script is built on"; dwa nowe zapytania o liczbę na produkcji. Suma jest sumą rozłącznych zbiorów — rozłączność roczników zmierzona 2026-09-07. | domknięte |
| T12 | Opisany stan po zmianie w kodzie: zamiast daty 31.12.2026 stoi warunek „usunąć nie wcześniej niż 1 lutego 2027 i dopiero po pomiarze udziału wpisów nieosiągalnych dzisiejszymi kodami". Data jest dolną granicą, bo ustawa zastrzega „jeżeli jest to możliwe". | Zmiana wprowadzona: `ceidg_tool/pkdmap.py`, `scripts/build_pkd_transition.py`, nagłówek `ceidg_tool/data/pkd2007_2025.yaml` (plik przebudowany ze źródła GUS, nie edytowany ręcznie), `tests/test_pkdmap.py`, `tests/test_pkdmap_data.py`. Podstawa: `docs/adr/0020_pkd_sunset_has_an_observer.md`. | domknięte |
| T13 | Sekcja 7: dostawca Anthropic, model `claude-opus-5`, adres `api.anthropic.com` na osobnej liście dozwolonych adresów, około 25 tys. tokenów wejścia na pytanie (pomiar 2026-09-07). Kosztu w złotówkach **nie mierzono**; podany szacunek z ADR-0011 wraz z przyjętą stawką. Sekcja 8: nazwa dostawcy i adres wpisane, znacznik zawężony do tego, czego w repozytorium nie ma. | `config.MODEL_ALLOWED_HOSTS`, `assistant/caller.MODEL`, `httpclient.build_model_http_client`, `docs/adr/0011_phase4_language_assistant.md` („measured 2026-09-07, run A7: 24 854 tokens read from cache"). Region serwerów i retencja u dostawcy: przeszukane ADR-0011 i `docs/` — brak. | częściowo: **nie potwierdzono w repozytorium** (region, retencja) |
| T14 | Karta rozpisana na trzy akapity: pełny stan rejestru w województwie na dany dzień, nie lista zmian; 287 256 wierszy, 24 kolumny, 21 MB archiwum i 68 MB CSV; powstaje codziennie nad ranem i leży około sześciu dni. Kontakty **są** (telefon, e-mail, WWW, wszystkie kody PKD); brakuje wykreślonych i oczekujących, adresu do doręczeń, obywatelstwa, spółek, nazw branż i identyfikatora wpisu. Czasy: 1,11 s i 7,12 s na żądania, 13 s na przefiltrowanie. Zdanie o wyborze drogi dopisane. | `docs/decisions.md` „What columns the daily report actually has" i „Mini-probe results"; `docs/status.md` faza 6 (13 s, dwa żądania z czasami); `reports.report_covers`, `reports.UNFILLED_COLUMNS`, `pipeline.run_report_fetch`, `ui/flow.prepare_fetch`. | domknięte |
| T15 | Pole w tokenie nazywa się `pesel` i stoi obok imienia oraz nazwiska. Test szczelności jest automatyczny i uruchamiany przy każdej zmianie; raz przeszukano też prawdziwą sesję produkcyjną (2026-09-05). | `tests/resilience/test_s7_token_leak.py` (pięć przypadków, sadzi oba kształty sekretów); `config.inspect_token`; odczyt nagłówka i ładunku tokenu z `.env` lokalnie — wypisane zostały wyłącznie **nazwy** pól. | domknięte |
| T16 | Zmienne `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` ignorowane; zmienne wskazujące zestaw zaufanych certyfikatów też, więc **nie ma opcji własnego urzędu certyfikacji**. Zachowanie przy odmowie: ponowienia po 10, 30, 60 i dalej po 300 s, a po trzydziestu minutach zdanie „Brak połączenia z API przez ponad 30 min" i podpowiedź wznowienia. | `httpclient.build_http_client` (transport podawany zawsze, `trust_env=False` na kliencie i na transporcie), `client._get` z `CONNECTION_RETRY_DELAYS_S` i `CONNECTION_MAX_OUTAGE_S`, `errors.TransportError`. | domknięte |
| T17 | Trzydzieści dni to **domyślny próg polecenia czyszczącego**, nie automatyczne kasowanie. Polecenie usuwa zakończone pobrania starsze niż próg, rekordy niezwiązane z pobraniem i archiwa raportów; gotowych skoroszytów nie rusza, także w wariancie kasującym bazę i logi. Dopisane, co to znaczy dla listy utrzymywanej dłużej. | `config.DEFAULT_RETENTION_DAYS`, `cli.wyczysc` (jedyny wołający), `store.purge_older_than`, `pipeline.purge_report_files`, `ui/texts.purge_all_summary`. | domknięte |
| T18 | Adres testowy się rozwiązuje, ale połączenie przekracza czas oczekiwania po ośmiu sekundach, podczas gdy produkcja odpowiada w 0,03 s. Narzędzie traktuje to jak brak sieci i kończy po około 36 minutach błędem wznawialnym; pierwszy ekran nie ostrzega, że adres jest martwy. Liczby z bazy autora: 2 802 żądania na produkcję, zero na test. | `docs/status.md` faza 4i, `docs/audit-2026-09-09.md` punkt 2, `ui/texts.first_screen`, `client.py:50-51`. Propozycja zmiany domyślnego zachowania niżej. | domknięte |
| T19 | Reguła nie ma wyjątków, więc każdy numer zaczynający się od `+` dostaje apostrof: `+48 886 517 822` zapisuje się jako `'+48 886 517 822` i w skoroszycie, i w CSV. Apostrof jest częścią wartości, więc przy imporcie CSV trzeba go obciąć. | `safetext.sanitize_text`, `exporter._cell_value` (reguła obejmuje każde pole rodzaju `text`), `normalizer.FieldSpec("telefon", …, "text", …)`; sprawdzone na skoroszycie i CSV wygenerowanych trybem pokazowym 2026-09-13. | domknięte |
| T20 | Dwie listy `<ul class="plain">` pod krokami 1 i 2, z etykietami przepisanymi dosłownie. Poprawione też zdanie o menu: pozycji jest pięć, a szósta („Wznowić przerwane pobieranie") dochodzi na górze tylko wtedy, gdy w bazie czeka niedokończone zadanie. | `ui/wizard._menu_items`, `ui/texts.pobierz_menu_item`, `ui/prompts.criteria_questions`. | domknięte |
| T21 | Odbiór to dwa przejścia kreatora do gotowego pliku ze scenariuszem zamiast człowieka: „salony fryzjerskie w Gnieźnie" przez asystenta (Gniezno, 9621Z, 71 firm, sześć arkuszy) oraz osiem pytań z limitem dziesięciu rekordów przy 3 740 trafieniach. Razem 25 żądań, każde 200, bez ponowień; baza skończyła z 79 firmami i zerem duplikatów. | `docs/status.md` „Gate 3 — the walk (2026-09-09)". | domknięte |
| T22 | Bez zmian. | — | dla autora |
| T23 | Trzy scenariusze nazwane po imieniu: zabicie procesu w środku strony i wznowienie, dwuminutowe zerwanie sieci w trakcie pobierania, zapełnienie dysku w trakcie zapisu. Domknięcie każdego to jeden przebieg z prawdziwym zdarzeniem, z datą w raporcie odporności. Dopisany czwarty przypadek: asystent w chwili zerwania sieci. Poprawione zdanie wstępne — przeszły w zestawie testów offline, nie „w trybie pokazowym". | `docs/resilience-report.md` wiersze 1, 2, 8 („Manual run: pending"); `docs/status.md` punkt otwarty 2. | domknięte |
| T24 | Decyzja nazwana: czy dopisywać odnośnik do rejestru wierszom z gotowego raportu. Podane oba warianty i koszt (25 numerów NIP na żądanie, około 2,5 minuty na tysiąc rekordów, osobny wiersz w tabeli kosztów) oraz to, że decyzja należy do właściciela, a po przekazaniu do firmy. | `docs/status.md` punkt otwarty 3; `reports.UNFILLED_COLUMNS` (`link_ceidg` wśród kolumn, których raport nie umie wypełnić); ADR-0008 decyzja 5; `docs/decisions.md` „repeated `nip=` is OR-ed". | domknięte |
| T25 | Nazwa polecenia podana: `ceidg-tool sprawdz-token`, zero żądań. Okres ważności **nie istnieje**: token nie ma pola `exp`, więc polecenie wypisuje datę wystawienia i zdanie o braku daty wygaśnięcia. Repozytorium nie podaje okresu ważności z żadnego innego źródła. | `cli.sprawdz_token`, `config.inspect_token`, `config.TokenInfo.validity_text`; uruchomione: „Token: brak daty wygaśnięcia w tokenie, wystawiony 2026-09-05 10:14"; `docs/status.md` „The JWT in `.env` … (issued 2026-09-05, no `exp` claim)". | domknięte |

## Zmiana wprowadzona w kodzie (T12)

Jedyna zmiana w kodzie zlecona wprost w zadaniu. Wszystkie cztery bramki przechodzą po niej:
**1311 passed, 1 skipped** (z obecnym `probe_out/`), `mypy` czysty na 112 plikach, `ruff check`
i `ruff format --check` czyste.

| Plik | Co się zmieniło |
|---|---|
| `ceidg_tool/pkdmap.py` | `KONIEC_PRZEJSCIA` zostaje z wartością `2026-12-31`, ale komentarz mówi teraz, że to fakt prawny o klasyfikacji, a nie data usunięcia tablicy. Dochodzi `USUNIECIE_NIE_WCZESNIEJ_NIZ = "2027-02-01"` z uzasadnieniem: art. 12 ust. 1 ustawy z 21.11.2025 daje rejestrowi okno do 31 stycznia 2027 i zastrzega „jeżeli jest to możliwe", a rozstrzyga pomiar udziału wpisów nieosiągalnych kodem z 2025. |
| `scripts/build_pkd_transition.py` | `WYGASA` zastąpione przez `USUNIECIE_NIE_WCZESNIEJ_NIZ`; nagłówek generowanego pliku mówi warunek, podstawę prawną (Dz.U. 2025 poz. 1792) i konkretny pomiar z jego ostatnią wartością (24 494 = 8,6 % z 285 026). |
| `ceidg_tool/data/pkd2007_2025.yaml` | Przebudowany z `PKD/KluczePKD_2007_2025.xlsx`, a nie edytowany ręcznie — nagłówek tego zabrania i to jest jedyny sposób, żeby prowenienacja dalej opisywała zawartość. Zmienił się wyłącznie nagłówek (9 linii dodanych, 4 usunięte); treść tablicy bit w bit ta sama, suma SHA-256 źródła bez zmian. |
| `tests/test_pkdmap.py` | Test daty pilnuje teraz obu stałych i tego, że data usunięcia leży po końcu okresu przejściowego. |
| `tests/test_pkdmap_data.py` | Test nagłówka sprawdza warunek zamiast daty: obecność frazy „Usunięcie: nie wcześniej niż", słów „po pomiarze" i podstawy prawnej. |

## Propozycje zmian w kodzie, które wyszły przy okazji

Żadna nie została wprowadzona; każda z jednym zdaniem uzasadnienia.

1. **T05 — tryb pokazowy zapisuje `srodowisko: test`.** Kolumna i wiersz `Metadane` mają mówić,
   skąd wziął się plik, a `test` mówi, którego profilu API użyto; warto rozdzielić te dwie rzeczy
   i zapisywać w eksporcie `demo`, zostawiając `settings.environment` nietknięte, bo od niego
   zależy nazwa pliku bazy i klucz blokady.
2. **T16 — brak opcji własnego urzędu certyfikacji.** Rekomendacja: **nie dodawać**. Za dodaniem
   przemawia to, że inspekcja TLS jest najczęstszym powodem, dla którego narzędzie w firmowej
   sieci nie ruszy, i własny CA byłby jedynym wyjściem bez osobnego stanowiska; przeciw przemawia
   to, że token niesie PESEL, a konfigurowalny CA zamienia gwarancję z §B w ustawienie, które
   ktoś kiedyś zmieni, nie rozumiejąc stawki. Gdyby jednak miała powstać, jej miejsce to wyłącznie
   `httpclient.py`.
3. **T16 — trwały błąd certyfikatu jest ponawiany przez trzydzieści minut.** Podstawiony
   certyfikat nie naprawi się za 300 sekund, więc warto rozpoznać błąd weryfikacji certyfikatu
   osobno i zakończyć od razu zdaniem nazywającym proxy albo inspekcję ruchu, zamiast kazać
   operatorowi czekać pół godziny na komunikat o braku sieci.
4. **T18 — domyślne środowisko wskazuje martwy adres.** Domyślnego środowiska nie należy zmieniać
   na produkcję, bo to jedyna rzecz, która trzyma regułę „nic nie rusza na produkcję bez zgody",
   ale pierwszy ekran powinien powiedzieć, że ten adres nie odpowiadał, skoro projekt zmierzył to
   dwa razy i ma w bazie zero żądań testowych na 2 802 produkcyjne.
5. **T19 — każdy numer telefonu dostaje apostrof.** Warto rozważyć wyjątek dla kolumn, których
   zawartość jest strukturalnie numerem, a nie tekstem z rejestru; zysk jest jednak tylko po
   stronie CSV, a koszt to osłabienie reguły, która dziś nie ma wyjątków i właśnie dlatego jest
   sprawdzalna jednym zdaniem, więc decyzja nie jest oczywista.
6. **Poza zleceniem: generator klucza PKD wypisuje inny podział niż ten, którym posługuje się
   program.** `build_pkd_transition.py` drukuje „rozszerzenia czyste: 90, niejednoznaczne: 267",
   bo liczy wyłącznie rozgałęzienie kodu 2007, podczas gdy `pkdmap` liczy także drugą postać
   niejednoznaczności i daje 51 oraz 306 — a to druga liczba decyduje, czy operator dostanie
   pytanie.
7. **Poza zleceniem: docstring `criteria.py` wymienia wycofany plik zapytań YAML** („jedyny
   kontrakt między wejściem (CLI, kreator, YAML, asystent)"), którego ADR-0022 pozbył się
   2026-09-10.

## Czego w repozytorium nie ma, a co dokument zakładał

| Brak | Gdzie się ujawnił | Co z tym zrobiono |
|---|---|---|
| Region serwerów dostawcy modelu i jego polityka retencji zapytań | T13, sekcja 8 | Znacznik zostaje, z treścią „nie potwierdzono w repozytorium". Do ustalenia w umowie z dostawcą, nie w kodzie. |
| Okres ważności tokenu | T25, sekcja 13 | Token nie ma pola `exp`, a dokumentacja hurtowni nie jest w repozytorium. Dokument mówi to wprost zamiast podawać liczbę. |
| Powód, dla którego rejestr zwraca pusty adres działalności | T04, sekcja 4 | Usunięte zdanie o „braku stałego miejsca wykonywania działalności" — to było domysł. Zostaje pomiar: 76 z 196 rekordów. |
| Czy `/zmiana` zgłasza wykreślenie wpisu | T02, sekcja 3 | Napisane wprost, że nie zmierzono; podane to, co wiadomo (wykreślony wpis zostaje w rejestrze ze statusem `WYKRESLONY`). |
| Semantyka dopasowania dla `powiat`, `gmina`, `ulica` | T07, sekcje 5 i 11 | Opisana jako przyjęta z dokumentacji, nie zmierzona; ADR-0018 wycenia domknięcie na trzy żądania. |
| Co automatyczne przeklasyfikowanie zrobi z wpisami, których „nie da się" przeliczyć | T12, sekcja 6 | Zostaje jako otwarte w sekcji 11; w kodzie zastąpione warunkiem opartym na pomiarze. |

## Zmiany poza znacznikami

Wszystkie wynikły z przebiegu spójności opisanego w zadaniu.

- **Sekcja 4 i sekcja 14, kolumna `link_ceidg`.** Dokument twierdził, że odnośnik jest przy każdym
  wierszu. Na ścieżce gotowego raportu tej kolumny nie ma i eksport ją ukrywa, bo raport nie niesie
  identyfikatora wpisu (`reports.UNFILLED_COLUMNS`). Oba miejsca dostały zdanie o tym wyjątku; to
  ta sama sprawa, którą T24 nazywa otwartą decyzją.
- **Podsumowanie, punkt 1** — „z odnośnikiem do źródła w każdym wierszu" zawężone do wierszy
  pobranych przez API, z tego samego powodu.
- **Podsumowanie, punkt 3** — dopisane 45 % dla wpisów aktywnych, żeby zgadzało się z sekcją 6 po
  pomiarze z T11.
- **Sekcja 7, akapit „Jak to skaluje się w górę"** — arytmetyka była poprawna, ale niepełna:
  szczegóły idą po pięć firm na żądanie (`apiprofile.ids_batch_size = 5`), a nie po dwadzieścia
  pięć. Liczby na paskach (35, 411, ~2 460) sprawdzone i zgodne, więc zostają: 6 + ⌈144/5⌉ = 35,
  ⌈10 258/25⌉ = 411, 411 + ⌈10 258/5⌉ = 2 463.
- **Sekcja 7, czwarty pasek i jego podpis** — dopisane zmierzone czasy (8 s żądań, 13 s
  filtrowania) i zdanie, że ten sam plik niesie kontakty, żeby porównanie nie sugerowało, że
  szczegóły są dostępne wyłącznie przez API.
- **Stopka** — zdanie o znacznikach przepisane na stan faktyczny (T06 i T22 dla autora, T13
  niepotwierdzony); do źródeł liczb dopisane daty 2026-09-09 i 2026-09-13.
- **Blok `<style>`** — jedna linia: `ul.plain li small { display: block; … }`, żeby podpisy przy
  pozycjach menu i pytaniach kreatora wyglądały tak samo jak w pozostałych listach dokumentu.

## Co sprawdzono na koniec

- Bloki `<div class="term">`, makieta `<div class="xls">` i blok `<script>` porównane bajt w bajt
  z wersją sprzed zmian: **identyczne**.
- Domknięcie znaczników HTML sprawdzone `html.parser` — zero niedomkniętych i zero zamknięć bez
  otwarcia. `node` nie jest w tym środowisku zainstalowany, więc bloku `<script>` nie da się
  przeparsować zleconym poleceniem; zamiast tego wykazano, że nie został tknięty.
- Pauz „—" w dokumencie było pięć i jest sześć. Nowa jest jedna i stoi w przepisanej dosłownie
  etykiecie menu („Pobrać firmy — opisz zdaniem albo podaj kryteria"), której T20 kazał nie
  zmieniać.
- Odsyłacze „sekcja N" bez zmian, kolejność i liczba sekcji bez zmian.
