# Prompt: audyt architektury, kodu i testów

Wklej wszystko poniżej linii do **nowej** sesji Claude Code w `C:\Users\jdoe\BIAP\Ceidg`.
Plik jednorazowy — po przebiegu kasujesz.

---

Przeprowadź audyt architektury, kodu i testów tego projektu, z rozpoznaniem zewnętrznym.
Dziś **diagnoza**: żadnych zmian w kodzie i żadnej przebudowy. Decyzje moje.

## Najpierw przeczytaj

| Plik | Po co |
|---|---|
| `CLAUDE.md` | doktryna projektu i pułapki, które już kosztowały |
| `docs/status.md` | żywy plan. Sekcje żywe to nagłówek, „Open items" i ostatnie fazy — **~1 076 linii środka to retrospektywa**, nie spalaj na niej kontekstu |
| `docs/audit-2026-09-09.md` | audyt z 2026-09-08: sześć przebiegów, jedenaście mutacji, listy Tier A–D |
| `docs/design/phase2_core.md:86-166` | pełny tekst 14 reguł granic z uzasadnieniami |
| `.claude/sessions/` | rejestr defektów — sporo pomiarów tego projektu istnieje wyłącznie tutaj |

## Stan remediacji — nie zgaduj go z numeracji tierów

**Tier A: zamknięty w całości** (A1–A10). **Tier B: zamknięty poza B4** (przycięcie retrospektywy).
**Tier C i D: otwarte.** Zweryfikowane 2026-09-09:

| Poz. | Stan |
|---|---|
| C1 maskowanie sekretu nie-JWT | otwarte — mutacja M1 nadal byłaby zielona |
| C2 `ratelimit` nie widzi bazy | otwarte — słowo `ratelimit` nie pada w `test_boundaries.py` ani razu |
| C3 `approx` bez `abs=` | otwarte — brak skanu |
| C4 tekst z rejestru nie steruje terminalem | otwarte — `safetext.py:16` to `ord(ch) >= 32`, więc `\x7f`, `\x9b`, `\x85` przechodzą, a oracle w teście kopiuje ten sam warunek |
| C5–C8 | wg `docs/audit-2026-09-09.md`, nieweryfikowane od tamtej pory |
| D2 `datetime.now` poza `Clock` | otwarte — 13 wywołań w 5 plikach (było 12) |
| D5, D8 | otwarte |
| F12 pięć pól tekstowych | otwarte — 5 żądań produkcyjnych |

Te pozycje są **zmierzone i ponumerowane**. Nie odkrywaj ich drugi raz — sprawdź, czy nadal
stoją, i szukaj tego, czego tamten audyt nie objął.

**Powstało po audycie:** tryb demo (ADR-0014), ADR-0015…0017, ewaluacja asystenta
(`scripts/eval_asystenta.py`, `docs/eval-asystenta.md`), ścieżka raportowa domknięta na produkcji.
Sam asystent jest **starszy** niż audyt i audyt go dotknął — nowa jest ewaluacja.

## Sześć kształtów defektów tego projektu

Szukaj tych, nie generycznych code smells — generyczne już się tu nie zdarzają, te zdarzyły się
po kilka razy:

| # | Kształt | Pytanie kontrolne |
|---|---|---|
| 1 | gwarancja bez obserwatora | co by się wypisało, gdyby ją złamano? „nic" jest defektem |
| 2 | generator dowodów kasuje badaną własność | kto zbudował tę atrapę i co po drodze ujednolicił? |
| 3 | cichy podzbiór | czy operator dostaje mniej, niż prosił, i czy to widać? |
| 4 | wyjątek w środku reguły | reguła jest — czy jej wyjątek nie licencjonuje właśnie defektu? |
| 5 | dane referencyjne z pamięci | czy ta tablica pochodzi z generatora, czy z czyjejś głowy? |
| 6 | metryka mierzy tylko to, co nazwano | jakie pole ta metryka przepuszcza, bo nie ma go we wzorcu? |

## Zakres — łącznie z tym, co liczby wykluczają

13 046 linii w 41 modułach `ceidg_tool/`, 22 337 linii w 71 plikach `tests/` (69 z treścią, dwa puste `__init__.py`), 17 ADR-ów.
`docs/` to 7 713 linii; **cała proza projektu to ~9 911** i to jest inna metryka niż ta,
którą liczył poprzedni audyt (7 146 na mniejszym drzewie) — jeśli przeliczasz stosunek
proza:kod, powiedz, którą liczysz.

**Poza tymi liczbami, a w zakresie audytu** — cztery z sześciu kształtów mieszkają właśnie tam:

`scripts/anonymize_samples.py` (kształt 2) · `scripts/build_pkd*.py` (kształt 5) ·
`scripts/eval_asystenta.py` i `tests/eval/zapytania.yaml` (kształt 6) · `tests/fixtures/`
(atrapy, 1 871 linii) · `ceidg_tool/data/*.yaml` (2 552 linie danych referencyjnych) ·
8 sond + `scripts/probe_support.py` (niosą produkcyjny token i wspólny limiter).

## Podział pracy

Wzorzec: **równoległe rozpoznanie, synteza jednowątkowa**. Agenci diagnozują, Ty składasz.
Przekazuj im **cel i zakres, nie plan** — wyliczanka kroków zamienia specjalistę w wykonawcę.

**Zakresy mają się nakładać.** Sprzeczności między agentami są najcenniejszym produktem tego
audytu, a w poprzednim powstały wyłącznie tam, gdzie dwa przebiegi patrzyły na ten sam artefakt.
Agenci z rozłącznymi zakresami nie mają jak się nie zgodzić i oddadzą sumę zamiast syntezy.
Dlatego **atrapy i fixtures ogląda i tester, i code-reviewer** — z różnych pytań.

Jeden agent = **jedno pytanie**. Dwa „pełne audyty" na 35 tys. linii oddadzą raport wyglądający
na kompletny nad nieznanym podzbiorem — to kształt 3 zastosowany do samego audytu.

| Agent | Pytanie | Nakłada się z |
|---|---|---|
| `tester` | czy 22 337 linii testów **obserwuje** to, co deklaruje — czy przeszłyby przy usuniętej własności | code-reviewer na `tests/fixtures/`, `tests/support.py` |
| `tester` (drugi) | czy atrapy zgadzają się z rejestrem — `tests/fixtures/api_traits.yaml` jest punktem odniesienia | — |
| `code-reviewer` | poprawność i bezpieczeństwo tego, co powstało **po** audycie: demo, ADR-0015…0017, ewaluacja | testerem na atrapach |
| `code-reviewer` (drugi) | `scripts/` — generatory dowodów i danych referencyjnych, sondy z produkcyjnym tokenem | — |
| `researcher` ×2–4 | niżej | — |
| `architect` | **po fali 1**, z jej ustaleniami: czy warstwy, `Criteria` jako jedyny kontrakt i 14 reguł granic nadal odpowiadają temu, czym ten program się stał | wszystkimi |

**Kontrakt każdego spawnu — bez tego wielo­agentowość się sypie na koordynacji:**

- **Wejście:** cel, zakres jako lista ścieżek, i zdanie o tym, czego już nie trzeba sprawdzać.
- **Wyjście:** lista ustaleń, każde z plikiem i linią, ≤ 2 000 tokenów. Nie transkrypt.
  Każde ustalenie deklaruje **zakres faktycznie przejrzany** i regułę wyboru próbki.
- **Granica:** agent nie widzi pracy pozostałych. Nie pisze do plików. Nie wysyła żądań do CEIDG.

**Bez worktree.** `.env`, `.venv`, `probe_out/` i `PKD/` są w `.gitignore`, więc w worktree ich
nie ma i nie da się ani uruchomić bramek, ani sprawdzić twierdzeń na próbkach. To przeważa nad
domyślną regułą z `~/.claude/rules/agents.md`.

**Trzy zakazy dopisz dosłownie do każdego spawnu** — nie jadą z `CLAUDE.md`:
`CEIDG_DATA_DIR` na katalog roboczy przy każdym uruchomieniu narzędzia · `wyczysc` nie pada
ani razu · **nie cytuj zawartości `.env` ani `probe_out/`** w żadnym produkcie, bo produkt trafia
do repozytorium innego zespołu, a tam leży JWT z PESEL-em i 21 MB surowych danych osobowych.

## Rozpoznanie zewnętrzne

Zanim rozstawisz researcherów, pokaż mi perspektywy i zapytaj o zakres. `agents.md` domyślnie
robi **dwa przebiegi** dla ciężkich decyzji — rozstrzygnij ze mną, czy tu też, bo to największa
pojedyncza pozycja kosztowa.

Kierunki, uszeregowane wg tego, co uważam za nośne — zweryfikuj, nie przyjmuj:

1. **Wygasanie PKD 2007 z dniem 31.12.2026.** `pkdmap.py`, `Criteria.pkd_2007`, krok rocznikowy
   w `ui/flow.py`, flagi i `data/pkd2007_2025.yaml` mają twardą datę ważności za ~15 miesięcy.
   To jedyna część tej architektury z terminem. Czy ścieżka jej usunięcia jest zaprojektowana?
2. **Dostarczenie narzędzia.** Bramka 3 mówi „osoba bez wiedzy o API dochodzi do gotowego pliku",
   a instalacja to `pip install -e .[dev,asystent]` z repozytorium, którego ta osoba nie sklonuje.
   Jak inni pakują CLI nad publicznym rejestrem dla nietechnicznego odbiorcy?
3. **Egzekwowanie granic.** Nie „czy zamienić skan na `import-linter`/`tach`" — z 13 reguł tylko
   1–8 i 13 są regułami grafu importów. Reguły 9, 10 i 12 to analiza kształtu wywołań i to
   **one** mają udokumentowane złapania, a wymienne 1–6 i 8 nie mają ani jednego. Pytanie:
   które dałoby się przenieść, czy narzędzie łapie defekty **egzekwowania** (skan widział
   `from ..store import`, nie formę absolutną; widział `httpx`, nie `httpx2`), i ile kosztuje
   utrzymanie dwóch mechanizmów zamiast jednego.
4. **Ekonomia ścieżki raportowej vs API** — 287 tys. rekordów w jednym żądaniu wobec trzech
   godzin. Jedyne otwarte pytanie **produktowe** z poprzedniego audytu.

## Standard dowodu

Każde ustalenie niesie **plik i linię**, **co się stanie operatorowi**, i **dowód**: uruchomienie,
mutację albo cytat z pomiaru. Nie „wygląda na". Czego nie da się rozstrzygnąć bez żądania do
rejestru — napisz **ile żądań** kosztuje i zostaw mi decyzję.

Rozdziel **defekt** od **preferencji**. Większość nietypowych rozwiązań tutaj ma za sobą pomiar
zapisany w komentarzu albo ADR-ze; komentarz wyjaśniający strażnika jest tu zwykle historią
defektu, nie hałasem. Zanim zarekomendujesz usunięcie — znajdź powód, dla którego coś powstało.

## Co ma zostać

1. **`docs/audit-architecture-<data>.md`** — po angielsku, nagłówek wg
   `~/.claude/rules/knowledge-docs.md`. Metoda, ustalenia per obszar, **sprzeczności między
   agentami**, lista uszeregowana: najpierw utrata albo przekłamanie danych, potem to, co blokuje
   wdrożenie, potem reszta.
2. **ADR-y** w stanie `proposed` na decyzje architektoniczne, do mojej akceptacji.
3. **Rozpoznanie zewnętrzne** jako osobna sekcja ze źródłami: gdzie jesteśmy inni i czy różnica
   jest decyzją, czy długiem.

## Na koniec

Cztery bramki z `CLAUDE.md`. Oczekiwane: **1258 passed, 1 skipped** — jeden skip jest sygnałem
(atrapa demo nie obsługuje `/raporty`), więc „1258 passed" bez skipa znaczy, że ktoś go sprzątnął,
a nie że jest lepiej.

Zdanie o żądaniach do CEIDG postaw **strukturalnie**: żaden agent nie miał prawa ich wysłać,
a `--srodowisko prod --produkcja` nie padło w tej sesji. Nie odczytuj tego z `request_log` —
`store.py:31` trzyma wpisy **dwie godziny** i `trim_request_log()` sam je kasuje, więc półdniowy
audyt odczyta stamtąd zero, które nic nie znaczy.

**Jeśli zamierzasz coś w tym przebiegu naprawić — nie rób tego.** `CLAUDE.md` wymaga recenzji po
każdej zmianie zachowania, a recenzent biegnie w fali 1, czyli przed poprawką. Defekt jednoznaczny
i drobny zapisz na górze listy z gotowym opisem naprawy; zastosuję ją osobno, z recenzją.
