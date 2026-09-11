# Stage 1 core: module map and boundary rules

Date: 2026-09-10
Status: draft — rules 1, 2, 3, 6 and 7 have observers as of step 1; the rest gain theirs in steps 2-6
Author: P0w3r223
Related to: `../adr/0001_zakres_etapu_1_i_granica_offline.md`

---

## Module map

Flat layout, package at the root, like `ceidg-tool` — which is why the CI matrix entry names
directories instead of `.`.

```
krs_tool/
  cli.py            typer; authors no sentence of its own (rule 7)
  errors.py         taxonomy, no network members
  clock.py          injected clock; no sleep
  logsetup.py       file log with masking
  secrets.py        secret registry — empty in stage 1, and says so
  progress.py       event protocol, stage-1 members only
  console.py        console implementation of the protocol
  safetext.py       neutraliser: spreadsheet half
  richtext.py       neutraliser: terminal half, sole seam with rich
  marktext.py       neutraliser: markdown half                      [step 5]
  render.py         Block and Raport to rich (no raport/render.py)  [step 5]
  identity.py       sole producer of NumerKRS                        [step 2]
  odpis/
    zrodlo.py       port RejestrKRS + adapter OdpisZPliku            [step 2]
    model.py        read model of an extract                         [step 2]
    czytanie.py     parsing, sole producer of DzienBilansowy         [step 2]
  signals/
    katalog.py      loader-guard over the rule catalogue             [step 3]
    model.py        Sygnal | Wykluczony | Nieustalony                [step 4]
    ocena.py        the catalogue walked over one extract            [step 4]
    terminy.py      sole producer of TerminUstawowy                  [step 4]
    reguly/*.yaml   the rules themselves, as data                    [step 3]
  raport/
    texts.py        pure view models, no output library              [step 5]
    render.py       Block to rich                                    [step 5]
    markdown.py     Block to markdown                                [step 5]
  dziennik/
    zapis.py        append-only journal + separate payload store     [step 6]
```

Dependency direction: `cli -> raport -> signals -> odpis`, with `dziennik` to the side, called by
`cli`. `signals/` knows nothing about files; `odpis/` knows nothing about rules; `raport/` knows
neither beyond the result model.

One crossing between contexts, carried over from ADR-0023: the
`dzial3.wzmiankiOZlozonychDokumentach` block tells the signal layer **which periods were filed**, and
that is the only thing the signal layer learns about financial statements.

## Boundary rules

Thirteen rules — twelve planned, and rule 13 added in step 5 together with the field it protects.
Each is enforced by a mechanism, and each mechanism has a test of itself with a seeded
violation — a rule that cannot be shown to fail is indistinguishable from an empty set.

| # | Rule | Mechanism | Step |
|---|---|---|---|
| 1 | No module in `krs_tool/`, `tests/` or `scripts/` imports a networking library or `socket` | AST scan against a data-set of forbidden roots, matched on **dotted prefixes** | 1 |
| 2 | `pyproject.toml` declares no networking dependency | test reading `[project].dependencies` and every `optional-dependencies` group against an allow-list | 1 |
| 3 | The whole suite runs with sockets forbidden | autouse fixture replacing `socket.socket`, `create_connection` and `getaddrinfo`, plus a test that the ban actually bites | 1 |
| 4 | `signals/` does not read the clock | scan: no `datetime.now`, `date.today`, `time.time`, `time.monotonic`, no import of `clock` | **3** |
| 5 | `signals/` and `texts.py` are pure — no `rich`, `typer`, `questionary`, `sqlite3` | import scan | **3** |
| 6 | Every string from outside the program reaches an output channel through **that channel's** neutraliser | scan generalised to pairs (channel, neutraliser): `rich` to `richtext.safe`, markdown to `marktext.safe_md` | 1, extended in 5 |
| 7 | `cli.py` authors no sentence | scan | 1 |
| 8 | Only `identity.py` produces `NumerKRS` | mypy strict, `NewType`, **plus an AST scan for the constructor call** | 2, scan in 4 |
| 9 | Only `terminy.py` produces `TerminUstawowy`, and it takes a `DzienBilansowy` produced only by `odpis/czytanie.py` | mypy strict, two `NewType`, plus the same scan | 4 |
| 10 | No numeric literal other than 0 and 1 appears in `signals/` | AST scan | **3** |
| 11 | The accusatory lexicon is closed | `Poziom` has no "late" member; scan of the **parsed values** of `reguly/*.yaml` and of the non-docstring string constants of `texts.py` | **3** |
| 12 | `dziennik/` is append-only | scan: `open()` only in `a`, `x` or `r`; no `unlink`; no overwrite of an existing path | 6 |
| 13 | `signals/` never reads `Dzial.klucze` — the verbatim field names inside a division | AST scan for the attribute read, with a seeded self-test | 5 |

### Note on copying the scan from `ceidg-tool`

The original matches import **roots**, so `import urllib.request` would pass a rule that reports
itself as closed. The generalisation to dotted prefixes happens at the moment of copying, not later —
this is the exact shape that once let a second HTTP stack into `ceidg-tool` unnoticed.

### Rule 6 is a pair, not a list

`ceidg-tool` has one output channel that carries hostile text and therefore one neutraliser. Stage 1
gains a second — markdown, where `](http://...)` makes a link and a vertical bar breaks a table row.
So the scan matches pairs: a string reaching the terminal must pass `richtext.safe`, and one reaching
markdown must pass `marktext.safe_md`. Sending a string through the wrong neutraliser is a violation,
not a near miss.

## What is deliberately not copied

**The doctrine that silence is a defect measured in requests.** In `ceidg-tool` an operation runs for
half an hour against a request spacing, so a stretch without output reads as a hang. Stage 1 here has
no requests: its longest operation is reading a file. Copying the doctrine would invite someone to
build a heartbeat around forty milliseconds of parsing. The event protocol therefore keeps its shape
and loses the members that only make sense with a network: `on_request`, `on_wait`, `on_page`,
`on_details`, `on_download`.

`close()` stays, with its reason intact: a live `rich` display overwrites everything printed after it.

**`Clock.sleep`.** Nothing here waits. A `sleep` in the protocol invites a rate limiter with nothing
to limit. The injection seam stays, because the journal timestamp must be substitutable for `odtworz`
to be testable.

**`ratelimit.py` and `httpclient.py`** — see ADR-0001 decision 3.
