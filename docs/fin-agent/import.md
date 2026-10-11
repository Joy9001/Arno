# Import

This file holds the detail behind [`plan.md`](plan.md) §2: how statements, photos, and voice
notes become ledger rows—and how imported rows reconcile with what was already logged by chat,
voice, or photo.

The `plan.md` file owns decisions, and this file owns their shape. If the two disagree, the plan
wins and this file is stale.

---

## The split: Buy the extraction, build the mapping

The following table shows who does each half of an import:

| Half | Who does it |
|---|---|
| Get structured rows out of a document | A library for each bank where one exists; a generic fallback otherwise |
| Map rows onto the Fin-Agent schema, check them, reconcile them | Fin-Agent's own code. No library knows its data model |

**The interface is generic.** "We only accept CSV" is never a thing the user is told. An
unsupported file gets a straight answer about what isn't wired up—and the generic path tries.

---

## The pipeline

```
upload ─> detect ─> parse ─> invariant ─> dedupe ─> reconcile ─> review screen ─> commit
           │         │                                                   │
   password needed?  per-bank parser, else generic fallback        nothing is written
   → ask, retry                                                     before this step
```

1. **Detect.** Find which parser claims the file. The `pypdfium2` library opens
   password-protected PDFs; on a password error, the agent asks, the user answers, and the agent
   retries. Passwords are not stored—statements arrive monthly, so asking is cheap.
2. **Parse.** Use the registered parser for that bank, or the generic fallback.
3. **Invariant.** Check the balances (described later). A failure writes nothing and says why.
4. **Dedupe.** Match against earlier imports by `content_hash`.
5. **Reconcile.** Match against rows logged by chat, voice, or photo (described later).
6. **Review screen.** The web app shows new rows, merged rows, ambiguous matches, and categories.
   The user commits, edits, or rejects.
7. **Commit.** Write the rows, update the account's `balance` and `balance_as_of` fields from the
   closing balance, and recompute baselines.

### The generic fallback

The generic fallback covers any bank without a parser—Union Bank, very likely, and every bank a
self-hoster brings. It runs in three steps:

1. Use a permissive library to extract tables or text (`pypdfium2` text, or `docling` if a spike
   shows it's needed and its size is acceptable in the image).
2. An _isolated, tool-less_ model call maps columns to a typed schema
   (`date, description, debit, credit, balance`)—rule 2 in `plan.md` §4.
3. The invariant decides whether the result is trusted. It almost always catches a bad mapping
   because running balances don't add up.

A generic-path success can be saved as a column mapping for that bank, so the next import of the
same layout skips the LLM.

---

## Parser candidates

The following table lists the parser candidates, checked against PyPI on October 7, 2026:

| Package | Version | License | What it covers |
|---|---|---|---|
| `xfina` | 0.8.1 (Oct 2, 2026) | Apache-2.0 | Written in Rust (PyO3). Indian: SBI (PDF, password), HDFC, ICICI, Axis, Bank of Baroda (XLS); HDFC, ICICI, Axis cards; CAMS. ReBIT-shaped output. Three GitHub stars; classifiers stop at 3.13—check for a 3.14 wheel |
| `bankstatementparser` | 0.0.20 (Sep 25, 2026) | Apache-2.0 OR MIT | CSV, OFX and QFX, MT940, CAMT, PDF with an LLM and vision fallback, Golden Rule check built in. No Indian banks named. Pulls LiteLLM |
| `casparser` | 1.4.1 | MIT | CAMS, KFintech, NSDL, and CDSL _Consolidated Account Statement_ (_CAS_) PDFs. Built on `pypdfium2`, no GPL dependencies. Parked with statement-driven net worth ([`parked.md`](parked.md)) |
| `docling` | 2.x | MIT | General document → tables. Strong but heavy (ML models)—fallback candidate only |
| `monopoly-core` | Not recorded | AGPL-3.0 | Excluded by the license decision in `plan.md` §1 |

My banks: SBI (the `xfina` parser claims it), Union Bank of India (no parser found; PDFs are
password protected—typically the first four letters of the name plus DDMM of birth), and the
Slice credit card (export format unknown).

---

## The spike: Milestone 21 in the plan

Timeboxed to one or two evenings. _The output is a decision for each bank, not code._

1. Download 12 months of real statements: SBI, Union Bank, Slice.
2. SBI through `xfina`. Amounts, dates, balances right?
3. Union Bank and Slice through `xfina` and `bankstatementparser`; if neither, the generic fallback
   by hand (text extraction + one model call) to see if the invariant passes.
4. Record for each bank: parser, format to download, password pattern, quirks.

Real statements never enter the repository. Parser tests use synthetic fixtures; a git-ignored
local folder holds real files for manual runs.

**Result:** _(unfilled—run the spike)_

---

## The invariant: Non-negotiable

Extraction can be wrong, especially anything LLM- or OCR-assisted. Every import is checked
_before a single row is committed_:

```
opening_balance + credits − debits == closing_balance
```

Plus, where the statement has a running balance, every row's balance must follow from the
previous one. The result is `VERIFIED`, `DISCREPANCY`, or `FAILED`. Only a `VERIFIED` import can
be committed.

A wrong balance the user trusts is worse than no answer: a mis-parsed statement silently corrupts
the ledger, and every projection downstream is wrong with no signal.

---

## Reconciliation: Logged rows versus statement rows

Logging "spent 450 on swiggy" by chat, or sharing its UPI screenshot, and later importing the
statement must not double-count.

The following table shows the three match outcomes for a statement row:

| Match | Rule |
|---|---|
| Certain | Same UPI reference number. Otherwise: same account (or the logged row had none), same amount, date within ±3 days, one candidate |
| Ambiguous | Several candidates, or amount within a small tolerance—shown on the review screen |
| None | New row from the statement |

On a merge, the _statement wins on amount, date, and account_; the logged row keeps its
category, notes, claims (splits), photo, and link to the conversation. Logged rows still unmatched
after a statement covering their date appear on the review screen as "logged but not on the
statement"—wrong account, cash, or a mistake.

---

## Photos and voice

**Photos.** A receipt photo or a UPI payment screenshot (GPay, PhonePe, Paytm) → isolated vision
call (the `VISION_*` endpoint) → typed `{merchant, amount, date, method, upi_ref}` → a review card
in chat → one transaction, image kept as an attachment. On Android, the installed app is a share
target, so a screenshot goes straight from the UPI app to Fin-Agent. The UPI reference number is
what later matches the row to its statement line. Line items are not extracted.

**Voice.** Push-to-talk audio → the `STT_*` endpoint (a local `speaches` server or a cloud API) →
text → the normal chat path. Hinglish accuracy is an eval category (L7).

---

## Investments

Parked. Asset accounts take hand-updated balances; CAS import and _net asset value_ (_NAV_)
refresh come back with statement-driven net worth ([`parked.md`](parked.md)).
