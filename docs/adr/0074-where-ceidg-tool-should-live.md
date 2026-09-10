# 0074 — Where `ceidg-tool` should live

Date: 2026-09-10
Status: proposed (draft — awaiting the team's decision; no code or workflow is touched by it)
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

**To be taken by the team.** Three options are on the table; the draft recommends the third.

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

Whichever option wins, two things follow.

**The root description must state the outcome, not the intention.** The current draft of
`README.md` describes the branch as it stands today. If the team picks C, that paragraph becomes a
pointer to another repository; if it picks A, it becomes a fourth sub-project entry in the map. The
failure mode this whole audit exists to fix is a description that reads as a statement of fact while
describing a plan.

**Option A is the only one that touches `ci.yml`.** Options B and C leave the root workflow exactly
as it is. This draft touches no workflow file at all.

## Risks

- **Delay is not neutral.** Every commit on the branch widens the gap and adds to what a decision
  has to move. The branch was created 2026-09-08 and had 33 commits by 2026-09-10.
- **Option C has a window in which two copies exist.** Naming an owner and a date for deleting the
  branch here is part of choosing it, not a follow-up to it.
- **Option B is the default if nobody decides.** That is worth saying out loud, because it is the
  only option that requires no action and therefore wins by silence.
