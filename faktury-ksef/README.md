# faktury-ksef — reconnaissance for a planned cost-invoice analysis tool

Date: 2026-09-11
Status: **research only — no code, no decision, provisional name**
Author: P0w3r223

---

## Read this before anything in `docs/research/`

This directory holds the evidence base for a **planned** tool that would pull cost invoices from the
national e-invoicing system, verify them, categorise them and report on spending. Nothing here is a
decision, and no architecture has been chosen. The directory name is **provisional** and will change
when the project is named.

### What this is NOT — the three things it could be mistaken for

| Not this | Where it actually lives | How it differs |
|---|---|---|
| **`doc-extract`** | separate repository, `P0w3r223/doc-extract` | Extracts FA(3) fields **from documents** using a model, and uses the invoice's own arithmetic as a label-free error detector. **Owner's decision of 2026-09-11: it stays a separate project and is not touched by this one.** Consequence accepted knowingly: this project will need its own FA(3) model and its own invariant checks |
| **`krs-tool`** | `krs-tool/` in this repository | Company risk signals from the court register. Different register, different act, different population |
| **`ceidg-tool`** | `ceidg-tool/` in this repository | Sole-trader records from the business-activity register. Its `docs/research/` holds the **KRS** evidence base, which is a different subject entirely |

Confusing these is easy and the cost is real: all three touch Polish public registers, two of them
touch the FA(3) schema, and the register research already sits one directory away.

## What is in `docs/research/`

Six documents from two reconnaissance passes — eight agent runs in total — all dated 2026-09-11.

First pass, one document per perspective:

- `ksef-dostep-i-obowiazek.md` — machine access, authentication, permissions, environments, limits,
  the obligation timeline and penalties
- `ksef-struktura-faktury.md` — the structured invoice, its versions and the traps in reading it
- `ksef-kontrola-faktury-kosztowej.md` — what the system guarantees, what the law still requires,
  what is good practice and what has become ceremony
- `ksef-ekosystem-narzedziowy.md` — libraries, official tooling, and what practitioners say is missing

Second pass, closing named gaps:

- `ksef-metadane-faktury.md` — the metadata model field by field, and the resolved question of
  whether the source can filter purchase invoices for us
- `ksef-praktyka-wdrozeniowa.md` — what actually broke between February and September 2026

## The caveat, and what the second pass did to it

**Every agent in the first pass worked with an exhausted search budget.** The upside was that the
material came from the statute, the ministry's own documentation and live probes rather than from
advisory blogs. The cost was uniform: nobody saw practitioner experience, tax rulings or case law.

`ksef-praktyka-wdrozeniowa.md` closes that gap. It is worth reading first if you only read one, and
it changed three things the first pass had wrong or missing: the system is **not** a complete cost
repository, the document carries **nothing** usable for routing, and an export once declared it had
not truncated while dropping data.

**One pass-1 claim was withdrawn outright.** The ecosystem document reported that purchase invoices
could not be filtered at the source. They can; the evidence is in the ministry's own test code, and
the correction is recorded in both affected documents rather than quietly edited away.

## Shelf life

Shorter than usual. The interface version that raises the maximum query window reaches production
twelve days after these documents were written, and the obligation timeline has a further step on
1 January 2027. Treat every version number and date here as a measurement with a timestamp, not as a
standing fact.

## What is still unmeasured, and can be

Seven questions remain, and unlike the register project **all of them are answerable by measurement**
— the test environment can fabricate an entity, its permissions and its invoices. The list is at the
end of `ksef-metadane-faktury.md`. Nothing here should be treated as settled until that run happens.

## Shelf life

Shorter than usual. The interface version that raises the maximum query window reaches production
twelve days after these documents were written, and the obligation timeline has a further step on
1 January 2027. Treat every version number and date here as a measurement with a timestamp, not as a
standing fact.
