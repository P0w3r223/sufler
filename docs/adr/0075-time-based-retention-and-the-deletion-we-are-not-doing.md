# 0075 — Time-based retention, and the deletion we are not doing

Date: 2026-09-14
Status: proposed — the windows (90 days of conversation content, 365 days of audit) are the owner's
decision of 2026-09-03; what this ADR adds is the **scope** (which stores, which columns) and the
**mechanism** (what a delete has to touch to actually be one). That part is what needs review.
Author: P0w3r223
Related to: [ADR 0014](0014-conversation-compaction.md) (summaries replace archived turns — the
first time we wrote that a transcript may stop being kept verbatim),
[ADR 0065](0065-mutable-knowledge-base-and-model-judged-writes.md) (snapshots, the reversibility this must not
break), [ADR 0067](0067-observability-audit-journal-and-notifier-dead-letter.md) (the audit journal
and its promise of a *longer* retention than conversations), and the package plan's Faza 7.

---

## Context

`conversations.db` keeps every turn **verbatim and indefinitely**. There is no pruning path in the
code: the only `DELETE FROM messages` in `SqliteConversations` runs once, during the foreign-key
migration, to clear rows orphaned before the constraint existed (`sqlite_conversations.py:218`).
`prune_stale` exists, but it deletes **shell scratch directories**, not rows — retention of content
has never been implemented.

The owner decided the windows on 2026-09-03: **90 days for conversation content, 365 days for the
audit journal.** ADR 0067 already promised the asymmetry in prose ("a different retention, longer
than conversations, Faza 7"); this ADR is where it becomes a contract.

Four things measured in the code, because each one changes what the implementation must do:

1. **`ON DELETE CASCADE` is real, and it is off by default.** `messages.conversation_id` and
   `conversation_summaries.conversation_id` both carry `REFERENCES conversations(id) ON DELETE
   CASCADE`. But SQLite enforces foreign keys **per connection**, defaulting to OFF, and
   `SqliteConversations` turns them on only at the end of `_init_schema`, after its own migration
   (`sqlite_conversations.py:194`, with the reason in the comment above it). A retention job that
   opens the file with a plain `sqlite3.connect` deletes the thread row, leaves every message, and
   reports success.
2. **The full-text index is a second copy of the text, and nothing maintains it on delete.**
   `messages_fts` is an external-content FTS5 table; rows are inserted by hand
   (`sqlite_conversations.py:320`) and there are **no triggers** — `grep TRIGGER` over the module
   returns nothing. Deleting a message therefore leaves its text in the index. Search will not show
   it, because the query inner-joins `messages` on `messages_fts.rowid`
   (`sqlite_conversations.py:506`) and the join drops orphans — so the index looks clean while the
   bytes are still in the file. A retention pass that measures itself with a search query would
   confirm its own failure as success.
3. **The person is not a column.** `conversations` is `(id, channel, external_id, status,
   created_at, updated_at, tainted, first_tainted_at, taint_source)`. `(channel, external_id)` names
   a **thread**, not a human; `messages.role` is `user`/`assistant`/`tool`. The only person-shaped key
   in any of these stores is `audit_tool_calls.actor_key` — and it is a pseudonym
   (`sha256(aad_id)[:16]`, unsalted, which is its own debt).
4. **Snapshots are knowledge-base content, not conversation content.** ADR 0065 snapshots live in
   `note_snapshots_dir` as `<note-id>/<timestamp>.md` (`filesystem_snapshots.py`) and hold the note's
   bytes before a mutation. Nothing prunes them either — but they age with the **note**, not with the
   conversation that touched it.

---

## Decision

**Retention is time-based and store-by-store. It is not identity-based, and this ADR says so out
loud rather than leaving the gap to be discovered.**

| Store | What it holds | Window | Clock |
|---|---|---|---|
| `conversations` + `messages` + `conversation_summaries` + `messages_fts` | verbatim turns | **90 days** | `conversations.updated_at` — the **thread's** clock, not the message's |
| `audit.db` → `audit_tool_calls` | pseudonymous per-call journal, no content | **365 days** | `occurred_at` |
| `inbound_dead_letters`, `dead_letters` | why a message was dropped: door, ids, sender, reason — no content | **90 days** | `failed_at` — closes R6 of [ADR 0067](0067-observability-audit-journal-and-notifier-dead-letter.md) |
| `events.db` → `events` | GitHub facts, public by origin | **kept** | — |
| `metrics_calls` | weekly counters keyed by pseudonym | **kept** | — |
| `thread_links` | thread ↔ issue mapping the bridge needs to answer at all | **kept** | — |
| note snapshots (ADR 0065) | verbatim note bytes | **out of scope, named below** | — |

**The window is measured on the thread, not on the turn.** Deleting by `messages.created_at` would
cut the beginning out of a live conversation and leave a transcript that starts mid-sentence —
worse than either keeping it or deleting it whole. A thread whose `updated_at` is older than the
window is deleted entirely, and `CASCADE` takes its messages and summaries with it.

**A delete is only a delete if it touches all four places.** The implementation must, in one
transaction per thread batch:

1. `PRAGMA foreign_keys = ON` on the connection it uses — or go through `SqliteConversations`,
   which has already done it. Not optional, not a nicety: without it the cascade silently does not
   happen.
2. Remove the FTS rows explicitly (`INSERT INTO messages_fts(messages_fts, rowid, text)
   VALUES('delete', rowid, text)`) for every message about to go, **before** the row disappears —
   the delete command for an external-content FTS5 table needs the old text.
3. Delete the `conversations` rows and let the cascade run.
4. `VACUUM` after the pass. Deleting rows returns pages to a free list inside the same file; the
   bytes are still there until the file is rewritten. A retention promise that leaves the content
   recoverable with a hex editor is a promise about the query planner, not about the data.

**Acceptance is a measurement, not a log line:** row counts per table before and after, plus a
check that **rows younger than the threshold are untouched** — the second half is the one that
catches an off-by-one in the window, and the first run goes against a **copy** of the state volume
(`tools/backup-state.sh`), never against production.

---

## What this does not decide — deleting a person

**"Delete everything about X" cannot be expressed as a query in these stores today.** It would need
all four of:

- reconstructing `actor_key` from the AAD identifier (possible — the pseudonym is unsalted, which is
  exactly why it is also a finding, not a feature);
- a mapping from person to thread that lives **outside** `conversations.db`, because that database
  has no person column at all;
- a rule for **group threads**, where one person's messages sit next to other people's in the same
  `conversation_id` — and `CASCADE` is keyed on `conversation_id`, so the cheap mechanism deletes
  either everyone in the thread or no one;
- a decision about the knowledge base, where a note written during a conversation outlives it by
  design.

**It also does not answer ADR 0067's open question about the salt.** That ADR left
`sha256(aad_id)[:16]` unsalted and marked the choice for Faza 7 review. Retention neither needs the
answer nor supplies it: a pseudonym that survives 365 days is exactly as re-identifiable as one that
survives an hour. The question stays open, and stays ADR 0067's.

That is a project with its own ADR, not a flag on this one. Naming it here is the point: Faza 7
delivers **time-based** retention, and anyone reading "we have retention" should be able to see, in
the same document, what kind we do not have.

**Snapshots are in the same category.** They hold note bytes, and the knowledge base itself has no
retention — notes live until someone deletes them. Pruning snapshots on the conversation clock would
create the state where the undo copy expires while the note it protects is still current, which is
precisely the reversibility ADR 0065 bought. They get their own horizon when the knowledge base gets
one.

---

## Consequences

- **The transcript stops being a permanent record**, which is the point, and the compaction of
  ADR 0014 stops being the only thing that ever shortens it.
- **Audit outlives content by a factor of four.** After 90 days we can still answer *who called what
  and when* (pseudonymously) but no longer *what was said*. That asymmetry is deliberate: the
  journal exists to reconstruct behaviour, not conversations.
- **Dead letters expire with the content they describe.** Keeping "message M from sender S failed"
  after M itself is gone leaves an identifier attached to a person for no operational gain.
- **The first pass is destructive and irreversible**, so it is gated on E1 (the state-volume copy)
  and runs on the copy first. This ADR does not authorise a production run; the package's plan does,
  after the copy is proven by a restore.
- **`VACUUM` needs the file quiet.** It rewrites the database, so the pass belongs next to a
  migration window, not to an arbitrary hour — and both doors must be down, not just one.

---

## Alternatives considered

- **Retention by `messages.created_at`.** Cheaper (no thread bookkeeping) and wrong: it truncates
  live threads from the front, and the agent's memory would silently start mid-conversation.
- **Soft delete (`archived` flag).** `messages.archived` already exists for compaction. Reusing it
  for retention would mean the bytes stay forever and the promise becomes "we hide old content",
  which is not what was decided — and the FTS copy would remain regardless.
- **Deleting rows and skipping `VACUUM`.** Faster, and it would let us claim retention while the
  content is still in the file's free pages. Rejected for the same reason the FTS step is mandatory:
  the promise is about bytes, not about query results.
- **One window for everything.** Simple to explain, but it forces the audit journal down to 90 days
  (losing the ability to reconstruct behaviour across a quarter) or pushes conversations up to 365
  (keeping content four times longer than decided).

---

## Risks

- **The FTS step is the fragile one.** It is manual in insert (line 320) and would be manual in
  delete; the guard against drift is a test that deletes a message and then asserts the FTS table
  no longer matches its text — asserting on `search()` results would pass on the broken version,
  because the inner join hides orphans.
- **`updated_at` is only as good as its writers.** It is set on append and on close
  (`sqlite_conversations.py:278`, `:324`); a future write path that forgets it would make a live
  thread look stale and delete it. The pass should log the threads it selects before deleting them,
  at least for the first runs.
- **A 90-day window on a fleet with six turns per month means the first pass may delete almost
  nothing — or almost everything.** Either outcome is evidence about adoption, not about the code,
  and should be read as such rather than as a failed run.
