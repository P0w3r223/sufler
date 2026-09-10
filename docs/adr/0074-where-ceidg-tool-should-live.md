# 0074 — Where `ceidg-tool` should live

Date: 2026-09-10
Status: accepted (2026-09-10 — option A, decided by the owner; carried out in the same pull request)
Author: P0w3r223
Related to: [ADR 0044](0044-linux-container-deployment.md) (the deployment shape this repository
assumes), [ADR 0046](0046-workmate-powiadomienia-teams-coexistence.md) (the precedent for a second
product living in this tree). The branch itself is `ceidg-tool`; nothing about it is reachable from
`Main`.

---

## Context

`ceidg-tool` downloads sole-proprietorship records from the CEIDG Data Warehouse API v3 and writes
them into an Excel workbook. It is a complete product: 33 commits, its own `pyproject.toml`, its own
CI workflow, 22 of its own ADRs, an offline demo mode, and a walkthrough document. None of that is
in dispute. What is in dispute is the address.

Four facts about the address, all checkable:

1. **The branch has no merge base with `Main`.** `git diff Main...origin/ceidg-tool` answers
   `no merge base`. This is not a long-lived feature branch that drifted; it is an unrelated history
   that happens to share a remote.
2. **It is invisible to this repository's quality gate.** The matrix in `.github/workflows/ci.yml`
   has three entries — `workmate`, `powiadomienia-teams`, `claude-summary` — and the branch filters
   in `on:` are `Main` and `Dev`. The project is not ungated: it carries its own workflow, scoped by
   `branches: [ceidg-tool]`. But nothing in the root's gate can see it, and nothing in its gate can
   see the root.
3. **The licences disagree.** The root is `Proprietary`; `ceidg-tool/pyproject.toml` declares MIT.
   Two licences in one repository is a question someone will eventually have to answer under time
   pressure, which is the worst moment to answer it.
4. **The project already documents the awkwardness itself.** Its `README.md` opens the "where to get
   the code" section with: the `origin` remote belongs to another team and its default branch holds
   a different product. Its `ci.yml` explains the branch filter partly as a guard against the day
   this branch reaches `Main` and starts firing the matrix on someone else's pull requests.

The precedent that makes this a real question rather than an obvious one is `Powiadomienia_teams`:
a second product that **does** live in this tree, with its own `pyproject.toml`, its own Dockerfile
and its own `PLAN.md`. Co-tenancy here is a pattern, not an accident (ADR 0046). The difference is
that `Powiadomienia_teams` shares history, shares the gate, shares the licence and shares a domain —
Teams, Graph, the same tenant. `ceidg-tool` shares none of the four.

## Decision

**Option A.** The owner decided on 2026-09-10, against this draft's recommendation, and the
import happened in the same pull request. What the draft listed under A's "Against" had to be
answered rather than accepted, so each answer is recorded here — that is the part a later reader
needs, not the recommendation that lost.

- **The missing merge base.** History was preserved, not squashed: the branch was rewritten into
  a `ceidg-tool/` subdirectory with `git filter-repo` and merged with
  `--allow-unrelated-histories`, so all 36 commits of reasoning are reachable from `Main`.
- **Authorship.** The rewrite also unified the author to `P0w3r223`,
  because the `autorstwo` gate added on 2026-09-10 checks the whole history reachable
  from `Main` — a plain merge would have turned it red on the first push. Every commit number
  therefore changed.
- **The two CI shapes.** Neither lost. The root matrix gained a fourth entry (ubuntu, `uv`) and is
  the gate; the sub-project's own workflow kept the axis the root does not have — Windows and a
  second Python version, installed with `pip` — narrowed by `paths` instead of by branch. Issues
  #146 and #147 were Windows-only defects that survived weeks of green CI, which is what that axis
  is for.
- **The licence.** The `license = { text = "MIT" }` field was removed rather than rewritten to
  `Proprietary`: neither sibling sub-project declares a licence, and the root `LICENSE` governs the
  tree. One statement, one place.

The branch `ceidg-tool` is left in place, unrewritten, as the transition copy; deleting it is a
separate, irreversible step for the owner to take once they are satisfied with `Main`.

### A. Merge into `Main` as a fourth sub-project

Add a `ceidg_tool/` directory at the root, a fourth entry in the CI matrix, and align the licence
with the root.

- **For:** one clone, one gate, one place to look. Matches the `Powiadomienia_teams` precedent.
- **Against:** there is no merge base, so the merge either rewrites history or lands as one squashed
  commit that throws away 33 commits of reasoning. The two CI configurations disagree on shape —
  the root matrix is per-sub-project with `uv`, the branch matrix is 2×2 over OS and Python version
  with `pip` — so one of them has to lose. And the licence question has to be answered on the merge
  day rather than deliberately.

### B. Leave the branch where it is, and say so in the description

Keep the status quo, but make it legible: the root `README.md` names the branch, the licence and the
CI boundary, so nobody discovers it by accident.

- **For:** zero risk today; costs one paragraph.
- **Against:** it makes a temporary arrangement permanent by describing it well. Every future reader
  of `Main` still has to be told that the default branch is not the whole repository, and the branch
  keeps spending this organisation's Actions minutes under a name that says nothing about it.

### C. Split into its own repository (recommended)

Push the branch to a new repository as its default branch, keep this one read-only for a transition
period, then delete it here.

- **For:** every one of the four facts above stops being a special case. The licence stands alone.
  The CI workflow stops needing a branch filter to explain itself. History is preserved exactly,
  because a branch with no merge base is precisely the thing that transplants cleanly. The project's
  own `README.md` stops having to warn its readers about the neighbours.
- **Against:** a second repository to create and grant access to; any external links to the branch
  break; the `git clone --branch ceidg-tool` instruction in its `README.md` has to be rewritten
  (it wants rewriting anyway).

## Consequences

**The root description states the outcome.** `README.md` now lists `ceidg-tool/` as the fourth
sub-project of this tree, and `CLAUDE.md` names it in the sub-project section — the paragraph that
described a branch is gone. The failure mode this whole audit exists to fix is a description that
reads as a statement of fact while describing a plan; leaving that paragraph would have reinstated
it from the other side.

**`ci.yml` changed, as option A required.** The fourth matrix entry runs `uv sync --extra dev
--extra asystent`, lints `ceidg_tool tests scripts` (a flat layout, so the directories are named
rather than `.`), and `[tool.mypy]` in the sub-project gained `files = [...]` because the shared
step calls `uv run mypy` with no arguments.

**Two clones of one project exist for a while.** The old branch still holds the pre-import history
under its old commit numbers. Anyone working from it should move to `Main`; the SHA map makes an
old number resolvable in the meantime.

## Risks

- **The old branch outlives the decision.** It is deliberately left in place and deliberately not
  rewritten, so that anyone mid-work on it loses nothing. It is also the one thing here that can go
  stale silently: a commit pushed to it after 2026-09-10 does not reach `Main`. Deleting it, with
  a named owner, is the follow-up this ADR asks for.
- **A pull request against the old branch is now a trap.** The workflow file that used to serve it
  no longer exists there under that trigger, and its base is a history `Main` does not share.
- **Delay was not neutral, and was not free.** The branch grew from 33 to 36 commits between the
  draft and the decision — three commits' worth of extra surface for the import to move.
