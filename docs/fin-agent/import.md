# Document import

Detail behind [`plan.md`](plan.md) §2. How arbitrary documents become ledger rows.

`plan.md` owns decisions. This file owns their shape. If the two disagree, `plan.md` wins.

---

## The split — buy the extraction, build the mapping

Two halves, and only one is ours:

| Half | Who does it |
|---|---|
| Get structured rows out of an arbitrary document | **A library.** Never write this |
| Map those rows onto *our* schema — our categories, our dedupe key | **Us.** ~50 lines. No library knows our data model |

`import_document(path, password=None)` is a thin adapter: hand the file to the extractor,
map what comes back, run the invariant, commit or explain.

**The interface is generic from day one.** "We only accept CSV" is never a thing the user is
told. An unsupported type gets a straight answer about what isn't wired up yet.

---

## Candidate extractors

Verified against the PyPI JSON API on 2026-09-07. All Python 3.14-compatible.

| Package | Version | Licence | Fit |
|---|---|---|---|
| `bankstatementparser` | 0.0.19 | Apache-2.0 OR MIT | Purpose-built. CSV, OFX/QFX, MT940, CAMT, digital + scanned PDF, LLM/vision fallback. **Ships the Golden Rule check and page provenance already** |
| `docling` | 2.126.0 | MIT (IBM) | General document→structure. Mature, strong on tables. Banking-agnostic — we'd map more |
| `monopoly-core` | 0.23.1 | **AGPL-3.0** | Bank-statement PDF→CSV. Viral licence: fine private, a problem if this is ever published or hosted |
| `markitdown` | 0.1.7 | MIT (Microsoft) | Fast and shallow. Clean digital PDFs only |

### Preferred: `bankstatementparser`

It is, almost exactly, the import pipeline this project previously planned to build by hand
— including the invariant below. The
[archived prior-art research](../archive/prior-art/research-2026-05-24.md) already
identified it as best-in-class and said to adopt its *shape*. Adopting the package is
strictly less work.

### Two risks, neither a reason to hand-roll

**Version 0.0.19.** Early. Expect API churn, possibly breaking. Mitigated by the mapping
layer being thin — swapping extractors later costs ~50 lines, not a rewrite. This is the
main argument for keeping the adapter boundary clean.

**Indian banks unverified.** Its named formats — CAMT, MT940, OFX, QFX — are European and US
banking standards. Whether it reads an HDFC, SBI or ICICI PDF is genuinely unknown, and the
answer comes from one real file, not from more reading. `docling` is the fallback: extract
tables, map them ourselves.

### Indian-bank wrinkle

Statement PDFs are usually password-protected — typically PAN plus date of birth. Whichever
extractor wins must accept a password, and the agent asks for it rather than failing with a
parse error.

---

## The spike — `plan.md` M7

Timeboxed to one evening. **The output is a decision, not code.**

1. Download one real CSV export and one real password-protected PDF statement from my own
   bank
2. Feed both to `bankstatementparser`. Does it read them? Are the amounts and dates right?
3. If not, same two files through `docling`
4. If neither works, search for an India-specific parser before writing anything

Record the answer here, then build M8 against the winner. Do not write mapping code before
this is settled — the mapping shape depends on what the extractor returns.

**Result:** _(unfilled — run the spike)_

---

## The invariant — non-negotiable

Extraction can be wrong, especially anything LLM- or OCR-assisted. Every import is checked
structurally **before a single row is committed**:

```
opening_balance + credits − debits == closing_balance
```

Result is `VERIFIED` / `DISCREPANCY` / `FAILED`. A failed check writes nothing and the agent
says why.

Every row carries:

- `source_method` — which extractor produced it
- `confidence` — extractor-reported, or 1.0 for user-typed rows
- `content_hash` — dedupe key, so re-importing an overlapping statement is safe

### Why it's not optional

Without it, a mis-parsed statement silently corrupts the ledger, and every projection
downstream is wrong with no signal. A wrong balance the user trusts is worse than no answer.

`bankstatementparser` implements this as its "Golden Rule" — if it wins the spike, this
comes free. If `docling` wins, it's ours to write. Either way it ships.

---

## Deferred handlers

Scanned PDF and photo/receipt import are parked — see [`parked.md`](parked.md). The
dispatcher has the slot; the handler lands when real usage produces a document the shipped
path can't read.
