# 0070 — Teams-only identity, and what a map entry actually grants

Date: 2026-09-04
Status: accepted
Author: P0w3r223 (owner decisions: 2026-09-03 — allow a Teams-only identity; 2026-09-04 —
accept that such an entry carries FULL membership, §3)
Related to: [ADR 0062](0062-note-read-authorization.md) (note read gate — the one this unblocks),
[ADR 0042](0042-meeting-note-sender-authorization.md) (meeting-note write gate — the template),
[ADR 0054](0054-reduce-jira-to-read-only-my-tasks.md) (Jira member lookup),
[ADR 0063](0063-shell-membership-gate-and-conversation-isolation.md) (shell authorization),
[ADR 0065](0065-mutable-knowledge-base-and-model-judged-writes.md) (note mutation),
[ADR 0066](0066-content-trust-classes-and-sticky-conversation-taint.md) (trust classes T0–T3),
[ADR 0048](0048-thread-note-capture-from-teams-mention.md) (the second consumer of the write gate),
[ADR 0035](0035-weekly-per-person-worklogpro-sheets-and-teams-dm.md) / [ADR 0036](0036-shift-worklog-integration-identity-and-week-contract.md) (identity directory),
[ADR 0067](0067-observability-audit-journal-and-notifier-dead-letter.md) (the audit journal the 62/62 reading comes from),
infra `docs/pozostale-do-zrobienia.md` §2.5 (the measurement that forced this)

---

## Context

`ENABLE_NOTE_READ_AUTHZ` (ADR 0062) has been **off since it was built**. The reason was never a
defect in the gate; it was the identity map.

`YamlIdentityDirectory` requires **both** `aad_user_id` and `jira_user` on every entry
(`adapters/outbound/graph_identity_directory.py:75`) and rejects a start-up where either identifier
is shared by two people (`_reject_shared_identifiers:96-106`). The division has three people. One
of them — Tadeusz Anonimowski — **has no Jira Cloud account at all**. He therefore cannot be
written into the map, and ADR 0062's gate is fail-closed on note reads. Turning the flag on would
have cut him off from the knowledge base entirely.

So the flag stayed off, and the cost is measurable rather than theoretical: on 2026-09-03,
**all 62 of 62 audit entries carried `trust_class: unknown`**. The T1/T2 split from ADR 0066 rides
behind the same authorizer, so with the flag off, the trust classification records nothing. Phase 3
of the rebuild plan lists "an entry in `audit.db` **with the turn's trust class**" as an acceptance
criterion — a criterion currently satisfied in form and not in fact.

### The thing that makes this a decision and not a config edit

It is tempting to read "make `jira_user` optional" as a small tolerance change that unblocks a read
gate. It is not, and the reason is that **the identity map is not a directory of Jira accounts. It
is the division's membership roster**, and six separate places read it:

| Consumer | ADR | What membership grants |
|---|---|---|
| `can_read_note` | 0062 | reads the whole division knowledge base |
| `can_write_meeting_note` | 0042 / 0048 | writes meeting notes — **two doors**: the `/notatka` command and the „zapisz to" thread capture (`wiring_routers.py:73` and `:138`), the latter enabled on the fleet |
| `can_use_shell` | 0063 | **runs code in the per-conversation executor** |
| `_build_mutation_identities` | 0065 | **edits existing notes** (deleting rides a second gate, `ENABLE_NOTE_DELETE`, absent from the fleet environment today) |
| Jira catalog | 0054 | the whole `Jira` tool — see §3 for what its absence costs |
| `sender_trust` | 0066 | classifies the turn as T1 rather than unknown |

`WORKMATE_ENABLE_SHELL=true` on the fleet. Adding a third row to a YAML file therefore hands that
person a shell and the ability to mutate institutional memory. Whether or not that is desirable, it
must not happen as a **side effect** of unblocking a read gate. A capability granted without a
recorded reason rots exactly the way a flag disabled without a recorded reason rots — the project has
already written that lesson down once, about `ENABLE_NOTE_DELETE`.

### What the consuming code already tolerates

Downstream of the loader, an absent Jira account is **already** handled.
`teams_graph/wiring_catalogs.py:103`
returns `person.jira_user if person and person.jira_user else None`, and `:116` returns an empty
catalog on the same condition; `tools/jira.py:224-229` answers a `member_*` call for an unresolvable
account with a substantive refusal. The strictness lives in exactly one place — the loader — and it
is strictness about **data shape**, not about safety.

## Decision

### 1. `jira_user` becomes optional; `aad_user_id` stays required

An entry with no `jira_user` is a person **who has no Jira account**, not a person whose account is
unknown. `aad_user_id` remains mandatory because it is the identifier that *addresses* the person:
every **authorization** gate above resolves the inbound Teams sender through `_by_aad`, and `_by_aad`
itself skips empty values (`graph_identity_directory.py:33`). The one path that does not is the Jira
`member_*` lookup, which resolves by display name (`teams_graph/wiring_catalogs.py:101-103`) — it
answers questions *about* people rather than authorizing the sender. An entry without `aad_user_id`
cannot authorize anything and is a configuration error, so start-up keeps failing on it.

### 2. Uniqueness stays, computed over non-empty values only

`_reject_shared_identifiers` continues to reject two people sharing an `aad_user_id` or a
`jira_user`. It must skip empty values: without that, the **second** person with no Jira account
would crash start-up with the duplicate-identifier error (`graph_identity_directory.py:101-105`;
its text is Polish, rendered here in English) — a fail-closed stop caused by two people both
correctly having nothing.

### 3. A map entry means MEMBERSHIP, with everything membership carries

This is the decision, and it is deliberate rather than incidental. Adding Tadeusz Anonimowski to the
map grants him knowledge-base reads, meeting-note writes through both doors, **the shell**, **note
editing**, and trust class T1. The owner chose full membership on 2026-09-04, having been shown this
table before choosing.

**Full membership is not, however, a full tool surface — and that asymmetry is part of the decision,
not a defect to fix later.** The Jira catalog factory returns an empty list for a person with no
`jira_user` (`teams_graph/wiring_catalogs.py:112-117`), so that person does not get a `Jira` tool
with nothing in it: they get **no `Jira` tool at all**. They therefore also lose `task`, `search` and
`member_tasks`/`member_history` **about other people** — capabilities that have nothing to do with
their own missing account. Their tool surface is permanently narrower than the other two members'.
This is a consequence of not having a Jira account, not of the map; naming it here keeps it from
being rediscovered as a bug.

The alternative — splitting the roster so that membership and shell access are separate lists — was
considered and rejected **for now**, on cost: it is a contract change across six consumers and deserves
its own ADR about a permission model, not a clause in this one. It stays on the table; §3 of this
ADR is what a future ADR would have to overturn, and it is stated plainly here so that overturning it
is a visible act.

### 4. Unverified accounts are NOT mapped

Making `jira_user` optional removes the *mechanical* obstacle that kept a second Teams account out of
the map. The map file (`/opt/sufler/config/identities.yaml` on the host, mounted read-only as
`/app/config/identities.yaml`; **it lives outside this repository and outside git**) records that
account under "not mapped" with the reason: *Piotr's `jira_user` is already assigned, and validation
forbids a duplicate.* After this ADR that sentence stops being true.

**The obstacle is gone; the reason to abstain is not — but it has to be stated correctly.** The AAD id
is known, so it did come from an observed message; what was never established is that the account
*belongs to Piotr*. The map's own comment says "**possible** 2nd account", and possible is not
confirmed. That is the real reason, and it is the one to write down.

We map only accounts whose owner is established, because a wrong entry does not merely fail — it
**grants one person's capabilities to another account**, and after this ADR that includes the shell.
The comment in the map must be rewritten to say this, rather than to cite a validation error that no
longer exists; otherwise it reads as a stale technicality and someone will "fix" it by mapping the
account.

### 5. Ordering

ADR → code → map entry → flag. The flag (`WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_READ_AUTHZ=true`) is an
operator action after the image ships, not part of the code change, and it must not precede the map
entry: the gate is fail-closed, so turning it on against an incomplete map removes reads from a
person who should have them.

**Read the two steps for what each one does, because they are not symmetric.** The step that GRANTS
capabilities is the *map entry*: the shell authorizer, the mutation identities and the meeting-note
authorizer all read the map directly and do not wait for `ENABLE_NOTE_READ_AUTHZ` — and note mutation
is already on across the fleet. The flag only closes the read gap and starts the T1/T2 split. An
operator who reads §5 alone would have it backwards, which is why §3 is where the grant is stated.

The Bot Framework door carries a twin flag (`WORKMATE_TEAMS_ENABLE_NOTE_READ_AUTHZ`) over its own map
path (`WORKMATE_TEAMS_IDENTITIES`). It is out of fleet scope (`docs/how-to/gate-matrix.md`), so this
ADR does not cover it — said here so the omission is deliberate rather than missed.

**Prose that must change together with the code**, because it freezes the rule being reversed:
the module docstring and format example in `adapters/outbound/graph_identity_directory.py:1-16`,
the `_load_map` docstring (`:55-58`, "a missing required field is a HARD start-up error"),
`core/domain/identity.py:19` and `:27`, the header of the map file itself, and the test
`tests/adapters/test_identity_directory.py:53-56`, whose name states the old requirement.

## Consequences

### What actually changes when the flag goes on

On this fleet, with `ENABLE_SHELL=true`, the typed knowledge-base read tools are **not** in the agent
catalog — the shell replaces them (`agent_wiring/__init__.py:180-186`). So the gate does not visibly
change note reads in the agent's tool loop. It changes:

- the `/szukaj`, `/projekty` and `/status <project>` commands, the brief and the digest. **Three
  commands, not two:** `_status` with an argument runs through `_read_authz_refusal`
  (`adapters/inbound/commands.py:191-200`) because a project status is a synthesis over division
  notes — the same content `/szukaj` returns. The comment at `commands.py:101-104` still says
  `/status` sits *outside* the gate; it is stale, and the first draft of this ADR repeated it. Both
  are corrected in the same change;
- **the T1/T2 split in the audit journal**, because `sender_trust` depends on the presence of the
  authorizer and *not* on the read tools being present. The code says so in as many words
  (`agent_wiring/__init__.py:498-506`): tying the split to `notes_read_gated` made it expire
  silently once the shell was on — that is, in the target configuration.

The acceptance criterion is therefore precise: **new** entries in `audit.db` stop carrying
`trust_class: unknown`. The existing 62 entries stay `unknown` — that is correct history, not a
backlog, and nothing should rewrite them.

### Known gap, deliberately left open

This is the *other* direction from §3 — not what the person without Jira loses, but what everyone
else is told when they ask **about** them.

`member_tasks` for a mapped person with no Jira account answers with the "unknown person" refusal,
because `resolve_member` collapses "not in the map" and "in the map, no Jira account" into a single
`str | None` (`teams_graph/wiring_catalogs.py:101-103`). The answer is safe and substantive but
**untrue in its stated reason**: that person is in the map, and after this ADR they are a full
member. Fixing it means changing a factory signature that `build_jira_catalog` consumes, which the
project's rule 6 requires be checked against every caller. It is a separate, later commit, written
here so it is a known gap rather than a discovered one.

### Risk accepted

A person with the shell can read the knowledge base through the read-only mount regardless of what
the read gate says (ADR 0062 §Decision 4 already names this). Enabling `note-read-authz` therefore
narrows the *typed* read paths and the *command* paths, not every path. The gate is a boundary for
the doors, not for the executor — claiming otherwise would be the same category of dead promise this
project keeps finding in tool descriptions.
