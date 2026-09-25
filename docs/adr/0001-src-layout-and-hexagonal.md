# 0001. src-layout and hexagonal core/adapters structure

Date: 2026-07-07
Status: accepted
Author: P0w3r223
Related to: docs/explanation/architecture.md, roadmap_sufler.pdf

---

## Context

Sufler is a `uv`-managed, installable Python MCP server with a console entry
point. The roadmap mandates a "one core, many doors" architecture: an
interface-independent core, with cheap adapters (MCP now, Teams and GitHub
later). We need a repository layout that (a) is correct for an installable tool,
(b) makes the core/adapters boundary visible, and (c) survives adding Phase 2/3
doors without restructuring the core.

## Options considered

1. **Flat layout** (`sufler/` at repo root). Simple, common in small scripts.
   Risk: imports resolve against the working copy, hiding packaging errors;
   boundary not enforced by structure.
2. **src-layout + hexagonal** (`src/sufler/{core,adapters}`). Recommended by
   PyPA/pyOpenSci for installable packages; the official reference MCP servers
   use src-layout. Makes "core vs doors" legible from the tree.
3. **src-layout, flat internals** (all modules under `src/sufler/`). Correct
   packaging, but the architecture is not visible and the dependency rule has no
   structural anchor.

## Decision

Adopt **Option 2**: src-layout with an internal split into `core/`
(`domain/`, `ports/`, `application/`) and `adapters/` (`inbound/`, `outbound/`,
plus `teams/` and `github/` stubs). Enforce a one-way **dependency rule**:
`core` never imports from `sufler.adapters`.

## Consequences

- Core logic is testable in-memory against port protocols, without I/O or MCP.
- Swapping a data store or adding a door does not touch the core.
- Slightly more folders than an MVP strictly needs; justified by the roadmap's
  explicit multi-phase, multi-door trajectory.
- The dependency rule is a convention, not compiler-enforced; it must be
  respected in review (see CONTRIBUTING.md). An import-linter rule could enforce
  it later if drift appears.
