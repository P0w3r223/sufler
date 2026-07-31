# 0018 — Agent working directory: create_file on the delegated Teams door (Gate for a second mutating tool)

Date: 2026-07-13
Status: accepted (implemented — `WorkspaceSettings`/`WORKMATE_ENABLE_WORKSPACE`, `config.py`)
Author: P0w3r223
Amends: [ADR 0002](0002-read-only-first.md) (read-only default), [ADR 0006](0006-write-capability-gate-2.md)
  (adopts the arbitrary-file Option 2 it rejected; opens write on Teams it pinned `false`),
  [ADR 0015](0015-teams-delegated-graph-polling.md) (workspace-write on a read-only door)
Related to: [ADR 0016](0016-user-multimodal-attachments.md) (attachments feed the file content)

---

## Context

The owner wants the agent to **create files/artifacts** (draft `.md`/`.txt`/CSV, reports) from the
content of the multiple attachments already materialized in a turn (ADR 0016), working in a
dedicated **working directory outside `data/`**, and to **send the finished file back to the Teams
channel** as a downloadable attachment. The capability is enabled on the delegated Teams door
(`teams_graph`).

This is a **second mutating tool** (ADR 0002/0006 Gate). It deliberately adopts the arbitrary-file
"Option 2" that ADR 0006 rejected as too broad — but confined to a **non-knowledge-base scratch
area**, not `data/`. It also amends ADR 0006/0015, which pinned Teams to `enable_write=false`
(less-trusted door, signed-in-user identity, untrusted channel content). Multiple files per message
already work (ADR 0016, up to 5) — no change needed there.

The design is split into two decisions with different permission/risk profiles so each is a
conscious checkpoint.

## Decision I — Working directory + file tools + `enable_workspace` gate

Add three agent tools — **`create_file` (mutating, create-only) + `read_file` + `list_files`
(read-only)** — behind a **new gate `enable_workspace`, independent of `enable_write`** (notes gate,
ADR 0006). Different capability, different trust profile (scratch vs. knowledge base), so the gates
must be separate; enabling workspace on Teams must NOT switch on `save_note` there.

Envelope (mirrors the `save_note` safety patterns):

- **Separate ports.** New `WorkspaceRepository` (read: `list`/`read`) and `WorkspaceWriter`
  (`exists`/`create`), split like `NotesRepository`/`NotesWriter` so the read side stays visibly
  read-only. `WorkspaceService`/`WorkspaceWriteService` depend only on ports (`core ↛ adapters`).
- **Per-conversation isolation, scope never from the model.** Workspace layout
  `…/workspace/<channel>/<sha256(conversation_id)[:16..32]>/`. The scope is built from the **trusted**
  `conversation_id` (`{team}/{channel}/{root}`) by the responder and **closed over** the tool
  functions — it is not a model argument, so the model cannot reach another conversation's files.
  The runtime gains an additive `run_turn(..., extra_tools=())` merged per turn.
- **Create-only, atomic.** `os.link` (temp + link), never overwrites; collision appends `-2/-3`.
  No edit/delete in v1 (out of scope, same discipline ADR 0006 set for notes).
- **Path-traversal safety.** Filename from the model → `slugify` (`[a-z0-9-]`) + extension
  whitelist; every write and read resolves under the workspace root (`resolve().relative_to`), so
  `read_file` can never escape to `.env`/token cache.
- **Extension whitelist (text only).** Default `md/txt/csv/json`; executables/scripts explicitly
  rejected — files are authored by an untrusted model from an untrusted door.
- **Quotas (DoS).** Per-file size, per-conversation file count and total bytes; control-char guard
  (`reject_dangerous_content`) on content.
- **Outside repo and `data/`.** Default `~/.workmate/workspace/` (like the tokens file and
  conversations DB), so a poisoned artifact never pollutes the knowledge base the agent reads.
- **MCP surface unchanged.** Workspace tools are built by a separate `build_workspace_catalog` used
  ONLY by the agent runtime, never `build_server` — the golden MCP surface test stays green. The
  agent-catalog surface gets its own tests as a conscious second-mutating-tool checkpoint.

## Decision II — File delivery: upload to the channel's SharePoint (new write scope)

The workspace lives on the firewalled poller host, so a pure server scratch has limited end-user
value. **Chosen: upload the created file to the channel's SharePoint document library and post a
message referencing it**, so the Teams user gets a real downloadable file (rejected alternative:
inline-text-only, which delivers copy-pasteable content but no file object).

Permission cost (owner-accepted): a delegated **write** scope — `Files.ReadWrite.All` (or
`Sites.ReadWrite.All`) added to `_DEFAULT_TEAMS_GRAPH_SCOPES`, requiring **admin consent** and a
**one-time device-code re-consent** (same mechanics as ADR 0016's read scopes). Files are written to
SharePoint **as the signed-in owner**. This is an escalation over ADR 0016's read-only file scopes
and is recorded here as its own decision.

Flow: `GET /teams/{team}/channels/{channel}/filesFolder` → driveItem; `PUT …/content` (<4 MB) or an
upload session (larger); then post a channel reply whose `attachments[]` references the driveItem
(`contentType: "reference"`) with a matching `<attachment>` in the body. Upload failure degrades to
a text reply (never crashes the poller).

## Consequences

- Read-only-first (ADR 0002) is amended for a **second** mutating tool; arbitrary-file scope
  (ADR 0006 Option 2) is deliberately adopted, confined to non-knowledge-base scratch. Teams-read-only
  (ADR 0015) is amended: `enable_workspace=True` on `teams_graph` while `enable_write` stays `False`.
- **`enable_workspace` is a second, independent write-gate** distinct from `enable_write`
  (documented in `CLAUDE.md`). Default off (`WORKMATE_ENABLE_WORKSPACE`, env-gated rollout).
- Any **further** mutating capability (edit/delete of workspace files) needs its own ADR — same
  discipline ADR 0006 set.
- Isolation becomes a real per-conversation boundary in the runtime (`extra_tools`/scope injection),
  the foundation the future multi-user Bot Framework door will need.
- New write scope `Files.ReadWrite.All` broadens the Entra grant; SharePoint artifacts are owned by
  the signed-in account. Revisit when the door goes multi-user, or when a narrower per-site grant
  (`Sites.Selected`, app-permission) becomes viable.
