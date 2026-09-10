# ceidg-tool

Narzędzie pobierające dane o jednoosobowych działalnościach gospodarczych z API v3
Hurtowni Danych CEIDG (`https://dane.biznes.gov.pl/api/ceidg/v3`) i zapisujące je do
skoroszytu Excel czytelnego dla człowieka i dla automatów.

Dokumenty: `INSTRUKCJA_CLAUDE_CODE.md` i `UZUPELNIENIE_01.md` (wymagania, w razie
sprzeczności obowiązuje uzupełnienie), `docs/status.md` (plan i stan bramek),
`docs/decisions.md` (ustalenia z sondy API), `docs/adr/` (decyzje architektoniczne,
0008 to warstwa użytkownika, 0011 to asystent), `docs/design/phase2_core.md` (projekt rdzenia),
`docs/resilience-report.md` (scenariusze odpornościowe), `docs/test-runs-phase4.md`
(przebiegi sprawdzające asystenta i ich wyniki), `docs/audit-2026-09-09.md` (audyt
i lista poprawek), `docs/demo-walkthrough.md` (jak przejść pokaz), `CLAUDE.md`
(fakty o projekcie dla Claude Code).

## Skąd wziąć kod

Repozytorium `origin` należy do innego zespołu i jego gałąź domyślna (`Main`) zawiera
**inny produkt**. Ten projekt żyje wyłącznie na gałęzi `ceidg-tool`:

```
git clone --branch ceidg-tool <adres repozytorium>
```

Kto pracuje na lokalnej gałęzi o innej nazwie (np. `master`), niech ustawi to raz:

```
git config branch.<gałąź>.merge refs/heads/ceidg-tool
git config push.default upstream
```

Bez tego podpowiedź gita (`git push origin HEAD`) utworzy na cudzym repozytorium nową
gałąź. **Nigdy nie pushuj do `Main` i nigdy nie używaj `--force`.**

## Instalacja

```
python -m venv .venv
.venv\Scripts\pip install -e .[dev,asystent]
```

**Extra `asystent` jest obowiązkowa**, także gdy nie zamierzasz używać asystenta: bez niej
`tests/test_assistant_caller.py` nie zbiera się (importuje `httpx2`), a skany granic 11 i 12
nie widzą prawdziwego grafu importów. CI instaluje dokładnie to samo. Zależności przypięte
w `requirements.lock`.

## Uruchomienie bez tokenu i bez danych osobowych

```
ceidg-tool pobierz --demo -w wielkopolskie --szczegoly
ceidg-tool sprawdz-nip --demo 9995237548     # karta jednej firmy z rejestru syntetycznego
```

Flagę `--demo` przyjmują `kreator`, `pobierz`, `aktualizuj`, `wznow`, `eksportuj`, `runy`
i `sprawdz-nip`. Nie przyjmują jej `raporty`, `token`, `sprawdz-token` ani `wyczysc`: pierwsze
pobiera archiwum, którego atrapa nie udaje, trzy pozostałe dotyczą poświadczeń i katalogu,
a nie pobierania. To wyliczenie nie jest notatką — pilnuje go
`tests/test_demo_markers.py::test_lista_polecen_z_demo_zgadza_sie_z_tym_co_cli_naprawde_przyjmuje`,
w obie strony.

Tryb `--demo` odpowiada z syntetycznego rejestru w pamięci procesu: nie wychodzi ani jedno
żądanie do CEIDG, nie jest czytany żaden token i nie ma tu niczyich danych. Środowisko
testowe API **nie odpowiada** (host `test-dane.biznes.gov.pl` nie działa), a token wymaga
Profilu Zaufanego — więc dla kogoś, kto właśnie sklonował to repozytorium, `--demo` jest
jedyną drogą, żeby zobaczyć narzędzie w działaniu. Szczegóły: `docs/demo-walkthrough.md`
i ADR-0014.

## Token

Token JWT uzyskasz usługą https://www.biznes.gov.pl/pl/e-uslugi/00_9999_00 (Profil
Zaufany). Narzędzie szuka go kolejno w: systemowym magazynie haseł, zmiennej
`CEIDG_TOKEN`, pliku `.env`.

```
ceidg-tool token zapisz        # zapis w magazynie haseł (bez echa)
ceidg-tool sprawdz-token       # środowisko, źródło i ważność tokenu
```

Token nigdy nie trafia do logów, bazy ani plików wynikowych.

## Środowiska

Domyślne jest środowisko testowe. Produkcja wymaga flagi `--srodowisko prod --produkcja`
(dane osobowe, limity 50 żądań / 3 min i 1000 / 60 min). Narzędzie łączy się wyłącznie
z `dane.biznes.gov.pl` i `test-dane.biznes.gov.pl`.

Uwaga praktyczna: host testowy `test-dane.biznes.gov.pl` nie odpowiada z tej sieci
(timeout na poziomie TCP), więc każde sprawdzenie na żywo wymaga produkcji i świadomej
zgody. Cała reszta jest pokryta testami offline, które nie ruszają sieci.

**Narzędzie nie korzysta z proxy ani z certyfikatów wskazanych przez środowisko.** Zmienne
`HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`, `SSL_CERT_FILE` i `SSL_CERT_DIR` są celowo pomijane,
a połączenie do hosta spoza listy odbija się jeszcze przed zapytaniem DNS. Token niesie w treści
PESEL, więc przepuszczenie go przez pośrednika byłoby wyciekiem danych osobowych — narzędzie woli
odmówić połączenia. W firmowej sieci z proxy albo z podmienianym TLS objawi się to błędem
połączenia; to nie jest usterka, tylko wymaganie bezpieczeństwa. Jedyne miejsce, w którym ta
zasada jest zapisana, to `ceidg_tool/httpclient.py`.

## Użycie

Bez argumentów uruchamia się kreator: pierwszy ekran (co robi program, dokąd wysyła dane,
środowisko, ważność tokenu), menu i prowadzenie krok po kroku aż do gotowego pliku.

```
ceidg-tool                                                   # kreator interaktywny
ceidg-tool kreator                                           # to samo, jawnie
```

Menu kreatora: wznowienie przerwanego pobrania (pokazywane pierwsze, gdy coś czeka),
pobranie firm według kryteriów, aktualizacja bazy o zmiany, gotowy raport, sprawdzenie
firmy po NIP. Po zebraniu kryteriów kreator wypisuje gotowe polecenie — do powtórzenia
tego samego zapytania i do harmonogramu.

Kreator wymaga terminala — uruchomiony z przekierowanym wejściem pokazuje pomoc zamiast
pytać. Na konsolach, które nie obsługują pytań ze strzałkami, schodzi do zwykłych pytań
tekstowych. Ctrl+C przerywa bieżące działanie i wraca do menu, nie kończy sesji.

```
ceidg-tool pobierz --wojewodztwo podlaskie --od 2014-01-01 --do 2014-12-31 --out test.xlsx
ceidg-tool pobierz --miasto Łomża --pkd 9621Z --status AKTYWNY --szczegoly --maks 500
ceidg-tool pobierz -w podlaskie --pkd 9621Z --tak            # tryb nieinteraktywny
ceidg-tool pobierz -w mazowieckie --partie                   # zgoda na podział dużego zapytania
ceidg-tool sprawdz-nip 1234563218                            # jedna firma, 2 zapytania
ceidg-tool wznow                                             # wznowienie przerwanego pobrania
ceidg-tool wznow --force                                     # gdy blokadę trzyma proces, który już nie żyje
ceidg-tool eksportuj --run-id <id> --format xlsx,csv,jsonl   # ponowny eksport bez API
ceidg-tool raporty                                           # gotowe raporty dzienne CEIDG
ceidg-tool aktualizuj                                        # zmiany od ostatniego uruchomienia (pyta o zgodę)
ceidg-tool runy                                              # lista pobrań w bazie
ceidg-tool wyczysc --starsze-niz 30                          # retencja danych
ceidg-tool wyczysc --wszystko                                # kasuje bazę, logi i raporty (pyta)
ceidg-tool wyczysc --wszystko --tak --potwierdzam-usuniecie  # to samo w harmonogramie
```

Każde kryterium ma własną flagę: adres (`--wojewodztwo`, `--powiat`, `--gmina`, `--miasto`,
`--ulica`, `--budynek`, `--lokal`, `--kod`), podmiot (`--nazwa`, `--imie`, `--nazwisko`, `--nip`,
`--regon`, `--nip-sc`, `--regon-sc`), branża i stan (`--pkd`, `--status`, `--od`, `--do`).
Flagę można powtórzyć — `--miasto Wrocław --miasto Gdańsk` znaczy „albo tam, albo tam".

```
ceidg-tool pobierz -w wielkopolskie --nazwisko Nowak --imie Marek
ceidg-tool pobierz -m Poznań --ulica Kwiatowa --budynek 12A
ceidg-tool pobierz --nip-sc 356-345-79-32     # wspólnicy danej spółki cywilnej
```

Plik zapytania YAML (`--zapytanie`) został wycofany 2026-09-10 (ADR-0022): odkąd każde pole ma
flagę, był drugim formatem wejścia, który nie potrafił nic ponad pierwszy. Powtarzalność i
harmonogram obsługuje polecenie wypisywane przez kreator.

Czego w tej liście nie ma — numeru KRS, PESEL-u, nazwy skróconej, obywatelstwa, dat innych niż
data rozpoczęcia — tego nie ma również API v3, więc publiczna wyszukiwarka CEIDG odpowie na kilka
pytań, na które to narzędzie nie odpowie; pełne porównanie stoi w
`docs/research/public-search-parity.md`.

Dla zapytań „województwo + okres” narzędzie proponuje raport dzienny CEIDG
(jeden plik zamiast tysięcy żądań); wybór ścieżki: `--zrodlo auto|api|raport`.
`--zrodlo raport` jest żądaniem stanowczym: gdy żaden gotowy raport nie pokrywa kryteriów,
narzędzie mówi to wprost, zamiast po cichu pobierać przez API.

## Asystent — kryteria zdaniem po polsku

Opcjonalny. Bez klucza narzędzie działa dokładnie tak jak wcześniej, a pierwszy ekran mówi
„asystent wyłączony”.

```
.venv\Scripts\pip install -e .[dev,asystent]   # dodatkowa zależność: anthropic
ceidg-tool token zapisz --asystent             # klucz w magazynie haseł (bez echa)
ceidg-tool pobierz --opis "firmy budowlane w Białymstoku założone w zeszłym roku"
```

Klucz szukany jest kolejno w magazynie haseł, zmiennej `ANTHROPIC_API_KEY` i pliku `.env`;
`sprawdz-token` pokazuje źródło i odcisk, nigdy wartość. Maskowanie obejmuje go tak samo jak
token CEIDG.

W kreatorze opis jest **pierwszym pytaniem** — Enter przechodzi do dotychczasowych ośmiu
pytań bez żadnej różnicy. Odpowiedź modelu trafia na ekran potwierdzenia (kryteria, kody PKD
**wraz z nazwami z lokalnego słownika**, lista rzeczy, których rejestr nie potrafi), a dopiero
po jego zatwierdzeniu idzie jedno zapytanie o liczbę trafień i tabela kosztów. Odmowa nie
kosztuje ani jednego żądania do CEIDG.

Czego asystent nie może z założenia: ustawić limitu rekordów, poszerzyć zgody na produkcję,
wyłączyć progu 50 tys. ani zobaczyć pobranych rekordów — do modelu idzie wyłącznie Twoje zdanie
i słownik PKD. `--opis` razem z `--tak` jest odrzucane: harmonogram nie może działać na
interpretacji, której nikt nie przeczytał. Do harmonogramu służy polecenie, które kreator
wypisuje z zatwierdzonej interpretacji — jedna linia z flagami, gotowa do wklejenia.

Kody PKD pochodzą z klasyfikacji **PKD 2025** (`ceidg_tool/data/pkd2025.yaml`, 728 podklas,
Dz.U. 2024 poz. 1936). Kod ze starszego rocznika — na przykład `62.01.Z` — zostaje przełożony
na dzisiejszy odpowiednik, a ekran mówi o tym wprost.

### Stare kody PKD — dlaczego program czasem o nie pyta

Rejestr jest w trakcie przejścia na PKD 2025 i potrwa ono **do 31.12.2026**. Filtr `pkd` dopasowuje
kod tak, jak zapisano go w rekordzie, więc firma, która jeszcze nie przeszła, ma stary kod i nie
odpowiada na nowy. Zmierzone na 285 026 prawdziwych rekordach: 58,6 % nadal ma kody z 2007,
a zapytanie o fryzjerów kodem `9621Z` sięga 17 % fryzjerów w kraju.

Program dokłada więc odpowiedniki z PKD 2007 (`ceidg_tool/data/pkd2007_2025.yaml`, oficjalny klucz
GUS). Gdy stary kod znaczy dokładnie to samo, dokłada go **bez pytania** i pokazuje, co dołożył.
W pozostałych przypadkach **pyta**, bo szersze wyszukiwanie przynosi też coś, o co nie prosiłeś,
a rekordu ze starym kodem nie da się przypisać do jednej branży. Dzieje się to na dwa sposoby:

- **PKD 2025 rozbiło stary kod na kilka nowych.** `9602Z` „Fryzjerstwo i pozostałe zabiegi
  kosmetyczne" stało się osobnym `9621Z` i `9622Z`, więc szukając fryzjerów tym starym kodem,
  znajdziesz też kosmetyczki.
- **Stary kod nadal istnieje, ale znaczy dziś co innego.** `8551Z` to kiedyś były pozaszkolne formy
  edukacji sportowej, a kluby fitness przeniesiono na `9313Z`; dokładając `8551Z`, weźmiesz też
  firmy, które mają go jako kod dzisiejszy.

Pytanie pokazuje obie liczby, każdą policzoną naprawdę, i nazywa branże, które dojdą.

W harmonogramie pytania nie ma: bez flagi program zachowuje się jak dotąd (tylko PKD 2025) i pisze
jedno zdanie o tym, czego nie objął. Jawny wybór to `--pkd-2007` albo `--bez-pkd-2007`, a kreator
wstawia go do wypisanego polecenia, więc powtórzenie daje ten sam wynik. Jedna różnica warta
wiedzy: polecenie niesie **decyzję**, nie listę starych kodów — dobiera je tablica przejścia
z chwili uruchomienia, więc po jej aktualizacji populacja może być inna.

## Ile to potrwa — zanim się zacznie

Przed każdym pobraniem pada jedno zapytanie o liczbę trafień, po czym program pokazuje
tabelę kosztów (zakres danych, liczba zapytań, szacowany czas, zawartość) i pyta, co dalej.
`aktualizuj` działa tak samo: jedno tanie zapytanie o liczbę zmian, tabela kosztów i pytanie
— bo aktualizacja kilku tysięcy wpisów to kilkadziesiąt minut, o których nikt nie powinien
dowiadywać się z paska postępu. W trybie `--tak` odpowiedź domyślna jest twierdząca, więc
harmonogram działa bez zmian.

Podczas długich etapów program nie milczy: pasek rusza się po każdej porcji żądań, pobieranie
archiwum raportu liczy megabajty, skan raportu odmierza czas przeczytanymi wierszami, a zapis
skoroszytu ma własny pasek — 287 tys. firm to około dziesięciu minut samego zapisu. Program,
który wygląda na zawieszony, bywa zabijany w trakcie poprawnej pracy, więc cisza jest tu
traktowana jak defekt.
Powyżej 50 000 trafień nie startuje sam: proponuje zawężenie kryteriów albo podział na
partie po dacie rozpoczęcia działalności (dekady, lata, kwartały, miesiące — najgrubszy
podział, który mieści się w progu). Każda partia jest osobnym pobraniem z własnym
checkpointem, a wynik trafia do jednego skoroszytu bez duplikatów. W trybie `--tak`
podział wymaga jawnej flagi `--partie`.

## Jednorazowe ujednolicenie bazy

Rejestr zwraca ten sam identyfikator wpisu w dwóch pisowniach: `/zmiana` małymi literami,
pozostałe końcówki wielkimi. Do wersji z 2026-09-08 program traktował pisownię jak tożsamość,
więc `aktualizuj` zapisywał każdą zmienioną firmę **dwa razy** — raz jako pusty wpis, raz jako
komplet danych — a cache szczegółów nie mógł trafić ani razu i każda aktualizacja płaciła pełną
cenę za rekordy, które już były w bazie.

Naprawa dzieje się sama: przy pierwszym uruchomieniu dowolnego polecenia baza przechodzi na
schemat v3, scala zdublowane wpisy i mówi o tym zdaniem na ekranie oraz wpisem w logu. Trwa to
około sekundy, idzie w jednej transakcji (przerwanie zostawia stan sprzed), a powtórne
uruchomienie nic już nie zmienia. Żadne dane nie są przy tym usuwane.

> **Jeśli aktualizujesz narzędzie na istniejącej bazie, nie zaczynaj od `wyczysc`.** Retencja
> kasuje rekordy, których nie trzyma żadne pobranie — a to dokładnie te, które migracja scala
> z powrotem z ich wpisami. Każde inne polecenie jest bezpieczne i samo załatwi sprawę.

Po migracji warto raz powtórzyć `aktualizuj` na tym samym zakresie: powinien nie wysłać ani
jednego zapytania o szczegóły, bo wszystkie są już w cache. To najtańsze potwierdzenie, że
naprawa zadziałała.

## Kasowanie danych

`wyczysc` bez flag usuwa tylko dane starsze niż retencja. `wyczysc --wszystko` kasuje bazę,
checkpointy, logi i pobrane raporty ZIP — i pyta o potwierdzenie. Bez terminala (harmonogram,
przekierowane wejście) pytanie nie ma jak paść, więc polecenie **odmawia** i kończy się kodem 3;
zgodę wyraża się wtedy jawnie: `--potwierdzam-usuniecie`. Samo `--tak` nie wystarcza, bo
skasowania nie da się cofnąć, a raporty ZIP to pełne dzienne zrzuty województwa.

## Po awarii

Jedno pobranie na środowisko naraz — drugi proces dostaje komunikat o blokadzie zamiast
równoległego pobierania. Gdy program został ubity (zanik zasilania, `kill`), blokada zostaje
po nim, bo nie zdążył jej zwolnić. Komunikat podaje wtedy godzinę, o której wygasa sama
(10 minut od ostatniego zapisu). Można poczekać albo przejąć ją flagą `--force` — ale
dopiero wtedy, gdy naprawdę żaden inny proces nie pobiera, bo dwa naraz zderzą się o limit API.

Postęp jest zapisywany po każdej porcji żądań, więc wznowienie nie pobiera niczego drugi raz.
Po wznowieniu program odczekuje 180 s przed pierwszym żądaniem: nie wie, ile żądań poszło
sprzed awarii, a limit API liczy się od ostatniego z nich.

Jeśli komputer **uśpił się** w trakcie długiego pobierania, program powie o tym po wybudzeniu
(„Przerwa w pracy programu: … min") — bo zamrożony proces nie odświeża blokady i ta mogła
w międzyczasie wygasnąć. Gdy okaże się, że przejął ją inny proces, pobieranie kończy się
komunikatem zamiast pisania do bazy równolegle; postęp zostaje i wystarczy `wznow`.
Same przerwy — postoje limitu API i uśpienia — trafiają też do logu razem z godziną, o której
program planował ruszyć dalej. Bez tego dziura w logu wygląda tak samo niezależnie od przyczyny.

## Gdzie są dane

Baza SQLite, pliki wynikowe (`wyniki/`), logi (`logi/`) i pobrane raporty (`raporty/`)
leżą w katalogu danych użytkownika (Windows: `%LOCALAPPDATA%\ceidg-tool`), osobno dla
środowiska testowego i produkcyjnego. Surowe rekordy są przechowywane w bazie, żeby
eksport dało się powtórzyć bez pobierania; domyślna retencja to 30 dni
(`CEIDG_RETENTION_DAYS`), cache szczegółów 7 dni (`CEIDG_CACHE_TTL_DAYS`).

## Skoroszyt

Arkusze `Firmy`, `PKD`, `Spolki`, `Adresy` (dwa ostatnie gdy występują), `Slownik`
(opis kolumn), `Metadane` (kryteria, cel pobrania, środowisko, czasy, liczby, wersja).
Każdy arkusz z danymi to tabela Excela z autofiltrem. Zamrożony jest nagłówek **i kolumny
tożsamości** (`nip`, `nazwa` w arkuszu `Firmy`), więc po przewinięciu w prawo nadal widać,
czyj jest telefon czy kod PKD. Identyfikator wpisu `id` stoi w bloku technicznym przy `link`
— jest kluczem łączącym arkusze, nie kolumną do czytania.
Kolumn, których dane źródło nie umie wypełnić, skoroszyt nie pokazuje: ścieżka raportu chowa
kilkanaście pustych kolumn (`Odkryj` w Excelu je przywraca), a `Metadane` wymieniają je
w wierszu `kolumny_ukryte`. Schemat zostaje pełny, więc automat czytający po nazwie nagłówka
widzi w obu ścieżkach to samo.
`nip`, `regon`, `kod_pocztowy`, `terc`, `simc` są tekstem, daty są datami Excela.
Wartości z rejestru zaczynające się od `=`, `+`, `-`, `@` są neutralizowane, a znaki
sterujące usuwane — tak samo w skoroszycie, w CSV i na ekranie, bo nazwa firmy pochodzi
z publicznego rejestru i trzeba ją traktować jak wrogie wejście.
Powyżej 1 048 575 wierszy plik jest dzielony na części `_czesc01`, `_czesc02`, …

## Rozwój

Polskie znaki na konsoli Windows wymagają `PYTHONUTF8=1` — bez tego wyjście CLI, sond
i pytest jest zniekształcone. **Ustawia się je inaczej w każdej powłoce**, a zapis
`PYTHONUTF8=1 polecenie` działa wyłącznie w bashu: PowerShell odpowiada na to
`The term 'PYTHONUTF8=1' is not recognized`.

**PowerShell** — raz na sesję, potem zwykłe polecenia:

```powershell
$env:PYTHONUTF8 = "1"
.venv\Scripts\python -m ceidg_tool
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy ceidg_tool tests
.venv\Scripts\ruff check ceidg_tool tests scripts
.venv\Scripts\ruff format --check ceidg_tool tests scripts
```

Bramki są cztery, nie trzy. `ruff format` bez `--check` **przepisuje pliki i nie umie
paść**, więc w roli bramki niczego nie pilnuje; CI uruchamia wariant z `--check`.

**cmd**: `set PYTHONUTF8=1` raz na sesję. **bash (Git Bash)**: `PYTHONUTF8=1 polecenie`
albo `export PYTHONUTF8=1`.

**Nie dopisuj `-q`.** `addopts = "-q"` stoi już w `pyproject.toml`, więc jawne `-q` daje `-qq`,
a przy tej gadatliwości pytest nie drukuje linii podsumowania — same kropki postępu. Liczby
cytowane niżej pochodzą z polecenia **bez** `-q`.

**Lokalnie: 1272 testy przechodzą, jeden jest pomijany.** Żaden nie łączy się z siecią. To samo
uruchamia CI (`.github/workflows/ci.yml`) na Linuksie i Windowsie, dla Pythona 3.11 i 3.12 —
ale **w CI liczby są inne**: katalog `probe_out/` jest poza repozytorium (niesie dane osobowe),
więc pięć testów konfrontujących pomiar z surowymi próbkami pomija się uczciwie i to samo drzewo
raportuje 1267 przechodzących i 6 pominiętych.

Pominięcie, które coś znaczy, jest jedno: atrapa trybu demo nie obsługuje końcówki `/raporty`,
więc test pisowni identyfikatorów nie ma dla niej czego sprawdzić. To ta sama otwarta krawędź
dema, którą wymienia `docs/status.md`, i jedyne miejsce, w którym widać ją z poziomu bramki.
Wartownikiem jest **nazwa tego testu, nie liczba** — gdy przestanie być pomijany, ścieżka
raportowa dostała atrapę. Liczba tej roli nie udźwignie, bo zależy od środowiska.

Sonda API: `PYTHONUTF8=1 python scripts/ceidg_probe.py --env prod --skip-raport` (raport
w `probe_out/`; host testowy nie odpowiada, więc sonda wymaga zgody na produkcję).
Sondy z `scripts/` przechodzą przez ten sam limiter i tę samą historię żądań co narzędzie
(`scripts/probe_support.py`), więc sonda uruchomiona w trakcie pobierania nie doprowadzi już
do 429 — obie strony widzą swoje żądania i odczekują. Limit API jest nakładany na token,
a nie na proces, więc liczy się to, co robi token, a nie który program go użył.
Plik `ceidg_probe.py` w katalogu głównym to dostarczony oryginał, kopia robocza jest
w `scripts/`; fixtures z próbek: `python scripts/anonymize_samples.py`. Anonimizacja **zachowuje
wielkość liter identyfikatorów** i kształt identyfikatorów raportów — to własności API, nie
kosmetyka, i ich ujednolicenie ukrywało kiedyś defekt przed całą suitą. Pilnuje tego
`tests/fixtures/api_traits.yaml` razem z `tests/test_api_traits.py`, który porównuje fixtures
z zapisanym pomiarem, a przy obecnych surowych próbkach także z nimi.

Warstwy i reguły granic opisuje `docs/design/phase2_core.md`; reguły dotyczące warstwy
użytkownika pilnuje `tests/test_boundaries.py` skanem importów.
