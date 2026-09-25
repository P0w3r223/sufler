# ADR 0001 — Structure, prompt discriminator and repo↔transcript correlation

Date: 2026-07-23
Status: accepted
Author: P0w3r223
Related to: Sufler ADR 0034/0035 (worklog/Jira), Powiadomienia_teams (subproject template)

---

## Context

`claude_summary` compiles a per-day account of a person's work by combining their Claude Code
prompt history with a repository's commit history, as source material for a larger agent that
records a weekly schedule/worklog in Jira. Two problems dominate the design: reliably extracting
*genuine human prompts* from transcripts, and attributing sessions to the right repository.

## Decision

**Self-contained uv subproject**, hexagonal split: `core/` is pure, I/O-free logic (models,
discriminator, day grouping, rendering) — fully unit-testable; `adapters/` do I/O (read
`~/.claude/projects` transcripts, shell out to `git log`, optional Claude API). Mirrors the
`Powiadomienia_teams` template and Sufler conventions (uv, hatchling, src-layout, ruff/mypy/pytest).

**Prompt discriminator.** A `type:"user"` line is not automatically a human prompt. We keep a line
only when ALL hold: `promptSource ∈ {typed, suggestion_accepted, queued}`, `origin.kind == "human"`,
`message.content` is a string, no top-level `toolUseResult`, and `isSidechain` is not true. This
excludes tool results, sub-agent `task-notification`s, slash-command expansions, local-command
wrappers and sub-agent turns. Defensively strip harness-appended blocks (`<system-reminder>…`,
`<command-*>`, `<local-command-*>`) before using the text.

**Timezone.** Transcript timestamps are UTC (`…Z`); git gives an offset (`%aI`). Both are parsed to
aware datetimes; conversion to the local zone happens in ONE place — day grouping — before
truncating to a date, so a late-evening UTC prompt lands on the correct local day.

**Repo↔transcript correlation.** With `--repo`, include: the folder whose name equals the repo path
with separators replaced by `-` (`C:\…\repo` → `C--…-repo`) in full, PLUS prompts from any folder
whose per-line `cwd` lies under the repo tree (covers parent-launched sessions and worktrees).
Without `--repo`, include all folders.

**Consent.** Reading private prompt history is fail-closed: `--consent` / `CLAUDE_SUMMARY_CONSENT=1`
is required, otherwise the tool refuses with a clear message. *(The policy still holds; the
mechanism does not. As written, the check lived in the orchestrator — a convention of the call site,
so the adapter accepted a call from anyone. Superseded by
[[0004-consent-as-a-type-at-the-read-boundary]]: consent is now a `ConsentProof` argument required
at the read itself.)*

## Consequences

- The discriminator is a pure function tested against every excluded variant — the crux is verifiable.
- Introduces the first `subprocess`/git and `.jsonl` code in the repository; kept minimal and isolated
  in adapters, with a pure `parse_git_log` tested without git.
- Output may contain private prompt text; it defaults outside the repo and is git-ignored.

## Alternatives considered

- **`~/.claude/history.jsonl`** as the prompt source — simpler, but lacks per-session `cwd`/day
  fidelity needed for repo attribution. Project transcripts are authoritative.
- **Filtering by launch-folder name only** — misses sessions launched from a parent directory or in
  worktrees; the additional `cwd`-under-repo rule recovers them.
