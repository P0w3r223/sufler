# 0057. Run model-written code in a separate container with no network

Date: 2026-08-05
Status: accepted
Author: P0w3r223
Related to: docs/adr/0018-agent-working-directory.md,
  docs/adr/0044-linux-container-deployment.md,
  docs/adr/0056-agent-system-prompt-two-blocks.md

---

## Context

The harness rebuild gives the agent a `Bash` tool. Adding it to the process as it stands would
put all three legs of the lethal trifecta in one context: **[A]** untrusted input (Teams
messages, attachments, note bodies, event payloads), **[B]** the division's data (notes, Jira),
and **[C]** an exit to the outside world (GitHub, file delivery, and — once a shell exists —
any request the model cares to compose).

An egress allowlist on the container was considered and rejected for two reasons, either of
which is sufficient on its own.

1. **It fails to separate what needs separating.** The application and the shell would share a
   network namespace, so no rule can distinguish "an adapter is calling Graph" from "the model
   is posting notes there".
2. **An allowlist is not a boundary.** A trusted, permitted destination still carries out data
   the attacker chose — a GitHub issue with the content pasted into it clears any allowlist.

Removing `curl` from the image changes nothing while Python is present.

## Decision

**Separate processes, not policies.** A dedicated executor container runs the same image as the
rest of the fleet (ADR 0044 — one image, many entrypoints) with `network_mode: none`.

| Container | Network | Mounts | What the model does there |
|---|---|---|---|
| `workmate-exec` | **none** | scratchpad rw, `/mnt/system` **ro**, `/mnt/user` rw, socket | runs arbitrary code |
| application doors | as today | as today + socket | acts through typed tools with a pre-bound target |

The `Bash` tool starts no local shell. It sends the command over a unix socket to the executor.
The protocol is deliberately trivial: one connection is one command, request and response are a
single JSON line each. No sessions, no state, no streaming — there is nothing to resynchronise
between the processes, and a dropped connection leaves nothing behind but a killed child.

The core sees only the outcome. `CommandRunner` (`core/ports/command.py`) maps a command string
to a `CommandResult`; the agent loop and the tool catalog know nothing of sockets or process
groups, and tests substitute a fake.

**The executor does not inspect command content.** Judging a shell string is unreliable;
the security here comes from what the container *lacks* — network, writable notes, secrets —
rather than from filtering.

### How the trifecta splits

| | [A] input | [B] data | [C] exit |
|---|---|---|---|
| executor | yes | yes | **no** |
| application | yes | yes | yes, but only actions computable with a target from outside the model |

The executor satisfies the rule of two. The application still holds all three legs, but the
model operates there as an Action-Selector — the most robust shape available. Freedom of
composition and the ability to reach outside stop occurring in the same place.

## Consequences

- **ADR 0018's read-only mount becomes a real boundary.** It had been a speed bump: the same
  note content was writable through `/app/data` in the same container. The note-writing command
  cannot live in the executor, which has the knowledge base read-only, so writing moves into the
  application as a typed action — and the executor is left with no writable path to the
  knowledge base at all. The `Bash` tool therefore cannot damage it, by construction.
- **`curl` leaves the image.** Under this split it grants nothing: the executor has no network
  and the application gives the model no shell. A tool that exists only because another control
  neutralises it becomes a capability on the day that control changes. When fetching resources
  is needed, it returns as a typed tool with an allowlist and content marked untrusted.
- **A socket volume mounted by exactly two services.** Socket file permissions are the only
  access control on the shell, so nothing else may mount that volume.
- **The executor does not inherit the fleet's compose template.** That template carries
  `env_file` with secrets, and the process that runs model-written code cannot see them. The
  cost is that any configuration the executor legitimately needs must be listed explicitly.
- **Output is capped at 64 KB with an explicit `truncated` flag.** A tool result returns to
  context and is re-sent on every later turn, so `cat` on a large file would be paid for until
  the conversation ends. The flag is separate from `exit_code` on purpose: without it the model
  would read a fragment as the whole. This cap is what makes context editing a prerequisite
  rather than a follow-up (ADR 0058).
- **A timeout kills the whole process group**, otherwise `sleep 999 &` survives the shell being
  killed. `timed_out` is likewise separate from `exit_code`.
- **Executor failure returns as a non-zero result, not an exception** — to the model it is an
  ordinary failed command to correct on the next turn.
- **Executor and client code are POSIX-only** (unix sockets, process groups). Their tests skip
  on the development machine and run in the image's `test` stage — the platform production runs
  on. That stage has already caught one defect Windows could not surface (`cwd` accepted without
  validating the fallback directory).
