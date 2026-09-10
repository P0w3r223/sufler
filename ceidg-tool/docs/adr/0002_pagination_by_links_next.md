# ADR-0002: Paginate by `links.next` with an opaque cursor in the checkpoint

Date: 2026-09-05
Status: accepted
Author: P0w3r223
Related to: ADR-0001, ADR-0004, docs/api_notes.md

---

## Context

Interrupted downloads must resume "without re-downloading saved records". The page
numbering start is unknown and the documentation contradicts itself (`page=1` in the
argument table, `page=0` in the example links). A whole-voivodeship download can take
hours while CEIDG data changes daily, so record order between pages is not stable.

## Options

**A. Page counter; checkpoint = number of the last completed page.**
Human-readable ("page 37 of 120"), easy to resume. Depends directly on the unknown
`page_start`; a wrong assumption silently skips or duplicates the first page, the
worst failure mode because it is symptomless. Effort S, risk medium.

**B. Follow `links.next`; checkpoint = URL of the next page.**
The server owns numbering, so the 0-vs-1 question disappears; keeps working if the
server changes its paging scheme. In the documented example (`count: 2`)
`next == self == last`, so a naive loop never ends; the URL must be validated (host
must equal `base_url`); the cursor is opaque to humans. Effort S/M, risk low with
termination guards.

**C. Keyset by `id`.**
Rejected on evidence: `/firmy` has no sort or cursor parameter.

## Decision

**B as the primary mode, A as a fallback selected by `profile.paging_mode`**, both
behind one generator `client.iter_pages()`. The checkpoint stores an opaque cursor
(`cursor_mode` + `cursor TEXT`), so switching modes does not change the schema.

Five termination guards, all of them, not "one of":

1. empty page: stop;
2. no `links.next`: stop;
3. `next` equals the current URL: stop;
4. `pages_done > ceil(count / limit) + 1`: stop with a warning in `Metadane`;
5. hard page cap from configuration (e.g. 10 000): `PagingRunawayError`.

The host in `links.next` must equal `base_url`, otherwise `UntrustedLinkError`:
the API might return test links in production responses (or vice versa), and the
Bearer token must never be sent to an arbitrary URL taken from a response body.

## Consequences

- Resume correctness rests on two levels: coarse (pages already done are not
  refetched) and fine (`INSERT ... ON CONFLICT(id) DO UPDATE`, so a repeated record is
  harmless). Server-side data shifts between sessions therefore do not corrupt the result.
- `count` from the first page is stored as `run.count_api`; on resume the current
  value is compared and drift is reported in `Metadane` instead of hidden.
- The first request sends no `page` at all (`send_page_on_first_request: false`);
  the server default page is correct by definition.
- Revisit: if the probe shows `links.next` disappears on the last page and numbering
  is unambiguous, A could replace B. Not worth it while B works.
