# KSeF: machine access to purchase invoices, and the state of the obligation

Date: 2026-09-11
Status: draft — first reconnaissance pass
Author: P0w3r223
Project: **faktury-ksef** (provisional) — see `../../README.md`. This is **not** `doc-extract`, **not** `krs-tool`, **not** `ceidg-tool`.
Related to: `ksef-struktura-faktury.md`, `ksef-kontrola-faktury-kosztowej.md`, `ksef-ekosystem-narzedziowy.md`

---

## The finding that shapes the product

**The duty to receive invoices started before the duty to issue them.** Issuing became compulsory on
2026-02-01 for taxpayers whose 2025 gross sales exceeded PLN 200 million and on 2026-04-01 for
everyone else. Receiving became compulsory for everyone on **2026-02-01** — the transitional
provisions defer only issuing, and there is no equivalent deferral for receipt.

So for any taxpayer, the stream of purchase invoices in the system is **complete from February
2026**, regardless of when that taxpayer started issuing. A backfill today reaches roughly seven
months back.

An invoice reaches the buyer automatically the moment it is assigned a number. There is no inbox, no
acceptance step and no consent — a point with consequences taken up in
`ksef-kontrola-faktury-kosztowej.md`.

## Obligation timeline, from the statute rather than from commentary

- **2026-02-01 to 2026-03-31**: taxpayers below the PLN 200 million threshold may still issue
  electronic or paper invoices. The construction is inverted — the duty caught everyone, and the
  smaller ones got the deferral.
- **2026-04-01 to 2026-12-31**: an exemption for invoices totalling **PLN 10 000 or less per month**.
  The right is lost **from the invoice that crosses the limit** — it is not a monthly allowance that
  resets.
- **2026-02-01 to 2026-12-31**: invoices from cash registers and receipts treated as invoices remain
  admissible.
- **2027-01-01**: the PLN 10 000 exemption expires, penalties enter into force, and payments must
  carry the KSeF number.

**Penalties** — up to **100% of the tax shown** on an invoice issued outside the system, or up to
**18.7% of the total amount** where no tax is shown. Payable within 14 days of the decision. The
statute expressly excludes the "instruction instead of a penalty" mechanism of the business law.
They enter into force **2027-01-01**.

The figure of a "minimum penalty of PLN 1 000" circulates widely and **is not in the published
text**. It was checked and is not repeated here.

## Authentication

Methods: qualified signature or seal (XAdES), KSeF token, trusted profile, KSeF certificate, Peppol
provider certificate.

Flow for an unattended integration:

1. request a challenge — valid **10 minutes**;
2. either sign an authorisation request in XAdES, or encrypt `{token}|{timestamp}` with RSA-OAEP
   SHA-256 under the ministry's public key; the challenge timestamp is the replay nonce;
3. poll the operation status — the documentation warns that on production, certificate revocation
   checking introduces delay, so this must be polling rather than a single check;
4. redeem for an **access token** (minutes, expiry in the token itself) and a **refresh token**
   (up to 7 days);
5. refresh to pick up the **current permission state**.

**A security trap worth knowing:** terminating a session invalidates the refresh token, but
**already-issued access tokens stay valid until they expire**. Revoking permissions does not kill a
live access token either. Plan for a window of some minutes during an incident.

## Token or certificate

A June 2026 consultation reversed the earlier direction: **tokens stay** as a login method beyond
2026, with 1-365 day validity and automatic renewal planned. An earlier note in this project
recommending certificates was based on an unconfirmed integrator question and is withdrawn.

For a read-only cost integration, a token carrying only the invoice-read permission is simpler: no
PKI, no certificate rotation. Both routes share one constraint — **issuing either requires
authenticating with a signature first.** Neither can be bootstrapped from the API alone.

Certificate limits, if that route is taken anyway: 300 applications and 100 active certificates per
tax number; 12 and 6 for a personal identifier or fingerprint.

## Permissions

Owner rights are **assigned systemically** to the taxpayer's own tax number — they are not granted.

The tool needs exactly one: **invoice read**. Not invoice write. An audit-oriented permission for
operation history also exists and is worth considering for the harvester's own logging.

An indirect model exists for accounting offices and shared service centres: a delegating entity may
grant read rights further, either for a named client or generally. That is the route if the tool ever
serves several clients.

## Environments

| Environment | Character |
|---|---|
| TEST | self-signed certificates accepted; data **not isolated** between integrators, so use random tax numbers; limits **10× production** |
| DEMO | production configuration, real credentials, **limits identical to production** |
| Production | full legal force |

**TEST has an API for fabricating entities, permissions and invoices.** A full scenario — a company,
its permissions, its invoices — can be stood up without a single contact with an office and without
touching real data. This removes the problem that blocked the register project, where the absence of
such an environment left nine assumptions unmeasured.

**Test on DEMO before production.** With limits ten times higher, TEST will not reveal a cadence
problem.

## Limits, and what they do to the architecture

Per pair of (context, IP address), sliding window, exceeded gives HTTP 429 with a dynamic retry
header. Higher at night.

| Operation | per second | per minute | per hour |
|---|---|---|---|
| invoice metadata query | 8 | 16 | **20** |
| initiate an export | 8 | 16 | **20** |
| check export status | 10 | 60 | 600 |
| fetch one invoice by number | 8 | 16 | **64** |

**The hourly limit is the bottleneck, not the per-second one.** Sixty-four single invoices per hour
makes invoice-by-invoice fetching architecturally untenable at any volume — package exports are the
only viable path. The operator may apply for higher limits with a justified request.

Content limits: 1 MB per invoice without an attachment, 3 MB with one, 10 000 invoices per session.

## Incremental download — the algorithm the ministry publishes

This is the heart of the harvester and it is specified rather than left to us:

- use **the permanent-storage date type only**; other date types give unpredictable synchronisation,
  because an invoice can appear "in the past" relative to its issue date;
- iterate **separately over each subject type**, since the same entity appears in different roles;
- keep two cursors between runs: the high-water mark (the point to which completeness is guaranteed)
  and the last permanent-storage date, used when a package was truncated;
- **do not send an end date** — the system builds the largest consistent package itself;
- deduplicate on KSeF numbers from the package manifest;
- cadence: at most 20 exports per hour overall, four per subject type recommended, **minimum 15
  minutes** between exports of one type.

## Encryption is mandatory

AES-256-CBC with PKCS#7 padding, key wrapped with RSA-OAEP under the ministry's public key, each
package part encrypted separately. Certificates are fetched from the interface and **rotation
handling is a requirement, not a nicety**: on emergency rotation the old key disappears immediately
and using its identifier returns a specific error, on which the implementation must reload the
certificate list and retry.

## Organisational steps before the first request

| # | Step | Note |
|---|---|---|
| 0 | Establish whether the company holds a **qualified seal** | If so, owner rights are automatic and the notification to the tax office is unnecessary. Shortest route |
| 1 | Otherwise file the notification of granting rights | The only step with real waiting time on the administration's side. Paper or the tax e-office; **filing through the older public administration platform has not counted as effective delivery since January 2026** |
| 2 | Grant the invoice-read permission | In the taxpayer application or through the API; the earlier certificate and permissions module was retired in February 2026 |
| 3 | Generate the machine credential | **Requires authenticating with a signature** — cannot be bootstrapped from the API |

For a sole trader step 1 does not apply.

If the company used the previous generation of the system: tokens from it are **not compatible**, and
employee permissions did **not** carry over, except those based on the filed notification and owner
rights.

## Version in flight

The interface version current at the time of writing reached the test environment in August 2026 and
**production on 2026-09-23** — twelve days after this document. It raises the maximum query window
from three months to a hundred days. A backfill written before that date must use windows of three
months or less.

## Open items

- The metadata model is not enumerated in the narrative documentation. Its fields decide how much
  analysis is possible without fetching and parsing the full document — the first thing to settle.
- Retention of export packages and the maximum page size were not found.
- The exact access-token lifetime is variable and must be read from the token, never hard-coded.
- No practitioner experience informed this document; see the caveat in `../../README.md`.
