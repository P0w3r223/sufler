# ADR-0009: Closing boundary rules 9 and 10, and automating resilience scenarios 1, 2 and 8

Date: 2026-09-06
Status: accepted 2026-09-06 by the project owner
Author: P0w3r223
Related to: ADR-0008, docs/design/phase2_core.md, UZUPELNIENIE_01.md §B/§C/§D/§E, docs/resilience-report.md, docs/status.md

---

## Context

Phase 3 shipped with three items left open in `docs/status.md`, all of them blocked on nobody:

1. **Boundary rule 9 was half-done.** `cli.py` was declared a thin adapter that authors no
   user-facing sentence, but `aktualizuj`, `eksportuj`, `wyczysc`, `runy`, both `token`
   subcommands and the `KeyboardInterrupt` handler still built their own strings and printed
   them through a `rich` console with markup parsing on. No injection was possible today
   because those strings are tool-controlled, but the rule existed precisely so that nobody
   has to re-derive that fact at every edit.
2. **Boundary rule 10 had no enforcement.** Rules 6-8 are checked by an import scan in
   `tests/test_boundaries.py`; rule 10 ("every string that reaches `rich` from outside the
   program goes through `render.safe`") rested on review alone. Worse, the existing scan's
   `rich` allowlist *contained* `cli.py`, so the test encoded the violation it should have
   caught.
3. **Resilience scenarios 1, 2 and 8 were still marked pending**, although §D permits them as
   integration tests driven by a server stub.

## The claim that orders the work

**Rule 9 is the precondition for enforcing rule 10, not a neighbour of it.** While `cli.py`
printed through `rich` directly, "every outside string passes through `safe`" would have needed
dataflow analysis across the whole package: any of the ten print sites could receive a value
computed three calls away. With `cli.py` off the `rich` allowlist, the same property collapses
to an import allowlist plus a check on the arguments of the remaining print calls — about forty
lines of AST walking, deterministic on both operating systems, in the same shape as rules 6-8.

That is why finishing rule 9 came first even though rule 10 was the more valuable of the two.

## Decision 1 — how `cli.py` stops authoring sentences

| Option | Verdict |
|---|---|
| A. Every sentence becomes a function in `ui/texts.py` | Literal compliance, but fills `texts.py` with one-line functions returning constants. |
| **B. A message catalogue: `Final` constants for fixed sentences, functions for the five that format values** | **Chosen.** Same guarantee as A without the function noise; constants are greppable and type-checked. |
| C. Leave the strings inline but route them through `view` | Rejected for the reason ADR-0008 rejected its own option B: it makes divergence discouraged rather than impossible, and the two duplicated sentences would survive. |

Sentences moved **verbatim**. Rewording is a separate decision and was deliberately not taken
here: moving strings unchanged is what let four existing assertions stay green and made the
relocation reviewable as a no-behaviour-change edit.

Two sentences existed in two copies — `"Brak przerwanych pobrań."` and
`"Zmienionych wpisów: …, szczegółów: …"` — one printed with markup parsing on and one without.
Both now come from `texts.NO_RESUMABLE` and `texts.update_summary()`. That pair is the concrete
thing rule 9 exists to prevent, and until now it was prevented by luck.

**Scope taken deliberately narrowly:** exception messages raised inside `cli.py` (`ConfigError`
for a bad `--od`, for an unknown report id, for a run kind that cannot be resumed) stay at their
raise sites. They are not printed by `cli.py` — they travel through the error taxonomy and are
rendered once, neutralised, by `_fail` → `view.error`. Moving them into `texts.py` would
separate each message from the condition that raises it and buy nothing: they have no second
copy in the wizard, because the wizard has no `--od` flag. Rule 9 is about the program's
*screens*, and that is how the enforcing test reads it.

## Decision 2 — the three prompts that stay outside the `Prompter` protocol

`_settings`' production confirmation, `wyczysc`'s destructive confirmation and `token zapisz`'s
hidden input use `typer` directly. Option B (extend `Prompter` with `secret()` and route all
three through it) was rejected: ADR-0008 deliberately resolves environment and consent
*above* the UI layer and names `tests/test_cli.py` as the regression gate for exactly that, and
`hide_input` is a capability the protocol does not have. Restructuring consent in the same
session that touches every print site is how a refactor turns into an incident.

Only the *wording* moved, into `texts.CONFIRM_PROD`, `texts.confirm_purge_all()` and
`texts.TOKEN_PROMPT`, with a comment recording why the mechanism stayed.

## Decision 3 — where the `rich` seam lives

`cli.py` still has to hand one `Console` to both `ConsoleEvents` and `ConsoleView`: two
consoles would let an independent print corrupt a live progress bar.

| Option | Verdict |
|---|---|
| A. `cli.py` keeps `Console()` and stays on the allowlist; the scan forbids `.print(` there | Smallest diff, but the rule becomes "may construct, may not print" — a subtler line to hold. |
| **B. A new `ceidg_tool/richtext.py` owning `make_console()` and `safe()`** | **Chosen.** `cli.py` imports no `rich`; rule 10 becomes one sentence. Mirrors `safetext.py` — a tiny pure module holding one shared neutralisation — so it is a pattern this codebase already uses. |
| C. A `SafeConsole` wrapper accepting only `rich.Text` | Rejected: `Progress(console=…)` needs a real `Console`, so the wrapper must expose the raw object and reopens the hole. |

A side effect worth naming: the neutralisation expression
`Text(strip_control(mask_tokens(value)))` existed in `ui/render.py` and again in `console.py`.
Two copies of a security-critical rule is one too many — a fix to one would not reach the other.
There is now one.

`make_console()` constructs `Console()` with **no** `file=` argument. `rich` resolves
`sys.stdout` per write, which is what makes `CliRunner` output capture work; freezing the stream
at import time would break every CLI test silently.

## Decision 4 — how rule 10 is enforced

**Chosen: an AST scan in `tests/test_boundaries.py`**, alongside the behavioural tests that
already exist in `tests/test_ui_render.py` (hostile markup literalised, ESC stripped, token
masked). The pair is complementary: the behavioural test proves today's claim, the scan fails on
tomorrow's violation. Neither alone is enough, and only the behavioural half existed.

The scan asserts (i) the modules importing `rich` are exactly `richtext.py`, `ui/render.py` and
`console.py`; (ii) inside them, every content-bearing argument — positional arguments and the
`title`/`header`/`description`/`label` keywords of `print`, `log`, `add_task`, `add_row`,
`add_column`, `Table`, `Column`, `TextColumn` and `Panel` — is a string literal, a name bound to
a `rich` renderable, or a call to `safe` / `safe_or_none`; and (iii) `cli.py` makes no output
call at all, through any channel.

Four properties, recorded here so that nobody discovers them by weakening the test:

- **It reads the root of the expression, not the tree.** An earlier draft accepted any argument
  containing a `safe(…)` call anywhere, which let `f"{safe(a)} {raw}"` and `safe(a) + raw`
  through — the code review found both. Only a whole argument that *is* a neutraliser call, a
  string literal, or a name bound to one counts.
- **It is syntactic.** It rejects `console.print(f"…{liczba}")` even when the value is an
  integer. That is not a false positive — such a sentence belongs in `ui/texts.py` under rule 9.
  The remedy is to move the sentence, never to relax the scan.
- **Aliasing defeats it** (`p = console.print`). Its job is catching accidents, not adversaries.
- **It permits literal-plus-safe mixes**, e.g. `console.print("[red]Błąd:[/red]", safe(text))`.
  That is the correct idiom: the program's own decoration is markup, the outside string is not.

Two coverage decisions the review corrected, both worth stating because the obvious scan misses
them. First, `print` is not where registry text mostly goes: `table.add_row(…)`,
`Column(header=…)` and `Table(title=…)` carry the cells, so the scan covers those calls and the
content-bearing keywords (`title`, `header`, `description`, `label`) while ignoring `style=` and
`total=`. Second, the `cli.py` half must cover **every** output channel, not the `rich` one:
in a `typer` program the first thing anyone reaches for is `typer.echo`, so a scan that only
knew `rich` would have reported rule 9 closed while leaving the easiest way to break it open.

`strip_control` is deliberately **not** a neutraliser for this purpose. It removes control
characters but does not call `mask_tokens`, and the token carries a PESEL.

A scan that always passes is indistinguishable from one that works, so
`test_the_rule_10_scan_would_notice_a_violation` feeds it twenty-six snippets and asserts which
are rejected: the four shapes that crashed the program in phase 3, and the rest demonstrated by
two rounds of code review against earlier drafts of this scan.

**The invariant that ends the sequence.** Two review rounds produced the same shape of finding
twice — a `rich` object that makes a name safe (`Table`, `Column`, then `Columns`, `Group`)
while its own arguments went uninspected. Enumerating the third batch would only postpone the
fourth, so the relationship is now a test of its own: `RENDERABLE_FACTORIES <= TEXT_BEARING_CALLS`
(anything trusted to carry text is itself scanned) and `TEXT_BEARING_CALLS <= OUTPUT_CALLS`
(every channel rule 10 knows about is a channel rule 9 forbids in `cli.py`). Adding a factory
to one set without the other is now a failing test rather than a thing to remember.

## Decision 5 — the lock defect found by inspecting scenario 1

`store.acquire_lock` raised: *"Poczekaj albo użyj --force"*. **No command had a `--force` flag.**
`force_lock` existed as a keyword on `pipeline.run_fetch` and `run_report_fetch`, and neither
`cli.wznow` nor `flow.execute` passed it.

The consequence is specific to the scenario that had never been exercised. A graceful failure
runs `finally: store.release_lock()`, so the lock row disappears. A killed process does not: the
row survives with a dead PID and a heartbeat from the last saved page, and the documented
recovery — `ceidg-tool wznow` — then fails with `StoreLockedError` for up to
`DEFAULT_LOCK_STALE_S` (600 s), pointing the operator at a flag that does not exist. Same-PID
does not help; the SQL clause tests force-or-stale only.

| Option | Verdict |
|---|---|
| **A. Add the flag the message already promises** | **Chosen.** `--force` on `pobierz`, `wznow` and `aktualizuj` — every command that takes the lock — threaded through `flow.execute`, `run_batched_fetch` and `run_update`. |
| **B. Make the message truthful: say when the lock expires** | **Chosen too.** Without it, waiting is a blind wait; with it the operator has a real choice and `--force` stays the escape hatch instead of becoming the habit. |
| C. Detect a dead owner by PID liveness | Deferred. Best UX, but platform-specific plus a PID-reuse hazard, for a single-user desktop tool. Revisit if the tool ever runs on a shared machine. |

Three refinements came out of the code review, each closing a way the fix could have been worse
than no fix:

- **The flag reaches the batch that has never run.** `run_batched_fetch` forwarded `force_lock`
  to the resume branch only, so `pobierz --partie --force` after a crash failed exactly as it
  had before. A stale lock from *any* killed command blocks the first fresh batch, which is the
  common case, not the rare one.
- **The flag is spent once.** After the first successful acquisition it is cleared, because each
  batch releases the lock behind it; repeating the takeover would mean a twelve-batch run
  stealing the lock from a process that legitimately took it in between.
- **Forcing a *live* lock says so.** `acquire_lock` now reports whether the row it overwrote was
  still fresh, and the caller emits a warning. Records cannot be duplicated (the primary key on
  `(run_id, firma_id)` and the `rowcount`-based counters see to that), but two writers both
  rewrite the checkpoint and both mark the run finished, so an export taken in that window can
  be short. The flag looks identical in both cases; only the warning tells the operator whether
  they inherited a corpse or elbowed a colleague.

**The wizard deliberately gets no `--force`.** Overwriting another process's lock is the kind of
decision ADR-0008 kept out of menus, and an interactive operator cannot establish that the other
process is dead. The lock message instead names the command line explicitly
("powtórz polecenie w wierszu poleceń z flagą --force"), which is a true and actionable
instruction from inside the wizard as well: leave it and run the command.

`lock_stale_s` stays at 600 s. Heartbeats are touched per page and per detail chunk, and a
connection-retry chain can legitimately run 300 s between them, so 600 s is near the floor.


## Decision 6 — how scenarios 1, 2 and 8 are simulated

| Option | Verdict |
|---|---|
| A. Real subprocess plus a real socket server stub | Rejected on three concrete grounds: §C mandates a 180 s wait before the first request after a resume, so a faithful scenario-1 test sleeps three real minutes unless the gap is injected — at which point it is no longer faithful; `kill -9` has no Windows equivalent and CI runs both operating systems; and it abandons the `FakeClock`/`MockTransport` foundation every other automated scenario rests on. |
| **B. In-process fault injection on `FakeApi` + `FakeClock`, with the process boundary simulated at the store** | **Chosen.** |
| C. A size-limited filesystem for scenario 8 | Possible on Linux with tmpfs, not on Windows without admin rights. That is what the owner's manual run is for. |

- **Scenario 1** raises a `BaseException` outside the `CeidgError` taxonomy from inside the
  transport, and suppresses `Store.release_lock` for the duration. What survives on disk is then
  exactly what a `kill -9` leaves: committed page transactions, a run still `w_toku` (never
  marked `przerwany`, because nobody cleaned up) and a held lock. A fresh `Store` on the same
  file then drives the resume. File-level integrity after an abrupt close is SQLite's WAL plus
  the `PRAGMA integrity_check` on open, covered elsewhere.
- **Scenario 2** cuts the transport for **120 seconds of clock time**, not for a fixed number of
  failures — the network decides when it returns, not the client. That framing surfaced a number
  worth writing down: the 10 → 30 → 60 s rungs end around second 111 (limiter spacing counts
  too), still inside the outage, so the fourth attempt only comes after 300 s. A two-minute
  outage costs the whole ladder and about seven minutes of waiting. It fits the 30-minute
  budget of §C, but it does not "wait itself out" cheaply.
- **Scenario 8** injects `ENOSPC` after the temporary file has been partially written, which is
  the half `check_free_space` cannot cover: space sufficed for the estimate and ran out
  mid-write. "Database untouched" is asserted the way the operator would check it — by
  repeating the export successfully afterwards — rather than by counting rows in the same
  process that just failed.

## Automation does not discharge §E

§D permits scenarios 1-3 and 8-10 as integration tests. §E separately requires scenarios 1, 2
and 8 to be **executed manually and recorded in `docs/resilience-report.md` with a date and a
result**. These suites convert the manual run from discovery into confirmation; they do not
replace it. The report keeps the two columns distinct, and each new suite says in its module
docstring which properties it models and which it delegates to the manual run.

## Consequences

| File | Status | Note |
|---|---|---|
| `ceidg_tool/richtext.py` | new | `make_console()` and `safe()`; the only way an outside string becomes printable. |
| `ceidg_tool/ui/render.py`, `console.py` | changed | Import `safe` instead of re-implementing it. |
| `ceidg_tool/ui/texts.py` | extended | Message catalogue at the end of the module. |
| `ceidg_tool/cli.py` | changed | No `rich` import, no print call, `--force` on `pobierz` and `wznow`. |
| `ceidg_tool/ui/wizard.py` | changed | Two sentences now come from `texts`. |
| `ceidg_tool/ui/flow.py`, `pipeline.py`, `store.py` | changed | `force_lock` threaded through; lock message states its expiry. |
| `tests/test_boundaries.py` | extended | Rules 9 and 10 enforced; scan self-tested. |
| `tests/resilience/test_s1_…`, `test_s2_…`, `test_s8_…` | new | Scenarios 1, 2, 8 offline. |
| `ceidg_tool/console.py` | fixed | `TextColumn(…, markup=False)`. The column formats the description and passes the result through `Text.from_markup`, so a `Text` returned by `safe` is stringified and parsed again — a task label from the registry would have raised `MarkupError` despite the neutralisation. Latent today (labels are constants), pinned by a test so it stays fixed. |
| `ceidg_tool/ratelimit.py` | changed | `REASON_NO_CONNECTION` moved here beside the other wait reasons; `console.py` was comparing against a hard-coded copy of it. |

Boundary rules 9 and 10 move from "documented" to "enforced", joining 6-8. Rules 1-5 still rest
on review.

**Review outcome.** The code review blocked this work on two high-severity findings — the
batched `--force` gap and the CLI scan that saw only `rich` — and on four scan holes it proved
empirically rather than argued. Every finding was applied; each shape the review demonstrated is
now a case in the scan's own self-test, so the next draft of this scan cannot lose them
silently. Nothing was dropped.

A second round verified the fixes end to end (both original reproductions re-run against the
working tree) and lifted the block. It found three more scan-coverage gaps of the same shape,
all latent; those are fixed, and the two subset invariants above now make that shape a failing
test instead of a recurring finding. 500 offline tests, `ruff` and `mypy ceidg_tool tests`
clean.

## Revisit when

A second output channel appears (TUI, GUI, web) — `richtext.py` is then the seam to generalise —
or the tool starts running on a shared machine, at which point Decision 5 option C (PID
liveness) becomes worth its platform-specific cost.
