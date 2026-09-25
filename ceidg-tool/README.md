# ceidg-tool

Narzędzie pobierające dane o jednoosobowych działalnościach gospodarczych z API v3 Hurtowni Danych
CEIDG (`https://dane.biznes.gov.pl/api/ceidg/v3`) do skoroszytu Excel — czytelnego dla człowieka,
a od 2026-09-23 także dla automatu.

Dokumenty: `CLAUDE.md` (fakty o projekcie), `docs/status.md` (plan i bramki), `docs/adr/` (decyzje),
`docs/decisions.md` (pomiary z sondy API), `docs/design/phase2_core.md` (rdzeń i reguły granic),
`INSTRUKCJA_CLAUDE_CODE.md` + `docs/reference/uzupelnienie-01.md` (wymagania; w razie sprzeczności
obowiązuje uzupełnienie).

> [!IMPORTANT]
> ### Ta paczka nie zawiera żadnego poświadczenia. Własne musisz wstawić sam.
>
> **Nie ma tu tokenu CEIDG ani klucza API asystenta.** Token autora nie jest udostępniany, a w
> repozytorium nigdy go nie było — sprawdzone 2026-09-24 na klonie pobranym tego dnia: wszystkie
> 4710 blobów bazy obiektów, po wartości sekretu i po jego kształcie, na 163 refach — każdej
> gałęzi, każdym tagu i każdym refie pull requesta tego klonu. Że `.env` nigdy nie był śledzony, mówi osobny pomiar: `git log --all -- .env`
> jest pusty. Plik jest w `.gitignore`.
>
> **Token jest potrzebny wszędzie poza trzema miejscami.** Bez niego działają: `szukaj-pkd`
> (słownik PKD, zero żądań, bez bazy), `token zapisz` i `token usun` (przyjmują poświadczenie,
> więc same go nie potrzebują) oraz **każde** polecenie uruchomione z `--demo` (rejestr syntetyczny
> generowany w pamięci procesu). Działa też cały zestaw testów.
>
> Każde pozostałe polecenie — łącznie z tymi, które rejestru wcale nie dotykają, jak `eksportuj`,
> `runy`, `wyczysc`, `kreator` czy `sprawdz-token` — kończy się kodem wyjścia **3** i zdaniem
> zaczynającym się od „Brak tokenu:", a nie śladem stosu. Tej listy pilnuje test, w obie strony.
>
> **Jak wstawić własny token** — pełny opis w sekcji [Token i środowiska](#token-i-środowiska):
> 1. uzyskaj token JWT usługą <https://www.biznes.gov.pl/pl/e-uslugi/00_9999_00> (wymaga **Profilu
>    Zaufanego**, więc nie jest to kwestia minuty),
> 2. zapisz go: `ceidg-tool token zapisz` (magazyn haseł systemu, bez echa) — albo skopiuj
>    `.env.example` na `.env` i wpisz wartość tam,
> 3. sprawdź: `ceidg-tool sprawdz-token`.
>
> **Klucz API asystenta jest osobnym i opcjonalnym poświadczeniem.** Bez niego narzędzie działa
> w całości; niedostępne jest wyłącznie `--opis` (kryteria zdaniem po polsku), a pierwszy ekran
> to mówi. Token CEIDG go **nie** zastępuje i odwrotnie.
>
> Jedno ostrzeżenie o kolejności: środowisko testowe API **nie odpowiada** (host
> `test-dane.biznes.gov.pl` nie działa), więc token użyty naprawdę oznacza produkcję — a ta
> wymaga jawnego `--srodowisko prod --produkcja` i niesie dane osobowe.

## Skąd wziąć kod

Ten projekt jest **czwartym pod-projektem repozytorium `PIWorkmate`** i mieszka w katalogu
`ceidg-tool/` na gałęzi `Main` (od 2026-09-10, [ADR 0074](../docs/adr/0074-where-ceidg-tool-should-live.md)
w korzeniu). Zwykły klon wystarczy:

```
git clone <adres repozytorium>
cd PIWorkmate/ceidg-tool
```

Do 2026-09-10 kod żył na osobnej gałęzi `ceidg-tool`, bez wspólnego przodka z `Main`; gałąź
została usunięta 2026-09-11. Kto ma stamtąd klon, niech sklonuje repozytorium na nowo — historia jest ta sama,
ale numery commitów są nowe (autorstwo ujednolicono przy imporcie).

W korzeniu repozytorium mieszka inny produkt (Sufler). Zmiany w tym pod-projekcie idą
zwykłą drogą repozytorium — gałąź robocza i PR do `Main`; obowiązuje tu zakaz `--force`
z `CLAUDE.md` korzenia.

## Instalacja

```
python -m venv .venv
.venv\Scripts\pip install -e .[dev,asystent]
```

**Extra `asystent` jest obowiązkowa**, także gdy nie zamierzasz go używać: bez niej
`tests/test_assistant_caller.py` się nie zbiera, a skany granic 11 i 12 nie widzą prawdziwego grafu
importów.

Powyższe polecenie rozwiązuje zależności z zakresów, czyli daje zestaw ŚWIEŻY, niekoniecznie
ten, na którym przeszła bramka. Żeby dostać dokładnie tamten:

```
.venv\Scripts\pip install -r requirements.lock
.venv\Scripts\pip install -e . --no-deps
```

Tak instaluje CI (§B wymaga przypiętych zależności). `requirements.lock` jest **generowany**,
nie pisany ręcznie — powstaje z `uv.lock`, więc obie drogi instalacji niosą jedno rozwiązanie:

```
uv export --frozen --no-hashes --all-extras --no-emit-project -o requirements.lock
```

Zgodności obu plików pilnuje `tests/test_locki_zgodne.py`; przed jej wprowadzeniem rozjechały
się po cichu o dwa pakiety.

## Pierwsze uruchomienie — bez tokenu i bez cudzych danych

```
ceidg-tool szukaj-pkd fryzjer                       # słownik PKD, zero żądań, bez tokenu i bazy
ceidg-tool pobierz --demo -w wielkopolskie --szczegoly
ceidg-tool sprawdz-nip 9995237548 --demo            # karta firmy z rejestru syntetycznego
```

`--demo` odpowiada z rejestru generowanego w pamięci procesu: nie wychodzi ani jedno żądanie, nie
jest czytany żaden token, nie ma tu niczyich danych. Środowisko testowe API **nie odpowiada**
(host `test-dane.biznes.gov.pl` nie działa), a token wymaga Profilu Zaufanego — więc dla kogoś, kto
właśnie sklonował repozytorium, `--demo` jest jedyną drogą, żeby zobaczyć narzędzie w działaniu.

Flagę `--demo` przyjmują `kreator`, `pobierz`, `aktualizuj`, `wznow`, `eksportuj`, `runy`
i `sprawdz-nip`. Nie przyjmują jej `raporty` (pobiera archiwum, którego atrapa nie udaje), `token`,
`sprawdz-token` ani `wyczysc`. Tego wyliczenia pilnuje test, w obie strony.

## Token i środowiska

**Ta paczka nie zawiera tokenu — musisz wstawić własny.** Token JWT uzyskasz usługą https://www.biznes.gov.pl/pl/e-uslugi/00_9999_00 (Profil Zaufany).
Narzędzie szuka go kolejno w: magazynie haseł systemu, zmiennej `CEIDG_TOKEN`, pliku `.env`.

```
ceidg-tool token zapisz        # zapis w magazynie haseł, bez echa
ceidg-tool sprawdz-token       # środowisko, źródło i ważność — nigdy wartość
```

Domyślne jest środowisko testowe. **Produkcja wymaga `--srodowisko prod --produkcja`** (dane
osobowe; limity 50 żądań / 3 min i 1000 / 60 min). Token nigdy nie trafia do logów, bazy ani plików
wynikowych.

**Narzędzie nie korzysta z proxy ani z certyfikatów wskazanych przez środowisko.** `HTTPS_PROXY`,
`HTTP_PROXY`, `ALL_PROXY`, `SSL_CERT_FILE` i `SSL_CERT_DIR` są celowo pomijane, a połączenie do
hosta spoza listy odbija się przed zapytaniem DNS. Token niesie w treści PESEL, więc przepuszczenie
go przez pośrednika byłoby wyciekiem danych osobowych. W firmowej sieci z proxy albo podmienianym
TLS objawi się to **błędem połączenia — to wymaganie bezpieczeństwa, nie usterka.**

## Użycie

Bez argumentów rusza kreator: pierwszy ekran (co program robi, dokąd wysyła dane, środowisko,
ważność tokenu), menu i prowadzenie aż do gotowego pliku. Po zebraniu kryteriów wypisuje **gotowe
polecenie** — do powtórzenia i do harmonogramu. Kreator wymaga terminala; z przekierowanym wejściem
pokazuje pomoc zamiast pytać.

```
ceidg-tool                                                   # kreator
ceidg-tool pobierz -w podlaskie --od 2014-01-01 --do 2014-12-31 --out test.xlsx
ceidg-tool pobierz --miasto Łomża --pkd 9621Z --status AKTYWNY --szczegoly --maks 500
ceidg-tool pobierz -w podlaskie --pkd 9621Z --tak            # nieinteraktywnie
ceidg-tool pobierz -w mazowieckie --partie                   # zgoda na podział dużego zapytania
ceidg-tool sprawdz-nip 1234563218                            # jedna firma, 2 zapytania
ceidg-tool wznow                                             # wznowienie przerwanego pobrania
ceidg-tool eksportuj --run-id <id> --format xlsx,csv,jsonl   # ponowny eksport bez API
ceidg-tool raporty                                           # gotowe raporty dzienne CEIDG
ceidg-tool aktualizuj                                        # zmiany od ostatniego uruchomienia
ceidg-tool runy                                              # lista pobrań w bazie
ceidg-tool wyczysc --starsze-niz 30                          # retencja
```

Każde kryterium ma flagę: adres (`--wojewodztwo`, `--powiat`, `--gmina`, `--miasto`, `--ulica`,
`--budynek`, `--lokal`, `--kod`), podmiot (`--nazwa`, `--imie`, `--nazwisko`, `--nip`, `--regon`,
`--nip-sc`, `--regon-sc`), branża i stan (`--pkd`, `--status`, `--od`, `--do`). Flagę można
powtórzyć — `--miasto Wrocław --miasto Gdańsk` znaczy „albo tam, albo tam".

Czego w tej liście nie ma — numeru KRS, PESEL-u, nazwy skróconej, obywatelstwa, dat innych niż data
rozpoczęcia — tego nie ma również API v3; pełne porównanie z publiczną wyszukiwarką stoi
w `docs/research/public-search-parity.md`.

Dla zapytań „województwo + okres" narzędzie proponuje raport dzienny CEIDG (jeden plik zamiast
tysięcy żądań); wybór ścieżki: `--zrodlo auto|api|raport`. `--zrodlo raport` jest żądaniem
stanowczym: gdy żaden gotowy raport nie pokrywa kryteriów, narzędzie mówi to wprost, zamiast po
cichu pobierać przez API.

## Wyjście maszynowe — `--wynik json`

Dla harmonogramu i dla agenta. **stdout niesie jeden dokument JSON i nic poza nim**, a cała ludzka
narracja — ekrany, ostrzeżenia, błędy, postęp — idzie na **stderr**. Nic nie jest wyciszone.

```
ceidg-tool pobierz --demo --tak -w wielkopolskie --maks 5 --wynik json
ceidg-tool szukaj-pkd 9602Z --wynik json
```

Koperta zawsze niesie `wersja`, `polecenie`, `status`, `kod_wyjscia`, `demo`, `zapytania` i `uwagi`;
resztę (`kryteria`, `run_ids`, `rekordy`, `pliki`, `blad`, pola własne polecenia) wtedy, gdy
polecenie je wytworzyło. Dokument jest czystym ASCII, więc przechodzi przez każde kodowanie
strumienia.

| Kod | Znaczenie |
|---|---|
| 0 | `ok` albo `przerwano` |
| **4** | `brak_trafien` albo `nic_do_zrobienia` — **tylko pod flagą**; bez niej te same zakończenia dają 0 |
| 1 / 2 / 3 | nieodwracalny / **wznawialny** / konfiguracja, autoryzacja lub decyzja, której tryb `--tak` nie podejmie |
| 130 | Ctrl+C; bez koperty |

`--wynik json` wymaga `--tak` przy każdym poleceniu, które potrafi zapytać, i jest odrzucane przy
`kreator`, `eksportuj`, `wyczysc`, `sprawdz-token` i `token`. Pełny opis powierzchni:
`.claude/skills/ceidg-tool/SKILL.md`.

> Konsekwencja przeniesienia ekranów: **stdout jest pusty, dopóki nie poprosisz o JSON.**
> `ceidg-tool sprawdz-token > out.txt` zapisze pusty plik — treść poszła na stderr.

## Kody PKD — dlaczego program czasem pyta o stare

Słownik to **PKD 2025** (`data/pkd2025.yaml`, 728 podklas, Dz.U. 2024 poz. 1936). Rejestr jest
w trakcie przejścia i potrwa ono **do 31.12.2026**: filtr dopasowuje kod tak, jak zapisano go
w rekordzie, więc firma, która jeszcze nie przeszła, na nowy kod nie odpowiada. Zmierzone na
285 026 rekordach: 58,6 % ma nadal kody z 2007, a zapytanie o fryzjerów kodem `9621Z` sięga 17 %
fryzjerów.

Program dokłada więc odpowiedniki z PKD 2007 (`data/pkd2007_2025.yaml`, oficjalny klucz GUS). Gdy
stary kod znaczy dokładnie to samo — dokłada bez pytania i pokazuje, co dołożył. Gdy nie — **pyta**,
bo rekordu ze starym kodem nie da się przypisać do jednej branży:

- **stary kod rozbito na kilka nowych** — `9602Z` „Fryzjerstwo i pozostałe zabiegi kosmetyczne" to
  dziś `9621Z` i `9622Z`, więc szukając fryzjerów, znajdziesz też kosmetyczki;
- **stary kod nadal istnieje i znaczy co innego** — `8551Z` to kiedyś edukacja sportowa, a kluby
  fitness przeniesiono na `9313Z`.

Pytanie pokazuje obie liczby, każdą policzoną naprawdę. W harmonogramie pytania nie ma: bez flagi
program bierze tylko PKD 2025 i pisze zdanie o tym, czego nie objął; jawny wybór to `--pkd-2007`
albo `--bez-pkd-2007`. Polecenie wypisane przez kreator niesie **decyzję**, nie listę starych kodów,
więc po aktualizacji tablicy przejścia populacja może być inna.

**Zanim wpiszesz `--pkd`, sprawdź kod:** `ceidg-tool szukaj-pkd <kod albo fraza>` mówi, co dany kod
obejmuje dziś i co dołoży `--pkd-2007`. Zero żądań, bez tokenu.

## Asystent — kryteria zdaniem po polsku

Opcjonalny. Bez klucza narzędzie działa dokładnie tak jak wcześniej, a pierwszy ekran to mówi.

```
ceidg-tool token zapisz --asystent                 # klucz w magazynie haseł, bez echa
ceidg-tool pobierz --opis "firmy budowlane w Białymstoku założone w zeszłym roku"
ceidg-tool pobierz --bez-asystenta ...             # nie buduj go i nie pytaj o opis
```

W kreatorze opis jest **pierwszym pytaniem**; Enter przechodzi do dotychczasowych ośmiu. Odpowiedź
modelu trafia na ekran potwierdzenia (kryteria, kody PKD wraz z nazwami ze słownika, lista rzeczy,
których rejestr nie potrafi), a dopiero po zatwierdzeniu idzie zapytanie o liczbę trafień. Odmowa
nie kosztuje ani jednego żądania do CEIDG.

Do modelu idzie **wyłącznie Twoje zdanie i słownik PKD** — nigdy pobrane rekordy. Asystent nie może
ustawić limitu rekordów, poszerzyć zgody na produkcję ani wyłączyć progu 50 tys. `--opis` razem
z `--tak` jest odrzucane: harmonogram nie może działać na interpretacji, której nikt nie przeczytał
— do harmonogramu służy polecenie wypisywane przez kreator.

## Ile to potrwa i co, gdy się przerwie

Przed każdym pobraniem pada jedno zapytanie o liczbę trafień, po czym program pokazuje tabelę
kosztów (zakres, liczba zapytań, czas, zawartość) i pyta, co dalej. `aktualizuj` działa tak samo.
**Powyżej 50 000 trafień nie startuje sam**: proponuje zawężenie albo podział na partie po dacie
rozpoczęcia; w trybie `--tak` podział wymaga jawnej flagi `--partie`.

Podczas długich etapów program nie milczy. Na terminalu rusza się pasek; poza terminalem — bo tam
`rich` nie rysuje klatek pośrednich — idzie wiersz co osiem żądań, ze znacznikiem czasu.

Jedno pobranie na środowisko naraz. Gdy program został ubity, blokada zostaje po nim i wygasa sama
10 minut od ostatniego zapisu; można poczekać albo przejąć ją `--force` — ale dopiero gdy naprawdę
żaden inny proces nie pobiera. Postęp zapisuje się po każdej porcji żądań, więc `wznow` nie pobiera
niczego drugi raz; po wznowieniu program odczekuje 180 s, bo nie wie, ile żądań poszło przed
awarią. Uśpienie komputera jest meldowane po wybudzeniu, razem z godziną planowanego startu — także
w logu, bo dziura w logu wygląda tak samo niezależnie od przyczyny.

## Gdzie są dane i co jest w skoroszycie

Baza SQLite, `wyniki/`, `logi/` i `raporty/` leżą w katalogu danych użytkownika (Windows:
`%LOCALAPPDATA%\ceidg-tool`), osobno dla środowiska testowego i produkcyjnego. Surowe rekordy
zostają w bazie, żeby eksport dało się powtórzyć bez pobierania; retencja 30 dni
(`CEIDG_RETENTION_DAYS`), cache szczegółów 7 dni (`CEIDG_CACHE_TTL_DAYS`).

> **Aktualizujesz narzędzie na istniejącej bazie? Nie zaczynaj od `wyczysc`.** Przy pierwszym
> uruchomieniu dowolnego innego polecenia baza przechodzi na schemat v3 i scala wpisy zdublowane
> przez dwie pisownie identyfikatora — w jednej transakcji, około sekundy, bez kasowania czegokolwiek.
> Retencja usuwa akurat te rekordy, które migracja ma z powrotem scalić.

`wyczysc --wszystko` kasuje bazę, checkpointy, logi i raporty, i pyta o potwierdzenie. Bez terminala
pytanie nie ma jak paść, więc polecenie **odmawia** i kończy się kodem 3; zgodę wyraża się wtedy
jawnie przez `--potwierdzam-usuniecie`. Samo `--tak` nie wystarcza.

Skoroszyt: arkusze `Firmy`, `PKD`, `Spolki`, `Adresy` (dwa ostatnie gdy występują), `Slownik`
i `Metadane`. Każdy arkusz z danymi to tabela z autofiltrem; zamrożony jest nagłówek **i kolumny
tożsamości**, więc po przewinięciu w prawo nadal widać, czyj jest telefon. Kolumn, których dane
źródło nie umie wypełnić, skoroszyt nie pokazuje (`Metadane` wymieniają je w `kolumny_ukryte`), ale
schemat zostaje pełny, więc automat czytający po nazwie nagłówka widzi w obu ścieżkach to samo.
`nip`, `regon`, `kod_pocztowy`, `terc`, `simc` są tekstem, daty datami Excela. Wartości zaczynające
się od `=`, `+`, `-`, `@` są neutralizowane — nazwa firmy pochodzi z publicznego rejestru i trzeba
ją traktować jak wrogie wejście. Powyżej 1 048 575 wierszy plik dzieli się na `_czesc01`, `_czesc02`…

## Rozwój

Polskie znaki na konsoli Windows wymagają `PYTHONUTF8=1`. **Ustawia się je inaczej w każdej
powłoce**, a zapis `PYTHONUTF8=1 polecenie` działa wyłącznie w bashu.

```powershell
$env:PYTHONUTF8 = "1"          # PowerShell, raz na sesję (cmd: set PYTHONUTF8=1)
.venv\Scripts\python -m pytest
.venv\Scripts\python -m mypy ceidg_tool tests
.venv\Scripts\ruff check ceidg_tool tests scripts
.venv\Scripts\ruff format --check ceidg_tool tests scripts
```

Bramki są cztery, nie trzy: `ruff format` bez `--check` **przepisuje pliki i nie umie paść**, więc
w roli bramki niczego nie pilnuje. **Nie dopisuj `-q`** — `addopts = "-q"` stoi już w
`pyproject.toml`, a `-qq` wyłącza linię podsumowania.

Lokalnie **1510 testów przechodzi, jeden jest pomijany**; w CI, gdzie `probe_out/` jest poza
repozytorium, pięć dalszych pomija się uczciwie i to samo drzewo daje **1505 / 6**. Żaden test nie
łączy się z siecią. Pominięcie, które coś znaczy, jest jedno: atrapa dema nie obsługuje `/raporty`.
**Wartownikiem jest nazwa tego testu, nie liczba.**

Sonda API: `PYTHONUTF8=1 python scripts/ceidg_probe.py --env prod --skip-raport` (host testowy nie
odpowiada, więc sonda wymaga zgody na produkcję). Sondy przechodzą przez ten sam limiter i tę samą
historię żądań co narzędzie, bo limit jest nakładany na **token**, nie na proces. Fixtures
z próbek: `python scripts/anonymize_samples.py` — anonimizacja **zachowuje wielkość liter
identyfikatorów**, bo to własność API, a jej ujednolicenie ukrywało kiedyś defekt przed całą suitą.

Warstwy i reguły granic 1-15 opisuje `docs/design/phase2_core.md`; pilnuje ich
`tests/test_boundaries.py` skanem składni.
