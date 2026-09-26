# Sufler

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/P0w3r223/sufler/actions/workflows/ci.yml/badge.svg)](https://github.com/P0w3r223/sufler/actions/workflows/ci.yml)
[![Wersja](https://img.shields.io/badge/wersja-1.16.0-green.svg)](CHANGELOG.md)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**An MCP server and agent runtime built for one team: a shared, searchable knowledge base of project
notes and status, reachable from Claude Code, Microsoft Teams, a command line and GitHub.** Meeting
notes and project status go into one store instead of scattered files, GitHub and Teams exchange
events, and Jira answers "what are my open tasks" without leaving the conversation.

> **About this copy.** I built Sufler during my internship at BIAP (Intelligent Technologies
> division, July to September 2026) and publish it with the company's consent. Personal data and
> company infrastructure (hosts, tenant, team and channel identifiers) were removed from every
> commit, and `#N` in commit messages refers to pull requests in the private original. The
> documentation under `docs/` is in Polish.

## Status

Deployed as a pilot in one team. The code of phases 1 to 4 is complete: the MCP server, the agent
runtime, the Teams, CLI and GitHub entry points, the GitHub ↔ event store ↔ Teams bridge with gated
writes, read-only Jira, the Teams Shifts schedule and lexical retrieval over notes. Open: the HTTP
deployment ([`docs/roadmap.md`](docs/roadmap.md)).

## Architecture

One core, several entry points. All logic lives in a core with no I/O (`core/`); Claude Code over
MCP, Teams, the CLI and GitHub are thin adapters over the same tool catalogue, so a new tool is
written once. The core never imports an adapter, and import-linter checks that in CI.

## Quick start

Requires **Python 3.11** and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync                               # core; the MCP server runs on local files with no secrets
uv run sufler                         # MCP server (stdio)
uv run mcp dev src/sufler/server.py   # inspect the tools
```

## Details

<details>
<summary><strong>What it does</strong></summary>

| Area | What it can do |
|---|---|
| **Knowledge base (MCP)** | 4 read tools (search, read note, list projects, project status) and one gated write tool (`save_note`, append-only). Three more tools appear only under specific configuration: reading bridge events, and "my tasks" / "my history" in Jira. |
| **Agent on Teams, CLI and GitHub** | The same tool catalogue drives an agent runtime (Claude in a loop) with conversation memory and history compaction. |
| **`Bash` shell** | Commands from the model run in a separate executor container **with no network**, created per conversation and able to see only that conversation's scratch space, behind a membership gate ([ADR 0057](docs/adr/0057-shell-executor-container-without-network.md), [ADR 0063](docs/adr/0063-shell-membership-gate-and-conversation-isolation.md)). |
| **`File` tool and editing the knowledge base** | `read` puts a file (image, PDF, HTML) into the model's context; `edit` changes an existing note through an independent judge model, a snapshot before the change and a human confirmation step. `delete` has its own gate and, when that gate is closed, is not even listed ([ADR 0064](docs/adr/0064-file-tool-and-model-initiated-materialization.md), [ADR 0065](docs/adr/0065-mutable-knowledge-base-and-model-judged-writes.md)). |
| **GitHub ↔ event store ↔ Teams bridge** | Ingests issue, pull request, comment, review, CI and issue-closure events; posts to a channel and to 1:1 chats; two-way threads; from Teams, opens an issue or answers in the GitHub thread. |
| **Jira (read-only)** | My open and finished tasks, one issue with comments, search, and a team member's tasks through a trusted identity map. There is no write path ([ADR 0054](docs/adr/0054-reduce-jira-to-read-only-my-tasks.md)). |
| **Teams Shifts schedule (read)** | Who works today or this week, on site or remote, and who is off ([ADR 0059](docs/adr/0059-teams-shifts-schedule-read.md)). |
| **Meeting notes from transcripts** | The agent reads a Teams meeting transcript and writes a structured note; participants are counted from the transcript, not by the model. |
| **Lexical retrieval (BM25)** | Search over Polish lemmas (`simplemma`) with a substring fallback; quality is held by a small evaluation in `eval/`. |

Both MCP tool counts, five frozen and three additive, are held by the golden test
`tests/adapters/test_mcp_tool_surface.py`. The agent has eight tools with the shell enabled and
fourteen without it; the full set, each tool's actions and a byte ceiling on tool descriptions are
held by `tests/core/test_tool_descriptions.py`.

</details>

<details>
<summary><strong>What Sufler does not do</strong></summary>

- **Jira is read-only.** It does not create issues, comment or change status; there is no write path
  in the code, and the Jira account never comes from the model.
- **Writes are off by default everywhere.** Code for a mutating capability can be in the tree and
  still be unreachable until an operator opens its gate. `tests/test_gates_closed_by_default.py`
  finds the gates by reflection and checks the configuration templates too.
- **The HTTP entry point does not write.** The `streamable-http` transport forces writes off
  regardless of configuration and does not expose Jira tools
  ([ADR 0007](docs/adr/0007-gate-3-http-auth-deployment.md)).
- **The MCP tool surface is frozen.** A new tool there breaks the golden test and needs an ADR first.
- **Meeting and thread notes are immutable**; immutability is how idempotency works here.
- **Dense retrieval is built but switched off** until it passes the evaluation gate
  ([ADR 0039](docs/adr/0039-gated-local-dense-retrieval.md)).

</details>

<details>
<summary><strong>Code layout and the bridge</strong></summary>

```
src/sufler/
├── core/            # no I/O, no SDKs
│   ├── domain/      # models and pure logic
│   ├── ports/       # interfaces (repositories, LLM, GitHub, Jira, notifications)
│   ├── application/ # use cases and the single tool catalogue
│   └── agent/       # agent runtime
├── adapters/
│   ├── inbound/     # entry points: mcp, teams, teams_graph, cli, github
│   └── outbound/    # clients of external APIs
├── server.py        # MCP server wiring
└── config/          # SUFLER_* settings, one module per domain
```

The bridge connects GitHub, a shared append-only event store with deduplication, and Teams; no
component calls another directly. Jira is not part of the bridge: "my tasks" is a direct, read-only
query scoped to the asking user's account. Diagrams: [`docs/explanation/architecture.md`](docs/explanation/architecture.md).

</details>

<details>
<summary><strong>Configuration and extras</strong></summary>

The repository ships a project-scoped [`.mcp.json`](.mcp.json), so Claude Code opened in this
directory picks up the `sufler` server. `save_note` is off by default; copy
[`.env.example`](.env.example) to `.env` to enable it locally. Other entry points need extras
(`uv sync --extra <name>`): `agent`, `teams`, `teams-graph`, `github`, `jira`, `retrieval`,
`retrieval-dense`, `file-reply`, `seed`. The full list of console scripts and settings is in
[`docs/reference/`](docs/reference/) and [`docs/how-to/gate-matrix.md`](docs/how-to/gate-matrix.md).

</details>

<details>
<summary><strong>Tests and gates</strong></summary>

```bash
uv run --no-sync pytest
uv run --no-sync ruff check src tests eval deploy scripts
uv run --no-sync ruff format --check src tests eval deploy scripts
uv run --no-sync mypy
uv run --no-sync lint-imports      # core must not import adapters
```

This is the same gate CI runs ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)). The
sub-projects have their own environments and are tested separately. A security suite covers
injection, path traversal and secret leakage ([`tests/security/`](tests/security/)).

</details>

<details>
<summary><strong>Security</strong></summary>

- Reading is the default; every mutating capability has its own gate, off by default, opened per
  entry point.
- Note and event content is data, not instructions. Only control characters are rejected or
  stripped by code; trust classes T0 to T3 label content rather than block it
  ([ADR 0066](docs/adr/0066-content-trust-classes-and-sticky-conversation-taint.md)). The defence
  against prompt injection is the capability gates, read-only mounts, the network-less executor and
  reversibility.
- Secrets are read from the environment or files outside `data/`, never from the repository.

</details>

<details>
<summary><strong>Repository map</strong></summary>

- **`sufler`** (`src/`, `tests/`, `docs/`, `deploy/`, `eval/`, `scripts/`): the core described above.
- **[`Powiadomienia_teams/`](Powiadomienia_teams/README.md)**: weekly Microsoft Shifts reminders in Teams, writing shifts back only after an explicit "yes".
- **[`claude_summary/`](claude_summary/README.md)**: a CLI that summarises a person's day from Claude Code prompt history (with explicit consent) and commit history, redacting sensitive content at parse time.
- **[`ceidg-tool/`](ceidg-tool/README.md)**: exports data on sole proprietorships from the CEIDG register API to Excel, with a wizard and an offline `--demo` mode.
- **[`krs-tool/`](krs-tool/README.md)**: reads a court-register (KRS) extract saved by the operator and reports registry signals about a company; it has no network client by design.

Each sub-project has its own environment, CI entry and README.

</details>

<details>
<summary><strong>Documentation (Polish)</strong></summary>

[`docs/`](docs/README.md) follows [Diátaxis](https://diataxis.fr/): tutorial, how-to, reference,
explanation, decision records in [`docs/adr/`](docs/adr/) and research notes. Changes between versions:
[`CHANGELOG.md`](CHANGELOG.md).

</details>

## License

MIT, see [LICENSE](LICENSE).
