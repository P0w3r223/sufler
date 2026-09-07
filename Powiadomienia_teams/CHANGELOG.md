# Changelog

Wszystkie istotne zmiany w projekcie `powiadomienia-teams`. Format oparty na
[Keep a Changelog](https://keepachangelog.com/pl/1.1.0/); wersjonowanie
[SemVer](https://semver.org/lang/pl/). Decyzje projektowe: [`docs/adr/`](docs/adr/).

Numeracja wersji śledzi **tagi obrazu Dockera** (`powiadomienia-teams:X.Y.Z`) — to jedyne źródło
prawdy o iteracji na produkcji; metadane wewnątrz obrazu (`pyproject.toml`) bywały z nimi
rozjechane.

**Numery linii w sekcji [0.2.19] opisują ODZYSKANE DRZEWO z obrazu, nie bieżący `HEAD`.** Od fali 1
repozytorium nie jest już tożsame z obrazem, więc te wskaźniki rozjeżdżają się z każdą naprawą — są
zapisem stanu, w którym usterkę znaleziono, i celowo nie są odświeżane. Wskaźniki, które mają
prowadzić do KODU (`reason` przy `xfail`, komentarze w testach), są aktualizowane razem ze zmianą,
która je przesuwa.

## [0.2.22] — 2026-09-07

Wydanie naprawcze dla skryptu diagnostycznego. **Zachowanie usługi bez zmian wobec 0.2.21** —
różnica jest wyłącznie w `scripts/` i w zasięgu bramki typów.

### Naprawione

- **Pomiar D1 wywracał się przy pierwszym członku zespołu.** `scripts/zbierz_historie.py` czytał
  `Shift.member_id`, a pole nazywa się `user_id` — `AttributeError` w połowie przebiegu, na żywym
  tenancie, po pobraniu 267 zmian. Przy okazji porównanie identyfikatorów szło znak w znak zamiast
  przez `domain.tozsamosc.ten_sam`: rozjazd wielkości liter nie zapisałby nic złego, ale zaniżyłby
  historię do zera i pomiar powiedziałby „brak danych" o osobie z pełnym grafikiem.

### Zmienione

- **`mypy` obejmuje `scripts/`, nie tylko `src/`.** To jest właściwa naprawa tamtej usterki:
  literówka w nazwie pola przeszła przez WSZYSTKIE cztery bramki, bo skrypty były poza zasięgiem
  kontroli typów — mimo że wchodzą do obrazu i sięgają po ten sam model domeny co usługa.
  Zweryfikowane: przywrócenie `member_id` daje teraz `mypy` czerwony.
- `tests/test_zbierz_historie.py` — bucketowanie historii na danych syntetycznych: dzień
  ROZPOCZĘCIA (nocka należy do piątku), porównanie przez tożsamość, urlop jako brak danych,
  kolejność tygodni od najświeższego. Cztery rzeczy, z których każda daje CICHO zły pomiar, a więc
  złą decyzję o `wzorzec.py`.

## [0.2.21] — 2026-09-07

Wydanie porządkowe i wydajnościowe, zbudowane z drzewa, w którym `src/` po raz pierwszy podlega
wszystkim regułom lintera. Poza dwiema pozycjami niżej wchodzą tu także zmiany bez wpływu na
zachowanie usługi: odtworzony `docs/plan-rozwoju.md` (kod cytował go 40 razy, a dokumentu nie było),
jedenaście martwych odsyłaczy przekierowanych na istniejące cele, osiem nowych strażników
statycznych, ADR 0008 (dwa środowiska bramki) i skrypt pomiarowy `scripts/zbierz_historie.py`.

**Znane ograniczenie tego wydania, świadome:** obraz 0.2.20 przepracował kilka godzin, a nie pełny
cykl tygodniowy, więc fala 4 (rdzeń „co najwyżej raz") i fale 1–3 wchodzą do obserwacji razem.
Gdyby piątkowy przebieg zachował się nieoczekiwanie, rozdzielenie przyczyn będzie trudniejsze niż
przy wdrożeniu falami. Rollback: podmiana tagu na `0.2.20`.

Wersja `0.2.20` (fale 1–3 napraw) stoi u klienta od 2026-09-07. Poniżej zmiany, które pojadą
następnym obrazem.

### Naprawione

- **Ostrzeżenie o uciętym odczycie czatu mierzyło długość strony, nie lukę wobec watermarku.**
  Warunek `len(wiadomosci) >= top` był prawdziwy dla każdego czatu mającego w ogóle 50 wiadomości,
  więc w produkcji zapalał się CO GODZINĘ, nieprzerwanie, dla jednego czatu — a docstring
  obiecywał wykrycie sytuacji „między dwoma zajrzeniami przyszło więcej niż `top`". Ostrzeżenie,
  które pada zawsze, uczy operatora nie czytać kanału, którym przyjdzie to prawdziwe.

  Od tej zmiany zapala się wtedy, gdy strona jest pełna **i** jej najstarsza wiadomość jest nowsza
  od watermarku. Przy niepewności (nieczytelny watermark, strona bez znaczników czasu, znacznik
  bez strefy) ostrzega DRUGIM zdaniem, które mówi wprost, że rozstrzygnięcia nie było — kierunek
  jak w ADR 0003: brak dowodu nie jest dowodem braku.

### Zmienione

- **Grafik dla wykrywania samouzupełnienia czytany rzadziej** (ADR 0009). Odczyt pobiera całą
  kolekcję zespołu (u klienta trzy strony, ~2000 zmian) i szedł CO OBIEG nasłuchu, całą dobę,
  dopóki jakakolwiek rozmowa była otwarta — około 61 pełnych pobrań na cykl tygodniowy. Wyniki
  żyją teraz sześć godzin, ale **wyłącznie dla kroku 1.5**; ścieżka poprzedzająca nieodwracalny
  zapis do Shifts zostaje przy odczycie per przebieg.

  Cena widoczna dla użytkownika: podziękowanie za samodzielne uzupełnienie grafiku może przyjść
  do sześciu godzin później niż dotąd. Zamknięcie tematu nigdy nie idzie z pamięci — jest
  potwierdzane świeżym odczytem, bo wysyła człowiekowi zdanie „Twój grafik jest już uzupełniony",
  a wpis po terminie odczytu z pamięci w ogóle nie dostaje.

- `GraphClient.list_chat_messages` ma nowy argument **wymagany i wyłącznie nazwany**
  `od_watermarku`, wzorem `replies.incoming_after(..., *, nadawca)`. Wartość domyślna znaczyłaby,
  że przyszły wołający po cichu traci wykrywanie — ta sama klasa cichej degradacji, którą ten
  warunek zamyka.

### Efekt operacyjny do zapowiedzenia

Powtarzające się co godzinę ostrzeżenie „Historia czatu … zwróciła pełną stronę" **zniknie**.
Jeśli pojawi się nowe, o innej treści („jest NOWSZA niż watermark" albo „NIE UMIEM
rozstrzygnąć"), znaczy to realną możliwość utraty odpowiedzi i warto obejrzeć czat.

## [Nieopublikowane] — naprawy usterek 0.2.19, fala 4: watermark

**Lista dziewięciu usterek importu 0.2.19 jest zamknięta. 0 xfailed.**

Wysyłka, która nie doszła, przestaje kasować wiadomość pracownika.

### Przyczyna

Trzy miejsca w `_interpret_and_confirm` utrwalały obsługę porcji PRZED wysyłką i nie cofały jej,
gdy wysyłka padła. Watermark zostawał przesunięty za wiadomość, o której pracownik nie usłyszał
ani słowa — kolejny cykl jej nie widział, a po terminie człowiek dostawał nieprawdziwe „nie
dostałem odpowiedzi". Jedno z miejsc miało `finally` cofające status i flagę zapisu, ale nie
watermark; dwa pozostałe (gałęzie „brak powodu wolnego" i `unclear`) nie miały `try` w ogóle.

### Naprawione

- Migawka pól sprzed obsługi (`_migawka_commitu`) i wycofanie w `finally` (`_wycofaj_commit`)
  we wszystkich trzech miejscach. Cofane są: `watermark`, `employee_memory`, `memory_started_at`,
  `fail_count`, `status`, `resolved`, `resolved_time_off`. `awaiting_yes` wraca twardo na `False`,
  nie z migawki — to bramka nieodwracalnego zapisu do Shifts (N38), a wycofanie nigdy nie ma prawa
  ROZSZERZYĆ uprawnienia.
- Prośba o potwierdzenie przestaje POŁYKAĆ wyjątek z wysyłki. Sufit pętli daje `_record_failure`
  wołane wyżej, w `_process_pending`.
- Strażnik szwu wysyłki: `assert poza == []` zamiast przypiętej liczby dwa, plus nowa reguła —
  `_wycofaj_commit` musi stać w `finalbody`.

### Rozstrzygnięcia

**Bez nowego ADR-a.** Reguła 10 żąda ADR-a przy ZMIANIE niezmiennika; tu go przywracamy. Docstring
`_commit` już deklarował, że „nieudane przetworzenie zostawia watermark nietknięty", a `poll_replies`
zakresuje „co najwyżej raz" na skutki NIEODWRACALNE — dosłownie „zapis do Shifts, wysyłka
domknięcia". Prośba o potwierdzenie nie jest ani jednym, ani drugim.

**Wycofanie NIE przez `_record_failure`**, wbrew rekomendacji z planu i przeglądu fali 3. Ta funkcja
woła `save_state` i `do_pracownika`; z bloku `finally` znaczyłoby to przy utracie sesji próbę
napisania do człowieka martwym tokenem — usterkę zamkniętą w fali 1. Sygnatura `_wycofaj_commit`
(tylko `pending` i migawka) czyni brak I/O własnością konstrukcji.

**`dostarczono` wstaje po `do_pracownika`, przed `_oznacz_wyslane`.** Ten drugi zapisuje stan, więc
`StateWriteError` z niego znaczy, że wiadomość JUŻ JEST u pracownika — cofnięcie wymieniłoby
zgubioną wiadomość na zdublowaną.

### Cena, świadomie zaakceptowana

Ta sama wiadomość wraca do interpretacji: **dwa dodatkowe wywołania modelu** na jedną niedoręczoną
wiadomość (`_MAX_PENDING_FAILURES - 1`), nie jedno, jak zakładał plan. Rozłożone na około trzy
godziny, bo wynikiem obiegu jest teraz `UNKNOWN`, więc backoff się nie resetuje. Taniej niż zgubiona
odpowiedź pracownika.

Nowe, drobne ryzyko odwracalne: jeśli Graph przyjmie POST, a klient zobaczy błąd, kolejny cykl
wyśle prośbę drugi raz. „Co najwyżej raz" chroni skutki nieodwracalne, a duplikat PYTANIA nim nie
jest.

Efekt operacyjny do zapowiedzenia: kolumna `błędy` w `--stan` zacznie pokazywać 1–2 dla osoby,
której nie udało się odpowiedzieć — w tym wtedy, gdy nie doszła PROŚBA O POTWIERDZENIE. Dla tej
jednej gałęzi stało tam dotąd zawsze 0 i nie dlatego, że nic się nie działo: wyjątek był połykany
w miejscu, więc `_record_failure` nigdy nie ruszał, a `_commit` licznik przed chwilą wyzerował.
Dwie pozostałe gałęzie (`brak powodu wolnego`, `unclear`) stały poza `try`, więc ich niepowodzenie
licznik podbijało już wcześniej.

### Usunięte z „Znanych ograniczeń"

Wiersz „Pracownik nie dostał prośby o potwierdzenie → nieudana wysyłka nie jest ponawiana; napisz
do niego ręcznie". Opisywał usterkę jako świadomy kompromis, **przemilczając**, że odpowiedź
przestaje być widoczna dla kolejnych cykli. Po tej fali jest nieprawdziwy podwójnie: prośba przyjdzie
sama w kolejnym cyklu, a po trzech nieudanych próbach pracownik dostaje prośbę o doprecyzowanie.

## [Nieopublikowane] — naprawy usterek 0.2.19, fala 3: wpis nie do rozstrzygnięcia

Wpis, którego czatu nie da się odczytać, przestaje wisieć w nieskończoność (ADR 0007).

### Przyczyna

`list_chat_messages` woła się PRZED `_record_failure`, więc wyjątek z odczytu leci do
per-osobowego `except Exception` w `poll_replies` i staje się `ReadOutcome.UNKNOWN`: `fail_count`
nie rośnie, watermark nie rusza, a `should_expire` **słusznie** odmawia wygaszenia bez dowodu.
Skutek: wpis zostaje otwarty na zawsze i — bo kluczem stanu jest `member_id` — blokuje
przypomnienie tej osoby w KAŻDYM kolejnym tygodniu. Jedynym śladem był `logger.exception`
w kontenerze, a proces żył, puls bił i healthcheck świecił na zielono, czyli z zewnątrz wyglądało
to dokładnie jak spokojny tydzień.

### Dodane

- Pole stanu `PendingReminder.unknown_count` — obiegi z rzędu, w których padł ODCZYT CZATU.
  Wstecznie **i wprzód** zgodne (`state._FIELDS` odsiewa nieznane klucze), więc cofnięcie po tagu
  obrazu zostaje bezpieczne. Trzymane OSOBNO od `fail_count`: przekroczenie progu `fail_count`
  wysyła „Nie do końca zrozumiałem" DO CZATU, czyli tam, gdzie z definicji nie ma dostępu.
- Alert progowy dla operatora po trzech obiegach bez odczytu — raz, przy równości progowi, plus
  osobny alert powrotu wagi INFO. Ten sam wzorzec co `_PULS_PROG_ALERTU`. Alert progowy i alert
  o zamknięciu mają RÓŻNE tytuły: mogą paść w tym samym obiegu i mówią rzeczy przeciwne.
- `POWIADOMIENIA_SUFIT_WPISU_BEZ_ODCZYTU_H` (domyślnie 144 h): twardy sufit wieku wpisu. Po nim
  wpis schodzi z obiegu **po cichu** — status `EXPIRED`, ZERO wiadomości do pracownika, alert do
  operatora. Nie da się wyłączyć zerem; walidacja żąda `> REPLY_MIN_HOURS` i `<= 168`.
  **Sufit wymaga OBU przesłanek: wieku ORAZ serii nieudanych odczytów.** Sam wiek nie wystarcza —
  po przestoju dłuższym niż sufit wszystkie wpisy są stare, więc jeden 429 z Graph przy pierwszym
  obiegu po powrocie zamykałby je razem z odpowiedziami czekającymi w czatach.
- Kolumna `bez odcz.` w `--stan`, obok `błędy` — bo to dwa różne liczniki, a operator wywołany
  alertem musi mieć czym go sprawdzić.
- `ReadOutcome.READ_FAILED` i `ReadOutcome.BLOCKED` — dwie nowe wartości, wyłącznie w pamięci.
  Rozbijają `UNKNOWN`, który znaczył naraz trzy różne rzeczy: awarię odczytu czatu (liczoną),
  obcego nadawcę w wątku (operator już zawołany) i awarię obsługi PO udanym odczycie (czat
  odpowiada). `READ_FAILED` powstaje w jednym miejscu, w wąskim `try` wokół `list_chat_messages`,
  więc „licznik mierzy dostępność czatu" jest własnością konstrukcji, nie dyscypliny.

### Cena, świadomie zaakceptowana

Wpis może teraz zostać zamknięty **bez** dowodu z udanego odczytu, czyli ADR 0003 zostaje
osłabiony. Dopuszczalne wyłącznie dzięki ciszy: ADR 0003 nie zakazuje zamykania, tylko twierdzenia
o zachowaniu pracownika bez dowodu — zamknięcie, które nie wysyła zdania, nie wypowiada
twierdzenia. Dlatego ścieżka NIE idzie przez `zamknij_bez_zapisu` (ta wysyła „Nie dostałem
odpowiedzi"). W podsumowaniu tygodniowym takie zamknięcie wpada do rubryki „zamknięte bez zapisu",
więc przyczyna ginie — osobnego statusu dodać nie wolno (N34), zostaje alert i kolumna w `--stan`.

### Poza listą usterek — sprostowania obietnic bez pokrycia

- Komentarz przy `_ZGLOSZONE_OBCE` zapowiadał, że dławienie alertu „docelowo przejmie licznik
  obiegów `UNKNOWN`". Od tej zmiany to nieprawda i nie ma być planem — `unknown_count` liczy
  wyłącznie awarie odczytu.
- Docstring `do_domkniecia` powoływał się na strażnika statycznego w `test_cisza.py`, który
  **nigdy nie powstał** (jedyny `ast.parse` w `tests/` mieszka w `test_szew_wysylki.py`, dopisanym
  w fali 1). Zdjęte. To druga taka martwa obietnica znaleziona w tym pliku — pierwsza dotyczyła
  szwu wysyłki i kosztowała usterkę, której nikt nie zauważył.
- Cytowanie `listener.py:462-469` w `reason` ocalałego `xfail` wskazywało na sygnaturę funkcji,
  a nie na zdanie, które cytowało. Poprawione na linię niosącą tę obietnicę.

### Znalezione przeglądem tej fali, przed scaleniem

Pierwsza wersja tej zmiany miała dwie usterki własne, obie w nowym kroku pętli nasłuchu:

- **Sufit pytał wyłącznie o wiek wpisu**, więc pojedynczy 429 z Graph na wpisie starszym niż
  144 h zamykał go po cichu razem z odpowiedzią pracownika czekającą w czacie — REGRES wobec
  0.2.19, w którym wpis zostawał otwarty i kolejny obieg tę odpowiedź czytał. Naprawione
  koniunkcją; strażnik `test_twardy_sufit_zamyka_wpis_CICHO_i_z_alertem` dostał pętlę, bo
  pojedynczy obieg zamrażał wadliwe zachowanie.
- **`unknown_count` nie liczył tego, co deklarował.** Krok liczący widział `ReadOutcome.UNKNOWN`,
  a ten powstawał dla KAŻDEGO wyjątku z obsługi — także dla uciętego odczytu GRAFIKU, który
  świadomie omija `_record_failure`. Alert „czat nie odpowiada" potrafił więc paść dla czatu
  czytanego bez zarzutu i skierować operatora do złego podsystemu. Naprawione rozdzieleniem
  wyniku odczytu (`READ_FAILED`).

Przy okazji odpadła nieudokumentowana zależność między `_MAX_PENDING_FAILURES`
a `_PROG_CYKLI_BEZ_ODCZYTU` (progi musiały być równe, żeby licznik nie fałszował) oraz zerowanie
`unknown_count` w `_commit`, które po rozdzieleniu przestało być potrzebne.

## [Nieopublikowane] — naprawy usterek 0.2.19, fala 2: tożsamość

Trzy usterki jednej klasy: identyfikatory AAD porównywane znak w znak. Graph nie obiecuje tej
samej wielkości liter w `/me`, `list_members` i `list_chat_messages`, a czwarte źródło —
`ONLY_USER_IDS` — wypełnia człowiek, czasem kopiując z portalu, czyli w klamrach.

### Naprawione

- **Bot nie bierze już własnej wiadomości za odpowiedź pracownika.** Przy rozjeździe wielkości
  liter `sender == me_id` nie rozpoznawało własnego komunikatu: szedł do modelu i przesuwał
  watermark, a prawdziwa odpowiedź pracownika — starsza — znikała za nim na zawsze.
- **Nadawcą musi być adresat, nie „ktokolwiek poza botem".** Warunek „nie bot" wygląda
  równoważnie tylko dopóki czat jest 1:1. Gdy wątek przestanie nim być, cudza treść stawała się
  „odpowiedzią pracownika" i mogła skończyć ZAPISEM W JEGO GRAFIKU. `incoming_after` dostaje
  wymagany, wyłącznie nazwany argument `nadawca`.
- **GUID wielkimi literami nie wypada już z pilotażu.** Taki wpis nie pasował do nikogo — pilotaż
  milczał, a podsumowanie mówiło „0 próśb", nieodróżnialnie od spokojnego tygodnia.

### Rozstrzygnięcia projektowe

- **Normalizacja PRZY PORÓWNANIU, nie podmiana identyfikatora.** Klucze w pliku stanu to surowe id
  z Graph sprzed tygodni, a `member_id` trafia stamtąd wprost do `POST`-a tworzącego zmianę
  w Shifts. Znormalizowanie „żywej" strony rozminęłoby `state.get(member.user_id)` z istniejącym
  wpisem: osoba z otwartą rozmową dostałaby DRUGĄ prośbę, a obok powstałby drugi wpis o ten sam
  tydzień. Wyjątkiem jest `only_user_ids` — pochodzi wyłącznie z `env`, nie trafia ani do stanu,
  ani do Graph, więc normalizuje się je RAZ, w `Settings.__post_init__`.
- **Obcy nadawca daje `UNKNOWN`, nie `NOTHING_NEW`.** Sam odsiew nie wystarczy: pusta lista znaczy
  u wołającego „pracownik milczy", a to jedyna przesłanka wygaszenia. Bez tego rozróżnienia każde
  błędne odrzucenie kończyłoby się nieprawdziwym „nie dostałem odpowiedzi" i TERMINALNYM
  zamknięciem tematu — na podstawie naszego nieporozumienia, nie jego zachowania. Wpis zostaje
  otwarty, operator dostaje alert. Odwracalne.
- **`guards.ensure_single_owner` świadomie BEZ normalizacji.** To jedyne miejsce, gdzie
  rozluźnienie porównania OSŁABIA zabezpieczenie: stoi na granicy nieodwracalnego zapisu do
  grafiku, a kierunek błędu przy ścisłości jest tam bezpieczny (odmowa zapisu). Komentarz w kodzie
  mówi to wprost, żeby nikt nie „dokończył" normalizacji.
- **Nowy parametr jest wyłącznie nazwany.** `after_iso` jest trzecim argumentem pozycyjnym i tak
  bywa wołany — parametr wstawiony przed nim po cichu przyjąłby watermark jako tożsamość.
  Brak argumentu to głośny `TypeError`.

### Dodane

- `domain/tozsamosc.py` — `znormalizuj` i `ten_sam`, jedno miejsce zamiast dziesięciu `==`.
  Wzorzec z `domain/powody.py`, gdzie ten sam zabieg obowiązuje dla nazw powodów nieobecności.
- Filtr konta bota w `nudge.run_once` (`m.user_id != me_id`) też porównuje teraz przez tożsamość —
  ta sama klasa usterki, ten sam skutek (bot pisze sam do siebie), a żaden strażnik jej nie pilnował.

### Skutki uboczne

- Alert startowy wypisuje identyfikatory w postaci znormalizowanej, więc przestaje być DOSŁOWNĄ
  kopią wpisu z `env` — a jego docstring mówi, że służy właśnie do porównania. Alert mówi o tym
  teraz wprost.
- Krąg odbiorców może się wyłącznie POSZERZYĆ, nigdy zawęzić. W instalacji u klienta sprawdzone:
  wszystkie 8 identyfikatorów jest już w postaci kanonicznej, więc krąg **nie zmieni się wcale**.

## [Nieopublikowane] — naprawy usterek 0.2.19, fala 1

Pierwsze zmiany w kodzie odzyskanym z obrazu. Od tego miejsca repo NIE jest już tożsame
z `powiadomienia-teams:0.2.19` — źródłem prawdy staje się repozytorium, a obraz wymaga
przebudowy. Cztery z dziewięciu udokumentowanych usterek; pozostałe pięć nadal ma strażników
`xfail(strict=True)` z dowodem.

### Naprawione

- **Utrata sesji na szybkiej ścieżce zapisu zatrzymuje usługę.** Szeroki `except Exception`
  w `_apply_confirmed_yes` łapał `AuthExpiredError` przed strażnikami w `_odsiej_juz_zapisane`,
  `poll_replies` i `_process_pending`. Martwy token był raportowany jako zwykła awaria Shifts,
  pracownik dostawał nieprawdziwe „uzupełnij ręcznie", a ścieżka alert +
  `AUTH_FAILURE_EXIT_DELAY_S` + restart do `--login` nie ruszała — token żył do doby.
  Dodatkowo `logger.critical` przed `raise`: wyżej wyjątek nie niesie już informacji, w czyim
  grafiku i którego tygodnia szukać dziury po przerwanym zapisie (status jest wtedy `APPLYING`,
  czyli terminalny i nigdy niewznawiany).
- **Przebieg dowodzi zapisywalności stanu PRZED pierwszą wiadomością.** Dotąd prośby wychodziły
  do ludzi, a dopiero potem próbowaliśmy utrwalić stan; przy niezapisywalnym wolumenie nie
  zostawał po nich ślad i następny przebieg wysyłał je DRUGI RAZ. Kolejności „wyślij, potem
  utrwal" nie odwrócono — jest świadoma i chroni przed pendingiem bez wiadomości. Zamiast tego
  ten sam `save_state`, który i tak stał na końcu funkcji, wykonuje się też przed pętlą.
  Sonda nie daje gwarancji (dysk może zapełnić się po niej), ale zabiera przypadek TRWAŁY.
- **Alerty nie wypuszczają adresów kont poza organizację** (ADR 0006). `_jedyne_konto` sklejało
  służbowe adresy e-mail w komunikat wyjątku, a `zglos_utrate_sesji` podawał `str(blad)` żywcem
  na webhook — u klienta jest nim kanał Discorda. Redakcja jest **opt-in**: wyjątek deklaruje
  `publiczny`, a `operator.tresc_publiczna` go preferuje. Log zachowuje pełną treść, instrukcja
  dla operatora („usuń ten plik, potem `--login`") zostaje nietknięta — znikają tylko adresy.
  Hurtowego filtra świadomie NIE ma: wymieniłby wyciek na ciszę.

### Dodane

- **Strażnik statyczny szwu wysyłki** (`tests/test_szew_wysylki.py`, AST po `runtime/`).
  `runtime/wysylka.py` powoływał się na niego od wydań — i było to NIEPRAWDĄ: odsyłał do
  `tests/test_cisza.py`, gdzie takiego strażnika nigdy nie było (w całym `tests/` nie występował
  ani jeden `ast.parse`). Dlatego jedno pominięcie `NIE_POLYKAJ` wśród jedenastu punktów wysyłki
  mogło przeżyć wydanie. Strażnik sprawdzony na kodzie sprzed naprawy: wskazuje dokładnie to
  miejsce. Odsyłacz w docstringu poprawiony.

### Znane, nienaprawione w tej fali

Strażnik wykrył dwie wysyłki w `_interpret_and_confirm` bez ŻADNEGO `try` (gałęzie „brak powodu
wolnego" i „unclear") — nie podlegają polityce `NIE_POLYKAJ` w ogóle. Domykane razem
z watermarkiem w fali 3. Oznaczone `xfail(strict=True)`.

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
- **Utrata sesji na szybkiej ścieżce zapisu NIE zatrzymuje usługi.** Szeroki `except Exception`
  w `_apply_confirmed_yes` (`listener.py:819`) łapie `AuthExpiredError`, zanim dojdzie ona do
  strażnika w `_process_pending` (`listener.py:623`). Skutek: martwy token jest raportowany jako
  zwykła awaria zapisu do Shifts, pracownik dostaje „uzupełnij ręcznie" (nieprawda — winna jest
  sesja), a ścieżka alert + `AUTH_FAILURE_EXIT_DELAY_S` + restart do `--login` nie rusza. Token
  zostaje martwy do najbliższego pulsu, czyli nawet 24 h. Ten sam plik deklaruje przeciwny
  kontrakt trzy razy (linie 195, 346, 623) — to niespójność, nie decyzja.
  Strażnik: `test_utrata_sesji_przy_zapisie_grafiku_zatrzymuje_usluge` (`xfail(strict=True)`).
- **Nieudana prośba o potwierdzenie zjada odpowiedź pracownika.** `_commit` przesuwa watermark
  BEZWARUNKOWO (`listener.py:511`), także gdy prośba nie została doręczona. Status wraca do
  `AWAITING_REPLY`, ale wiadomość jest już oznaczona jako obsłużona, więc kolejny cykl jej nie
  zobaczy: pracownik czeka na pytanie, które nigdy nie padło, a po terminie dostaje nieprawdziwe
  „nie dostałem odpowiedzi". Docstring `_commit` (`listener.py:462-469`) obiecuje coś odwrotnego.
  Strażnik: `test_nieudana_prosba_o_potwierdzenie_nie_zostawia_wpisu_w_awaiting_confirm`.
- **Brak sondy zapisywalności stanu przed przebiegiem.** Przebieg najpierw PISZE do ludzi, potem
  próbuje utrwalić stan. Przy niezapisywalnym pliku prośby wychodzą bez śladu, więc następny
  przebieg wysyła je DRUGI RAZ — a idempotencja opiera się wyłącznie na tym pliku.
  Strażnik: `test_niezapisywalny_stan_zatrzymuje_przebieg_PRZED_pierwsza_wysylka`.
- **Trwale nieodczytywalny czat blokuje osobę w nieskończoność.** Odczyt rzuca PRZED
  `_record_failure`, więc `fail_count` nie rośnie, watermark nie rusza, a `should_expire` słusznie
  odmawia wygaszenia bez dowodu. Wpis zostaje otwarty na zawsze, co tydzień blokując ponowny nudge,
  a jedynym śladem jest `logger.exception`. Linia repozytorium miała na to twardy sufit wieku.
  Strażnik: `test_twardy_sufit_zamyka_wpis_CICHO_i_z_alertem`.
- **Alerty nie mają redakcji treści.** `zglos_utrate_sesji` podaje `str(blad)` żywcem, a
  `graph.auth._jedyne_konto` wkleja w komunikat nazwy kont z cache'u MSAL (`auth.py:60-63`).
  Adresy kont trafiają więc na webhook alertów — u klienta jest nim kanał Discorda, czyli poza
  organizacją. Linia repozytorium miała `_tresc_publiczna` i atrybut `publiczny`; w 0.2.19 nie ma
  ani jednego, ani drugiego. Strażniki opisują stan faktyczny:
  `test_alert_o_utracie_sesji_WYPUSZCZA_adresy_kont_na_webhook`,
  `test_alert_o_nieudanym_przebiegu_WKLEJA_surowy_komunikat_wyjatku`.
- **Odpowiedź pracownika NIE jest zwolniona z godzin ciszy.** `wysylka.do_pracownika` odmawia
  bezwarunkowo, więc kto odpisze o 22:00, nie dostanie potwierdzenia do 7:00. Wpis czeka
  nietknięty (odpowiedź nie przepada). Zmiana wobec linii repozytorium, która rozmowę zaczętą
  przez pracownika z ciszy zwalniała. Strażnik: `test_odpowiedz_pracownikowi_TEZ_czeka_na_koniec_ciszy`.

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
