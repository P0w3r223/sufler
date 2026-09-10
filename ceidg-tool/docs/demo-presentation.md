# Demo presentation — a 25-minute script for an audience

Date: 2026-09-10
Status: accepted
Author: P0w3r223
Related to: docs/demo-walkthrough.md (the operator's technical walkthrough), ADR-0014 (demo mode),
            docs/research/public-search-parity.md (what the public form can ask and we cannot)

This document is in English by project convention. **Every sentence meant to be spoken is Polish**,
because the audience and the tool are — spoken lines are quoted, so they can be read aloud as-is.

`demo-walkthrough.md` answers "how do I run the demo". This one answers "what do I say, in what
order, and what do I put on screen while saying it".

---

## Shape of the session

| Act | Minutes | On screen | Point being made |
|---|---|---|---|
| 1. What this is | 3 | nothing | The problem, in one sentence |
| 2. Where the data comes from | 3 | the public CEIDG search page | We use the API, not the page |
| 3. Ask in a sentence | 5 | terminal, `--demo --opis` | The operator needs no API knowledge |
| 4. Where the result lands | 4 | Excel | A workbook, and what each sheet is |
| 5. The options | 5 | terminal `--help` and the menu | One sentence per command |
| 6. *(optional)* one real query | 5 | terminal + the government page | The comparison that only production can make |
| 7. Close | 2 | nothing | What it does, what it does not |

Without act 6: **22 minutes**. Leave ten for questions; they always come.

## Before you walk in

- `PYTHONUTF8=1` in the environment, or the Polish characters break on this console.
- `set CEIDG_DEMO_TEMPO=8` — the demo sleeps eight times faster. The cost table still prints the
  true minutes; only the waiting is compressed. A full fetch with details takes ~19 s at tempo 8
  and ~69 s at tempo 2.
- Decide **before** the session whether act 6 happens. It needs the owner's consent, a live token
  and a network — see "Why the demo and not production" below.
- If you will show the assistant (act 3), `ANTHROPIC_API_KEY` must be set, and that call really
  leaves the machine and really costs money (about 1-9 gr per question). Without the key the tool
  falls back to eight ordinary questions and the demo still works — say so instead of apologising.
- Run `ceidg-tool wyczysc --wszystko --tak --potwierdzam-usuniecie` on the **demo** directory if you
  want a clean slate; a leftover interrupted run changes the wizard's menu (resume appears first).

---

## Act 1 — what this is (3 min, no screen)

> „CEIDG to publiczny rejestr jednoosobowych działalności gospodarczych. Ponad sześć milionów
> wpisów."
>
> „Państwo udostępnia go na dwa sposoby. Strona internetowa — dla człowieka, jeden wpis naraz.
> I API — dla programów, hurtowo."
>
> „To narzędzie używa API. Zadajesz pytanie, dostajesz plik Excela z wszystkimi firmami, które
> pasują."
>
> „Zaprojektowane dla kogoś, kto nie wie, co to API. Nie trzeba znać żadnego kodu."

Do not open a terminal yet. Three sentences and a pause work better than a screen nobody reads.

## Act 2 — where the data comes from (3 min)

Open `https://aplikacja.ceidg.gov.pl/ceidg/ceidg.public.ui/search.aspx`. Type a surname. Show the
result list.

> „To jest oficjalna wyszukiwarka. Działa dobrze, gdy szukasz jednej firmy."
>
> „Pokazuje dwadzieścia wyników na stronę. Nie da się z niej wyeksportować listy."
>
> „My pytamy to samo państwo, tylko drugimi drzwiami: API v3 Hurtowni Danych CEIDG."
>
> „To jest oficjalny interfejs, z tokenem, z limitami. Nie kopiujemy strony — strona ma CAPTCHA
> i nie jest do tego przeznaczona."

If someone asks whether this is legal: the register is public, the API is the state's own
integration channel, access needs a token issued through Profil Zaufany, and the provider logs
every request for 36 months. Say exactly that.

**The honest half**, and it belongs here rather than in the questions:

> „API pozwala pytać o mniej rzeczy niż formularz na stronie. Nie ma numeru KRS, nie ma PESEL-u,
> nie ma dat zawieszenia. Za to pozwala pytać hurtowo i wyeksportować wynik."

## Act 3 — ask in a sentence (5 min)

```
set CEIDG_DEMO_TEMPO=8
ceidg-tool pobierz --demo --opis "firmy Marka Nowaka"
```

Say the demo line **before** the first screen appears, not after:

> „Uruchamiam tryb pokazu. Dane są wymyślone — dwieście czterdzieści firm, które nie istnieją.
> Żadne zapytanie nie wychodzi do rejestru."

Then walk the screens in the order they appear:

1. **First screen.** Point at `dokąd wysyłam rekordy` and `środowisko`.
   > „Program zaczyna od powiedzenia, dokąd wysyła dane i w jakim trybie pracuje. Zawsze."
2. **Interpretation.** The model turned the sentence into filters, and the screen shows them.
   > „Zdanie zamieniło się w filtry: imię Marek, nazwisko Nowak. Program pokazuje, co zrozumiał,
   > **zanim** cokolwiek pobierze. Jak źle zrozumiał — poprawiasz."
3. **Cost table.** Two rows: list, and list with details.
   > „Teraz mówi, ile to potrwa i ile zapytań kosztuje. Nic się jeszcze nie dzieje. To ty decydujesz."
4. Choose **details**, let it run, and let the progress bar be seen.

In the demo corpus this returns **5 firms named Marek Nowak** (verified 2026-09-10), one in each
of the five towns — Poznań, Gniezno, Kalisz, Białystok, Łomża — all `AKTYWNY`, and **four of the
five carry a PKD code from the 2007 vintage**. Small enough to read on screen, which is the point,
and it sets up the next query without any staging.

**If there is no assistant key**, press Enter at the description prompt and answer the eight
questions instead. The tool is identical from the cost table onwards; nothing in the demo depends
on the model.

**A second query worth showing**, because it exposes the trap nobody expects:

```
ceidg-tool pobierz --demo -w wielkopolskie --pkd 9621Z --tak
```

> „Pytam o fryzjerów kodem PKD z klasyfikacji dwa tysiące dwudziestego piątego roku."
>
> „Program mówi: część wpisów ma jeszcze stary kod z dwa tysiące siódmego. Pyta, czy dołożyć."
>
> „Rejestr jest w połowie przeprowadzki. Prawie sześćdziesiąt procent wpisów ma stary rocznik.
> Kto o tym nie wie, dostaje połowę wyniku i nie ma o tym pojęcia."

That screen is the strongest thirty seconds in the whole demo. It is a property of the register,
measured — 58.6 % of a 287 256-row archive — not a feature anyone invented.

## Act 4 — where the result lands (4 min)

Open the workbook the run just wrote (`DEMO_ceidg_*.xlsx` — the prefix is deliberate).

> „Wynik to zwykły plik Excela. Otwiera się wszędzie."

Walk the sheets, one sentence each:

- **Firmy** — one row per business: name, NIP, REGON, address, status, start date, contact,
  and `link_ceidg`.
  > „Ostatnia kolumna to odnośnik do wpisu na rządowej stronie. Klikasz i sprawdzasz u źródła."
- **PKD** — every code of every firm, one row per code.
- **Spolki** — the civil partnerships a firm belongs to, when it belongs to any.
- **Slownik** — what each column means. > „Żeby nikt nie musiał zgadywać, co znaczy nagłówek."
- **Metadane** — criteria, purpose, environment, times, counts, tool version.
  > „Ten arkusz mówi, **skąd wziął się ten plik**. Kryteria, data, tryb. Za pół roku nikt nie
  > będzie pamiętał, a plik będzie."

Two things to point at in `Metadane`, because they are the ones people ask about later:

- the `zrodlo` row — API or the daily report;
- the `kolumny_ukryte` row when it appears — columns the source could not fill, with the reason.

**Hidden columns are worth ten seconds:** unhide one in Excel and show it is empty, not missing.

> „Kolumny nie znikają. Są puste i program mówi dlaczego."

## Act 5 — the options (5 min)

Show the wizard menu — `ceidg-tool kreator --demo` — and read it out. Six positions, and the first
one only appears when there is something to resume:

| Menu position | One sentence |
|---|---|
| Wznowić przerwane pobranie | „Prąd padł w połowie? Wraca tam, gdzie skończył. Nic nie pobiera drugi raz." |
| Pobrać firmy | „Główna droga: opisz zdaniem albo odpowiedz na osiem pytań." |
| Zaktualizować bazę o zmiany | „Pyta rejestr, co się zmieniło od ostatniego razu. Tanie — nie pobiera wszystkiego od nowa." |
| Pobrać gotowy raport | „Rejestr wystawia codzienny zrzut na województwo. Jeden plik zamiast tysięcy zapytań." |
| Sprawdzić firmę po NIP | „Jedna firma, dwa zapytania, karta na ekranie." |
| Wyjść | — |

Then `ceidg-tool --help` for the command line, and say only this:

> „To samo, co w kreatorze, da się wpisać jedną linią. Kreator zresztą tę linię wypisuje na końcu
> — kopiujesz ją do harmonogramu i masz to samo zapytanie co tydzień."

If someone asks about the criteria themselves, show `ceidg-tool pobierz --help` and group them out
loud rather than reading nineteen flags: **adres** (województwo, powiat, gmina, miejscowość, ulica,
numer, lokal, kod), **podmiot** (nazwa, imię, nazwisko, NIP, REGON, NIP i REGON spółki cywilnej),
**branża i stan** (PKD, status, daty rozpoczęcia).

> „Każdą flagę można powtórzyć. Dwa miasta znaczy: albo tu, albo tu."

## Act 6 — *optional* — the one comparison only production can make (5 min)

**This act uses real personal data.** It needs the owner's consent given in the session, a live
token, `--srodowisko prod --produkcja`, and a decision that the screen may be seen by the room.
Skip it if the session is recorded or streamed.

Pick a query small enough to verify by eye — a rare surname in one town, capped:

```
ceidg-tool pobierz -s prod --produkcja --nazwisko <nazwisko> -m <miasto> --maks 25
```

Then run the same search in the government page and put the two side by side.

> „To samo pytanie, dwie drogi. Strona pokazuje wpisy jeden pod drugim. Narzędzie zwraca plik."

Expect — and name in advance — three differences, so none of them looks like a defect:

1. **Counts can differ** when the criteria touch PKD, because of the 2007/2025 split above.
2. The page matches a **fragment** of the company name and the beginning of a street name; our
   `nazwa` and `miasto` are measured as fragment matches, and five other text fields are not
   measured at all, so they compare exactly (ADR-0018).
3. The page can ask about things the API has no parameter for: KRS, PESEL, short name, suspension
   dates, address type.

Say the last one plainly:

> „Nie jesteśmy kopią tej strony. Jesteśmy inną drogą do tego samego rejestru — hurtową."

## Act 7 — close (2 min)

> „Podsumowując. Jedno pytanie, jeden plik, zero klikania."
>
> „Program mówi, ile to kosztuje, zanim zacznie. Nic nie rusza bez twojej zgody."
>
> „Wszystko, co pokazuje, da się sprawdzić u źródła — ostatnia kolumna to odnośnik."
>
> „Czego nie zrobi: nie pokaże spółek z KRS, nie poda PESEL-u, nie policzy przychodów. To rejestr
> jednoosobowych działalności, nie wywiadownia."

---

## Questions that actually get asked

**„Skąd wiadomo, że dane są aktualne?"**
> „Z rejestru, w chwili pobrania. Arkusz Metadane zapisuje datę i godzinę. Aktualizacja pyta tylko
> o to, co się zmieniło."

**„Ile to kosztuje?"**
> „Dostęp do API jest bezpłatny, wymaga tokenu z Profilu Zaufanego. Limity: pięćdziesiąt zapytań
> na trzy minuty, tysiąc na godzinę. Płaci się tylko za asystenta, jeśli się go używa — grosze
> za pytanie."

**„Czy to jest legalne?"**
> „Rejestr jest jawny, a API jest oficjalnym kanałem państwa. Token wydaje się przez Profil
> Zaufany, a dostawca przechowuje historię zapytań przez trzydzieści sześć miesięcy."

**„Dlaczego nie po prostu skopiować strony?"**
> „Strona ma CAPTCHA i nie jest przeznaczona dla programów. API jest — i nie psuje się przy
> każdej zmianie wyglądu strony."

**„Czy dane osobowe są bezpieczne?"**
> „Pobrane dane zostają na tym komputerze. Nic nie idzie do chmury. Token nie trafia do logów ani
> do plików. Narzędzie odmawia połączenia przez firmowe proxy, żeby nikt po drodze nie zobaczył
> tokenu."

**„Czemu pokaz na wymyślonych danych?"** — see the next section; answer it with the first two
sentences from there.

---

## Why the demo and not production

The question comes up every time, and the honest answer has two halves.

**Production would look more convincing in exactly one place** — act 6, where a real count sits
next to the government page. That is why act 6 exists as an option.

**Everywhere else the demo is the better instrument, for four reasons that are not about comfort:**

1. **Every production record is a real person.** Name, address, phone. Putting that on a projector
   or in a recording is disclosing personal data to a room that has no reason to see it. The demo
   corpus invents 240 sole traders and NIPs with the unassigned `999` prefix, so they cannot
   collide with anyone real.
2. **A demo has to be repeatable.** The corpus is pinned by a fingerprint in the test suite: the
   same 240 firms today and next month, in the same towns, with the same statuses. Production
   changes daily, and a rehearsed number that has moved by showtime undermines everything said
   after it.
3. **Production is slow by design.** 50 requests per 3 minutes, ~3.75 s between requests. A fetch
   worth showing takes minutes of visible waiting; the demo compresses the sleeping without
   faking any of the code paths.
4. **No network, no token, no risk of the wrong environment.** Nothing to fail on a conference
   wifi, and no chance of a mistyped flag reaching the live register in front of an audience.

What the demo does **not** fake, and it is worth saying out loud once: the whole decision sequence,
the cost arithmetic, paging, the rate limiter, the workbook, resume after a kill. The only invented
things are the records themselves and the scale.

## Are the demo and production "in sync"?

**They are not two deployments, so there is nothing to synchronise.** `--demo` is a flag on the same
program. Same code, same decision sequence, same exporter, same messages. The flag swaps one thing:
where the answers come from — a synthetic register in the process instead of an HTTPS call — plus
four markers that keep a demo workbook from being mistaken for a real one (first screen, `Metadane`
row, `DEMO_` filename prefix, separate data directory).

So: **every change made "in the demo" is a change in the program**, and it works identically against
production. The flags added on 2026-09-10 (`--imie`, `--nazwisko`, `--ulica`, `--kod`, `--budynek`,
`--lokal`, `--nip-sc`, `--regon-sc`) send exactly the same parameters to the real API; the report
path's new refusal applies to the real daily archive; the command the wizard prints works in both.

The genuine differences are these, and they are properties of the double, not of the code:

| | Demo | Production |
|---|---|---|
| Records | 240 invented | 6 316 121 (measured at `limit=1`) |
| `/raporty` | not served by the double — `--zrodlo auto` falls back to the API | the real daily archives, ~816 of them |
| Personal data | none | all of it |
| Token | placeholder; `.env` is not read | real JWT, its payload carries a PESEL |
| Waiting | compressed by `CEIDG_DEMO_TEMPO` | real, ~3.75 s per request |
| Assistant | real call, real cost, if the key is set | same |

The one gap that matters for a demo is the report path: **the daily-report branch cannot be shown
offline**. If the audience needs to see it, it belongs in act 6, on production, as a `raporty`
listing (one request, no personal data on screen until something is downloaded).

## What not to promise

- Do not say "nic nie wychodzi z tego komputera" while `--opis` is on screen — the assistant call
  does leave, to `api.anthropic.com`.
- Do not read the demo's minutes as the register's minutes. The corpus is 240 records.
- Do not promise the tool answers everything the government page asks. It does not, by three
  categories, and act 2 already said so.
- Do not show a workbook from production and a workbook from the demo in the same breath without
  naming which is which. The `DEMO_` prefix and the `Metadane` row exist for exactly that moment.
