# Plan rozwoju — REKONSTRUKCJA z cytatów w kodzie

> **Ten plik nie jest oryginałem.** Oryginalny `plan-rozwoju.md` zginął razem z drzewem roboczym,
> z którego zbudowano obraz `powiadomienia-teams:0.2.19` — nie ma go ani w historii gita
> (`git log --all` po tej ścieżce jest pusty), ani w obrazie (niesie wyłącznie `src/` i `scripts/`),
> ani w archiwach kontekstu. Kod cytował go jednak **czterdzieści razy** jako uzasadnienie decyzji,
> więc czytelnik `listener.py` trafiał na „pozycja D5 planu zdejmie tę własność" i nie miał gdzie
> sprawdzić, czym jest D5.
>
> **Autorytetem każdej pozycji jest cytujący ją komentarz, nie czyjaś pamięć.** Treść wpisów
> wyprowadzono z tych komentarzy i tylko z nich. Pozycja, której cytat nie niesie treści, dostaje
> status `nieodtworzona` i **nie jest domyślana** — lepiej mieć w tabeli jawną dziurę niż zdanie,
> które brzmi wiarygodnie i nie ma pokrycia.
>
> Odtworzone 2026-09-07. Inwentarz zebrany maszynowo: `\b[NABCDE][0-9]{1,2}\b` oraz `§x.y`
> po `src/` i `tests/`. Dwucyfrowy limit odsiewa kody `ruff` (`E501`, `C901`, `B008`).
> Zgodności planu z kodem pilnuje `tests/test_plan_rozwoju.py`.

## Jak czytać statusy

| status | znaczenie |
|---|---|
| `zrealizowane` | pozycja jest w kodzie i cytat opisuje stan obowiązujący |
| `zrealizowane inaczej` | pozycja weszła, ale w innym kształcie niż zapowiadał plan — cytat mówi, dlaczego |
| `otwarte` | pozycja cytowana jako przyszła; kod jej dziś nie ma |
| `zrealizowane w części` | weszła JEDNA z rozdzielnych połówek pozycji; wpis mówi, która i co zostaje |
| `napisane, niepodłączone` | kod istnieje i nikt go nie woła |
| `nieodtworzona` | cytat nie niesie treści; wpis czeka na kogoś, kto ją pamięta |

Kierunek sprawdzania jest jednostronny: **każdy cytat w kodzie musi mieć wpis tutaj**, ale wpis
bez cytatu jest dozwolony — plan ma prawo zawierać rzeczy niezrobione.

---

## N — niezmienniki i rozstrzygnięcia rdzenia

| # | Treść | Status |
|---|---|---|
| **N1** | Zapis do Shifts wyłącznie po jawnym „tak" pracownika. Propozycja jest jedno słowo od nieodwracalnego zapisu, a `create_shift` nie deduplikuje. | `zrealizowane` |
| **N4** | `APPLYING` jest statusem TERMINALNYM: zapis rozpoczęty i niepotwierdzony nie jest wznawiany automatycznie, bo ponowienie mogłoby zdublować wpisy. Cena: takie wpisy zostają w stanie bezterminowo, poza retencją. | `zrealizowane` |
| **N5** | Tripwire: próba zapisu grafiku cudzą tożsamością jest naruszeniem, nie awarią sieci — `CRITICAL` w logu i alert wagi krytycznej. | `zrealizowane` |
| **N9** | Wygaszenie tematu wymaga DOWODU z udanego odczytu czatu, który nic nie przyniósł. Milczenie usługi nie jest milczeniem pracownika. | `zrealizowane` (ADR 0003) |
| **N10** | Kotwicą terminu odpowiedzi jest chwila prośby bota. | `zrealizowane inaczej` — od 0.2.13 termin jest KALENDARZOWY (północ poniedziałku tygodnia docelowego + offset); z N10 została wyłącznie dolna granica kurtuazji (`REPLY_MIN_HOURS`), której zero wyłączyć nie wolno |
| **N11** | Watermark rośnie dopiero po UDANEJ obsłudze porcji. Skutek uboczny, o którym trzeba pamiętać: błąd deterministyczny sprowadzałby tę samą wiadomość w każdym ticku — stąd sufit `_MAX_PENDING_FAILURES`. | `zrealizowane` |
| **N13** | Do pracownika nie trafia żaden tekst spoza zamkniętego zbioru stałych w `messages.py`. Zmienne są wartości, nie zdania. | `zrealizowane` |
| **N14** | Bramka godzin ciszy stoi PRZY WYSYŁCE, nie w rozproszonych warunkach u wołających. Wariant „trzynaście rozproszonych warunków" odrzucony świadomie. | `zrealizowane` |
| **N15** | Jedna prośba na osobę na tydzień — idempotencja przebiegu opiera się wyłącznie na pliku stanu. Wpis domknięty pozostaje pełnoprawnym strażnikiem tej reguły. | `zrealizowane` |
| **N17** | Tylko JEDNA instancja pisze plik stanu; blokada pliku chroni przed podwójnym zapisem do Shifts. Polecenia wyłącznie odczytowe (`--stan`) blokady nie biorą. | `zrealizowane` |
| **N19** | Ucięty odczyt na limicie stron jest fail-closed na ścieżce ZAPISU: „mam połowę danych" musi wstrzymać wpis. | `zrealizowane` |
| **N20** | Odmowa z powodu godzin ciszy ma WŁASNY typ wyjątku. Pusty wynik był nieodróżnialny od „nikomu nie brakuje grafiku" i odhaczał termin jako obsłużony. | `zrealizowane` |
| **N22** | Daty i dni liczy KOD, nie model. Ta sama rodzina zadań co wnioskowanie wzorca (D1). | `zrealizowane` |
| **N23** | Z granicy narzędzi modelu nie ma prawa wyjść nic poza kontraktem — każdy wyjątek zamieniany jest na `OdczytNiedostepny`. Zawężenie siatki do dwóch typów postawiłoby N23 na braku wyjątku zamiast na kontrakcie. | `zrealizowane` |
| **N28** | Dane osobowe (nazwisko, treść rozmowy, powód wolnego) poza `repr`, poza logami i poza alertami — tak samo jak klucz API. | `zrealizowane` |
| **N32** | Tydzień urlopowy nie przeczy wzorcowi pracy. Rozszerzone przeglądem D1 na poziom pojedynczego DNIA urlopowego. | **`odrzucone` 2026-09-07** — żyło wyłącznie w module skasowanym razem z D1; dziś nie obowiązuje w kodzie |
| **N33** | Pewność propozycji wolno wyłącznie OBNIŻAĆ; kierunek w dół jest zawsze bezpieczny. | **`odrzucone` 2026-09-07** — jw.; pojęcie „pewności propozycji" zniknęło razem z modułem |
| **N34** | Kontrakt zgodności: status nieznany temu wydaniu przeżywa cofnięcie obrazu i jest raportowany, a nie odsiewany. Chroni wartości ZAPISYWANE NA DYSK — enumy trzymane wyłącznie w pamięci pod N34 nie podpadają. | `zrealizowane` (§11) |
| **N35** | Narzędzia modelu nie mają własnej drogi do Graph: cały odczyt idzie przez wspólny snapshot przebiegu. | `zrealizowane` (0.2.14) |
| **N38** | `awaiting_yes` jest jedyną bramką szybkiej ścieżki zapisu, a KAŻDA ścieżka domykająca temat ją kasuje. Wycofanie commitu nigdy nie ma prawa tej bramki rozszerzyć. | `zrealizowane` (0.2.17) |
| **N39** | Termin obiecany w treści wiadomości nie może wypaść w przeszłości — także przy ujemnym przesunięciu. | `zrealizowane` |

## A — obserwowalność i praca bezobsługowa

| # | Treść | Status |
|---|---|---|
| **A3** | Maszyneria kopii pliku stanu: utrata stanu znaczy prośby wysłane drugi raz do całego zespołu i zerwane rozmowy. | `zrealizowane` |
| **A4** | Raport `--stan` mówi WPROST o utracie stanu bez użytecznej kopii — to jedyna pozycja raportu, przy której właściwą reakcją jest zatrzymanie usługi. Cisza w tym miejscu czytała się jak potwierdzenie, że wszystko gra. | `zrealizowane` |
| **A7** | Projekcje konfiguracji (`TeamContext`, `OknoOdpowiedzi`, `OknoCiszy`) zamiast przekazywania całych `Settings` do czystej logiki. Składanie tych wartości u każdego wołającego z osobna dawało warstwę, której skasowanie nie psuło ani jednego testu. | `zrealizowane` |
| **A9** | Podsumowanie dla administratorów rozbite PO TYGODNIU docelowym: bez tego wpisy tygodnia otwartego mieszają się z resztkami zamykanego i żadna liczba nie odpowiada na pytanie operatora. | `zrealizowane` |
| **A10** | `LOGUJ_NAZWISKA` (domyślnie `false`): logi i alerty nazywają pracownika identyfikatorem, bo alert zostaje w kanale bezterminowo i jest przeszukiwalny. Jedna flaga rządzi logami i alertami naraz. | `zrealizowane` |
| **A12** | Założenie robocze: **logów nikt nie czyta**. Lekarstwem na przeoczony problem nie może być następna linia w logu — musi nim być alert albo zatrzymanie startu. | `zrealizowane` jako zasada |

## B — termin odpowiedzi i mechanika rozmowy

| # | Treść | Status |
|---|---|---|
| **B1** | Przesunięcie terminu (`REPLY_DEADLINE_OFFSET_H`) jest rozstrzygnięciem klienta (§4.2/1), nie liczbą techniczną. Wolno ujemne. Świadoma cena wartości 5: odpowiedź z poniedziałku po 05:00 jest już po terminie. | `zrealizowane` |
| **B4** | Po odpuszczonej porcji cofnąć status na `AWAITING_REPLY`, żeby zamknąć szybką ścieżkę zapisu. | `zrealizowane inaczej` — bramką jest FLAGA `awaiting_yes` (N38), bo status ma drugiego konsumenta: rozstrzyga, który komunikat domknięcia dostanie pracownik |
| **B5** | Godziny ciszy PRZESUWAJĄ nadrabianie, a nie unieważniają go. Defekt B5 dał też rozdzielenie `now` (odniesienie tygodnia) od `teraz` (chwila faktyczna) — bez wartości domyślnej, żeby zlanie obu było błędem przy uruchomieniu, a nie o trzeciej nad ranem u pracownika. | `zrealizowane` (0.2.13) |
| **B7** | Treść prośby MÓWI o terminie, a wartość liczy ten sam kod, który termin egzekwuje. Rozjazd obietnicy z zachowaniem wychodził dopiero wtedy, gdy ktoś tracił tydzień grafiku. | `zrealizowane` |
| **B8** | Rozpoznanie potwierdzenia to miejsce, w którym „prawie na pewno tak" jest za mało. Stąd `is_pure_affirmation` zamiast heurystyki „są cyfry", i stąd „tak?" NIE jest potwierdzeniem — pytajnik zostaje przy tokenie. | `zrealizowane` (0.2.13) |

## C — fale naprawcze usterek importu 0.2.19

| # | Treść | Status |
|---|---|---|
| **C1** | Usterki dające się naprawić bez zmiany kontraktu stanu (utrata sesji na szybkiej ścieżce zapisu, sonda zapisywalności stanu, redakcja alertów). | `zrealizowane` (PR #102, #104) |
| **C2** | Pomiar z pilotażu: koszt modelu ma być policzalny z logu, w trzech składnikach wejścia osobno (nowe, zapis do cache'u, odczyt z cache'u), bo mają różne ceny. | `zrealizowane` (§7) |
| **C3** | Watermark: niedoręczona wiadomość cofa commit. Trzy miejsca w `_interpret_and_confirm` utrwalały obsługę porcji PRZED wysyłką i nie cofały jej, gdy wysyłka padła. | `zrealizowane` (PR #108) |

## D — rozwój funkcji

| # | Treść | Status |
|---|---|---|
| **D1** | Wnioskowanie wzorca pracy z historii czterech tygodni, z poziomem pewności. Dwie reguły nadrzędne: **twarda reguła alfabetu** (proponować wolno wyłącznie grafiki zaobserwowane w historii — żadnego uśredniania) i **dopasowanie dokładne** (6:00–14:00 i 6:15–14:00 to dwa różne grafiki). Priorytet: lepiej częściej pytać, niż częściej zgadywać. | **`odrzucone` 2026-09-07, PO POMIARZE.** Zero osób z pewnością WYSOKA i inną propozycją (tabela niżej) — w tym zespole nikt nie chodzi w rytmie przemiennym, czyli w tym, dla czego D1 powstało. Moduł, skrypt pomiarowy i jego testy skasowane; kod żyje w historii gita, wskrzeszenie to `git show b3188e9~1` po ścieżkach reminders/wzorzec.py i scripts/zbierz_historie.py |
| **D2** | Poprzedni czytnik grafiku dla narzędzi modelu miał `except Exception` przy każdym wywołaniu Graph; zastąpiony kontraktem `ZrodloTygodnia` z siatką o tej samej szerokości. | `zrealizowane` |
| **D5** | **Wznowienie rozmowy po statusie terminalnym.** Dziś temat domknięty jest zamknięty na zawsze, a `poll_replies` przetwarza wyłącznie wpisy otwarte. D5 ten filtr zdejmuje i dokłada ścieżki piszące do milczących (przypomnienia, wznowienia). Kilka miejsc w kodzie jest już napisanych tak, żeby przetrwały to zdjęcie — łącznie z kasowaniem `awaiting_yes` tam, gdzie dziś niczego to nie zmienia. | **`zrealizowane inaczej`** 2026-09-09 — obie połowy weszły, druga w kształcie WĘŻSZYM niż brzmiał plan. (1) `runtime/przypomnienie.py` wysyła JEDNO przypomnienie milczącemu (krok 1.7, `PRZYPOMNIENIE_PO_H`, domyślnie sobota rano). (2) Wznowienie rozmowy: plan mówił „zdejmuje filtr `open_items`", ale filtr zdjęty W CAŁOŚCI wpuściłby wpisy terminalne także do kroków 1.5–2, które orzekają o rozmowie TRWAJĄCEJ. Poszerzony został więc **wyłącznie krok 1 (odczyt)**, i tylko dla statusów `WZNAWIALNE` = {`DECLINED`, `EXPIRED`}. `APPLIED`/`SELF_FILLED` są wykluczone nie z ostrożności, lecz z BRAKU NARZĘDZIA: klient Graph nie ma kasowania ani zmiany zmiany, a `create_shift` nie deduplikuje — „poprawka" po zapisie znaczyłaby drugą zmianę nakładającą się na pierwszą. `APPLYING` wykluczone z N4. |

**Dlaczego akurat ta połowa i akurat teraz.** Pomiar z dwóch tygodni pilotażu: **10 próśb → 4
domknięte skutkiem, 6 wygasłych bez odpowiedzi**. Bot pytał dokładnie RAZ (piątek 16:00) i milczał
do terminu (poniedziałek 05:00), więc jedyną dźwignią wobec tej liczby było dołożenie zagadnięcia
— nie zmiana treści ani interpretacji. Druga połowa D5 (wznowienie po wygaśnięciu) ratuje
spóźnialskich, ale liczby milczących nie rusza, a dotyka wszystkich pięciu kroków `poll_replies`;
dlatego rozdzielona.

**Niezmiennik, na którym stoi ta połowa:** przypomnienie NIGDY nie przesuwa terminu odpowiedzi.
Każda wiadomość bota podnosi dolną granicę kurtuazji o `REPLY_MIN_HOURS` (`termin_odpowiedzi`
bierze maksimum), a treść pierwszej prośby obiecała pracownikowi konkretną godzinę (**B7**).
`lifecycle.czas_na_przypomnienie` wysyła więc wyłącznie wtedy, gdy kurtuazja mieści się pod
terminem kalendarzowym — przy przebiegu w piątek 16:00 i kurtuazji 24 h ostatnią dozwoloną chwilą
jest niedziela 05:00. Zbyt późna konfiguracja **wycisza** przypomnienie, zamiast łamać obietnicę.

## E — koszt modelu i pilotaż

| # | Treść | Status |
|---|---|---|
| **E0** | Koperta stanu dla trwałych liczników. Log wystarcza pilotażowi; trwały licznik wymaga E0. | `otwarte` — ale **E4 jej NIE potrzebowało**. Przesłanka „trwały licznik wymaga E0" jest prawdziwa tylko dla liczników GLOBALNYCH; miara E4 jest z definicji *per osoba*, więc zmieściła się w `PendingReminder` (dwa pola `int`) i sprząta się razem z wpisem. E0 zostaje otwarte dla tego, co naprawdę jest globalne — np. skumulowanego zużycia tokenów. |
| **E1** | Kontrola kosztu modelu — sufit oparty na pomiarze z pilotażu (C2), nie na przypuszczeniu. | **`otwarte` — rekomendacja: ODRZUCIĆ, po POMIARZE na żywym modelu (2026-09-09, tabela niżej): $0,0060 za wiadomość, ~$0,03–0,06 tygodniowo, ~$2–3 ROCZNIE.** Dodatkowo zweryfikowano wykonaniem, że koszt zależy od liczby wiadomości, a nie od czasu — nie istnieje scenariusz rozbiegowy, którego sufit miałby pilnować. Sufit broniłby budżetu mniejszego niż koszt jego utrzymania, a granice wobec NIEZAUFANEGO wejścia (`_MAX_OBIEGOW`, `_MAX_NARZEDZI_NA_TURE`, sufity znaków) już istnieją i mają inne uzasadnienie niż budżet. Decyzja należy do klienta — to ta sama procedura co przy D1: zmierzyć, zapisać wynik, dopiero potem kasować albo podłączać. |
| **E4** | Miara jakości interpretacji: **odsetek `unclear` per osoba** (§10.4). Stąd wymóg, żeby powody niejasności były zamkniętym enumem importowanym PO NAZWIE, a nie pozycją. | **`zrealizowane`** 2026-09-09 — `PendingReminder.interpretacje`/`niejasnosci` (para, bo plan mówi o ODSETKU, a liczba bez mianownika myli), podbijane przez `_commit` i cofane razem z nim; szybka ścieżka „tak" nie liczy się, bo modelu nie woła. Widoczne w `--stan` (kolumna `niejasne` jako `2/9`) i w podsumowaniu tygodniowym. |

---

## Sekcje planu cytowane w kodzie

| sekcja | o czym | gdzie cytowana |
|---|---|---|
| **§4.2** | Rozstrzygnięcia klienta — wartości, które wybiera klient, nie inżynier. `§4.2/1` to przesunięcie terminu odpowiedzi (B1). | `config.py` |
| **§7** | Pilotaż i pomiar kosztu modelu: co i jak liczyć z logu (C2, E1), łącznie z poleceniem zliczającym przebiegi po wzorcu „tokeny wejścia=". | `agent/anthropic_llm.py` |
| **§10.3** | Prompt systemowy interpretera — dokumenty odsyłają do niego pod nazwą `_SYSTEM`. | `agent/interpreter.py` |
| **§10.4** | Miary jakości interpretacji (E4). | `agent/interpreter.py`, `agent/schema.py` |
| **§11** | Kontrakt zgodności wprzód i wstecz (N34): nieznany status przeżywa cofnięcie obrazu. | `runtime/service.py` |

## Wynik pomiaru D1 (2026-09-07)

Pełna tabela, dla kogoś, kto będzie chciał ten pomiar powtórzyć albo zakwestionować:

| osoba | pewność | podstawa | inna niż „zeszły tydzień" | tygodni z danymi |
|---|---|---|---|---|
| os1 | niska | ostatni_tydzien | nie | 8/8 |
| os2 | niska | ostatni_tydzien | nie | 8/8 |
| os3 | **srednia** | bywalo_roznie | **TAK** | 8/8 |
| os4 | niska | ostatni_tydzien | nie | 8/8 |
| os6 | niska | ostatni_tydzien | nie | 8/8 |
| os7 | niska | ostatni_tydzien | nie | 8/8 |
| os8 | **srednia** | bywalo_roznie | **TAK** | 8/8 |
| os9 | niska | ostatni_tydzien | nie | 7/8 |

Dane są dobre — osiem tygodni historii dla siedmiu osób i siedem dla ósmej, więc wynik nie bierze
się z braku materiału. Bierze się z tego, że **w tym zespole nikt nie chodzi w rytmie przemiennym**,
czyli dokładnie w tym, dla którego D1 powstało. Sześć osób ma rytm stabilny na tyle, że wzorzec sam
schodzi do „jak w zeszłym tygodniu"; dwie mają rozrzut, przy którym moduł hedguje („bywało różnie")
zamiast twierdzić — zgodnie z własną zasadą „lepiej częściej pytać, niż częściej zgadywać".

**Czego pomiar NIE rozstrzyga.** Nie mówi, czy propozycja tych dwóch osób byłaby lepsza od
dzisiejszej — mówi tylko, że byłaby inna i opatrzona słabszym zdaniem. Kryterium celowo pytało
o pewność WYSOKĄ, bo tylko przy niej podłączenie ma dawać wartość, a nie samą zmianę.

**Skrypt pomiarowy został skasowany razem z modułem** — mierzył wyłącznie jego i bez niego nie ma
przedmiotu. Przy zmianie składu zespołu (praca zmianowa) obie rzeczy wskrzesza się z historii
gita: `git show b3188e9~1` po ścieżkach reminders/wzorzec.py i scripts/zbierz_historie.py, razem
z testami bucketowania historii. Dopiero praca zmianowa czyni tę pozycję opłacalną.

## Pozycje otwarte, nieprzypisane do litery

- **Nocka wchodząca w dzień oznaczony jako wolny nie jest uzgadniana.** `interpreter._zdecyduj`
  rozstrzyga rozłączność praca/wolne po dniu STARTU, więc „piątek 22:00–06:00" plus „sobota urlop"
  zapisują się oba, a sobota 00:00–06:00 jest pokryta dwoma sprzecznymi wpisami. To rozstrzygnięcie
  klienta (§4.2), nie usterka: żaden kierunek nie jest oczywiście poprawny.
- ~~**Wersja mieszka w pięciu miejscach i nie ma strażnika.**~~ **Zamknięte 2026-09-07 przy
  wydaniu 0.2.21.** Wszystkie pięć miejsc wyrównane, zgodności pilnuje `tests/test_wersje.py`
  (iteruje po miejscach deklaracji, więc szóste trzeba dopisać świadomie). Docstring `__init__.py`
  przestał twierdzić, że robi to `[tool.hatch.version]` i nieistniejący skrypt check_versions.

## Pomiar kosztu modelu (2026-09-09, podstawa decyzji o E1)

> **Ten rozdział zastąpił OSZACOWANIE z 2026-09-08.** Tamto mówiło „~$0,25–0,60 tygodniowo"
> i było **zawyżone 5–10×**, bo zakładało 30–90 wywołań tygodniowo (2–4 tury na wiadomość).
> Pomiar na żywym modelu pokazał **1,00 tury na wiadomość**. Zostawiam ten akapit zamiast po cichu
> podmienić liczby: pozycja E1 mówi wprost „sufit oparty na POMIARZE, nie na przypuszczeniu",
> a różnica między jednym a drugim jest tu właśnie rzędem wielkości.

**Sonda:** 10 syntetycznych wiadomości pracownika (żadnych danych z produkcji) przez
`interpret_reply` na `claude-haiku-4-5`, liczone z linii logu C2 (`tokeny wejścia=…`), czyli tym
samym mechanizmem, który plan przewidział do tego celu.

| miara | zmierzone |
|---|---|
| wejście na TURĘ | **5 081 tokenów** (odchylenie 5 072–5 101) |
| wyjście na turę | **176 tokenów** |
| tur na wiadomość | **1,00** — model zbiegał do decyzji bez pętli narzędziowej |
| **koszt jednej wiadomości** | **$0,0060** ($1/$5 za MTok) |
| cache promptu | **wyłączony**, potwierdzone (`cache-zapis=0, cache-odczyt=0`) |

**Stały prefiks to ~98 % każdego żądania** (system + narzędzia + schemat = 13 691 znaków wobec
14 063 znaków całej tury). Wiadomość pracownika jest przy nim szumem — i to jest najważniejsza
własność tego rozkładu, bo mówi, gdzie leżałaby jakakolwiek oszczędność, gdyby kiedyś była
potrzebna.

**Koszt tygodniowy.** Kosztują wyłącznie wiadomości pracowników; przy 8 osobach, z których
odpisuje 2–3, to ~5–10 wiadomości tygodniowo → **$0,03–0,06 na tydzień, czyli ~$2–3 rocznie.**

**Zweryfikowany mechanizm, ważniejszy od samej liczby:** koszt jest proporcjonalny do LICZBY
WIADOMOŚCI, nie do czasu ani do częstotliwości odpytywania. Sprawdzone wykonaniem: 20 obiegów
nasłuchu bez nowej wiadomości → **0 wywołań modelu**; 1 wiadomość + 19 obiegów ciszy → **1
wywołanie**. Usługa nie ma jak przepalać pieniędzy stojąc, więc nie istnieje scenariusz
„rozbiegowy", którego sufit miałby pilnować.

**Czego pomiar NIE obejmuje.** Żadna z dziesięciu wiadomości nie uruchomiła pętli narzędziowej,
więc koszt tury 2–4 pozostaje niezmierzony. Ogranicza go KOD (`_MAX_OBIEGOW=4`), więc najgorszy
przypadek jednej wiadomości to ~4 × 5 081 ≈ 20 tys. tokenów ≈ **$0,024** — nadal poniżej grosza
za trzy wiadomości.

**Kiedy to przeliczyć od nowa:** przy zmianie modelu, przy wzroście kręgu odbiorców o rząd
wielkości albo gdyby pętla narzędziowa zaczęła się realnie uruchamiać. Polecenie zliczające
z logu stoi w §7; od 0.2.23 liczbę wywołań widać też wprost w podsumowaniu tygodniowym
(`interpretacje`, pozycja E4), bo ten licznik przeżywa domknięcie tematu.

## Czego w tym pliku NIE ma

Dokumenty architektura, dane-osobowe, runbook i wdrozenie (wszystkie `docs/*.md`) zaginęły
tak samo jak ten plan, ale **nie są odtwarzane**: ich cytaty niosą samą nazwę albo numer sekcji,
bez treści, więc rekonstrukcja byłaby zgadywaniem. Odsyłacze do nich zostały przekierowane na
dokumenty, które istnieją, albo skreślone z zachowaniem samej reguły (2026-09-07). Jeśli ktoś
pamięta ich zawartość — to jest miejsce, w którym warto ją zapisać, zanim wygaśnie.
