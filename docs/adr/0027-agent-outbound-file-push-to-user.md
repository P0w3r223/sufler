# 0027. Agent sends images/files to a user on Teams (outbound attachments)

Date: 2026-07-17
Status: proposed
Author: P0w3r223
Related to: docs/adr/0026-agent-file-reply-in-thread.md, docs/adr/0016-user-multimodal-attachments.md,
  docs/adr/0022-proactive-dual-target-teams-push.md, docs/adr/0015-teams-delegated-graph-polling.md,
  docs/adr/0006-write-capability-gate-2.md

---

> **Update 2026-07-27:** Admin consent for `Files.ReadWrite.All` has since been granted — the write
> scope is available. The file variant is **no longer scope-blocked** (only `TeamsFileSender` + the
> outbound-attachment build remains); the images-only variant was already deliverable. The rest of
> this decision stands.

## Context

Inbound multimodal attachments exist (ADR 0016): a user uploads an image or document and the agent reads
it. The mirror image does not exist — the agent cannot **send** an image or file to a user. Product wants
this: the agent produces a chart/screenshot/rendered document and delivers it to the person it is talking
to on Teams.

This shares its core with ADR 0026: both need the **outbound Graph attachment** primitive (upload bytes +
reference them in a message). They differ only in the destination and what wraps the primitive:

| | Destination | Target source | Extra over the primitive |
|---|---|---|---|
| 0026 (file reply) | the current **thread** | `external_id` (trusted, already in the seam) | format rendering |
| 0027 (file push) | the current message's **sender** (1:1) | `sender_id` — present on the message but **dropped** in the seam | anti-exfiltration target binding |

`ChannelMessage.sender_id` carries the AAD id of the sender (`selection.py`), but `handler.py` forwards
only `sender_name` into `InboundMessage`, so the id is lost before it reaches the runtime. Delivering to a
person is a *mutating outward action to an individual*, so target selection is the security core.

Hard constraints: `core ↛ adapters`; frozen MCP surface untouched (rides `extra_catalog` / a per-turn
factory); per-door write gate (ADR 0006); sync tool dispatch → sync Graph client.

## Options considered

- **C1 (chosen).** A scoped `send_file_to_user` tool, **pre-bound to the sender of the current message**.
  Propagate `sender_id` through the seam (additive: `InboundMessage.sender_id`, filled in `handler.py`
  from `ChannelMessage.sender_id`); the per-turn factory binds `target = sender`, so the model never
  supplies an AAD id — it cannot spam or exfiltrate to an arbitrary person (the same discipline as the
  pre-bound issue number in `reply_on_thread`). Split delivery: **images → hostedContents inline** (no new
  scope), **files → OneDrive/SharePoint** (write scope, shared with ADR 0026), reusing `TeamsFileSender`.
- **C2.** A static allowlist of recipients in config; the model picks a recipient from it. More flexible
  (send to someone other than the sender) but a larger abuse/config surface. Deferred to a future
  multi-user iteration; for the pilot, binding to the sender is safer.

## Decision

Adopt **C1**, gated behind `enable_user_file_push` (default OFF). Deliver the **images-only** variant
first — it rides hostedContents inline and needs **no new Graph scope**, so it is a fast, low-risk pilot of
"the agent sends back an image". The **file** variant depends on the `Files.ReadWrite.All` write scope
introduced by ADR 0026 (admin consent + device-code re-consent) and lands with it. The MCP golden surface
is untouched. This ADR amends ADR 0022 (the proactive/outbound Teams surface now includes attachments to a
user).

## Threat model

- **Outward push to a person.** The risk is spam / exfiltration to an individual. Binding the target to
  the *current sender* (model cannot name a recipient) is the primary control — the destination is always
  the person who just messaged the agent, never an arbitrary AAD id.
- **Compensating controls (real, not a boundary):** gate OFF by default; images-only variant carries no
  new scope; the file variant inherits ADR 0026's pre-bound, create-only, length-bounded discipline.
- **Enabling condition:** turn `enable_user_file_push` ON only for a trusted team; ship images-only first,
  add files after the write-scope consent and validation.

## Consequences

- `sender_id` propagation is additive and backward-compatible (`selection.py` already parses it →
  `handler.py` → `InboundMessage`); nothing that ignores the new field breaks.
- `core/ports/notifications.py` gains an attachment method; `graph_teams_notifier.py` gains the 1:1
  file/image send; `config.py` gains the gate. Reuses `TeamsFileSender` from ADR 0026 — no second
  outbound-attachment primitive.
- Medium blast radius; images-only variant is deliverable without the write-scope blocker, so it can ship
  ahead of ADR 0026's file path.
- Reversible: `enable_user_file_push` defaults to today's no-outbound-attachment behavior.
