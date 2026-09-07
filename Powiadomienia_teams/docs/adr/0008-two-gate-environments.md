# 0008 — Two environments run the quality gate, and they are not interchangeable

Date: 2026-09-07
Status: accepted
Author: P0w3r223
Related to: ADR 0006 (public alert content), `Dockerfile`, `.github/workflows/ci.yml`

---

## Context

The same `pytest` suite runs in two places, and until today nobody had written down what each of
them is for.

**CI** (`.github/workflows/ci.yml`) runs it in a checkout of the repository: every file exists,
including `README.md`, `deploy/`, `docs/` and `.github/` itself.

**The image** (`Dockerfile`, stage `test`) runs it inside the artifact that ships to the client.
That stage sees `pyproject.toml`, `uv.lock`, `src/` and `tests/` — and, since PR #109, the two
configuration templates. It does not see the repository around them, and it must not: `docs/` and
`deploy/` are excluded from the build context on purpose.

Two failures made the missing rule expensive.

1. **The image could not be built at all.** `tests/test_config.py` reads `.env.example` and
   `deploy/env.example`; the `test` stage copied neither. `docker build` ended with
   `FileNotFoundError`. It went unnoticed because CI built only the fleet image, never this
   sub-project's, and because `tests/` was rewritten after the 0.2.19 sources were recovered — so
   in its current shape the suite had never passed through `docker build` even once. Meanwhile the
   documentation asserted that "the quality gate runs during the image build", and the deployment
   path rested on that sentence.
2. **The two environments installed different dependencies.** CI ran `uv sync` with no extras
   while the image ran `uv sync --extra agent`. The same suite, two dependency sets, and a
   divergence that would surface only when some test finally touched `anthropic`.

## Decision

Write the division down and enforce it.

> **The image is the reference for DEPENDENCIES. CI is the only environment for guards over the
> repository's own prose.** A test that verifies the ARTIFACT — configuration, code, behaviour —
> must run in both. A test that verifies repository prose runs in CI and skips explicitly in the
> image, stating why.

Three consequences, all implemented alongside this ADR:

- `sync_args` for the `powiadomienia-teams` matrix entry equals the extras used by the image's
  `test` stage. `tests/test_srodowiska_bramki.py` compares the two mechanically, so a new extra
  added in one place turns the gate red.
- CI builds this sub-project's image (`docker-build-powiadomienia`). Without that job the rule
  above is unenforceable: nothing would notice the `Dockerfile` drifting away from the suite again.
- Guards that need the repository tree skip when it is absent, and `addopts` carries `-rs` so the
  skip is printed in the `docker build` log. A silent skip is indistinguishable from a run.

## Consequences

**Accepted cost.** Every pull request now builds an extra image — roughly one and a half minutes.
The alternative was a gate that the documentation described and nobody ran.

**What this does not cover.** Only Python dependencies are compared. The base interpreter version,
the architecture and the `apt` layer are not — the image pins `python:3.11-slim` and CI uses
whatever `uv` resolves for the project. That difference is deliberate (the image's stage `test` is
the one that proves the POSIX `fcntl` branch of `single_instance.py` on the target platform), and
it is the reason the image gate cannot be replaced by the CI gate.

**Where the rule can break next.** A guard over prose added to `tests/` without a skip will pass in
CI and fail the image build — which is the safe direction, because it fails loudly at build time
rather than silently at run time. A guard over the artifact given a skip by mistake fails the
unsafe way: green in both, verifying nothing in the image. Whoever adds a skip states which of the
two kinds the test is.
