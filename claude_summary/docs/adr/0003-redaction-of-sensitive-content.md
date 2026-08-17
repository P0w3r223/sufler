# ADR 0003 — Redaction of sensitive and private content

Date: 2026-07-23
Status: accepted (amended 2026-08-17 — report metadata, see *Amendment*)
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
   `user@host`), `[IP]` (IPv4 and IPv6, full and `::`-compressed), `[ID]` (UUID/GUID),
   `[UŻYTKOWNIK]` (username in `Users\`/`home/` paths, incl. the dash-encoded project folder
   name). The value itself is never stored.
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

## Amendment (2026-08-17) — the guarantee also covers report metadata

The original decision said raw sensitive content must never reach the output, the JSON contract or
the LLM request, but it was implemented for *prompt and commit text only*. An audit found the
report **metadata** bypassing redaction entirely, which made the guarantee false in practice:

- `repo` — the absolute path the user passed on `--repo`, carrying the OS username, went verbatim
  into JSON and Markdown;
- `session_id` — the full transcript session UUID went into JSON, pointing straight at a private
  transcript file;
- `person` / commit `author` — the git email address went into JSON, Markdown *and* the Claude API
  request.

Decision, effective at the **emission boundary** (`core/render.py`, `adapters/anthropic_summarizer`),
so it holds for every caller of the renderers, not just for the CLI orchestrator:

| Field | Treatment |
|-------|-----------|
| `repo` | `redact_text` — the path keeps its shape, the username becomes `[UŻYTKOWNIK]` |
| `session_id` | truncated to the first 8 characters — enough to tell sessions of one day apart, not enough to name a transcript file |
| `person`, commit `author` | `person_label` — an email becomes a display name (`jan.kowalski@firma.pl` → `Jan Kowalski`); a non-email value goes through normal inline redaction |

`--author` / `git config user.email` stay full addresses **inside** the process — that is what
`git log --author` filters on. The redaction applies to what leaves the tool. The owner decided the
`person` field explicitly: a label, never an address (previously this field was left as-is).

Two further hardenings landed with the same amendment:

- IPv6 (full and `::`-compressed) joined the `ip` category — previously only IPv4 was detected;
- the username pattern now matches a **whole path segment** up to the next separator, so
  `C:\Users\Jan Kowalski\app` and `/home/jan-kowalski/app` no longer leak half the name. In the
  dash-encoded project folder name (`C--Users-Jan Kowalski-repo`) the dash *is* the separator, so a
  username containing a dash is only partially redacted there — an ambiguity inherent in the
  encoding, resolved in favour of keeping the project part readable.

Scanning is now windowed: patterns run over at most `8 × _MAX_KEEP` characters of input, because
`sanitize_prompt` truncates to `_MAX_KEEP` *after* redaction anyway. A 60 kB single-token paste used
to freeze the CLI for tens of seconds on quadratic scanning; the window (plus a bounded prefix in
the secret-assignment pattern) removes the cost without changing any surviving output.

The window cuts on a **token boundary**, never inside a token. Secret patterns match whole tokens,
so a half token (`AKIAABCDEF`, a JWT missing its third segment) would match nothing — and since
redacting the earlier secrets shortens the text, such a fragment can land inside the kept
`_MAX_KEEP` characters. When the window boundary falls mid-token, that whole token is dropped.

An unquoted secret value is redacted **to the end of the line** (`haslo: moje tajne haslo` →
`haslo: [SEKRET]`); passphrases contain spaces, and over-redacting the rest of that line is cheaper
than leaking one.

## Alternatives considered

- **LLM-based redaction** — rejected: non-deterministic, costs tokens, and would itself receive the
  raw sensitive text (the very thing we must avoid sending).
- **Opt-in redaction / `--no-redact`** — rejected as the default: the requirement is to *ensure* no
  private data leaks, so redaction is unconditional.
