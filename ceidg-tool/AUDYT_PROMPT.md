# Prompt: audyt ceidg-tool + środowisko demo

Wklej wszystko poniżej linii do **nowej** sesji Claude Code uruchomionej w `C:\Users\jdoe\BIAP\Ceidg`.

---

Masz cały dzień. Chcę dwóch rezultatów, w tej kolejności ważności:

1. **Działające środowisko demo** — pełna ścieżka narzędzia od zdania po polsku do gotowego skoroszytu, odtwarzalna bez żądań do CEIDG i bez prawdziwych danych osobowych na ekranie.
2. **Poprawiony projekt** — po szczerym audycie, z planem od architekta i przeglądem kodu każdego domkniętego etapu.

Gdy dzień się kończy, **demo wychodzi, a lista poprawek się kurczy**. Powiedz wtedy wprost, co odpadło i dlaczego akurat to.

„Poprawiony projekt" znaczy: naprawione wszystko, co audyt oceni jako **utratę albo przekłamanie danych**, plus wszystko, co blokuje demo. Reszta — wygoda, dług, uproszczenia — jest listą uszeregowaną, z której bierzesz tyle, ile zmieści się w dniu. Nie zaczynaj kolejnej pozycji, jeśli nie zdążysz jej domknąć przeglądem kodu; niedokończony etap jest gorszy niż nierozpoczęty, bo zostawia drzewo w stanie, którego nikt nie ocenił.

## Zanim cokolwiek uruchomisz

`%LOCALAPPDATA%\ceidg-tool\store-prod.sqlite` to 51 MB i 16 310 przedsiębiorców kupionych tysiącami żądań przy odstępie 3,75 s. Jest **nieodtwarzalny** i jest jedyną taką rzeczą na tej maszynie.

- Ustaw `CEIDG_DATA_DIR` na katalog roboczy dla **każdego** uruchomienia narzędzia w tej sesji — audytu i dema. Produkcyjny katalog danych czytaj, nie zapisuj.
- Zrób kopię `store-prod.sqlite` w katalogu roboczym, zanim ruszysz.
- Polecenie `wyczysc` w żadnej formie nie pada dziś ani razu.
- Nie ruszaj: `.env`, `probe_out/`, `raporty/*.zip`, `wyniki/`, `PKD/`.

Audyt biegnie w głównym katalogu roboczym, **bez worktree**: `.env`, `.venv`, `probe_out/` i `PKD/` są w `.gitignore`, więc w worktree ich nie ma i nie dałoby się ani uruchomić bramek, ani sprawdzić twierdzeń na próbkach.

## Stan faktyczny

Sprawdzone dziś, możesz na tym budować:

- 999 testów offline zielonych, mypy strict i ruff czyste; CI zielone na Linuksie i Windowsie, Python 3.11 i 3.12 (pierwszy zielony przebieg w historii projektu: 2026-09-08).
- Baza produkcyjna jest **po** migracji do schematu v3: 16 310 firm, zero duplikatów, zero sierot. **`docs/status.md` twierdzi w otwartych pozycjach, że migracja jeszcze nie przeszła — to nieaktualne** i jest jedną z rzeczy do poprawienia.
- `test-dane.biznes.gov.pl` nie odpowiada (timeout TCP), `dane.biznes.gov.pl` odpowiada w 0,03 s. To jest powód, dla którego demo jest zadaniem projektowym.
- Lokalna gałąź to `master` i śledzi `origin/ceidg-tool` — nazwy się nie zgadzają, więc `push.default` jest ustawione lokalnie na `upstream` i **gołe `git push`** trafia tam, gdzie ma. Nie podawaj refspeców i nie słuchaj podpowiedzi gita, gdy coś pójdzie nie tak: proponowane przez niego `git push origin HEAD` utworzyłoby gałąź `master` na cudzym repozytorium. `origin` to repozytorium innego zespołu — nigdy `Main`, nigdy `--force`. Commituj, kiedy etap jest domknięty i bramki zielone; pushuj zbiorczo, bo `on: push:` nie ma filtra gałęzi i każdy push wydaje minuty Actions organizacji.
- Końcówki linii **nie są usterką**: indeks jest w całości LF, `core.autocrlf=true` i nie ma `.gitattributes`, więc mieszanka widoczna w katalogu roboczym jest przejściowa i niewidoczna w repozytorium. Nie renormalizuj — dotknęłoby to 154 plików jednym commitem na cudzym repo.
- Otwarte i czekające na mnie: bramka 3 (ścieżki API przez kreatora nikt nigdy nie przeszedł — 2026-09-07 skoroszyt powstał, ale ścieżką raportową, która pomija tabelę kosztów), asystent nigdy nie szedł przez kreatora na prawdziwym terminalu, ręczne scenariusze odporności 1/2/8 i B4, oraz ADR-0010, który wymaga **i** mojej zgody, **i** jednego żądania produkcyjnego na rozmiar partii NIP-ów.

Bramki jakości — w PowerShellu ustaw raz `$env:PYTHONUTF8 = "1"`, w bashu prefiksuj `PYTHONUTF8=1`:

```
python -m pytest -q
python -m mypy ceidg_tool tests
ruff check ceidg_tool tests scripts
ruff format --check ceidg_tool tests scripts
```

## Audyt

Czytaj `CLAUDE.md`, `docs/status.md`, `docs/design/phase2_core.md`, `docs/decisions.md` i `docs/adr/` jak **twierdzenia do sprawdzenia**. W tym projekcie konfrontacja dokumentu z kodem złapała już cztery rzeczy, których lektura nie pokazała: przyjęty model kosztów cytował stałą, której program nie używa (`docs/status.md:212`); limiter chodził o jedno żądanie od limitu API, deklarując 4 % zapasu (`:210`); skoroszyt spełniał specyfikację i był niewygodny; a zdanie „CI biegnie na Linuksie i Windowsie" opisywało workflow, który nigdy się nie uruchomił (`:1142`).

Przy każdym twierdzeniu, które sprawdzasz, zapisuj **twierdzenie → metoda → wynik** do `docs/audit-2026-09-09.md`, na bieżąco. To jedyny artefakt, dzięki któremu następny audyt będzie tani, i jedyny, który przeżyje kompaktowanie kontekstu.

Prowadź audyt równolegle w kilku niezależnych przebiegach, tylko do odczytu, i sam zdecyduj ile ich potrzeba i jak je pociąć. Potem **zsyntetyzuj jedną ocenę** — nie streszczaj mi każdego przebiegu z osobna; interesuje mnie zwłaszcza to, gdzie przebiegi się nie zgadzają, bo tego nie widzi nikt poza tobą.

Mutacje rób punktowo, nie jako przeszukiwanie: około dziesięciu, na gwardiach, które `CLAUDE.md` nazywa nośnymi, plus to, co audyt sam wskaże. Mutowanie edytuje kod produkcyjny, więc rób to na własnej kopii drzewa albo wtedy, gdy nic innego nie biegnie.

### Czego oczekuję poza listą usterek

- **Co jest niedokończone, choć wygląda na zrobione?** CI był taki przez cztery dni.
- **Czy projekt przeżyje bez autora?** Co zablokuje kogoś, kto dostanie go jutro.
- **Czy każda kontrola zarabia na siebie?** Dla 13 ADR-ów, 14 reguł granic, `docs/status.md` (1225 linii) i suity testów: nazwij defekt, który dana kontrola złapała albo złapałaby — z datą albo plikiem. Te z wpisem zostają. Te bez wpisu są listą kandydatów do usunięcia. Chcę tego samego ciężaru dowodu w obie strony: „to jest ceremonia" i „to zarabia" mają kosztować tyle samo.
- **Czy budujemy właściwą rzecz?** Jeśli najkrótsza droga do celu operatora omija połowę tej maszynerii, powiedz to.

Oceniaj z dowodu, nie z nastroju. Nie potrzebuję wstępu ani pochwał na rozgrzewkę, ale nie potrzebuję też surowości na pokaz — najgorszy możliwy wynik tego audytu to uznanie za zbędną kontroli, pod którą leży zamknięty defekt, bo ta ocena idzie prosto do architekta i do wdrożenia.

## Środowisko demo

Chcę przejść: zdanie po polsku → kryteria → tabela kosztów → decyzja → pobranie → skoroszyt, plus `aktualizuj` i wznowienie po przerwaniu.

Ograniczenia, które to kształtują — sprawdź je, bo od nich zależy projekt:

- Reguła granic 11 i `AllowedHostsTransport` odbijają wszystko spoza `ALLOWED_HOSTS` na poziomie gniazda, a `trust_env=False` blokuje podmianę CA. Lokalny serwer-atrapa zderzy się z tym wprost.
- `pipeline.build_deps(..., http=…, clock=…)` **już** przyjmuje wstrzyknięty klient i zegar (ADR-0006). Demo może być decyzją korzenia kompozycji, a nie dziurą w bramce wyjścia.
- Limiter siedzi w pipelinie, więc demo odgrywające zapisane odpowiedzi nadal odczeka 3,75 s na żądanie, jeśli nie wstrzyknie się zegara. Bez tego „scenariusz z policzonym czasem" jest nieosiągalny.
- Kreator wymaga TTY (`ui/prompts.py`, `interactive_available`), a twój Bash go nie ma. **Nie buduj pty.** Przepływ prowadź w procesie przez podstawiony `Prompter` i mów wprost, że to nie jest przejście po terminalu — a osobno przygotuj listę kroków, którymi ja przejdę je na żywo. Różnica między „przepływ się wykonał" a „człowiek przeczytał ekrany" jest tym, po co istnieje bramka 3.

Warunki, nie zakazy: każda zmiana w kodzie produkcyjnym ma być taka, którą przyjęlibyśmy na stałe — powiedz, ile kosztuje i gdzie mieszka. Demo ma mówić prawdę o tym, co pokazuje, i nie udawać produkcji, jeśli jej nie pokazuje.

**Dane demo to najbardziej prawdopodobny wyciek tego dnia.** Jedyny dostępny materiał to produkcja. Wszystko, co nagrasz albo zaszczepisz, przechodzi przez `scripts/anonymize_samples.py` (albo jego rozszerzenie), a `.gitignore` rozszerz **zanim** cokolwiek nagrasz — dziś obejmuje `*.sqlite`, `*.csv`, `*.xlsx`, ale **nie** `.json`, `.yaml` ani `.ndjson`, więc `demo/recordings/*.json` trafiłoby do commita domyślnie, do repozytorium czytanego przez inny zespół.

Zanim zaprojektujesz, zadaj mi cztery pytania, których nie zgadniesz — ale **nie czekaj na odpowiedź bezczynnie**. Jeśli nie odpowiem, przyjmij założenie z prawej kolumny, powiedz wprost, że je przyjąłeś, i pracuj dalej; zmiana odpowiedzi ma kosztować przeprojektowanie, nie dzień.

| Pytanie | Założenie, gdy milczę |
|---|---|
| Kto uruchamia demo i na czym | Ja, na tej maszynie, przy widowni |
| Czy ma przeżyć `git clone` na innym laptopie | Tak — to warunek projektowy, nie życzenie |
| Asystent (`api.anthropic.com`, mój klucz, grosze) na żywo czy z nagrania | Z nagrania, żeby demo było powtarzalne; z przełącznikiem na żywo |
| Jak wywołać „wznowienie po przerwaniu" przy widowni | Ubicie procesu, bo to jest scenariusz, który naprawdę się zdarza |

## Przebieg dnia

1. **Audyt** → jedna zsyntetyzowana ocena, zapisana w `docs/` **zanim** mi ją pokażesz. Zatrzymaj się.
2. **Architekt**: co naprawiamy i w jakiej kolejności; osobno projekt dema z **co najmniej trzema wariantami, w tym jednym, którego nie wymieniłem**, z kompromisami. Zatrzymaj się. Decyzje architektoniczne wymagają mojej zgody.
3. **Wdrożenie etapami.** Każdy domknięty etap kończy się przeglądem kodu; znalezisko przeglądu jest wdrożone albo jawnie odrzucone z uzasadnieniem. Testy do nowego zachowania pisze tester i uruchamia kontrolę mutacyjną — test przechodzący po wyłączeniu mechanizmu traktujemy tu jak brak testu. Po każdym etapie dopisz akapit do dokumentu audytu.
4. **Demo** zbudowane i przejechane, z zapisanym wynikiem przejścia i listą kroków dla mnie.
5. Aktualizacja `docs/status.md`, `CLAUDE.md`, `README.md` i briefu w `.claude/sessions/`.

Przy dwóch bramkach nie stój bezczynnie — rób w tym czasie rzeczy, które nie zależą od mojej odpowiedzi, i powiedz, co wybrałeś.

## Moje hipotezy — sprawdź je, nie zakładaj

Poniższe to moje przeczucia, nie ustalenia. Jeśli któreś jest błędne, chcę o tym usłyszeć bardziej niż o jego potwierdzeniu, a jeśli najważniejsze problemy leżą gdzie indziej — idź tam.

Podejrzewam, że najsłabszym miejscem tego projektu jest **materiał dowodowy, nie kod**: powtarzającym się źródłem defektów były fixtures, atrapy i asercje, a nie logika. Anonimizator kasował własność, którą testy miały sprawdzać; cztery atrapy gubiły kontrakt metody; dwie asercje nie mogły upaść (`pytest.approx` na skali epoch daje tolerancję ±1700 s; próg porównany ze stałą, która go wyznacza). Podejrzewam też, że projekt jest przeinwestowany w dokumentację względem celu, którym jest plik Excela dla jednej osoby.

Twierdzenia z `docs/decisions.md` o zachowaniu API (wielkość liter identyfikatorów, rozmiar partii `ids`, numeracja stron, semantyka `count`, PKD, `/zmiana`) sprawdzaj na `probe_out/samples/` i przez `tests/test_api_traits.py` — nigdy nowym żądaniem produkcyjnym. `CLAUDE.md` ostrzega, że jedno takie „zmierzone" twierdzenie było kiedyś ręcznie wpisaną linią w `conftest.py`, więc nie zakładaj ich prawdziwości; zakładaj tylko, że sprawdzenie ich kosztuje zero żądań.

## Granice, które obowiązują przez cały dzień

- **Każde żądanie produkcyjne wymaga mojej zgody w tej sesji.** Zanim poprosisz, podaj ile i po co. Zaproponuj z góry pułap na cały dzień. Znane wartościowe: ~6 na przejście bramki 3, jedno na rozmiar partii NIP-ów z ADR-0010, oraz powtórzenie `aktualizuj`, które po migracji powinno kosztować **zero** żądań o szczegóły — to najtańszy istniejący dowód, że naprawa ADR-0013 działa na prawdziwym rejestrze.
- **Produkcyjny katalog danych jest tylko do odczytu**, `CEIDG_DATA_DIR` wskazuje katalog roboczy, `wyczysc` nie pada.
- Token niesie PESEL w payloadzie: trzymaj go poza logami, komunikatami, bazą i plikami wyjściowymi.
- Komentarze, docstringi i teksty dla użytkownika po polsku; dokumenty w `docs/` po angielsku.

Jeśli któryś z moich powyższych osądów okaże się błędny — powiedz to i uzasadnij pomiarem. Wolę stracić założenie niż dzień.
