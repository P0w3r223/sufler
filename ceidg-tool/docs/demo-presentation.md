# Demo presentation — a 30-minute script, on the live register

Date: 2026-09-10
Status: accepted
Author: P0w3r223
Related to: docs/demo-walkthrough.md (the offline fallback), docs/research/public-search-parity.md,
            docs/decisions.md (every count below is measured, not recalled)

This document is in English by project convention. **Every sentence meant to be spoken is Polish**,
because the audience and the tool are — spoken lines are quoted, so they can be read aloud as-is.

**The show runs against production.** The owner chose this on 2026-09-10: the point of the session
is that the numbers are real and verifiable against the government page while the room watches, and
that only production can do. The offline demo is now the **fallback** — see the last section.

---

## Shape of the session

| Act | At | Length | Requests | On screen |
|---|---|---|---|---|
| 1. What this is | 00:00 | 3 min | 0 | nothing |
| 2. Two doors | 03:00 | 3 min | 0 | the government search page |
| 3. Our own company | 06:00 | 4 min | 2 | terminal, then the register entry |
| 4. A real query | 10:00 | 6 min | ~6 | terminal, then the page side by side |
| 5. The trap | 16:00 | 5 min | 2 | terminal |
| 6. What lands on disk | 21:00 | 4 min | 0 | Excel |
| 7. Scale | 25:00 | 3 min | 1 | terminal |
| 8. Close | 28:00 | 2 min | 0 | nothing |

Thirty minutes end to end, about **11 requests**. Leave ten minutes for questions.

## Audience and posture

The room is the board. That decides three things throughout:

- **Value and risk lead, mechanics follow.** Every technical fact gets one sentence, not three.
- **Excel gets more time than the terminal.** The workbook is the thing they will actually receive.
- **Limits are said out loud, not discovered.** A board that hears what the tool cannot do trusts
  what it says it can.

## The numbers this script is built on

Measured on production 2026-09-10, 21 requests, counts only — no records were fetched to disk.
**Re-measure before the show**, because the register moves daily; the rehearsal command is below.

| Query | Hits |
|---|---|
| `6210B` + Poznań — programming, today's code | **3 157** |
| `6201Z` + Poznań — the same trade, 2007 code | **7 101** |
| both codes together | **10 258** |
| `6210B` + Poznań + `AKTYWNY` | 2 532 |
| `6220A` + Poznań — cybersecurity | 574 |
| `6210A` + Poznań — game development | 472 |
| `nazwa` contains "software" + Poznań | 205 |
| `nazwisko=Nowak` + `6210B` + Poznań | **23** |
| `nazwisko=Kowalski` + `6210B` + Poznań | 3 |
| `6210A` + Gniezno | 10 |
| `6220A` + Gniezno | 21 |
| `6210B` — whole country | 142 915 |
| `6201Z` — whole country | 234 090 |

**The number that carries the show is the first three rows.** Asking with today's code returns
**31 %** of the trade in Poznań. The rest is still filed under the 2007 code — and this is the
board's own industry, not a curiosity from someone else's.

## Request budget

The whole script spends **about 12 requests**. The ceiling is 50 per 3 minutes and 1 000 per hour,
and the tool paces itself at ~3.75 s between requests, so the arithmetic is comfortable — but two
rules keep it that way:

- **Do not rehearse in the ten minutes before the show.** A rehearsal is another dozen requests into
  the same 3-minute window if you cut it fine. Rehearse the day before, or at least an hour ahead.
- **Do not repeat a fetch "because it looked wrong".** Read the cost table instead; it is on screen
  precisely so nothing has to be re-run to be understood.

## Before you walk in

- `PYTHONUTF8=1` set, terminal font at 18 pt or more, window maximised.
- `ceidg-tool sprawdz-token` — **zero requests**, shows the environment, the source and the expiry.
  Do it now, not on stage. The token expires; the screen says when.
- **Never put `.env` on screen.** The token's payload carries a PESEL. The tool masks it everywhere;
  a text editor does not.
- Government search open in a second tab: `https://aplikacja.ceidg.gov.pl/ceidg/ceidg.public.ui/search.aspx`
- Excel closed — an open workbook from the rehearsal blocks the write.
- Decide the NIP for act 3. **Use your own company**; it is the one record in the room whose
  personal data nobody has to think twice about.
- If the session is recorded or streamed: read "When the room is recording" at the end **before**
  deciding to go ahead.

**Rehearsal command** — one request, no fetch, no file. It shows the cost table and stops:

```
ceidg-tool pobierz -s prod --produkcja --pkd 6210B -m Poznań
```

Answer **wyjdź** at the question. Write the two numbers it shows into the table above.

---

## Act 1 — what this is (3 min, no screen)

> „CEIDG to publiczny rejestr jednoosobowych działalności. Ponad sześć milionów wpisów."
>
> „W samym Poznaniu jest ponad dziesięć tysięcy firm z naszej branży. To jest nasz rynek —
> i on jest opisany w jawnym rejestrze państwowym."
>
> „Państwo udostępnia go dwiema drogami. Strona — dla człowieka, jeden wpis naraz.
> API — dla programów, hurtowo."
>
> „To narzędzie chodzi drogą hurtową. Zadajesz pytanie, dostajesz plik Excela."

No terminal yet. The 10 258 is the hook: it is their market, counted this week.

## Act 2 — the two doors (3 min)

On screen: the government search page. Search for something small — surname **Nowak**, PKD
**6210B**, town **Poznań** — and let the result list load.

> „To jest oficjalna wyszukiwarka. Świetna, gdy szukasz jednej firmy."
>
> „Dwadzieścia wyników na stronę. Eksportu nie ma — kto potrzebuje listy, przepisuje ją z ekranu."
>
> „My pytamy to samo państwo, tylko drugimi drzwiami: oficjalnym API Hurtowni Danych CEIDG."
>
> „Token wydaje się przez Profil Zaufany, a każde zapytanie jest po stronie państwa
> rejestrowane przez trzydzieści sześć miesięcy. To nie jest obchodzenie systemu — to jest system."

Leave that search on screen. **Act 4 comes back to it and compares the counts.**

## Act 3 — one company, verified in ten seconds (4 min)

```
ceidg-tool sprawdz-nip -s prod --produkcja NIP-WŁASNEJ-FIRMY
```

*(2 requests)*

> „Zaczynam od nas samych, żeby było widać, że to prawdziwe dane."
>
> „Numer sprawdza się lokalnie, zanim cokolwiek poleci — literówka nie kosztuje ani jednego zapytania."

When the card appears, read two or three fields aloud and point at the last row:

> „Ostatnia linia to odnośnik do wpisu na stronie państwa. Klikam — i to jest ten sam wpis."

Click it. This is the cheapest trust you will buy all session.

## Act 4 — a real query, checked against the page (6 min)

```
ceidg-tool pobierz -s prod --produkcja --nazwisko Nowak --pkd 6210B -m Poznań --szczegoly
```

*(about 6 requests: 1 for the count, 1 page of results, 4-5 for the details)*

> „Pytam o firmy programistyczne w Poznaniu, prowadzone przez osoby o nazwisku Nowak."

1. **Cost table.** Point at it before anything downloads.
   > „Znalazł dwadzieścia trzy. Mówi, ile zapytań i ile czasu to zajmie. Nic jeszcze nie pobrał —
   > decyzja jest moja."
2. Choose **szczegóły**, let the progress run (~25 seconds — say what is happening).
   > „Rejestr wpuszcza pięćdziesiąt zapytań na trzy minuty, więc narzędzie samo trzyma tempo.
   > Nie da się go przyspieszyć i nie próbuje."
3. **Switch to the government tab** — the same three fields — and put the two counts side by side.
   > „Dwadzieścia trzy tam, dwadzieścia trzy tutaj. To samo źródło, dwie drogi."

If the counts differ by one or two, do not improvise — say this:

> „Rejestr zmienia się codziennie. To jest ta sama chwila z dokładnością do godzin, nie do sekund."

**Worth saying once, because it is the difference between a tool and a script:**

> „Nazwisko i imię rejestr dopasowuje dokładnie — sprawdziliśmy to pomiarem, nie założeniem.
> Wpisanie „Nowa" zamiast „Nowak" nie zwraca nic. Narzędzie o tym wie i nie udaje, że wie więcej."

## Act 5 — the trap, on our own trade (5 min)

```
ceidg-tool pobierz -s prod --produkcja --pkd 6210B -m Poznań
```

*(2 requests: one count per population — both spent before any consent)*

> „Pytam o naszą branżę dzisiejszym kodem PKD."

The tool stops and asks whether to add the old vintage. Both numbers are on screen.

> „Trzy tysiące sto pięćdziesiąt siedem firm ma kod dzisiejszy."
>
> „Siedem tysięcy sto — ten sam zawód, kod sprzed reformy."
>
> „Pytając dzisiejszym kodem, widzę **niecałą jedną trzecią** rynku. Reszta jest niewidoczna."
>
> „Nikt tego nie zgłasza jako błędu, bo wynik wygląda poprawnie. Po prostu jest o dwie trzecie za mały."

Answer **wyjdź** — the point is the screen, not another download.

> „Klasyfikacja zmieniła się w tym roku, a rejestr przechodzi na nią do końca dwa tysiące
> dwudziestego szóstego. Do tego czasu każde pytanie o branżę ma dwie odpowiedzi."

This is the strongest ninety seconds of the session. It is measured, it is theirs, and no competitor
demo will have it.

## Act 6 — what lands on the disk (4 min)

Open the workbook from act 4. Slow down here; this is the deliverable.

> „Wynik to zwykły plik Excela. Otwiera się wszędzie, nie trzeba nic instalować."

| Sheet | One sentence |
|---|---|
| **Firmy** | Jeden wiersz na firmę: nazwa, NIP, REGON, adres, status, data rozpoczęcia, kontakt. |
| **link_ceidg** | Ostatnia kolumna — odnośnik do wpisu w rejestrze. **Kliknij jeden przy sali.** |
| **PKD** | Wszystkie kody branżowe każdej firmy, po jednym w wierszu. |
| **Spolki** | Spółki cywilne, do których należy przedsiębiorca. |
| **Slownik** | Co znaczy każda kolumna — żeby nikt nie zgadywał z nagłówka. |
| **Metadane** | Kryteria, cel pobrania, środowisko, godziny, liczby, wersja narzędzia. |

> „Każdy wiersz da się sprawdzić u źródła."
>
> „A ten arkusz mówi, skąd wziął się ten plik: jakie pytanie, kiedy, w jakim trybie.
> Za pół roku nikt nie będzie pamiętał. Plik będzie."

Point at the `zrodlo` row, and — if it is there — at `kolumny_ukryte`:

> „Kolumny, których źródło nie umie wypełnić, są ukryte, nie usunięte. Program mówi, dlaczego."

## Act 7 — scale, and doing it without a person (3 min)

```
ceidg-tool raporty -s prod --produkcja
```

*(1 request)*

> „Państwo wystawia codzienny zrzut na województwo. Jeden plik zamiast tysięcy zapytań —
> narzędzie samo go proponuje, gdy pytanie pasuje."

Then the scale sentence, without running anything:

> „W całej Polsce firm z naszej branży jest sto czterdzieści dwa tysiące pod dzisiejszym kodem
> i dwieście trzydzieści cztery tysiące pod starym."
>
> „Takiego pobrania nie robi się przy publiczności — trwa godziny i narzędzie samo dzieli je
> na partie. Ale robi się je raz, w nocy, bez nikogo przy klawiaturze."

> „Na końcu każdego pytania program wypisuje gotową linię do skopiowania. Wklejasz ją
> w harmonogram i masz to samo zestawienie co tydzień."

## Act 8 — close (2 min)

> „Jedno pytanie, jeden plik, zero klikania."
>
> „Program mówi, ile to kosztuje, zanim zacznie. Nic nie rusza bez zgody."
>
> „Wszystko da się sprawdzić u źródła — ostatnia kolumna to odnośnik do rejestru."
>
> „Czego nie zrobi: nie pokaże spółek z KRS, nie poda PESEL-u, nie policzy przychodów.
> To rejestr jednoosobowych działalności, nie wywiadownia."

---

## Questions that actually get asked

**„Czy to jest legalne?"**
> „Rejestr jest jawny, a API to oficjalny kanał integracyjny państwa. Token wydaje się przez
> Profil Zaufany, a dostawca przechowuje historię zapytań przez trzydzieści sześć miesięcy."

**„Skąd wiadomo, że dane są aktualne?"**
> „Pochodzą z rejestru w chwili pobrania — arkusz Metadane zapisuje datę i godzinę. Aktualizacja
> pyta tylko o to, co się zmieniło od ostatniego razu, więc kosztuje ułamek pierwszego pobrania."

**„Ile to kosztuje?"**
> „Dostęp do API jest bezpłatny. Limity: pięćdziesiąt zapytań na trzy minuty, tysiąc na godzinę.
> Płatny jest tylko asystent językowy, jeśli go używamy — grosze za pytanie."

**„Co z danymi osobowymi i RODO?"**
> „Rejestr jest jawny z mocy ustawy, więc samo pobranie jest legalne. To, co z tymi danymi
> zrobimy dalej, podlega RODO tak samo jak każda inna baza kontaktów — i to jest decyzja
> biznesowa, nie techniczna."
>
> „Technicznie: dane zostają na tym komputerze, nic nie idzie do chmury, token nie trafia
> do logów ani do plików."

**„Czym to się różni od kupienia bazy od firmy zewnętrznej?"**
> „To jest źródło pierwotne, nie czyjaś kopia sprzed pół roku. Każdy wiersz ma odnośnik do wpisu
> w rejestrze, więc da się go sprawdzić pojedynczo."

**„Czy ktoś musi umieć programować?"**
> „Nie. Kreator prowadzi krok po kroku i pyta po polsku."

**„Czy możemy dostać całą Polskę?"**
> „Tak, ale to godziny pobierania i setki tysięcy wierszy. Narzędzie dzieli takie zadanie na
> partie i potrafi je wznowić po przerwaniu. To zadanie na noc, nie na spotkanie."

---

## When something goes wrong on stage

| Objaw | Co powiedzieć i zrobić |
|---|---|
| **Pobranie stoi, licznik czeka** | „Rejestr wpuszcza pięćdziesiąt zapytań na trzy minuty — narzędzie właśnie czeka, zamiast dostać blokadę." Zostaw, wróci samo. |
| **Błąd połączenia** | Najpewniej firmowe proxy. „Narzędzie odmawia przepuszczenia tokenu przez pośrednika, bo token niesie dane osobowe." Przełącz się na sieć z telefonu. |
| **401 / token wygasł** | Nie naprawiaj na scenie. Przejdź do aktu 6 na pliku z próby generalnej i wróć do tematu po spotkaniu. |
| **Wynik inny niż na stronie** | „Rejestr zmienia się codziennie" — i jeśli w grę wchodzi PKD, to jest akt 5, czyli temat, a nie usterka. |
| **Ktoś prosi o dane konkretnej osoby z sali** | Nie rób tego. „To jawny rejestr, ale nie będę wyświetlał czyichś danych na rzutniku." |

## When the room is recording

Everything on screen in acts 3-6 is real personal data of real sole traders. The register is public,
so showing it is lawful — but a recording turns a lawful screen into a distributed copy.

If the session is recorded or streamed, do one of these, in order of preference:

1. **Keep acts 1, 2, 5, 7 and 8** — the trap, the scale and the cost table carry the argument, and
   none of them needs a personal record on screen. Acts 3, 4 and 6 move to a private follow-up.
2. **Run those three acts offline** — `--demo` answers from a synthetic register with 240 invented
   sole traders. Everything except the data and the scale is real, and
   `docs/demo-walkthrough.md` describes that path in full. Say plainly that it is a demo corpus.
3. **Cancel the recording** for eight minutes.

## What not to promise

- Do not read a count as "the market". It is the register's answer to one filter today, and act 5
  is the reason to say that out loud.
- Do not promise the tool answers everything the government page asks. It does not — no KRS, no
  PESEL, no short name, no suspension dates.
- Do not say "nic nie wychodzi z tego komputera" if the assistant is on screen; that call goes to
  `api.anthropic.com` and costs money.
- Do not re-run a fetch to make a point. Every re-run spends the same quota the rest of the script
  is budgeted against.
