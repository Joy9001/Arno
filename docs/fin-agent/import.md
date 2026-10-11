# Import

Detail behind [`plan.md`](plan.md) §2: how statements, photos and voice notes become ledger
rows — and how imported rows reconcile with what was already logged by chat, voice or photo.

`plan.md` owns decisions. This file owns their shape. If the two disagree, `plan.md` wins.

---

## The split — buy the extraction, build the mapping

| Half | Who does it |
|---|---|
| Get structured rows out of a document | **A library**, per bank where one exists; a generic fallback otherwise |
| Map rows onto *our* schema, check them, reconcile them | **Us.** No library knows our data model |

**The interface is generic.** "We only accept CSV" is never a thing the user is told. An
unsupported file gets a straight answer about what isn't wired up — and the generic path tries.

---

## The pipeline

```
upload ─> detect ─> parse ─> invariant ─> dedupe ─> reconcile ─> review screen ─> commit
           │         │                                                   │
   password needed?  per-bank parser, else generic fallback        nothing is written
   → ask, retry                                                     before this step
```

1. **Detect** which parser claims the file. Password-protected PDFs are opened with `pypdfium2`;
   on a password error the agent asks, the user answers, it retries. Passwords are not stored —
   statements arrive monthly, asking is cheap
2. **Parse** with the registered per-bank parser, or the generic fallback
3. **Invariant** (below). A failure writes nothing and says why
4. **Dedupe** against earlier imports by `content_hash`
5. **Reconcile** against rows logged by chat, voice or photo (below)
6. **Review screen** in the web app: new rows, merged rows, ambiguous matches, categories. The user
   commits, edits, or rejects
7. **Commit** writes rows, updates the account's `balance` and `balance_as_of` from the closing
   balance, and recomputes baselines

### The generic fallback

For any bank without a parser — Union Bank, very likely, and every bank a self-hoster brings:

1. Extract tables or text with a permissive library (`pypdfium2` text, or `docling` if a spike
   shows it's needed and its size is acceptable in the image)
2. An **isolated, tool-less** model call maps columns to a typed schema
   (`date, description, debit, credit, balance`) — `plan.md` §4 rule 2
3. The invariant decides whether the result is trusted. It almost always catches a bad mapping,
   because running balances don't add up

A generic-path success can be saved as a column mapping for that bank, so the next import of the
same layout skips the LLM.

---

## Parser candidates

Checked against PyPI on 2026-10-07.

| Package | Version | Licence | What it covers |
|---|---|---|---|
| `xfina` | 0.8.1 (2 Oct 2026) | Apache-2.0 | Rust/PyO3. **Indian:** SBI (PDF, password), HDFC, ICICI, Axis, Bank of Baroda (XLS); HDFC, ICICI, Axis cards; CAMS. ReBIT-shaped output. 3 GitHub stars; classifiers stop at 3.13 — check for a 3.14 wheel |
| `bankstatementparser` | 0.0.20 (25 Sep 2026) | Apache-2.0 OR MIT | CSV, OFX/QFX, MT940, CAMT, PDF with LLM/vision fallback, **Golden Rule check built in**. No Indian banks named. Pulls LiteLLM |
| `casparser` | 1.4.1 | MIT | CAMS, KFintech, NSDL and CDSL CAS PDFs. Built on `pypdfium2`, no GPL deps. **Parked** with statement-driven net worth ([`parked.md`](parked.md)) |
| `docling` | 2.x | MIT | General document → tables. Strong but heavy (ML models) — fallback candidate only |
| `monopoly-core` | — | **AGPL-3.0** | **Excluded** by the licence decision in `plan.md` §1 |

My banks: **SBI** (xfina claims it), **Union Bank of India** (no parser found; PDFs are password
protected — typically the first four letters of the name plus DDMM of birth), **Slice** credit
card (export format unknown).

---

## The spike — `plan.md` milestone 18

Timeboxed to one or two evenings. **The output is a decision per bank, not code.**

1. Download 12 months of real statements: SBI, Union Bank, Slice
2. SBI through `xfina`. Amounts, dates, balances right?
3. Union Bank and Slice through `xfina` and `bankstatementparser`; if neither, the generic fallback
   by hand (text extraction + one model call) to see if the invariant passes
4. Record per bank: parser, format to download, password pattern, quirks

Real statements never enter the repo. Parser tests use synthetic fixtures; a git-ignored local
folder holds real files for manual runs.

**Result:** _(unfilled — run the spike)_

---

## The invariant — non-negotiable

Extraction can be wrong, especially anything LLM- or OCR-assisted. Every import is checked
**before a single row is committed**:

```
opening_balance + credits − debits == closing_balance
```

Plus, where the statement has a running balance, every row's balance must follow from the
previous one. Result is `VERIFIED` / `DISCREPANCY` / `FAILED`. Only `VERIFIED` can be committed.

A wrong balance the user trusts is worse than no answer: a mis-parsed statement silently corrupts
the ledger, and every projection downstream is wrong with no signal.

---

## Reconciliation — logged rows vs statement rows

Logging "spent 450 on swiggy" by chat, or sharing its UPI screenshot, and later importing the
statement must not double-count.

| Match | Rule |
|---|---|
| Certain | Same UPI reference number. Otherwise: same account (or the logged row had none), same amount, date within ±3 days, one candidate |
| Ambiguous | Several candidates, or amount within a small tolerance — shown on the review screen |
| None | New row from the statement |

On a merge, the **statement wins on amount, date and account**; the logged row keeps its category,
notes, claims (splits), photo and link to the conversation. Logged rows still unmatched after a
statement covering their date appear on the review screen as "logged but not on the statement" —
wrong account, cash, or a mistake.

---

## Photos and voice

**Photos.** A receipt photo or a UPI payment screenshot (GPay, PhonePe, Paytm) → isolated vision
call (the `VISION_*` endpoint) → typed `{merchant, amount, date, method, upi_ref}` → a review card
in chat → one transaction, image kept as an attachment. On Android the installed app is a share
target, so a screenshot goes straight from the UPI app to Fin-Agent. The UPI reference number is
what later matches the row to its statement line. Line items are not extracted.

**Voice.** Push-to-talk audio → the `STT_*` endpoint (local `speaches` or a cloud API) → text →
the normal chat path. Hinglish accuracy is an eval category (L7).

---

## Investments

Parked. Asset accounts take hand-updated balances; CAS import and NAV refresh come back with
statement-driven net worth ([`parked.md`](parked.md)).
