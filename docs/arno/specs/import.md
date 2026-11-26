# Statement import, photos, and voice

This specification owns Arno's statement readers, import pipeline, verification checks,
reconciliation, own-account transfer and card-credit decisions, post-commit categorization, import
undo, receipt photo extraction, voice transcription, and bank format findings; [`plan.md`](../plan.md)
§2 owns product decisions, [`tools.md`](tools.md) owns the agent's tool contracts,
[`data-and-engine.md`](data-and-engine.md) owns write tiers and ledger invariants, and
[`cost-tracking.md`](cost-tracking.md) owns the usage log and daily spend cap.

## Architecture: readers, extraction, and verification

Arno separates deterministic file decoding from bank-specific layout extraction and ledger
verification. The following table assigns each responsibility:

| Responsibility | Owner |
|---|---|
| Decode files into sheets, text lines, or positioned words | Host readers in `src/arno/importers/readers.py` |
| Map cells or positioned words into transaction rows | Extraction, which is being rebuilt ([Extraction status](#extraction-status)) |
| Verify totals, dates, headers, and reconcile against the ledger | Deterministic host code in `checks.py`, `gate.py`, `reconcile.py`, and `commit.py` |

The upload interface accepts any supported bank format rather than requiring CSV. Unsupported
formats receive a plain explanation of what cannot be read.

## Extraction status

Nothing extracts rows from a statement today. The chat agent has no import tool and no parser
agent, and no code path opens an import from an upload. Attaching a statement in chat stores the
file in `attachments` (`POST /api/attachments`) and nothing more. The simplification plan's
[Phase 3](../planning/simplification-plan.md) replaces extraction. The rest of this page describes
what stays: the readers, the import card with its password field, the verification gate
(`submit_parser_code` in `src/arno/agent/parser.py`), reconciliation, review, commit, and undo.

## Import pipeline

![An import passes the gate before review](../diagrams/sd-import-pipeline.png)

_Figure: Extracted rows pass a code gate that verifies them against the current file and every
stored committed statement for that bank. Reconciliation and the review screen follow, and commit
saves the result ([diagram notes](../diagrams/sd-import-pipeline.md))._

Readers, checks, the verification gate, and commits are deterministic code. Each import moves
through these stages:

1. **Open:** `create_import()` creates the `imports` row so the import card appears, and each
   file's format is sniffed from magic bytes. PDFs exceeding 150 pages (`MAX_DOC_PAGES = 150`) are
   refused with instructions to split downloads into shorter date ranges. Multi-file ZIP archives
   are refused (`_archive_refusal`) so each statement receives its own review. If a file is
   encrypted, the import card renders a password field. Passwords travel over
   `POST /api/imports/{id}/password`, stay in memory only for that unlock, are never persisted, and
   never enter chat or any model.
2. **Verification gate:** `submit_parser_code` runs the extraction script through
   `src/arno/importers/gate.py` in a fresh Monty sandbox (`src/arno/importers/monty.py`) with the
   helper prelude prepended, runs all verification checks, and replays the script against every
   stored committed statement from the same bank. A failure returns its reason and the import stays
   in `parsing`; only a passing gate moves the import to `review`.
3. **Deduplicate:** Matches rows against existing `committed` or `review` lines for the same
   account by `content_hash`.
4. **Reconcile:** Matches remaining lines against effective movements already logged through chat,
   photo, or voice.
5. **Review:** The **Imports** screen shows new lines, matched lines, ambiguous candidates,
   suggested own-account transfers, required credit-card credit classifications, and any closing
   balance adjustment. Categories are omitted until after commit.
6. **Commit:** Re-reconciles `new` lines against current ledger movements so items logged while the
   import waited in review merge instead of duplicating. Inside one database transaction, commit
   books the evidenced opening balance at `period_start`, supersedes balance adjustments explained
   by the statement period, re-places the opening of any later statement whose first day the new
   rows now explain, preserves any stated balance dated after `period_end`, writes movements
   and `statement_line` captures, records any confirmed closing adjustment, recomputes baselines,
   and saves the passing script as a new active parser version.
7. **Categorize:** Commit enqueues a background `categorize_import` job that applies rules first
   and labels remaining payees with the categorizer agent.

Files that are not statements (such as payslips or loan letters) import nothing. Parser versions
are saved only on commit; rejected or gate-failed scripts are never saved.

### Monty sandbox limits and determinism

`src/arno/importers/monty.py` executes every submitted and replayed script in an isolated
`AsyncMonty` session with strict resource and determinism bounds. Phase 3 removes it:

- **Resource limits:** 256 MiB memory (`max_memory`), 30 seconds per feed
  (`max_feed_duration_secs`), 5,000 suspensions (`max_suspensions`), and 1 second total sleep
  (`max_total_sleep_secs`).
- **Frozen OS policy:** `datetime` is pinned to `2026-01-01T12:00:00Z`, `random` starts from seed
  `0`, and `sleep` is zeroed so replays are deterministic.
- **Cost-stop propagation:** If `transcribe_page` raises any exception in `COST_STOP_ERRORS`,
  `run_script` records it and re-raises it in `finally` even if the script catches the error,
  preventing a script from looping into additional paid vision calls.

Statement-page vision calls (`src/arno/importers/vision.py`) set `agent.instrument = False` so page
images never reach traces.

### Parser versions and replay regression set

`parser_versions` rows are append-only per `statement_layouts` entry, with at most one active
version enforced by a partial unique index on `parser_versions (layout_id) WHERE is_active`.
Activating a new version or rolling back (`POST /api/layouts/{id}/rollback`) flips flags inside one
transaction.

An unlocked copy of each statement is stored once opened (using `FPDF_REMOVE_SECURITY` for PDFs or
decrypted bytes for Office files) because passwords are never stored. You can purge a stored file
(`DELETE /api/attachments/{id}`), which removes it from the replay set. The replay set contains
every stored, non-purged statement for that bank whose import is committed and not undone; a new
parser version must reproduce each stored statement's saved `statement_lines` (dates, amounts,
balances, and descriptions) identically.

## File readers

`src/arno/importers/readers.py` selects a reader solely from magic bytes and content inspection
(`sniff`), never from filename extensions. The following table lists every supported format and its
reader behavior:

| Format | Reader and behavior |
|---|---|
| PDF with text layer (locked or unlocked) | `pypdfium2` extracts positioned words with automatic reading-rotation detection |
| Scanned PDF or statement image | `pypdfium2` renders pages to PNG (scale 2) and sends them to the isolated `VISION` role (`transcribe_page`) |
| `.xlsx`, `.xls`, `.ods`, `.xlsb` | `python-calamine` reads workbook sheets into trimmed cell grids |
| Password-protected `.xlsx` (`encrypted_ooxml`) | `msoffcrypto-tool` decrypts in memory, then re-sniffs and reads with `python-calamine` |
| HTML and HTML-in-`.xls` | Standard-library `HTMLParser` extracts `<table>` grids |
| CSV, TSV, TXT | Host decodes UTF-8, UTF-16, or cp1252 and selects the modal consistent delimiter (`\t`, `;`, `\|`, `,`) across rows |
| `.docx` | `python-docx` extracts tables and non-empty paragraphs as sheets |
| RTF | `striprtf` converts cell and row markers to text lines |
| OFX, MT940, JSON | Host decodes text lines into single-column cell rows |
| CAMT.053 and XML | Host flattens XML elements into `path: text` lines |
| Single-file ZIP archive | `zipfile` rejects paths with `..` or leading slashes, limits to 50 members and 200 MB uncompressed, and unpacks a single readable statement; multi-file archives are refused by `_archive_refusal` |
| Legacy `.doc` and `ole_other` | Detected by `sniff()` (`WordDocument` or unknown OLE2 container), left unpopulated, and raises `DocError` (`no_sheets` / `no_words`) when read; raw bytes are never sent to the model |

The helper prelude (`src/arno/importers/helpers.py`) supplies `money`, `money_float`, `to_iso`,
`date_any`, `serial_to_iso`, `lines_of`, `join`, `line_text`, and `find_col` to submitted scripts
and stamps each saved parser version with `HELPER_VERSION`.

## Verification checks

Every import must pass deterministic verification in `src/arno/importers/checks.py` before reaching
review or commit. The core accounting identity must hold in ledger sign:

```text
opening_balance + credits - debits == closing_balance
```

Where the statement prints a running balance, every row's balance must also chain from the previous
row. The result is `VERIFIED`, `DISCREPANCY`, or `FAILED`, and commit accepts only `VERIFIED`.

Because totals alone can miss swapped day/month dates or shifted narration columns, the gate
enforces six additional checks:

| Check | Failure prevented |
|---|---|
| Every row date falls inside the host-derived statement period (from the header or the ledger's last date) | Day/month date swaps |
| Normalized column headers match a known layout variant | A new bank column polluting narrations |
| New parser versions reproduce saved `statement_lines` across every stored committed statement for the bank | Overfitting a parser to one month |
| At least one row exists unless the script sets `empty_statement: true` with `opening == closing` | Silently dropping every row when opening equals closing |
| The statement period ends on or before today in `Asia/Kolkata` | Corrupt or mistyped future dates pinning bogus balances |
| The opening balance is evidenced by the statement's printed opening, a trusted ledger balance, or a manual entry on the review card (`POST /api/imports/{id}/opening`) | Silently defaulting a missing opening to zero or hiding a gap between statements |

No automated check validates narration spelling; the review screen remains the human guard. Editing
the amount of a statement-verified movement later is permitted, with a warning that the ledger will
diverge from the bank statement.

### Sign convention and opening balance anchoring

All figures below the raw statement text use **ledger sign**: `opening`, `closing`, running
balances, check verdicts, and stored account statement balances. Bank accounts already print in
ledger sign. Credit cards print amounts owed as positive numbers while ledger liabilities are
negative, so card parsers negate printed balances (₹4,450 owed is `-445000`, and an overpaid card
in credit is positive). Row `debit` and `credit` fields are always non-negative (`debit` leaves the
account, `credit` arrives).

When a statement omits its opening balance, `submit_parser_code` keeps its sound rows, parks the
import in `needs_input`, and answers `needs_opening` (a finished file, not a failure); other files
in the same submit still run. The review screen asks for the printed
figure and converts it once by account kind (`stated_opening_to_ledger` in
`src/arno/importers/checks.py`).
Commit books the evidenced opening at `period_start`, supersedes adjustments inside the statement
period, and surfaces any remaining closing discrepancy—or any conflict with a balance you manually
stated for a date inside the statement period—for explicit confirmation (`adjustment_confirmed`)
before booking.

Statements can be committed in any order. When an older statement lands after a newer one, its
rows already carry the balance to the newer statement's first day, so commit shrinks that later
opening to what is still unexplained, and recomputes (never stacks) a stated-balance re-anchor
dated after the period (`rebalance_after` in `src/arno/importers/balance_plan.py`). Undoing the
older import restores the newer one's opening exactly.

## Reconciliation

Reconciliation (`src/arno/importers/reconcile.py`) prevents double-counting between statements and
movements already logged by chat, photo, or voice. Every line is matched against effective
movements on the same account within ±3 days and the same debit/credit direction.

The following table defines the match outcomes:

| Outcome | Rule |
|---|---|
| `ignored` | `content_hash` already exists in a committed or review import for the same account |
| `certain` | Exact amount and direction match with a unique UPI reference match, unique payee identifier-hash match, or single exact candidate |
| `ambiguous` | Multiple exact candidates, or an amount within tolerance (`max(100, min(5000, abs(amount) // 200))` paise), resolved manually on the review screen |
| `new` | No existing ledger match; creates a new movement on commit |

On a merge, the statement line attaches as a `statement_line` capture and **the statement wins on
amount, date, and account**. A date difference updates the movement date in place; an amount or
account difference reverses and replaces the transfers while preserving labels, notes, split
receivables, photos, and conversation links. Unmatched ledger movements inside the statement period
appear on the review screen as logged but not on the statement.

Because you can keep logging while an import waits in review, `commit_import` runs
`rematch_new_lines` before opening the commit transaction. Any newly matched unique candidate
merges automatically; any newly ambiguous match blocks commit and returns the line to review.

## Own-account transfers and card credits

Moving money between your own accounts (credit-card bill payments, ATM withdrawals to cash, or
self-transfers) is not spending. Extraction inspects your active accounts and calls
`suggest_transfers` (`src/arno/agent/parser.py`) for lines whose own text names the destination account: its name or
institution, its last four digits, or a card-payment phrase (`CC PAYMENT`, `ccrepayment`) when you
have exactly one card. A payment to your own name alone is not evidence (it may go to a wallet or
an account the ledger lacks), and the host refuses a suggestion whose line does not name its
account (`names_account`). Commit refuses to proceed until you confirm or reject every suggested transfer
on the review screen (`PATCH /api/imports/{id}/lines/{line_id}`). When the counterpart account's
statement arrives later, its line merges into the transfer rather than booking twice.

Credit-card credit lines receive an additional safeguard (`credit_decision_required` in
`src/arno/importers/reconcile.py`). A positive line on a `card` account that is not ignored,
matched, or already a confirmed transfer is never defaulted to income: commit refuses until you
classify it on the review screen as:

- **`refund`:** Records a refund (optionally assigned to a category).
- **`transfer`:** Records a card repayment from a chosen `credit_source_account_id`.
- **`income`:** Records credit-card cashback or reward income explicitly.

## Post-commit categorization

![Rules label most rows, and the model labels the rest](../diagrams/sd-categorize.png)

_Figure: After commit, deterministic rules label every matching row. The background categorizer
agent sends remaining payees to the model, checks calibration agreement,
saves trusted answers as rules, and leaves unsure rows uncategorized with a hint
([diagram notes](../diagrams/sd-categorize.md))._

Categorization runs asynchronously in a background `categorize_import` job (`src/arno/importers/categorize.py`
and `src/arno/agent/categorizer.py`) and never starts a chat conversation.

- **Rules first:** User rules take priority over `llm` rules, which take priority over seed rules.
  Indian payment-rail prefixes (`UPI/DR`, `NEFT`, `IMPS`, `POS`, `NACH`, `WDL TFR`) are stripped
  before pattern matching (`equals`, `contains`, or `regex`).
- **Seed merchant list:** `src/arno/importers/seed_rules.py` ships **163** Indian merchant rules
  inserted idempotently at boot. Eight payment aggregators in `PAYMENT_APPS` (`one97`, `paytm`,
  `phonepe`, `google pay`, `gpay`, `cred`, `amazon pay`, and `razorpay`) are excluded from
  name-only matching so their rows go to the model with amount and narration context.
- **Direct execution:** The categorizer agent (`CATEGORIZE_USAGE_LIMITS`: 40 requests, 80 tool
  calls) groups payees into chunks of 12 (`CHUNK_PAYEES = 12`) and calls `categorize_now` (up to 8
  concurrent chunks with thinking disabled). There is no provider Batch API path, so categorizing
  always runs through the `CHAT` role.
- **Empirical calibration:** The host injects up to 5 already-ruled payees (`CHECK_PAYEES = 5`)
  without their labels and measures the model's agreement rate instead of trusting self-reported
  confidence. Low agreement causes the agent to distrust more keys.
- **Never default to "Other":** Unsure payees, distrusted keys, and `"Other"` guesses stay
  uncategorized with `category_hint` preserved for the **Uncategorized** queue, grouped by payee
  so UPI payments to people are answered once and saved as rules.
- **Single undoable write group:** `save_categories` writes all trusted labels and `llm` rules in
  one audit write group and links its ID to `imports.categorize_groups`.

## Undoing an import

Committed imports are undone from the **Imports** screen (`POST /api/imports/{id}/undo`), not by
the agent. One transaction reverts `imports.categorize_groups` first (newest first, removing
categorizer labels and `llm` rules created from the import) and then reverts the commit's own
movements, opening adjustments, captures, and newly created payees. Any later manual edit to those
rows or rules conflicts and returns HTTP `409` naming what changed so you undo the later edit
first.

To prevent deadlocks among concurrent commits, undos, and background categorizer saves,
`_lock_import_scope` in `src/arno/importers/commit.py` acquires row locks in one strict order:

1. Account row (`accounts FOR UPDATE`) as the single gate per account.
2. Import row (`imports FOR UPDATE`).
3. Referenced payees in ascending ID order (`payees ORDER BY id FOR UPDATE`).

`save_categories` acquires the import row lock before reading pending rows, so a save racing with
an undo waits and exits cleanly with nothing left to label.

## Photos and voice

### Images and screenshots

Images upload to `POST /api/attachments` with `purpose="photo"`, either from the chat composer or
from the Android PWA share target, which opens a chat turn immediately. Each turn stores
`{type: "data-photo", data: {attachment_id, filename}}` in the UI message. Text inside an image is
data, never an instruction.

The main agent reads attached images natively via `ImageAttachments` (`src/arno/agent/images.py`):

1. **Wire-only hydration:** Only compact `[Attached image: ...]` markers persist in
   `model_history`; `ImageAttachments.before_model_request` fetches attached images in one batched
   query and attaches raw `BinaryContent` onto the outgoing request for the image turns in history
   while preserving prompt cache prefixes and omitting binary bytes from OTel spans.
2. **Atomic batch logging:** `record_movements` logs one entry or a multi-row screenshot (1–50
   movements in one `write_group` with one undo button), linking each movement back to its
   `attachment_id`.

### Voice notes

Push-to-talk audio posts to `POST /api/transcribe` (saving the audio first so failures offer
`POST /api/transcribe/{attachment_id}` retry). `src/arno/capture/transcribe.py` calls
`POST {STT_BASE_URL}/audio/transcriptions` (any OpenAI-compatible STT endpoint, a cloud API or
a server you run yourself):

- **Configuration:** `STT_LANGUAGE` pins a language code (preventing misdetection on short clips;
  unset keeps auto-detect), `STT_TIMEOUT_SECONDS` sets the HTTP timeout (default `120` seconds),
  and `STT_PAYEE_CONTEXT` (enabled by default; `0` disables) passes up to 80 active payee names
  (capped at 500 characters) as a recognition hint.
- **Usage log:** Because transcription is not an `Agent` run, `transcribe_audio` checks the shared
  daily cap, posts the multipart request with `httpx`, and records the usage the endpoint reported
  (zero when it reports none) in `llm_usage`. A reply without `usage` or with malformed `usage`
  still returns its transcript ([`cost-tracking.md`](cost-tracking.md)).

Desktop dictation tools that paste text directly into the composer bypass `STT_*` completely.

## Bank format findings

Real-statement testing validated the reader and model-written parser design against Indian bank exports and
evaluated third-party parser libraries. The following table summarizes the bank findings:

| Bank | Download format | Password | Parser status | Format quirks and notes |
|---|---|---|---|---|
| SBI | 12-month PDF or `.xlsx` (encrypted OLE2) | Required | Verified (939 rows; saved parser) | Both formats emit identical rows matching the statement's debit and credit counts; `.xlsx` parses faster than PDF |
| Slice credit card | Monthly unlocked PDFs | None | Verified (5 cycles, 80 rows; saved parser) | Billing cycle runs 25th–24th; header prints yearless `DD MON - DD MON` while rows use `DD Mon 'YY`; exports omit several repayments shown in the app and occasionally shift a repayment date by one day, making own-account confirmation and visible closing adjustments essential |
| Union Bank of India | PDF | Required | Open | No real statement tested; no layout is saved |

Third-party parser libraries and standalone OCR were not adopted:

- **`xfina` (Apache-2.0):** Installed on Python 3.14 but failed to recognize real SBI files and
  lacks Slice support.
- **`bankstatementparser` (Apache-2.0 / MIT):** Pulls LiteLLM and has no Indian bank coverage.
- **`casparser` (MIT):** Covers mutual-fund CAS PDFs; parked with statement-driven net worth
  ([`parked.md`](../planning/parked.md)).
- **`monopoly-core` (AGPL-3.0):** Excluded by Arno's permissive-license policy (`plan.md` §1).
- **Standalone OCR:** Real bank statements shipped with text layers or spreadsheets, and vision
  transcription handles scanned pages without adding an OCR runtime.
