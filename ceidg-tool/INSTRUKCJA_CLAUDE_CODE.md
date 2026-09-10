# Narzędzie CEIDG → Excel — instrukcja realizacji

## Cel

Narzędzie w Pythonie, które pobiera dane o jednoosobowych działalnościach gospodarczych
z oficjalnego API v3 Hurtowni Danych CEIDG (`https://dane.biznes.gov.pl/api/ceidg/v3`)
według kryteriów podanych przez użytkownika (nazwa, zakres dat rozpoczęcia, region, PKD,
status) i zapisuje wynik jako skoroszyt Excel czytelny dla człowieka i dla automatów.
Obsługa ma wymagać od użytkownika minimum wiedzy technicznej.

Decyzje już podjęte (nie wracamy do nich):

- Źródłem jest API v3, a nie scraping strony `aplikacja.ceidg.gov.pl` — formularz ASP.NET
  z CAPTCHA jest kruchy i nie jest przeznaczony do automatów.
- Autoryzacja: token JWT w nagłówku `Authorization: Bearer …`. Token uzyskuje tylko
  użytkownik (logowanie Profilem Zaufanym na biznes.gov.pl); poproś go o token, gdy będzie
  potrzebny, i trzymaj go wyłącznie w `.env` / zmiennej środowiskowej.
- Limity API: 50 żądań / 3 min oraz 1000 żądań / 60 min jednocześnie; po przekroczeniu
  180 s blokady liczonej od ostatniego żądania. Zalecany odstęp 3,6 s.
- Środowisko testowe `https://test-dane.biznes.gov.pl/api/ceidg/v3` jest domyślne przez
  cały rozwój. Produkcję włączasz tylko po wyraźnej zgodzie użytkownika.
- Dane JDG to dane osobowe. Zbieramy tylko pola potrzebne do zadania, nie commitujemy
  pobranych danych produkcyjnych do repozytorium.

Dokumentacja API (pobierz i przeczytaj przed fazą 1):
`https://pliki.biznes.gov.pl/akademia/Hurtownia_danych/HD%20CEIDG%20-%20API%20v3%20HD%20-%20Dokumentacja%20dla%20integrator%C3%B3w%20v1.0.pdf`

Praca przebiega w fazach z bramkami. Po każdej bramce zatrzymaj się i pokaż użytkownikowi
wynik przed przejściem dalej.

---

## Faza 0 — środowisko

1. Utwórz repozytorium `ceidg-tool` z `pyproject.toml` (Python ≥ 3.11), `.gitignore`
   obejmującym `.env`, `probe_out/`, `*.xlsx`, `*.sqlite`.
2. Skopiuj dostarczony `ceidg_probe.py` do `scripts/`.
3. Poproś użytkownika o token i zapisz go w `.env` jako `CEIDG_TOKEN`. Jeśli użytkownik
   nie ma jeszcze tokenu, wskaż mu usługę online `https://www.biznes.gov.pl/pl/e-uslugi/00_9999_00`
   i zaczekaj — bez tokenu dalsze fazy nie mają sensu.

## Faza 1 — sonda API (bramka 1)

1. Uruchom `python scripts/ceidg_probe.py --env test`. Skrypt wykonuje ~30 żądań
   z odstępem 3,7 s i zapisuje `probe_out/findings.md` oraz surowe odpowiedzi w
   `probe_out/samples/`.
2. Jeśli sekcja 1 raportu pokazuje 401, token może dotyczyć tylko produkcji. Zapytaj
   użytkownika, czy wolno wykonać sondę na produkcji (`--env prod --skip-raport`);
   uruchom ją tylko po jego potwierdzeniu.
3. Przeczytaj `findings.md` i surowe próbki. Spisz w `docs/decisions.md` odpowiedzi na
   pytania, od których zależy kod:
   - od jakiego numeru zaczyna się `page` (0 czy 1);
   - maksymalny skuteczny `limit` dla `/firmy`;
   - jak API sygnalizuje brak wyników (204 z pustym ciałem czy 200 z pustą listą);
   - czy `count` to liczba wszystkich trafień, czy pozycji na stronie;
   - czy `/firma?ids=…` zwraca wiele firm w jednym zapytaniu i ile identyfikatorów
     przyjmuje — to decyduje o koszcie pobierania szczegółów;
   - semantyka `nazwa` (fragment / dokładna, wielkość liter, polskie znaki);
   - akceptowany format `pkd` i wielkość liter w `wojewodztwo` / `status`;
   - jakie raporty zwraca `/raporty`, jak często powstają, jakie kolumny, separator
     i kodowanie ma CSV, i czy pokrywają typowe zapytanie „region + okres";
   - jakie pola faktycznie występują w `/firma` i jak często wypełnione są
     `email`, `telefon`, `www`.
4. Skopiuj 3–5 reprezentatywnych odpowiedzi z `probe_out/samples/` do
   `tests/fixtures/` — posłużą jako dane do testów offline. Środowisko testowe zawiera
   dane sztuczne, więc fixtures można commitować; próbki z produkcji nie.
5. **Bramka 1:** pokaż użytkownikowi `docs/decisions.md` i wskaż, które ustalenia
   zmieniają założenia (np. batchowanie `ids[]` skraca pobranie 1000 firm z godziny do
   minut; raporty CSV mogą zastąpić API dla zapytań regionalnych). Zaczekaj na akceptację.

## Faza 2 — rdzeń (bramka 2)

Struktura pakietu `ceidg_tool/`:

| Moduł | Odpowiedzialność |
|---|---|
| `config.py` | token i środowisko z `.env`, ścieżki, stałe limitów |
| `criteria.py` | model `Criteria` (pydantic) odwzorowujący parametry API; walidacja dat, sumy kontrolnej NIP, formatu PKD, dozwolonych statusów; metoda `to_params()` produkująca query zgodne z ustaleniami z fazy 1 |
| `client.py` | HTTP z tokenem, limiter oparty na historii żądań (okno 3 min i 60 min liczone równocześnie), obsługa 429 z pełnym odczekaniem 180 s bez wysyłania żądań, retry z backoffem na 5xx, paginacja po `links.next`, obsługa braku wyników zgodnie z fazą 1; metody `list_firms`, `get_details` (z batchowaniem, jeśli działa), `list_reports`, `download_report`, `changes` |
| `store.py` | SQLite: cache szczegółów po `id` z datą pobrania, checkpoint postępu umożliwiający wznowienie przerwanego pobrania, znacznik ostatniej aktualizacji dla trybu `/zmiana` |
| `normalizer.py` | spłaszczenie JSON do stałego schematu kolumn; relacje 1:N (PKD, spółki cywilne, adresy dodatkowe) do osobnych tabel powiązanych `id`; w tabeli głównej `pkd_glowny_kod`, `pkd_glowny_nazwa` i `pkd_wszystkie` (kody rozdzielone `;`) |
| `exporter.py` | skoroszyt Excel, patrz specyfikacja niżej; opcjonalnie CSV i JSONL z tymi samymi kolumnami |

Specyfikacja skoroszytu:

- Arkusze: `Firmy` (tabela główna), `PKD`, `Spolki` (gdy występują), `Slownik`
  (nazwa techniczna kolumny → opis po polsku), `Metadane` (kryteria, środowisko, data
  i czas pobrania UTC, liczba trafień `count`, liczba pobranych rekordów, wersja narzędzia).
- Każdy arkusz z danymi jako tabela Excel (ListObject) z autofiltrem i zamrożonym
  nagłówkiem.
- Nagłówki w `snake_case` bez polskich znaków (`nip`, `nazwa`, `miasto`,
  `data_rozpoczecia`, `pkd_glowny_kod`); opis czytelny dla człowieka w arkuszu `Slownik`.
- `nip`, `regon`, `kod_pocztowy`, `terc`, `simc` jako tekst — inaczej Excel usuwa wiodące
  zera. Daty jako prawdziwe daty Excela.
- Kolumny pochodzenia w każdym wierszu: `zrodlo` (`CEIDG_API` / `CEIDG_RAPORT`),
  `srodowisko` (`test` / `prod`), `pobrano_utc`.

Testy: `pytest` z fixtures z fazy 1, bez połączeń sieciowych. Pokryj przynajmniej:
paginację (w tym początek numeracji), obsługę braku wyników, walidację `Criteria`,
spłaszczanie rekordu z wieloma PKD, zapis NIP jako tekst, wznowienie z checkpointu.
Limiter przetestuj z podstawionym zegarem.

**Bramka 2:** działający przebieg end-to-end na środowisku testowym z jednego polecenia
(np. `python -m ceidg_tool pobierz --wojewodztwo podlaskie --od 2014-01-01 --do 2014-12-31
--out test.xlsx`), plik otwiera się w Excelu/LibreOffice, testy przechodzą. Pokaż plik
użytkownikowi.

## Faza 3 — interfejs użytkownika (bramka 3)

Trzy wejścia, jeden kod:

1. **Tryb interaktywny** (domyślny bez argumentów) — kilka pytań krok po kroku
   z podpowiedziami i możliwością pominięcia (`questionary` lub podobne).
2. **Flagi CLI** (`typer`): `pobierz`, `aktualizuj` (tryb `/zmiana` od ostatniego
   uruchomienia), `raporty` (lista i pobranie gotowych raportów), `sprawdz-token`.
3. ~~**Plik zapytania YAML** o polach zgodnych z `Criteria`, do zadań powtarzalnych
   i harmonogramu.~~ — **wycofane 2026-09-10 decyzją właściciela (ADR-0022).** Wymaganie
   powstało, gdy część pól `Criteria` nie miała flag; tego samego dnia flagę dostało każde
   pole filtrujące, więc plik był drugim formatem wejścia, który nie potrafił nic ponad
   pierwszy — a stanowił drugie miejsce, w którym można się pomylić co do zakresu pobrania.
   Zadania powtarzalne i harmonogram obsługuje **gotowe polecenie**, które kreator wypisuje
   po podjęciu decyzji. Oryginalne brzmienie zostaje przekreślone, a nie usunięte, bo to
   zapis tego, co było zamówione.

Wspólny krok przed każdym pobraniem: jedno zapytanie po `count`, potem deterministyczne
podsumowanie i decyzja użytkownika, np.
`Znaleziono 1 240 firm. Lista podstawowa: ~50 zapytań, ok. 3 min. Z pełnymi szczegółami:
~N zapytań, ok. M min. [lista / szczegóły / popraw kryteria]`. Liczby N i M wynikają
z ustaleń fazy 1 (limit na stronę, batchowanie `ids[]`).

Gdy raporty z `/raporty` pokrywają zapytanie (region + okres), zaproponuj tę ścieżkę
jako pierwszą, z informacją o dacie wygenerowania raportu.

Podczas długiego pobierania: pasek postępu, zapis checkpointu co stronę, jasny komunikat
przy 429 („limit API, wznawiam o HH:MM"), możliwość przerwania i wznowienia
poleceniem `wznow`.

**Bramka 3:** użytkownik bez wiedzy o API przechodzi tryb interaktywny do gotowego pliku.

## Faza 4 — asystent językowy (dopiero po akceptacji użytkownika)

Warstwa `assistant.py` między CLI a `Criteria`: model językowy tłumaczy wypowiedź
użytkownika na `Criteria` (wymuszony schemat przez tool use / structured output),
proponuje kody PKD ze słownika i wyjaśnia, czego API nie potrafi (spółki są w KRS,
brak danych finansowych, filtr dat tylko dla rozpoczęcia działalności, kontakty
opcjonalne). Asystent nie wywołuje API CEIDG i nie zapisuje plików; każdy kod PKD
i każdy parametr przechodzi przez walidator z `criteria.py`, a podsumowanie do
potwierdzenia generuje kod, nie model. Wizard i CLI pozostają dostępne bez asystenta.

Projektując fazę 2, zostaw ten szew: `Criteria` jest jedynym kontraktem między
wejściem a pobieraniem.

---

## Kryteria odbioru całości

- Sonda wykonana, `docs/decisions.md` odpowiada na wszystkie pytania z fazy 1.
- Żadne żądanie nie trafia na produkcję bez wyraźnej zgody użytkownika; token nie
  występuje w kodzie ani w repozytorium.
- Limiter liczy oba okna jednocześnie; jedno uruchomienie nie wywołuje 429 przy
  poprawnym tokenie.
- Przerwane pobranie da się wznowić bez ponownego pobierania zapisanych rekordów.
- Skoroszyt spełnia specyfikację z fazy 2 (tabele, typy, słownik, metadane, pochodzenie).
- Testy działają offline na fixtures.
- Każda faza kończy się krótkim podsumowaniem dla użytkownika: co ustalono, co zbudowano,
  jaka decyzja jest potrzebna.
