# krs-tool

Narzędzie czytające **odpis z Krajowego Rejestru Sądowego zapisany wcześniej do pliku** i wystawiające
raport o sygnałach rejestrowych spółki: co rejestr niesie, czego z tego pliku ustalić się nie da,
i kto każdą z tych luk może zamknąć.

Piąty pod-projekt tego repozytorium, powołany decyzją
[`ceidg-tool/docs/adr/0023`](../ceidg-tool/docs/adr/0023_krs_company_risk_assessment.md).

## Jedna rzecz, którą trzeba wiedzieć przed wszystkim innym

**To narzędzie nie łączy się z niczym.** Nie ma klienta HTTP, nie ma zależności sieciowej, nie ma
`socket` — nie przez flagę do wyłączenia, tylko przez nieobecność w grafie importów i w manifeście
zależności. Pilnuje tego trzech niezależnych obserwatorów: skan importów, kontrola `pyproject.toml`
i zakaz gniazd obowiązujący całą suitę testów.

Powód jest prawny, nie techniczny: art. 60a ustawy o KRS przewiduje karę za pozyskiwanie informacji
z rejestru przez usługi sieciowe bez uprawnienia, o które podmiot prywatny nie może nawet wystąpić,
orzecznictwa nie ma w żadną stronę, a właściciel zdecydował poczekać na stanowisko ministerstwa.
**Zakaz obejmuje także rozwój i testy.**

W praktyce znaczy to jedno: **odpis do pliku zapisuje człowiek**, jedna czynność na spółkę.

## Instalacja

```
cd krs-tool
uv sync --extra dev
```

Polskie znaki na konsoli Windows wymagają `PYTHONUTF8=1` przed każdym wywołaniem Pythona.

## Polecenia

```
PYTHONUTF8=1 uv run krs-tool pokaz   --plik <odpis.json>
PYTHONUTF8=1 uv run krs-tool katalog
PYTHONUTF8=1 uv run krs-tool ocen    --plik <odpis.json>
PYTHONUTF8=1 uv run krs-tool raport  --plik <odpis.json> --markdown raport.md
PYTHONUTF8=1 uv run krs-tool odtworz --ocena-id <identyfikator z dziennika>
PYTHONUTF8=1 uv run krs-tool wyczysc-ladunki --potwierdzam
```

| polecenie | co robi |
|---|---|
| `pokaz` | karta podmiotu odczytana z odpisu — bez oceny i bez zarzutu |
| `katalog` | katalog reguł sygnałowych do przeglądu: kody, poziomy, podstawy prawne |
| `ocen` | werdykt każdej reguły wobec tego odpisu: sygnał, wykluczenie albo nieustalone |
| `raport` | pełny raport z podstawą i cytatem, plus sekcja „czego to narzędzie nie twierdzi" |
| `odtworz` | przelicza zapisaną ocenę z zachowanego ładunku i mówi, czy wyszło to samo |
| `wyczysc-ladunki` | usuwa kopie odpisów; bez `--potwierdzam` tylko pokazuje, co usunie |

`--krs` podaje numer, gdy nie niesie go sam odpis. `--magazyn` przenosi dziennik i ładunki poza
katalog domyślny, a `--bez-dziennika` w `raport` nie zapisuje na dysk niczego.

## Co zostaje na dysku

Domyślny magazyn to katalog danych użytkownika (`user_data_dir("krs-tool")`; na Windows
`%LOCALAPPDATA%\krs-tool`), a ścieżka jest wypisywana na ekranie przy każdym zapisie.

Zostają tam **dwie rzeczy o różnej trwałości**. Wpis w dzienniku — trwale, bo dziennik wyłącznie
rośnie. Oraz **ładunek**, czyli kopia odpisu: niesie dane osobowe wszystkich osób w organach spółki.
`wyczysc-ladunki` usuwa ładunki i zostawia dziennik, po czym `odtworz` zaczyna odmawiać zamiast
udawać, że przeliczył.

## Czego ten raport nie mówi — i dlaczego to jest funkcja

**Sztandarowa reguła nie może wystrzelić, celowo.** Stwierdzenie braku sprawozdania finansowego
wymaga wykluczenia sześciu zgodnych z prawem powodów jego nieobecności; dwóch nie da się ustalić
z odpisu, a jednego nie zmierzono. Reguła zwraca więc zawsze „nieustalone" i mówi o tym na wydruku.
Tak każe tabela „Gate" z ADR-0023 do czasu zmierzenia opóźnienia publikacji wpisu w rejestrze.
Narzędzie, które mówi rzetelnej spółce, że złożyła sprawozdanie po terminie, jest gorsze niż
narzędzie, które milczy.

**Nie ma słowa, którym można by oskarżyć.** Skala poziomów nie ma członu znaczącego „po terminie",
a skan sprawdza katalog reguł i teksty raportu wobec zamkniętej listy słów oskarżających.

**Odpis z innego rejestru niż rejestr przedsiębiorców jest odrzucany, nie oceniany.** Katalog
powołuje się na przepisy o tym jednym rejestrze, a uspokajający raport o podmiocie spoza zakresu
to najgorszy tryb awarii tego produktu — wykryty właśnie w ten sposób, przejściem po odpisach.

**Niepusty dział nie mówi, który wpis w nim stoi.** Model odczytu wie o dziale trzy rzeczy:
nieobecny, pusty, niepusty — nazw kluczy wewnątrz działu nikt nie zmierzył. Dlatego dziś powstaje
**jeden** rodzaj sygnału (niepusty dział 4, art. 41), a pozostałe reguły kończą jako „nieustalone"
wskazujące na czytnik, nie na rejestr.

## Stan

Etap 1 domknięty kodowo 2026-09-11: wszystkie sześć poleceń działa, trzynaście reguł granicznych
ma obserwatora, a każdy obserwator ma test siebie samego z zasianym naruszeniem.

Czytnik powstał na odpisach **syntetycznych** — `docs/pomiary.md` stoi na
`zmierzonych-wlasnosci: 0` i wylicza jedenaście założeń czekających na pierwszy prawdziwy odpis.
Odpis syntetyczny nie jest dowodem: raport z niego niesie znaczniki, przez które nie da się go
pomylić z prawdziwym, a `tests/fixtures/odpis_traits.yaml` go nie przyjmie.

Etap 2 (parser sprawozdań finansowych, wskaźniki, adapter sieciowy) czeka na odpowiedź ministerstwa
— droga prawna opisana jest w `docs/wniosek-ponowne-wykorzystywanie.md`.

## Bramki

```
PYTHONUTF8=1 uv run pytest
uv run ruff check krs_tool tests scripts
uv run ruff format --check krs_tool tests scripts
PYTHONUTF8=1 uv run mypy
```

Pomiar z 2026-09-11: 331 przechodzących, ruff i format czysto, mypy strict na 54 plikach.
Ten pod-projekt niesie **oba** zestawy reguł jakości repozytorium naraz: `mypy --strict` oraz sufit
funkcji `C901`/`PLR0915`. W CI odpowiada mu wpis `krs-tool` w `.github/workflows/ci.yml`.

## Gdzie mieszka projekt

- `docs/status.md` — żywy plan: kroki, bramki, pozycje otwarte
- `docs/adr/0001_zakres_etapu_1_i_granica_offline.md` — zakres etapu 1 i granica offline
- `docs/design/etap1_core.md` — mapa modułów i trzynaście reguł granicznych
- `docs/niezmierzone.md` — czego ten projekt nie ma prawa twierdzić i kto może to zamknąć
- `docs/pomiary.md` — założenia już zakodowane, czekające na potwierdzenie
- `docs/wniosek-ponowne-wykorzystywanie.md`, `docs/pismo-ms-art-60a.md` — droga prawna do API
- `CLAUDE.md` — to, co musi wiedzieć ktoś, kto siada do tego kodu
