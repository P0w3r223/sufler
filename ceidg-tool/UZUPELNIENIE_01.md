# Uzupełnienie 01 do „Narzędzie CEIDG → Excel — instrukcja realizacji"

Dokument rozszerza fazy 2 i 3 oraz kryteria odbioru. Instrukcja główna pozostaje
w mocy; w razie sprzeczności obowiązuje ten dokument. Jeśli prace fazy 2 już trwają,
najpierw wypisz, które gotowe elementy wymagają zmiany, i zaczekaj na akceptację.

---

## A. Przebieg sesji (wzorzec dla trybu interaktywnego)

Pierwszy ekran, przed jakimkolwiek pytaniem, informuje: co program robi, dokąd wysyła
dane (wyłącznie API CEIDG), na jakim środowisku pracuje (TEST / PROD) i do kiedy ważny
jest token. Potem menu:

1. Pobrać firmy według kryteriów
2. Zaktualizować istniejącą bazę o zmiany od ostatniego pobrania
3. Pobrać gotowy raport (region + okres)
4. Wznowić przerwane pobieranie — pokazywane jako pierwsze, gdy w bazie jest
   niedokończone zadanie
5. Sprawdzić pojedynczą firmę po NIP

Po zebraniu kryteriów: jedno zapytanie po `count`, tabela kosztów (zakres danych,
liczba zapytań, szacowany czas, zawartość) i wybór: lista / szczegóły / popraw / wyjdź.
Liczby wynikają z ustaleń fazy 1.

W trakcie pobierania: pasek postępu z ETA. Przy 429: „limit API, wznawiam o HH:MM".
Przy braku sieci: „brak połączenia, czekam, postęp zapisany".

Na końcu podsumowanie: ścieżka i rozmiar pliku, liczba firm według statusu, odsetek
rekordów z telefonem i e-mailem, lista arkuszy, ścieżka logu, zdanie o kolumnie
`link_ceidg` do ręcznej weryfikacji.

Ta sama logika i te same komunikaty obsługują flagi CLI i plik YAML; tryb
nieinteraktywny (`--tak`) przyjmuje wszystkie decyzje domyślne i nadaje się do harmonogramu.

Dodatkowe polecenia: `eksportuj` (ponowny Excel z bazy bez pobierania), `wyczysc`
(usuwa bazę, checkpointy i logi po potwierdzeniu), `sprawdz-token` (środowisko i data
wygaśnięcia, nic więcej).

## B. Bezpieczeństwo informacji

Token: odczyt z systemowego magazynu haseł przez `keyring`; `.env` z uprawnieniami
`600` jako fallback. Logger z filtrem maskującym wzorce JWT. Token nie trafia do
komunikatów błędów, metadanych Excela ani do bazy. Data wygaśnięcia odczytywana lokalnie
z treści JWT, ostrzeżenie 7 dni przed.

Sieć: połączenia tylko do hostów `dane.biznes.gov.pl` i `test-dane.biznes.gov.pl`
(lista dozwolonych w `config.py`), TLS z weryfikacją certyfikatu, zero telemetrii.
Gdy w fazie 4 powstanie asystent, do modelu językowego trafia treść pytania użytkownika
i słownik PKD; pobrane rekordy nigdy.

Dane w spoczynku: baza, pliki wynikowe i logi w katalogu danych użytkownika
(`platformdirs`), nie w katalogu programu. Arkusz `Metadane` zawiera pole „cel
pobrania" podane przez użytkownika.

Wejście: parametry API budowane wyłącznie przez `urlencode(doseq=True)`; wartości
`status`, `wojewodztwo` ze zbiorów zamkniętych; długość pól tekstowych ograniczona.
Nazwa pliku wynikowego generowana z kryteriów i oczyszczona ze znaków ścieżki, zapis
tylko w katalogu `wyniki/`.

Dane z rejestru traktuj jako wrogie. Eksporter neutralizuje każdą komórkę tekstową
zaczynającą się od `=`, `+`, `-`, `@`, tabulatora lub CR (prefiks apostrofu) i zapisuje
ją jako typ tekstowy; dotyczy to Excela i CSV. Znaki sterujące poza tabulatorem i nową
linią są usuwane.

Zależności przypięte w pliku lock; bez `eval`, `pickle` i dynamicznych importów.

## C. Niezawodność

Baza SQLite w trybie WAL. Każda strona listy i każdy rekord szczegółów zapisywane
w osobnej transakcji razem ze znacznikiem czasu żądania; ta historia zasila limiter po
restarcie. Po wznowieniu przerwanego zadania program odczekuje 180 s przed pierwszym
żądaniem, bo nie zna liczby żądań sprzed awarii.

Błąd połączenia (DNS, timeout, reset) jest odróżniany od błędu API. Ponowne próby
z rosnącym odstępem 10 s → 30 s → 60 s → 300 s; po 30 minutach bez sieci program kończy
z instrukcją wznowienia. Odpowiedź 429 oznacza pełne 180 s pauzy bez żądań.

Excel powstaje wyłącznie z bazy, po zakończeniu pobierania, strumieniowo (bez ładowania
całości do pamięci), do pliku tymczasowego przemianowanego atomowo po sukcesie. Przed
eksportem sprawdzane jest wolne miejsce na dysku. Powyżej 1 048 576 wierszy plik
dzielony jest na części o tej samej strukturze.

Limiter używa zegara monotonicznego; wykryty skok czasu lub powrót ze snu traktowany
jak restart. Przy starcie `PRAGMA integrity_check`; uszkodzona baza odkładana z sufiksem
`.uszkodzony`, praca zaczyna się od nowej. Drugi proces na tej samej bazie dostaje
komunikat o blokadzie zamiast równoległego pobierania.

Gdy `count` przekracza próg (domyślnie 50 000), program proponuje zawężenie kryteriów
albo podział na partie z szacunkiem czasu dla każdej.

## D. Testy odpornościowe (`tests/resilience/`)

Każdy scenariusz ma kryterium zaliczenia. Scenariusze 1–3 i 8–10 to testy integracyjne
uruchamiane ręcznie lub w CI z zaślepką serwera; pozostałe działają offline na fixtures.

| # | Scenariusz | Zaliczenie |
|---|---|---|
| 1 | `kill -9` w połowie strony, potem `wznow` | liczba rekordów = `count`, brak duplikatów po `id` |
| 2 | odcięcie sieci na 2 min w trakcie pobierania | kontynuacja bez interwencji i bez utraty danych |
| 3 | skok zegara systemowego o +2 h w trakcie pracy | brak 429, limiter liczy poprawnie |
| 4 | fixtures z nazwami `=CMD()`, `+1`, `@SUM`, `-2+3`, znakami sterującymi, 5 000 znaków | skoroszyt otwiera się, komórki są tekstem, formuły nie wykonują się |
| 5 | ucięty JSON, HTML zamiast JSON, 204 bez treści, 500 | czytelny komunikat, checkpoint nietknięty, brak wyjątku nieobsłużonego |
| 6 | token wygasły, pusty, z błędnym środowiskiem | zatrzymanie przed pierwszym żądaniem o dane, komunikat co zrobić |
| 7 | `grep` tokenu w logach, bazie, plikach wynikowych i komunikatach po pełnej sesji | zero trafień |
| 8 | pełny dysk podczas eksportu | brak pliku częściowego, baza nietknięta, komunikat |
| 9 | `count` = 400 000 | propozycja zawężenia lub podziału, brak automatycznego startu |
| 10 | dwa procesy na jednej bazie | drugi kończy się komunikatem o blokadzie |

## E. Kryteria odbioru (rozszerzenie)

Do listy z instrukcji głównej dochodzą:

- pierwszy ekran i podsumowanie końcowe zgodne z sekcją A;
- test 4 i test 7 przechodzą w CI przy każdym uruchomieniu;
- scenariusze 1, 2 i 8 wykonane ręcznie i opisane w `docs/resilience-report.md`
  z datą i wynikiem;
- `ceidg eksportuj` odtwarza identyczny skoroszyt z samej bazy;
- brak połączeń do hostów spoza listy dozwolonych (test z zaślepką DNS).
