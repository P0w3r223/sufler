# Alert content is public by default, and exceptions that know better say so

Date: 2026-09-04
Status: accepted
Author: P0w3r223
Related to: ADR 0003 (expiry requires evidence — `ReadOutcome` as the shape for "what we may
assert"), `runtime/operator.py`, `graph/auth.py`, `alerts.py`, `runtime/etykiety.py`

---

## Context

The alert webhook is the only channel that is **independent of Microsoft Graph**. That
independence is the whole point: the most important alert — "the session is gone" — is produced
at the moment the Graph token stopped working, so Teams is no longer a channel it can travel on.

The consequence is rarely spelled out: the webhook is therefore, by construction, **outside the
organisation's identity boundary**. In the installation this project runs in, it is a Discord
channel. Whatever we put in an alert body leaves the tenant, is retained by a third party
indefinitely, and is searchable by everyone with access to that channel.

Today `runtime/operator.py:119` sends `str(blad)` verbatim:

```python
alert(settings, "Utracono uwierzytelnienie", str(blad), waga=alerts.KRYTYCZNY)
```

For almost every exception that is correct and useful — an HTTP status, a timeout, a Graph error
code. For one exception it is not. `graph/auth.py:_jedyne_konto` builds its message by joining the
**account names from the MSAL token cache**:

```python
nazwy = ", ".join(sorted(str(a.get("username", "?")) for a in accounts))
raise AmbiguousAccountError(f"Cache tokenu zawiera {len(accounts)} kont ({nazwy}) — …")
```

Those are work e-mail addresses. They reach the webhook whole. The same text also reaches
`service.py` ("Przebieg powiadomień nie powiódł się") and `listener.py` ("Nieudany zapis grafiku
po potwierdzeniu"), because both interpolate the exception into the alert body.

The project already reasons about this class of problem elsewhere and reached the opposite
default there. `Settings.loguj_nazwiska` is `false` by default, and `runtime/etykiety.py` renders
an employee as an AAD id rather than a name, with the rationale written down: *an alert stays in
the Teams channel indefinitely and is searchable, while container logs rotate*. Alert bodies were
simply never held to that standard.

## Decision

**Alert content stays public by default. An exception that knows it carries personal data
declares a redacted alternative, and the alert layer prefers it.**

Concretely:

- An exception may carry a `publiczny` attribute: a message safe to leave the installation.
- `runtime/operator._tresc_publiczna(blad)` returns `publiczny` when present, otherwise
  `str(blad)` unchanged.
- Every alert that interpolates an exception goes through it: `zglos_utrate_sesji`,
  the failed-run alert in `runtime/service.py`, and the failed-write alert in `runtime/listener.py`.
- `AmbiguousAccountError` sets `publiczny` at the raise site: the **count** of accounts and the
  cache path to delete, without the names.
- **The log keeps the full text.** Logs rotate, live on the host, and are what the operator reads
  when they already have access to the machine. The redaction is about the channel, not the fact.

### Why opt-in and not a blanket filter

The tempting shape is to strip every alert body to a fixed sentence and tell the operator to read
the log. That trades a leak for silence, and silence is the failure mode this project fights
everywhere else: the weekly summary exists because *no message* is indistinguishable from *nothing
happened*. An alert that says only "something failed, go read the log" is a notification that the
operator learns to ignore, and in an installation without monitoring the webhook is the **only**
channel they have.

Opt-in also puts the knowledge where it belongs. `operator.py` cannot tell whether a `RuntimeError`
holds an e-mail address; `_jedyne_konto` knows exactly what it just interpolated. The attribute
travels with the exception that created the risk.

### Why an attribute and not a subclass

A `PublicMessageError` base class would force every raise site to choose a hierarchy position,
and `AmbiguousAccountError` already has one it must keep (it inherits `AuthExpiredError`, which is
what makes the service stop). An attribute composes with any hierarchy and is invisible to code
that does not look for it — which is the majority of raise sites, correctly.

## Consequences

- One alert body changes shape: the ambiguous-account alert loses the account names and keeps the
  count plus the cache path. The instruction it carries ("delete this file, then `--login`") is
  unchanged, so the operator's next action is unchanged.
- The `publiczny` attribute is a convention, not a type. Nothing enforces that a new exception
  carrying personal data declares one. This is a known limit: the mechanism reduces the blast
  radius of the case we found, it does not prove the absence of others.
- Diagnostics do not regress for any other exception, because the default is unchanged.

## What this ADR does not decide

Whether the webhook should be inside the organisation at all. That is a deployment choice made in
`env`, and the argument for keeping it independent of Graph (above) is strong enough that moving
it inside Microsoft would cost more than it saves. This ADR assumes the channel is external and
makes the content safe for that assumption.
