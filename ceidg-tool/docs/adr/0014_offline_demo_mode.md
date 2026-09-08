# ADR-0014: A register-free mode, and the demo built on it

Date: 2026-09-08
Status: accepted
Author: P0w3r223
Related to: ADR-0006 (test seam), ADR-0011 (assistant), docs/audit-2026-09-09.md,
docs/design/phase2_core.md (boundary rules 11 and 12)

---

## Context

The tool cannot be run without the production register. The `test` environment is dead —
measured, not assumed: 2 802 `prod` requests have ever been made and **zero** `test` ones, and
`test-dane.biznes.gov.pl` times out at TCP level. It also fails *slowly*: the retry ladder in
`client.py:50-51` is `(10, 30, 60, 300)` s against `CONNECTION_MAX_OUTAGE_S = 1800`, so about half
an hour passes before the tool says the host is unreachable, and `ui/texts.py:78-93` never warns
that it is known-dead. A CEIDG token additionally requires a Polish Profil Zaufany.

The audit named this the second of three blockers to the project surviving without its author, and
separately the owner asked for a demo: a reproducible walk from a Polish sentence through criteria,
a cost table, a decision and a fetch to a finished workbook, plus `aktualizuj` and
resume-after-interruption, with **zero requests to CEIDG** and **no real personal data on screen**.

These are the same problem. A demo that survives `git clone` — a stated design condition — is an
artefact that will be maintained anyway; an artefact only the demo uses will rot, while one a new
owner also uses will not.

## Constraints that shaped the decision

- **Boundary rule 11.** `httpclient.build_http_client` is the only place an `httpx.Client` is made,
  and `AllowedHostsTransport` refuses foreign hosts at the layer where the socket opens. This is the
  highest-value control in the project: `HTTPS_PROXY` once routed every request — PESEL-bearing
  token included — through an unvalidated host.
- **A standing guard forbids the obvious shortcut.** `tests/resilience/test_egress_allowlist.py:149-154`
  calls `build_deps` without `http=` and asserts it constructs `build_http_client(transport=None)`.
  A `--demo` branch *inside* `build_deps` turns that test red, correctly.
- **A local mock server is not available.** It collides with `apiprofile.py:99`,
  `pipeline.py:302-309`, `client.py:131-139` and `httpclient.py:53`; a TLS variant additionally needs
  `verify=False`. There is no non-weakening way to do it.
- **A third `Environment` is not available either.** `build_deps` raises unless the profile's host is
  in `HOST_ENVIRONMENT`, so a `demo` environment means a third host in the production egress map.
  Paying a permanent widening of rule 11 to buy a label is the wrong trade.
- **The credential seam already exists.** `config.load_settings` accepts `env_file=None`,
  `environ=`, `use_keyring=False`, `token=` and `data_dir=` (`config.py:362-373`). No new
  credential-bypass flag is needed, and none should be added.
- **Demo data is the most likely leak of the day.** The only material on this machine is production.
  `scripts/anonymize_samples.py` rewrites host and GUIDs only — real `nip=` values sit in
  `probe_out/mini/` and `probe_out/nip_ids/` query strings today — and there is no anonymiser at all
  for the report CSV or for SQLite. The remote is another team's repository.

## Options considered

**A. Composition root replaying recorded traffic.** A demo entry point outside `ceidg_tool/` builds
`Deps` with `http=build_http_client(transport=MockTransport(replay))`. Weakens nothing. But the
bundle *is* production traffic, and the anonymiser that would clean it has, on this project's
record, already sanitised away the exact property under test. Highest leak risk, highest maintenance
(every demo change needs a re-record and an anonymiser audit), and the demo can only walk the
recorded path.

**B. Composition root over a generated synthetic register.** Same seam, but the substitute is a
generator plus a request handler answering *any* query, implementing the measured traits in
`tests/fixtures/api_traits.yaml`. Leak risk is structurally zero — no production byte enters the
bundle — and the residual risk moves from data to behaviour, which already has an instrument
(`tests/test_api_traits.py`). But the demo path is not the operator's path, and the survival blocker
stays open.

**C. A transcript player.** Record `Block` view models from a real session and replay them through
the real `ui/render.py`. Cheap, safe, easy to audit — and it lies about everything except the
screens: no decisions, no fetch, no kill, no resume, and the workbook is a prop.

**D. Make "run without a register" a property of the product,** owned by `httpclient.py`, selected
by one explicit flag in `cli.py`, with option B's generated corpus as the substitute.

## Decision

**D, with B's substitute.**

`httpclient.py` — the module rule 11 already makes the single answer to "where can a connection go"
— gains a sibling factory for a replay client. `cli.py` selects it behind one explicit flag and
passes it as `http=`, exactly as tests already do. Every existing command then works unchanged: the
demo *is* `ceidg-tool pobierz …`, driven by a human at a real terminal.

The standing egress guard stays green by construction: `build_deps` still calls
`build_http_client(transport=None)` when nothing is injected, the selection lives in `cli.py` (which
calls our own factory rather than constructing a client), `AllowedHostsTransport` still wraps the
replay transport, and **no host is added to `HOST_ENVIRONMENT`**.

The corpus is generated, not recorded: synthetic traders, reserved-prefix identifiers,
checksum-valid NIPs from a reserved range, no PESEL anywhere. It reproduces the measured
distributions rather than a sanitised happy path — the 59.0 % PKD-2007 share, some pre-1990 start
dates, a hostile name carrying `=` and `[`, a `WYKRESLONY` status — so the audience sees the real
warnings fire.

### The cost, stated plainly

A fake register ships inside the product, and a mode exists that an operator could enter without
noticing. The second is the real design problem, and it is answerable only by making the mode
**loud in every artefact that outlives the session**:

1. the first screen (`ui/texts.first_screen`),
2. a row in the workbook's `Metadane` sheet,
3. a prefix on the output filename,
4. a separate data directory, so a demo run cannot touch the real store,
5. a refusal to combine the demo flag with `--srodowisko prod`.

If any of these is skipped this decision becomes the worst of the four, because a demo workbook
would be indistinguishable from a production one — precisely the risk the audit recorded against a
seeded demo database (`firma.zrodlo` has no `DEMO` value, so the database cannot label itself).

Marking the data at the schema level — a `'DEMO'` value in `firma.zrodlo`'s `CHECK` — is the
strongest form and is **deliberately deferred**: it costs schema v4, a migration, and changes to
`record_sources` and the hidden-columns predicate. It is worth taking once the demo has proved it
will be kept. Until then, items 1-5 are mandatory and item 4 is what keeps the real database safe.

### Time is scaled, not faked

The demo injects a clock, but a zero clock would finish the fetch before the audience sees it, leave
no window in which a kill is meaningful, and hide the machinery — limiter, heartbeat, progress
cadence — that dominates this codebase and exists for exactly the property being hidden. The demo
compresses time by a fixed factor instead. **The cost table keeps printing the true minutes**, because
`estimating.estimate` is a pure function of the count and the profile; that is the demo's honesty
anchor and should be said out loud when showing it.

## Consequences

- A new owner can run the tool on the day they clone it, without a token, without a Profil
  Zaufany, and without touching real personal data. This is the decision's main value; the demo is
  the by-product.
- A permanent user-visible mode, and therefore a permanent support surface and a permanent misuse
  risk, mitigated by the five markers above.
- The corpus must be held to `tests/fixtures/api_traits.yaml` by the same test that holds the
  fixtures, or it will drift from the register and start teaching the wrong thing.
- Scan roots must cover wherever the corpus and the demo entry point live; the rule-11 and rule-12
  AST scans currently cover `ceidg_tool/`, `scripts/`, `tests/` and `./ceidg_probe.py` only
  (`tests/test_boundaries.py:664`).
- `.gitignore` was widened before any of this was built, because the remote is another team's
  repository and a staged bundle is not a recoverable mistake.

## What would reverse this decision

If the owner declines a new user-visible mode in the shipped CLI — a legitimate refusal, since it is
a permanent support surface — the fallback is option B with an entry point under `scripts/demo/`
(already inside the rule-11 scan root), at the price that the demo path is not the operator's path
and the survival blocker stays open. If the simulator cannot satisfy `api_traits.yaml` in roughly
300 lines, recordings become cheaper than simulation and option A wins — but only after the
anonymiser gains real coverage for query strings, report CSV and SQLite, which is itself a day's
work.
