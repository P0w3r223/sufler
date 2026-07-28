# ADR 0003 — Redaction of sensitive and private content

Date: 2026-07-23
Status: accepted
Author: P0w3r223
Related to: [[0001-structure-and-prompt-discriminator]], [[0002-llm-summary-layer]]

---

## Context

Prompt history captures whatever the user typed or pasted, including highly sensitive material:
server IPs, SSH credentials (`user@host`), API keys/tokens/passwords, user GUIDs, and pasted
terminal logs. The per-day summary is intended to feed a Jira agent and may be shared, so raw
sensitive content must never reach the output, the JSON contract, or the LLM request.

## Decision

**Redact at the boundary, always on.** A pure `core/redaction.py` is applied in `parse_prompt`
(prompts) and `parse_git_log` (commit messages), so raw sensitive text never enters the domain
objects, rendering, or the LLM payload. There is no bypass flag — the guarantee is unconditional.

**Three tiers**, matching the requirement (don't read / describe very generally / ignore fully):

1. **Inline redaction** — sensitive *values* are replaced by labels: `[SEKRET]` (private keys,
   `key/token/password=…`, `Bearer …`, `sk-…`/`gh…_`/`AKIA…`/JWT), `[KONTO]` (email or
   `user@host`), `[IP]` (IPv4), `[ID]` (UUID/GUID), `[UŻYTKOWNIK]` (username in `Users\`/`home/`
   paths, incl. the dash-encoded project folder name). The value itself is never stored.
2. **Paste trimming** — a prompt that looks like a pasted log/dump (multi-line, very long, shell
   prompt or log-level tokens) is reduced to its leading human instruction plus a general marker
   `[…wklejona treść pominięta]`; the pasted remainder is discarded.
3. **Full drop** — if nothing meaningful survives (a prompt that is purely a terminal dump), it is
   ignored entirely (`parse_prompt` returns `None`).

**Auditability.** Each prompt carries a `redactions` set of the categories that fired (never the
values); it appears in the JSON output and as a discreet `(zredagowano: …)` tag in Markdown, so a
reader can trust the tool caught sensitive content.

## Consequences

- Deterministic, free, testable — no LLM, covered by `tests/core/test_redaction.py`.
- Meaningful instructions are preserved (e.g. "napraw testy", "zaloguj przez ssh [KONTO]"); only
  sensitive values and pasted noise are removed.
- Best-effort, not a proof: novel secret formats may slip through inline detection, but paste
  trimming + full drop bound the exposure of any surrounding dump. New patterns are cheap to add.

## Alternatives considered

- **LLM-based redaction** — rejected: non-deterministic, costs tokens, and would itself receive the
  raw sensitive text (the very thing we must avoid sending).
- **Opt-in redaction / `--no-redact`** — rejected as the default: the requirement is to *ensure* no
  private data leaks, so redaction is unconditional.
