# Import

This file holds the detail behind [`plan.md`](plan.md) §2: how statements, photos, and voice
notes become ledger rows—and how imported rows reconcile with what was already logged by chat,
voice, or photo.

The `plan.md` file owns decisions, and this file owns their shape. If the two disagree, the plan
wins and this file is stale.

---

## The split: Readers once, a parser for each layout

The following table shows who does each half of an import:

| Half | Who does it |
|---|---|
| Turn any file into cells or positioned words | Fin-Agent's readers, on the host, written once |
| Turn those into rows, for one bank's layout | A script the agent writes once in Monty, saved, and rerun with no LLM |
| Check them and reconcile them | Fin-Agent's own code. No library knows its data model |

**The interface is generic.** "We only accept CSV" is never a thing the user is told. An
unsupported file gets a straight answer about what isn't wired up—and the model tries it if it
accepts that type.

---

## The pipeline

![The agent learns each statement layout once](diagrams/sd-import-pipeline.png)

*Figure: a known layout reruns its saved parser with no LLM call. A new layout starts a parse run,
checks gate both paths, and the parser is saved only after you commit
([note](diagrams/sd-import-pipeline.md)).*

Each stage works as follows:

1. **Open.** Find the file's type from its contents, not its extension. If it's locked, the import
   card shows a password field. The password goes over REST, stays in memory, is never stored, and
   never reaches the model. Statements arrive monthly, so asking is cheap.
2. **Known layout?** Match the normalized column headers against `statement_layouts`. On a match, the
   server reruns the saved parser in Monty. If the checks fail, the layout counts as new.
3. **Parse run,** for a new layout. The same agent and model, with no write or ledger tools, guided
   by the `parse-statement` skill. It reads a sample, writes a script, and repairs it against the
   checks ([`tools.md`](tools.md#parse-run-functions-new-statement-layouts-only)). It submits the
   script, never rows, and the server reruns it from its text. A page with no text layer goes to the
   `VISION` model instead. If it can't find what it needs, its question comes back on the import card.
4. **Checks** (described later). A failure writes nothing and says why.
5. **Dedupe.** Match against earlier imports by `content_hash`.
6. **Reconcile.** Match against rows logged by chat, voice, or photo (described later).
7. **Categorize, then review.** One batched model call labels the distinct counterparties, not
   one call per line. The same call may name one of your own accounts as the other side. The web
   app shows new rows, merged rows, ambiguous matches, categories, and suggestions to move money
   between your accounts. The user commits, edits, or rejects.
8. **Commit.** Write the movements through pgledger, link each statement line as a capture, and
   record the closing balance on the account. A suggested own-account line is written as a
   transfer only after the user confirms it on the review screen. If the closing disagrees with
   the ledger, the difference is a visible adjustment. Then recompute baselines, and save the
   parser.

A parser is saved only after you commit. One that failed the checks, or came from an import you
rejected, isn't saved. A committed import is undone from the Imports screen, not by the agent.

Stages 1-6 run as a [job](platform.md#notifications-and-scheduler), off the chat path. A crash or a
failed attempt runs the job again from the top, which costs little, because nothing reaches the ledger
before commit. The password isn't stored, so after a crash the card asks for it again. After the last
attempt, the import is marked `failed`, with the reason on its card, and the file stays so you can
retry ([step 8](system-design.md#jobs-a-plain-table-and-one-loop)).

A file that isn't a statement, such as a salary slip or a loan letter, gets a plain "this isn't a
statement," and nothing is imported.

---

## Readers

The following table lists a reader for each kind of file, with the Monty test's result on synthetic
files ([report](../research/monty-document-parsing/README.md)):

| File | Reader | Tested |
|---|---|---|
| PDF with a text layer, password or not | `pypdfium2` (BSD-3-Clause or Apache-2.0). It gave the same words as `pdfplumber` about 10× faster | All 9 layouts |
| Scanned PDF, photo of a statement | The `VISION` model | Not tested. RapidOCR handled clean scans and a moderate photo, and returned nothing on a harsh one ([parked](parked.md#ocr-for-scanned-statements)) |
| `.xlsx`, `.xls`, `.ods`, `.xlsb` | `python-calamine` (MIT) | `.xlsb` read only, with no statement tested |
| Password-protected `.xlsx` | `msoffcrypto-tool` (MIT), then `python-calamine` | Yes |
| `.xls` that is really HTML or tab-separated text, and saved HTML | The host's `html.parser` or `csv` | Yes |
| CSV, TSV, TXT | The host's `csv`. The host decodes the file, because Monty has no cp1252 codec | Yes |
| `.docx` | `python-docx` (MIT) | Yes |
| RTF | `striprtf` (BSD-3-Clause) | Yes |
| OFX, MT940, JSON | The host's text lines | Yes, including public MT940 and OFX samples |
| CAMT.053 and other XML | The host parses it, because Monty has no XML library | Flat files only; prefixed namespaces not tested |
| ZIP of statements | The host's `zipfile`, refusing bombs and `../` paths, then each file on its own | Yes |
| `.doc`, anything else | No reader. The model tries it if it accepts that type; otherwise a straight answer | Not tested |

The readers and the helper block total about 500 lines. The helper block holds shared money, date,
and line-grouping functions, and each saved parser records the helper version it ran with.

### Parser libraries: optional

The following table lists the libraries checked against PyPI on October 7, 2026. None is required;
the spike keeps one only if it beats a written parser on real statements.

| Package | Version | License | What it covers |
|---|---|---|---|
| `xfina` | 0.10.0 | Apache-2.0 | Written in Rust (PyO3). Indian: SBI (PDF, password), HDFC, ICICI, Axis, Bank of Baroda (XLS); HDFC, ICICI, Axis cards; CAMS. ReBIT-shaped output. Three GitHub stars. Installs on Python 3.14, but recognized neither SBI file and has no Slice support |
| `bankstatementparser` | 0.0.20 (Sep 25, 2026) | Apache-2.0 OR MIT | CSV, OFX and QFX, MT940, CAMT, PDF with an LLM and vision fallback, Golden Rule check built in. No Indian banks named. Pulls LiteLLM |
| `casparser` | 1.4.1 | MIT | CAMS, KFintech, NSDL, and CDSL _Consolidated Account Statement_ (_CAS_) PDFs. Built on `pypdfium2`, no GPL dependencies. Parked with statement-driven net worth ([`parked.md`](parked.md)) |
| `monopoly-core` | Not recorded | AGPL-3.0 | Excluded by the license decision in `plan.md` §1 |

My banks: SBI (PDF or `.xlsx`, password protected), Union Bank of India (PDF, password protected),
and the Slice credit card (unlocked monthly PDFs).

---

## The spike: Milestone 21 in the plan

Timeboxed to one or two evenings. _The output is a decision for each bank, not code._ This is the
real test of the import design: the Monty test used synthetic files, with fixtures and parsers from
the same author.

To run the spike, follow these steps:

1. Download 12 months of real statements for SBI, Union Bank, and Slice.
2. Give the first statement for each bank to a fresh model in a parse run. Record the repairs, time,
   and cost, and whether the rows are right.
3. Rerun each saved parser on that bank's other 11 months, using
   [`check_real.py`](../research/monty-document-parsing/check_real.py) with `--parser`, `--period`, and
   `--ledger-opening` where needed. It prints only pass or fail and counts.
4. Run the SBI statements through `xfina`, and compare.
5. Score Tesseract, PaddleOCR, and the `VISION` model on a phone photo of a statement, using the
   checks.
6. For each bank, record the format to download, the password pattern, and any quirks—including
   which payer text (names, UPI notes) the narration carries. That's the outside text the agent will
   see ([`design.md`](design.md#untrusted-text-and-write-tiers)).

If a real statement can't be parsed in Monty, the fallback is a separate sandbox
([parked](parked.md#a-separate-code-sandbox)).

Real statements never enter the repository. Parser tests use synthetic fixtures; a git-ignored
local folder holds real files for manual runs.

**Result.** A fresh model in a parse run wrote a parser for the real SBI statement and another for
the real Slice statement; both are saved. Union Bank was not provided, so its decision is open.
Every real file had a text layer or was a spreadsheet, so OCR was not needed and the phone-photo
scoring was not run. `xfina` was not added.

| Bank | Format to download | Password | Parser result | Decision | Quirks |
|---|---|---|---|---|---|
| SBI | 12 months in one download (Nov 3, 2025 – Nov 3, 2026), PDF or `.xlsx` | Yes, both | PDF: 939 rows, `VERIFIED`, 1 repair, 321 s. `.xlsx`: 939 rows, `VERIFIED`, 0 repairs, 85 s | Saved parser (Monty). Either format works; the `.xlsx` is faster to parse | Both formats print the same rows, and the statement's own debit plus credit count equals the 939 rows. The `.xlsx` is an encrypted OLE2 file |
| Slice | One unlocked PDF for each monthly cycle; 5 cycles (May 25 – Oct 24, 2026) | No | 5/5 `VERIFIED`, 80 rows, 0 repairs, 297 s | Saved parser (Monty) | The cycle runs from the 25th to the 24th. The header prints it as a yearless `DD MON - DD MON` and the rows as `DD Mon 'YY`, so no 4-digit year appears; the period finder takes the year from the `'YY` date (commit 626e421). The exports are incomplete: the PDFs omit several repayments that the app shows (for example ₹200 on 27 Jun and ₹1,823 on 7 Jul), and one ₹4,969.91 repayment is dated 10 Sep in the PDF and 9 Sep in the app. The card's review screen asks the user to confirm an own-account transfer before commit |
| Union Bank | Unknown | Unknown | Not run | Open | Not provided |

The synthetic fixtures still pass through the readers, Monty, and `check_statement` (3/3
`VERIFIED`), and a known layout replays with no model call.

The generated counts and status words are in [`import-spike-results.md`](import-spike-results.md).
Milestone 24 (the backfill) is tracked in the completion checklist.

---

## The checks: Non-negotiable

Extraction can be wrong, especially anything model-assisted. Every import is checked _before a
single row is committed_:

```
opening_balance + credits − debits == closing_balance
```

Plus, where the statement has a running balance, every row's balance must follow from the previous
one. The result is `VERIFIED`, `DISCREPANCY`, or `FAILED`. Only a `VERIFIED` import can be committed.

A wrong balance the user trusts is worse than no answer: a mis-parsed statement silently corrupts
the ledger, and every projection downstream is wrong with no signal.

**Balances aren't enough.** In the Monty test, a day/month swap came out verified with 17 of 18
dates wrong, and a new column came out verified with 0 of 22 narrations right. A card statement
often has no running balance, so only its total is checked. So the following checks are added:

| Check | Catches |
|---|---|
| Every date falls inside the period, taken from outside the parser: a month name in the header, or the day after the ledger's last row. If the parser supplied it, a swapped format would flip both | A day/month swap |
| The normalized column headers match the saved fingerprint; otherwise the layout counts as new | A new column dirtying the narrations |
| At least one row, unless the statement says there were none | A parser that drops every row when opening equals closing |
| The opening is evidenced by a user manual entry, a trusted prior/ledger balance, or the statement's printed opening. A parser fallback zero is never labeled printed. On a first card import with no prior or manual opening, the review card asks before commit | A missing opening silently defaulted to zero, or a gap between statements |

No check covers the text of a narration. A misread payee shows up on the review screen, which stays
the last guard.

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

On a merge, the statement line becomes a capture of the logged movement, and the _statement wins on
amount, date, and account_: a different date edits the movement's date, and a different amount or
account appends a reversal and the corrected transfers. The movement keeps its labels, notes,
money owed by people, photo, and link to the conversation. Logged rows still unmatched
after a statement covering their date appear on the review screen as "logged but not on the
statement"—wrong account, cash, or a mistake.

---

## Own-account moves need a human yes

A card bill, a SIP, or a transfer to another account you own is not spending. The categorizer
gets the user's own accounts (id, name, kind, institution, last four) in the same batched call,
and can return one of them instead of a category. The review screen then shows the line as a
suggested transfer, with the other account's name, and asks the user to confirm or reject it.
Commit refuses to run while a suggestion is unconfirmed, so the model cannot move money between
accounts on its own.

The confirmation matters because the other statement is not always complete. Slice's monthly PDFs
for 2026 omit several repayments that its app shows, and one repayment is a day off. A confirmed
SBI line is therefore still booked when the card's PDF never printed the matching credit. The
resulting difference surfaces as the usual visible closing adjustment rather than as a silent
change to spending. The alternative, treating the line as spend, would overstate consumption.

## Photos and voice

**Photos.** A receipt photo or a UPI payment screenshot (GPay, PhonePe, Paytm) → isolated vision
call (the `VISION_*` endpoint) → typed `{merchant, amount, date, method, upi_ref}` → one
transaction, logged at once, image kept as an attachment. The undo card shows the screenshot beside
the extracted fields, so a misread digit is easy to spot and undo. On Android, the installed app is a share
target, so a screenshot goes straight from the UPI app to Fin-Agent. The UPI reference number is
what later matches the row to its statement line. Line items are not extracted.

The photo endpoint runs this call, so the server adds the extracted fields to the thread's
`model_history` as a marked entry and sets the outside-text flag. The client can't touch either
([step 7](system-design.md#decisions)). The photo is saved first, so a failed vision call leaves a
retry card rather than a lost photo.

**Voice.** Push-to-talk audio → the `STT_*` endpoint (a local `speaches` server or a cloud API)
→ text → the normal chat path, sent at once with the transcript shown. A transcript is your own
words, so it doesn't flag the thread. The audio is saved first, so a failed transcription leaves a
retry card.
The `STT_*` call can carry the active payee names as a context prompt, because those are the words a
Hinglish note is most likely to mishear. It is on by default; `STT_PAYEE_CONTEXT=0` turns it off.
With a cloud endpoint, those names reach that provider as well.
Hinglish accuracy is an eval category ([L7](plan.md#6-open-questions)).

A dictation app such as [Hex](https://github.com/anomalyco/hex) needs no code from us: it pastes into
the chat box as typed text, skipping the `STT_*` endpoint, and its transcription stays on your machine.
A phone keyboard's voice typing should work the same way, but it isn't tested, and where its audio
goes isn't checked.

---

## Investments

Parked. Asset accounts take hand-updated balances; CAS import and _net asset value_ (_NAV_)
refresh come back with statement-driven net worth ([`parked.md`](parked.md)).
