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
7. **Review screen.** The web app shows new rows, merged rows, ambiguous matches, and categories.
   The user commits, edits, or rejects.
8. **Commit.** Write the movements through pgledger, link each statement line as a capture, and
   record the closing balance on the account. If it disagrees with the ledger, the difference is a
   visible adjustment. Then recompute baselines, and save the parser.

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
| `xfina` | 0.8.1 (Oct 2, 2026) | Apache-2.0 | Written in Rust (PyO3). Indian: SBI (PDF, password), HDFC, ICICI, Axis, Bank of Baroda (XLS); HDFC, ICICI, Axis cards; CAMS. ReBIT-shaped output. Three GitHub stars; classifiers stop at 3.13—check for a 3.14 wheel |
| `bankstatementparser` | 0.0.20 (Sep 25, 2026) | Apache-2.0 OR MIT | CSV, OFX and QFX, MT940, CAMT, PDF with an LLM and vision fallback, Golden Rule check built in. No Indian banks named. Pulls LiteLLM |
| `casparser` | 1.4.1 | MIT | CAMS, KFintech, NSDL, and CDSL _Consolidated Account Statement_ (_CAS_) PDFs. Built on `pypdfium2`, no GPL dependencies. Parked with statement-driven net worth ([`parked.md`](parked.md)) |
| `monopoly-core` | Not recorded | AGPL-3.0 | Excluded by the license decision in `plan.md` §1 |

My banks: SBI, Union Bank of India (PDFs are password protected—typically the first four letters of
the name plus DDMM of birth), and the Slice credit card (export format unknown).

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

**Result:** the harness and the decision procedure are built; the real-statement decisions
are **pending**. No real SBI, Union Bank, or Slice statement was provided, so nothing was
touched in `private/statements/` and no bank got a verdict. What was verified now, with
synthetic Indian fixtures and the research harness:

| What | Result |
|---|---|
| SBI CSV layout, Union Bank TSV layout, Slice CSV layout | 3/3 `VERIFIED` through the readers, Monty, and `check_statement` |
| Same SBI script through the research `check_real.py --parser` | `PASS(amounts balance)` |
| A known layout replaying with `ALLOW_MODEL_REQUESTS = False` | Passes; no model call |
| A fresh parse run writing and submitting a script (`FunctionModel`) | Submits, reruns in a fresh `AsyncMonty`, checks pass |
| Locked PDF or Office file | The card asks, the password stays in memory, a wrong one is a readable `needs_password` |
| `xfina` comparison, Tesseract, PaddleOCR, the `VISION` model | Not run: not installed or no real page; the vision call is wired and isolated |
| Real SBI, Union Bank, Slice layouts, password patterns, quirks | Open until real files are handed over |
| Milestone 24: a 12-month backfill and baselines from real rows | Open for the same reason; the recompute path is built and tested on synthetic rows |

The generated counts are in [`import-spike-results.md`](import-spike-results.md). Run
`fin-agent import-spike` with files in `private/statements/<bank>/` to fill the per-bank rows;
the command prints and writes counts and status words only. The `parse-statement` skill is
written for the SBI, Union Bank, and Slice layouts described in `import.md`, including the
`await` + keyword-argument convention that replay requires.

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
| The printed opening matches the ledger, and a statement that prints none takes the ledger's. On a first import with neither, the import card asks | A gap between statements |

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
