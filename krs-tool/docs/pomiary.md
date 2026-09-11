# Measurements, and the assumptions still standing in their place

Date: 2026-09-11
Status: living
Author: P0w3r223
Related to: `niezmierzone.md`, `adr/0001_zakres_etapu_1_i_granica_offline.md` decision 8

---

zmierzonych-wlasnosci: 0

## What this file is for

`niezmierzone.md` lists what this project cannot claim about the **wire**. This file is about
something narrower and more dangerous: the assumptions about **the shape of an extract** that are
already encoded in `krs_tool/odpis/czytanie.py` and are therefore load-bearing.

Step 2 was built on synthetic extracts, at the owner's instruction, with real ones to follow. That
is a legitimate way to build a reader and an illegitimate way to learn what a register returns. The
count above is the number of properties confirmed against a real file supplied by the operator, and
it is currently **zero**. A test keeps this number equal to the number of entries in
`tests/fixtures/odpis_traits.yaml`, so it cannot drift by being forgotten.

## Assumptions encoded in the reader, awaiting confirmation

Each of these will either be confirmed or refuted by the first real extract. None of them may be
cited as a fact until then.

| # | Assumption | Where it lives | What happens if wrong |
|---|---|---|---|
| 1 | The payload is a JSON object with a top-level `odpis` key | `czytanie.wczytaj_odpis` | Reader raises `NieznanyKsztaltOdpisuError` naming the path — loud, not silent |
| 2 | `odpis.naglowekA` carries `stanZDnia` in `DD.MM.RRRR` | `czytanie.wczytaj_odpis` | Same; and every observation date in the tool depends on it |
| 3 | The extract carries its own `numerKRS` in `naglowekA` | `czytanie.wczytaj_odpis` | Already handled: the caller may supply the number, and the reader says so in the error |
| 4 | `naglowekA.rejestr` distinguishes the register the entry sits in | model field `rejestr` | The "outside the entrepreneurs register" premise loses its source |
| 5 | Company data sit at `dane.dzial1.danePodmiotu` with `nazwa`, `formaPrawna`, `identyfikatory.{nip,regon}` | `czytanie.wczytaj_odpis` | Reader raises naming the path |
| 6 | Filed-document entries sit at `dane.dzial3.wzmiankiOZlozonychDokumentach`, one array per document type, each element `{dataZlozenia, zaOkresOdDo}` | `czytanie._wzmianki` | The whole signal layer loses its input |
| 7 | The financial-year end sits at `dane.dzial3.informacjaODniuKonczacymRokObrotowy` | `czytanie.wczytaj_odpis` | Every statutory term loses the date it is counted from |
| 8 | Divisions are keyed `dzial1`..`dzial6`, and an empty division is present-but-empty rather than absent | `czytanie._dzialy` | The binary risk flag on division 4 becomes ambiguous — the model already distinguishes three states to survive this |
| 9 | Only two period spellings occur | `czytanie.czytaj_okres` | Nothing breaks: an unknown spelling is reported as unreadable, with its raw text, never guessed |
| 10 | `naglowekA.rejestr` carries `P` for the entrepreneurs register | `odpis/model.py`, used by `signals/ocena.py` | The "outside the entrepreneurs register" premise resolves wrongly. The direction is safe: an unrecognised value **excludes** the missing-statement rule rather than firing it, and an empty one leaves the premise unresolved |

Assumption 9 is the only one **observed** rather than invented — the reconnaissance saw both
spellings on two independent samples within one company. It is nonetheless listed here, because a
third spelling is not ruled out and the reader is built to survive one.

## How a row leaves this table

The operator supplies a real extract. The property is checked against it, an entry with the file
name, SHA-256, date and supplier goes into `tests/fixtures/odpis_traits.yaml`, the count at the top
of this file goes up by one, and the row moves from "assumption" to "measured". The anonymised copy
of that file may then be committed; the original never is.
