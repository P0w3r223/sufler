# Research: professional repository structure for Sufler

Date: 2026-07-07
Status: accepted
Author: P0w3r223
Related to: docs/adr/0001-src-layout-and-hexagonal.md

---

Synthesis of three parallel research passes that informed Sufler's layout:
(1) Python/MCP repo layout, (2) making a repo navigable for Claude Code,
(3) onboarding a newcomer who did not build it. Kept in English per the
knowledge-docs convention.

## 1. Python & MCP server layout

- **src-layout is the default for installable, `uv`-run tools.** It prevents
  accidental imports of the working copy and surfaces packaging errors that
  flat-layout hides. The official reference MCP servers (`modelcontextprotocol/
  servers`, `create-python-server`) use src-layout. → adopted.
- **Hexagonal core + adapters** maps "one core, many doors" onto the tree:
  `core/{domain,ports,application}` + `adapters/{inbound,outbound}`. The core is
  technology-agnostic; ports are interfaces the core owns; adapters implement
  them. FastMCP's `mount()`/composition and future `[project.optional-
  dependencies]` extras (`sufler[teams]`) support adding doors later. → adopted.
- **`pyproject.toml` is the single source of truth** with `uv`:
  `[project.scripts]` for the console command, `[dependency-groups]` (PEP 735)
  for dev tooling instead of `requirements-dev.txt`, `uv.lock` + `.python-version`
  for reproducibility. → adopted.

Sources: Python Packaging User Guide (src vs flat), pyOpenSci packaging guide,
FastMCP docs (tools, composition, server config), `modelcontextprotocol/servers`,
`create-python-server`, uv docs (projects, dependency groups), hexagonal-in-Python
write-ups.

## 2. Navigable for Claude Code

- **A short root `CLAUDE.md`** is the highest-leverage agent aid. Anthropic's
  test for every line: *"would removing this cause Claude to make mistakes? if
  not, cut it."* Bloated files make Claude ignore instructions. Include: repo map,
  commands it can't guess (with flags), non-obvious rules, gotchas. Exclude
  file-by-file descriptions and anything inferable from code. → applied.
- **Single root CLAUDE.md** is correct for a focused single-service repo; nested
  per-directory files are a monorepo mechanic and would be cargo cult here.
- **`.mcp.json` at project scope** (committed) shares the server with the whole
  team; each teammate approves once on first use. Secrets go through `${VAR}`
  expansion, never literal in the file — and Phase 1 needs no secrets at all.
  Tool descriptions are truncated ~2 KB, so keep them concise and keyword-first.
  → applied.

Sources: Claude Code docs (best practices, MCP, large codebases), GitHub's
2,500-repo agents.md study.

## 3. Onboarding a newcomer

- **README is the orientation map:** one-liner → phase status → architecture in a
  paragraph → copy-paste quickstart → repo map → links out to `docs/`. → applied.
- **`docs/` organized by Diátaxis** (tutorial / how-to / reference / explanation)
  plus `adr/` and `research/`, so a reader lands on the right kind of document
  for their need. → applied.
- **ADRs** capture the "why" code cannot, for decisions costly to reverse
  (core/adapters boundary, read-only-first, transport, note schema). Numbered,
  immutable, superseded rather than deleted. → applied (`docs/adr/0001–0004`).
- **Screaming architecture:** the folder tree should reveal the system's shape.
  Seeing `core/` next to `adapters/` (with `teams/`, `github/` stubs) communicates
  the whole roadmap before reading code. The load-bearing convention — `core`
  imports nothing from `adapters` — must be written down to be self-policing.
  → applied (README, CONTRIBUTING, ADR 0001).
- **`docs/roadmap.md` with explicit status markers** keeps "you are here" visible.
  → applied.

Sources: README best-practice guides, Diátaxis, adr.github.io / MADR, Screaming
Architecture (R. C. Martin), hexagonal folder-structure write-ups, developer
onboarding guides.

## Gaps / caveats

- No authoritative *MCP-specific* folder standard exists; the layout is the
  general Python + hexagonal consensus applied to MCP.
- `.mcp.json` approval/tool-loading semantics are version-sensitive (Claude Code
  2.1.x) — verify against the installed version before relying on auto-approval.
- File-size norms (200–400 typical / 800 max) are a sensible convention, not a
  measured benchmark.
