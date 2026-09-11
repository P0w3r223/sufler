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

Four documents, one per perspective of the first reconnaissance pass, all dated 2026-09-11:

- `ksef-dostep-i-obowiazek.md` — machine access, authentication, permissions, environments, limits,
  the obligation timeline and penalties
- `ksef-struktura-faktury.md` — the structured invoice, its versions and the traps in reading it
- `ksef-kontrola-faktury-kosztowej.md` — what the system guarantees, what the law still requires,
  what is good practice and what has become ceremony
- `ksef-ekosystem-narzedziowy.md` — libraries, official tooling, and what practitioners say is missing

## A caveat that applies to all four, without exception

**Every agent in the first pass worked with an exhausted search budget.** The upside is that the
material comes from the statute, the ministry's own documentation and live probes against the
interface rather than from advisory blogs. The cost is uniform and serious: **nobody looked at
practitioner experience from the first seven months of mandatory operation**, at tax rulings or at
case law. A second pass is running to close exactly that gap, plus three named factual questions.

## Shelf life

Shorter than usual. The interface version that raises the maximum query window reaches production
twelve days after these documents were written, and the obligation timeline has a further step on
1 January 2027. Treat every version number and date here as a measurement with a timestamp, not as a
standing fact.
