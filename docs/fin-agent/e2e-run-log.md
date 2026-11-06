# End-to-end session log

Session started on November 5, 2026. The [journey catalog](e2e-user-journeys.md) defines the
starting states, priorities, expected financial effects, and 360 numbered cases. This log is
the execution record. A written scenario, existing unit test, or convincing model reply does
not count as a completed user journey.

## Current state

- The initial greeting investigation and follow-up UI turns are recorded below as observed
  subchecks. The planning work did not replay those messages.
- The trace input/output mapping, authenticated user attribution, and conservative schema
  compaction are present in the working tree. Default Unset/Error status behavior was retained.
- The implementation delegate reported running the actual UI and inspecting Phoenix. Its
  supporting checks reported 8 passes for trace-preview/native tracing, 44 passes for the
  selected agent/write/approval/turn/safety set, and 15 passes on an overlapping final rerun.
  These are separate runs, not a summed count of unique tests or a replacement for UI evidence.
- No real statement was imported by the journey-planning work. No financial data was changed.
- All 360 catalog cases start Not run. The observed greeting subchecks do not mean that all
  steps and variations of J01 were executed.
- Checkpoint `55e1156` and a separate follow-up fix commit are on the current branch; no PR was
  opened. Record the actual build and dirty-diff identity before the next execution; a commit SHA
  alone is insufficient for this working tree.

## Observed greeting evidence

The following rows preserve the implementation delegate's UI execution report. Exact tool
schema component counts are local estimates, so only provider usage is in this table. Blank
cache reporting means unknown rather than zero. No financial movements were reported from these turns.

| Observation | UI action | Reported input / output tokens | Reported cache read | Observed trace behavior | Trace |
|---|---|---|---|---|---|
| O01 | Original user's Hey in an empty financial instance. | 11,523 / 246 | Not reported | Unset; one model request, no tool calls; root previews and User blank. | [Original Hey](http://127.0.0.1:6006/projects/UHJvamVjdDoy/spans/c2ad379d9fb0a8244031b871e2d9f076?timeRangeKey=1h&sessionView=traces) |
| O02 | Thanks in the same conversation, before the preview patch. | 11,773 / 110 | 11,392 | Root previews still blank; one model request, no tools. | [Thanks](http://127.0.0.1:6006/projects/UHJvamVjdDoy/spans/180692e7954c5fb71bc604363f1afa48?timeRangeKey=1h&sessionView=traces) |
| O03 | Hey again in the same conversation, after root preview mapping. | 11,888 / 78 | 11,648 | Current input and actual textual reply visible; Unset; no tools. | [Hey again](http://127.0.0.1:6006/projects/UHJvamVjdDoy/spans/957daa68533e679c24090b5ccd0e24c4?timeRangeKey=1h&sessionView=traces) |
| O04 | Hey in a new empty conversation after compaction of tool guidance. | 10,680 / 236 | 1,792 | Same model, instructions, and snapshot as original; current previews and authenticated User populated; Unset; no tools. | [Compact Hey](http://127.0.0.1:6006/projects/UHJvamVjdDoy/spans/8815ec3dd9afb9a38424a5d2f55605a9?timeRangeKey=1h&sessionView=traces) |
| O05 | What accounts do I have? in O04's conversation. | 10,925 / 136 | 10,624 | Correct empty-account answer; input preview is the new question rather than the snapshot or Hey. | [Accounts question](http://127.0.0.1:6006/projects/UHJvamVjdDoy/spans/1e8ebaee201ddfbd47d8356e8c7f9239?timeRangeKey=1h&sessionView=traces) |
| O06 | Hi in another new empty conversation. | 10,680 / Not recorded here | 10,624 | Same authenticated user ID across conversations; Unset. | [Second new conversation](http://127.0.0.1:6006/projects/UHJvamVjdDoy/spans/2102bd8db6ed3419611ac52175315436?timeRangeKey=1h&sessionView=traces) |

Schema verification from the implementation report: after removing only descriptions/titles,
the eight generated tool contracts were structurally identical. Local consistent serialization
estimated 6,500 to 5,760 tool-definition tokens; Phoenix-normalized definitions estimated 6,385
to 5,688. Earlier figures mixed serialization formats and must not be treated as a cost range.
The observed fresh-turn input change is not a fully controlled cost or latency experiment.

## Observed setup subcheck (trace c85f6355e9524ca0748295bc3e1973bd)

Read-only inspection on 5 November 2026, 17:25 IST (11:55 UTC). This is one setup turn, not a
journey pass. J02 and J03 remain Not run.

| Observation | Evidence |
|---|---|
| In a new conversation the user asked to set up an SBI savings account and a Slice card for statement import, explicitly saying not to assume balances or a credit limit. | UI transcript and trace input agree; conversation created 17:25:32 IST. |
| The agent added one bank account only. No card, balance, credit limit, statement day, due day, last4, floor, schedule, or import was created. | Persisted accounts and audit log: one `accounts` insert, actor agent, one undo group; zero imports. |
| The card add failed validation in the same turn: "A card needs a positive credit limit". The reply reported the failure and asked for the missing card facts. | The second `execute_tool update_setup` span is Error with that exception; root span Unset; no retry. |
| A Setup saved card appeared for the bank account, and the failed call showed an `output error` chip. No approval was pending. | UI transcript; conversation `outside_text_at` is null. |
| Trace input and output previews held the current user message and final reply, not the snapshot. The opaque authenticated user id and conversation session were present; root status Unset. | Phoenix root span attributes and trace tree. |
| The turn made two model requests and two `update_setup` calls, with no retries or fallback. Provider usage was 10,731/363 and 11,344/344 tokens; the second request read 10,624 cached tokens. | Phoenix chat spans; total latency 8.7 s. |

Trace: [c85f6355](http://127.0.0.1:6006/projects/UHJvamVjdDoy/spans/c85f6355e9524ca0748295bc3e1973bd?timeRangeKey=1h&sessionView=traces).
Source: read-only delegate inspection of the UI, Phoenix, and Postgres.

Unresolved findings from this subcheck, reported to the parent for classification:
- P1: The reply calls the statement day and due day skippable, but ledger validation requires both
  for a card. The card remains unsaved after a later supplied credit limit.
- P1: Home/Today rendered `Cash on hand ₹0` with a current `as of` date for a bank whose balance is
  unknown, and the Accounts summary did the same. The account row itself correctly said
  `₹0 Ledger balance` and `No ledger activity recorded yet`.
- The reply asks for four details at once although the setup instruction says one or two
  questions at a time.
- The reply says to upload the statements and that it will start the import, but the chat
  composer accepts images only; statement PDFs are uploaded in Activity -> Imports, which starts
  the review.

### Fix record (implementation, 5 November 2026)

- Card setup: validation now reports every missing or invalid card field in one message, keeps
  rejected writes atomic, and leaves partial updates working. The model-visible `AccountSetup`
  schema and `update_setup` description now say that an added card needs a positive credit limit,
  statement day, and due day; last4 and a bank balance stay optional. Instructions say those
  dates are not skippable and, when unknown, the card stays pending.
- Import route: instructions now send statement PDFs to Activity -> Imports, say Chat photo is
  images only, and keep `import_document` limited to an existing attachment id.
- Cash honesty: `/api/home` and `/api/accounts` carry typed `cash_quality`. Home/Today and the
  Accounts summary show `Unknown` with the account names when any active liquid account has no
  known balance; an explicitly stated zero stays ₹0, and no accounts reads `No accounts`. The
  legacy numeric `spendable_paise` and `liquid_total_paise` fields are unchanged. The weekly
  briefing now says `unknown` in the same case.
- Anchor definition corrected (P1 follow-up): a balance is known only when an explicit opening
  balance, user-stated balance, or committed statement writes `statement_balance_paise` with a
  valid `statement_balance_as_of`, including an explicit zero. A ledger expense or income changes
  a known balance but no longer creates one. The same shared rule now drives account summaries,
  Home/Today `cash_quality`, `get_financial_context`, the weekly briefing, and projection
  `balance_ages`. The legacy ledger total remains available, but an unknown liquid account keeps
  the complete cash headline Unknown; its known subtotal stays scoped.
- Ledger truth preserved: an unknown account keeps its real recorded net change in the account
  payload and Ledger. No movements were deleted, reset, or given a fabricated zero opening.
- Statement precedence: a backdated import no longer replaces a newer stated balance and does not
  erase an anchor when the statement has no closing balance. Undoing an import restores the prior
  anchor only while that import is still the effective source; undoing an ordinary expense leaves
  a surviving anchor intact.
- Tests: card missing/invalid fields, atomic rejection, partial update, known/unknown/zero/empty
  cash quality, Home payload states, movement-only unknown accounts in the shared definition,
  account summary, Home, model context, briefing and projection, explicit zero openings, stated
  balance after activity, sole-anchor undo, expense undo with a surviving anchor, mixed accounts,
  verified import commit/undo, and backdated import precedence. Frontend typecheck, lint, and
  production build pass.
- Manual remaining: the live card add still needs the user's next real message with the actual
  limit and both dates; the owner instance was not written to. The import route needs a real
  Activity -> Imports upload. Home/Today and Accounts were checked read-only with the existing
  unknown-balance SBI account; the movement-only regression is automated-test verified because
  demonstrating it live would require writing a fake bank transaction to the owner instance.

J02 is Partial: the first setup turn and the card retry state are observed. J03 remains Not run.

### Trace preview privacy fix (5 November 2026)

- Defect: `RootSpanIO` wrote the raw current prompt to `input.value` before the input guardrail
  sanitized it, so a pasted secret could appear in the Phoenix trace preview even though the model
  received only the redacted text.
- Fix: the capability now applies the existing `fin_agent.agent.redaction.redact_text` policy
  before setting `input.value`. No second detector copy, SDK patch, or temporary raw write was
  added; `include_content`, status, and `user.id` behavior are unchanged.
- Regression: a synthetic vendor-shaped key in `tests/test_agent_trace_preview.py` uses an
  in-memory exporter to assert the trace preview and the model-visible prompt contain no raw key
  and that the redacted placeholder reaches both. Trace-preview, native tracing smoke, and
  redaction/safety tests passed. No real secret or owner data was used.

## Run header

Copy this header for each execution block. Keep credentials and private financial values in
the ignored evidence folder, not in this tracked log.

| Field | Value to record |
|---|---|
| Run ID and operator | |
| Start/end time and ledger date/time zone | |
| App URL and instance label | |
| Commit/build and dirty-diff identity | |
| Model/provider and relevant configuration changes | |
| Browser/device/viewport; installed PWA or browser tab | |
| Starting-state checkpoint and restore/recreation method | |
| Real/synthetic fixture aliases and independent reference version | |
| Fault/device/operator prerequisites | |
| Expected model/spend limits and known pricing limitations | |

## Journey progress

Update individual case results below; do not mark a whole journey Pass because its happy path
worked. A journey is Partial until its required cases and invariants have been accepted or
explicitly deferred with a reason.

| Journey | Area | Initial state | Status | Case results / finding links |
|---|---|---|---|---|
| J01 | Greeting and empty-data honesty | E | Not run | O01-O06 provide starting subcheck evidence only. |
| J02 | Conversational setup | E → S | Partial | First setup turn and card retry observed in c85f6355. Card guidance, import route, and cash truth fixed in working tree; live card and import retest open. |
| J03 | Account identity and balances | S / H-copy | Not run | |
| J04 | Precise daily expense | S | Not run | |
| J05 | Clarification, defaults, duplicates | S | Not run | |
| J06 | Corrections, deletion, undo | S | Not run | |
| J07 | Category-rule scope | S | Not run | |
| J08 | Received versus expected income | S | Not run | |
| J09 | Own-account transfers | S | Not run | |
| J10 | Splits and partial settlements | S | Not run | |
| J11 | Lending, borrowing, reimbursement | S | Not run | |
| J12 | Credit-card lifecycle | S | Not run | |
| J13 | Loans and prepayment | S | Not run | |
| J14 | Goals and fenced savings | S | Not run | |
| J15 | Schedules and calendar boundaries | S / F | Not run | |
| J16 | Protected SBI import | H / H-copy | Not run | Password required; never place it in this log or chat. |
| J17 | First Slice cycle | H / H-copy | Partial; blocked before commit | All eight source rows independently checked; F11/F12 require fixes before accepting the financial import. |
| J18 | Multicycle backfill | H / H-copy | Not run | |
| J19 | Capture/statement reconciliation | H-copy | Not run | |
| J20 | Import reject, commit, undo | H-copy | Not run | |
| J21 | Import failure/format fixtures | E / H-copy / F | Not run | Fixtures beyond the six real PDFs still needed. |
| J22 | Analytics, coverage, sources | S / H | Not run | |
| J23 | Engine-derived affordability | S | Not run | |
| J24 | Read-only what-ifs | S / H-copy | Not run | |
| J25 | Memory, history, stale snapshots | S / F | Not run | Known rollover and compaction risks below. |
| J26 | Photo and UPI screenshot | S / D | Not run | Synthetic/redacted capture fixtures needed. |
| J27 | Voice capture | S / D | Not run | Actual microphone and STT needed. |
| J28 | Outside text, approval, secrets | S / H-copy | Not run | Use synthetic attack/secret fixtures only. |
| J29 | Streaming and reconnect | S / F | Not run | |
| J30 | Proactive and scheduled questions | S / F / D | Not run | Due-time/device prerequisites needed. |
| J31 | Consistent views and evidence | S / H | Not run | |
| J32 | Settings and exports | S | Not run | |
| J33 | Passkeys and sessions | E / D | Not run | Use disposable credentials for destructive paths. |
| J34 | Dependency failures and limits | F | Not run | Fault environment not established by planning. |
| J35 | Mobile, accessible, offline/PWA | S / D | Not run | |
| J36 | Golden day and monthly close | S / H-copy / H | Not run | Golden expected arithmetic checked during planning; UI run still required. |

## Case record template

Use one record for a failed/high-risk case; group routine passing variations only when their
case IDs and evidence are still individually identifiable.

~~~text
Case ID / run ID:
Status: Not run | Running | Pass | Fail | Blocked | Deferred
Starting checkpoint and dependency cases:
Exact UI action sequence / submitted prompt:
Expected chat, card, and money effects:
Actual chat/card result:
Before/after values and independent oracle: [private evidence reference]
Sources, movement/import/job IDs: [private when sensitive]
Trace URL, conversation/session, timestamp:
Model requests, tool calls, usage, cache fields, retry/fallback/ending:
Observed correlated log/error details: [sanitized]
Reload/persistence and reversal checks:
Finding ID / severity / owner:
Fix build and retest case IDs:
Cleanup or final checkpoint:
~~~

An absent trace after an exporter outage is an evidence gap, not proof that no financial write
occurred. A screenshot of a success card is insufficient if the ledger contradicts it.

## Findings and follow-ups

These are starting investigation items, not claims that the new journeys reproduced them.

| Finding | Cases | State | Next evidence needed |
|---|---|---|---|
| F01: time-derived snapshot may remain stale without audited writes | J25.06-J25.07 | Open; source-path inference | Idle thread across a ledger-date boundary, inspect snapshot/reminder and current answer. |
| F02: refresh may duplicate a pin after compaction changes history position | J25.08 | Open; source-path inference | Actual compaction followed by refresh; inspect exactly one current effective snapshot. |
| F03: Flag does not currently provide a direct trace navigation path | J31, evidence protocol | Open product gap | Flag a synthetic reply and verify discoverability via conversation/time; decide intended navigation separately. |
| F04: model unpriced, displayed $0 is misleading as spend evidence | J01.10, J34.09 | Open measurement limitation | Verify provider/model pricing and ceiling behavior independently before claiming real monetary metering. |
| F05: exporter batching configuration merits review | J34.07 | Open follow-up | Test telemetry outage impact in F; do not infer the app path from a healthy local sample. |
| F06: deferred-approval preview and multimodal privacy lack dedicated reported regression tests | J26, J28 | Open coverage gap | Exercise actual pending approval/content-disabled multimodal paths and inspect exported attributes. |
| F07 (P1): card create requirements were undiscoverable, validation failed one field at a time, and the reply called required dates skippable | J02, J12, J17 | Fixed in working tree; live retest open | Add a card with missing or invalid limit/days; confirm one aggregated message, zero account rows on failure, partial update still works, and the reply leaves the card pending instead of inventing values. |
| F08 (P1): Home/Today and the Accounts summary presented an unknown liquid balance as complete ₹0 cash | J03.07, J16, J28, J31 | Fixed in working tree; read-only UI evidence recorded; live retest open | Recheck Home/Today and Accounts after a fresh turn or reload; cover all-known, known-zero, no-accounts, one-unknown, and partial-known states without writing to the owner instance. |
| F09 (P1): an expense or income made an account without a stated opening balance look known | J03, J04, J08, J31 | Fixed in working tree; automated regressions pass; live UI retest open | Confirm Home/Today, Accounts, the model snapshot, the briefing, and projection quality still say Unknown for a movement-only account. Live proof needs a disposable synthetic ledger or a real statement import, not a fake entry in the owner instance. |

## Completion and deferrals

Before accepting a gate, list its executed case IDs, financial checks, and trace evidence.
Any deferral names the unavailable prerequisite, the risk left open, the responsible person,
and the next action. Do not label the whole agent end-to-end tested after greeting-only checks.
No journey is silently waived because it needs a phone, a password entry, a longer-running
thread, a due-time job, or an isolated fault environment.

## Runtime reload verification (5 November 2026)

Read-only pre-check, then an api-only rebuild and recreate. No application code was edited, no
test suite was rerun, and no database was reset, seeded, or cleaned.

| Field | Value |
|---|---|
| Active work before reload | 0 conversations with `run_started_at`, 0 pending/running jobs, 0 imports in `parsing`/`needs_password`/`needs_input`; no deferral needed. |
| Prior api container / image | `10fa093d11ac`, image `sha256:93dba99b1287ac604e829e22693956cae072411b019ede61f7220aab442a0183`, created 12:48:23 UTC. Recorded before rebuild; the tag now resolves to the new image and the old ID no longer exists locally, so recovery requires rebuilding from prior source rather than pulling that ID. |
| Rebuild | `docker compose build api` with the existing Compose file; tag `ghcr.io/joy9001/fin-agent:0.1.2`. |
| Recreate | `docker compose up -d --no-deps api`. `db`, `backup`, and `phoenix` stayed up in their existing containers; volumes untouched. |
| New api container / image | `894089dd9ec7`, image `sha256:4085d25f7fa61dd34c48f13cf19b231d6a08328c5a58ed24f505e20a02cf0ef1`, started 13:24:40 UTC, restart count 0. |
| Health | `GET /api/health` returned `{"status":"ok","database":"ok","schema_version":12}`; Compose health `healthy`. |
| Startup errors | No error, traceback, exception, failed, or critical lines in the api logs since the recreate. |
| Loaded balance rule | Imported `fin_agent.ledger.common` inside the recreated container and inspected `has_known_balance`: it requires both `statement_balance_paise` and `statement_balance_as_of`. Runtime cases: both present (including explicit zero) True; paise only False; as-of only False; neither False; ledger activity alone False. |
| Source identity | Container `common.py` SHA-256 `006775ba396c0afb03f55d84666a909e4bf5ea3c475420e6cb4d0eaf8b18aa5c` equals the host working-tree file. `read.py`, `accounts.py`, `importers/commit.py`, and `tools/projection.py` also hash-match the host. |

UI check: `preview_status` reported no existing tab. `preview_open` on `http://127.0.0.1:8000`
loaded the Fin-Agent page but rendered the passkey sign-in screen ("This instance is behind one
passkey"). Browser work stopped at that boundary; no authenticated UI was observed and no sign-in
was attempted.

Outstanding manual UI check: an authenticated owner should sign in and confirm Home/Today and the
Accounts summary still show `Unknown` for the unknown-balance SBI account (F08), and that a
movement-only account stays Unknown (F09). This run made no financial writes and sent no chat
messages.

## Chat stream validation fix (6 November 2026)

Observed failure: the user uploaded one real Slice statement in chat and sent "Import this
statement into my Slice credit-card account. Let me review it before committing." The import
started, then the frontend rejected the assistant's streamed query card with
`Type validation failed: Value: {"type":"data-card","id":null,...,"transient":null}` and showed
"Fin-Agent could not finish that answer."

| Item | Evidence |
|---|---|
| Root cause | Fin-Agent's own SSE encoder (`agent/cards.py:data_part`) always wrote `id` and `transient`, including `null`. The installed client parser (`ai@7.0.159`, `uiMessageChunkSchema`) declares `id: z.string().optional()` and `transient: z.boolean().optional()`; null fails. Pydantic AI's own chunk encoder already uses `exclude_none=True`, so only the hand-built parts were malformed. A read-only reproduction with the installed `parseJsonEventStream` produced the same `AI_TypeValidationError` shape. |
| Fix | `agent/cards.py` now omits unset `id`/`transient` in `data_part` and `with_data`, preserving explicit ids and `transient: false`; `agent/turns.py:encode_saved_turn` strips null optionals when replaying older stored parts; `api/capture.py:_save_card` and `agent/history.py:recover_crashed_runs` no longer store `transient: null`. Client validation was not loosened and no card was removed. |
| Regression | New `tests/test_chat_stream_schema.py` streams a real `run_code` -> `run_query` card through `/api/chat` and validates the emitted SSE with the installed frontend `ai` package (`parseJsonEventStream` + `uiMessageChunkSchema`). It also covers replayed legacy nulls, an import card with explicit `id`, and `transient: false`. Result: 3 new tests passed; with `tests/test_chat_statement_attachment.py`, 9 passed. Ruff check and format pass on the touched files. |
| Import state (read-only) | Exactly one import exists: `imp_01M6PXJGRJFBE83FDGDHG0MHER`, status `review`, account linked (`acc_01M6NVSZMAEZCTVCN6S2RG6X43`), 8 new parsed lines, created 2026-11-05 22:34:19 UTC. Its `import_statement` job is `done`; no job is pending/running and no conversation has `run_started_at`. No upload, parse retry, approval, commit, cancellation, or owner-data write was performed. |
| Runtime | No active turn/import job before reload. `docker compose build api` + `docker compose up -d --no-deps api` only; `db`, `backup`, `phoenix`, and `speaches` stayed up and volumes were untouched. New api container `b9c90ff3dafb` is healthy; `GET /api/health` returned `{"status":"ok","database":"ok","schema_version":12}`; no errors in the recent api log. Container SHA-256 for `cards.py`, `turns.py`, `history.py`, and `capture.py` matches the host working tree, and an in-container check confirms unset optionals are omitted while explicit values are preserved. |
| UI boundary | `preview_status` reported no preview tab (`available: false`), so no authenticated live UI verification was possible; no sign-in or browser switch was attempted. This is protocol-level regression evidence, not a claim that the owner UI was retested. |

The import card was already at `review` before the reload, so no second import is needed. The
review/commit step remains the owner's human UI action. The client bundle did not change, so a
page refresh is not required for the fix; retrying the failed answer (or sending the next message)
uses the fixed API stream.

## Independent source review of the first chat statement (6 November 2026)

The parent performed this bounded check after the user explicitly allowed direct work because
two DeepSeek verification attempts failed with provider overload. This was a read-only source
review, not a new upload, parser retry, approval, or financial commit.

| Item | Observed result |
|---|---|
| Source identity | Uploaded attachment and local Slice June PDF have the same SHA-256. Detailed evidence is confined to ignored private/e2e/slice-june-review.md. |
| Extraction | All eight transaction rows match the source's full dates/year, narration, exact paise and debit/credit direction. Six purchases and two credits; no extra summary/rewards/GST rows, omissions, or duplicates found. Printed period and summary arithmetic also match. |
| Current import | One import remains in review, linked to Slice; job done; no line has a committed movement ID. No owner financial changes were made by the check. |
| F11 (P0): credit booking is incorrect | Both source credits lack an own-account transfer suggestion. The commit path in importers/commit.py:_new_movement treats positive unclassified lines as income. A merchant refund must offset consumption, and a card repayment must not become income. The latter needs a human funding-account decision; the current review has no pending transfer decision and no general row-type editor. Do not commit this review as-is. |
| F12 (P1): absent opening is falsely labeled printed | The generated parser defaults a missing ledger opening to zero. The PDF prints no opening balance, but check_result says opening_source=printed. A balancing sum is not independent evidence of a printed opening. Correct provenance/verification must cover the existing review, not just future parser instructions. |
| UI boundary | T3 preview opened a temporary app tab, but snapshot timed out twice with Preview automation snapshot timed out after 15000ms. The tab was closed; no live signed-in review acceptance is claimed. |

J17 has partial source-extraction evidence but is blocked on F11/F12 before financial commit.
No full journey is marked Pass. The ignored private review contains exact source comparisons;
public findings deliberately omit transaction amounts and counterparties.

## F11 card-credit booking fix, local implementation (6 November 2026)

F11 is fixed in the working tree only. No deployment, image rebuild, owner-data write, import
retry, approval, or commit was performed, and no live signed-in UI or owner import acceptance is
claimed. The existing Slice review was not modified.

| Item | Result |
|---|---|
| Root cause | `importers/commit.py:_new_movement` booked every remaining positive statement line as income, including card refunds and card repayments. |
| Decision model | A positive unmatched line on a card account now requires an explicit `credit_kind` recorded in `statement_lines.match_detail`: `refund`, `transfer` (own-account repayment), or `income`. Card direction alone no longer implies income. No migration was needed. |
| Refund | Booked with the existing `record_refund`, so it offsets consumption in the selected/suggested category instead of creating income. |
| Repayment | Booked with the existing balanced `record_transfer`; the user must choose an active bank/cash/wallet account. The card itself, a system account, missing, inactive, or unknown accounts are rejected. No funding account is assumed. |
| Explicit income | Still possible only when the user selects "Other income" for that card credit. |
| Preflight | `commit_import` now blocks unclassified card credits and classified repayments with a missing/invalid funding account before the line loop, so no ledger, capture, or payee writes happen. Ignored lines, reconciled movements, and genuinely suggested/confirmed transfers keep their previous paths. |
| Review API | Narrow typed extension: `PATCH /api/imports/{id}/lines/{line_id}` accepts `resolution=classify_credit` with optional `credit_kind`, `category_id`, `source_account_id`. The import payload exposes `credit_kind`, `credit_source_account_id`, and `credit_decision_required` per line, so existing saved reviews acquire the controls without re-uploading. |
| Shared UI | The existing `ImportsPanel` (used by Activity -> Imports and chat deep links) shows a Card credits decision block with the three choices and a funding-account selector for repayments. It also counts these in the decision blockers and line table. No redesign. |
| Tests | New focused tests in `tests/test_import_pipeline.py` cover: refund reduces spending and never creates income; repayment debits the chosen bank and reduces card liability with no income/spending; missing classification and missing funding account block before any movement is written; unknown/same/system funding accounts are rejected; explicit income still works; a confirmed suggested transfer still commits. Existing transfer and reconciliation tests were kept. |
| Checks | `pytest tests/test_import_pipeline.py tests/test_import_reconcile.py tests/test_import_api.py` -> 35 passed. Ruff check/format on touched Python files passed. `pnpm typecheck`, `pnpm lint`, and `pnpm build` passed. |
| UI / runtime boundary | Not deployed or reloaded; no authenticated live UI check. The frontend bundle was built locally only. |
| Remaining | F12 (absent opening balance wrongly labeled as printed) is a separate next task and is not addressed here. The existing review must still be reviewed and committed by the owner after both fixes. |

## F12 opening-provenance guard, local implementation (6 November 2026)

F12 is narrowed to the affected card path in the working tree only. No deployment, image rebuild,
owner-data write, import retry, approval, or commit was performed. The existing review was not
modified.

| Item | Result |
|---|---|
| Provenance rule | A parser-supplied zero is recorded as `parser-zero`, never as `printed`. A user value is `manual`; a host ledger value is `ledger`. A parser's nonzero value still carries the existing `printed` label for bank compatibility. |
| Card guard | `commit_import` now re-evaluates the saved evidence before any write. A first card import with neither a user manual opening nor a trusted prior/ledger opening is blocked with an opening-input conflict, even when the saved check says `opening_source=printed`. |
| Resolution | The existing `review.manual_opening_paise` and `POST /api/imports/{id}/opening` route are reused. For a review that already has rows, the host rechecks the stored rows in place and labels the result `manual`, so F11 card-credit decisions survive; an import with no rows keeps the existing job retry. The Imports review screen shows the opening input and disables commit until it is resolved. |
| Compatibility | Bank imports keep their previous contract, including printed openings with no running-balance column. Empty statements and manual zero remain valid. Signed manual card values are accepted rather than clamped. General historical-anchor/source-proof redesign is deferred. |
| Files | `importers/checks.py`, `importers/commit.py`, `importers/pipeline.py`, `importers/monty.py`, `agent/parse_run.py`, `agent/skills/parse-statement/SKILL.md`, `api/imports.py`, `web/src/panels/Imports.tsx`, `web/src/types.ts`, plus focused tests. |
| Tests | `pytest tests/test_importers.py tests/test_import_pipeline.py tests/test_import_api.py` -> 50 passed. Directly affected import set: `pytest tests/test_import_reconcile.py tests/test_agent_parse_run.py tests/test_import_injection.py tests/test_import_history_pagination.py tests/test_chat_statement_attachment.py` -> 26 passed. Ruff check/format passed on touched Python files; `pnpm typecheck`, `pnpm lint`, and the production build passed. |
| UI / runtime boundary | Not deployed or reloaded; no authenticated live UI check. The owner still performs the review and commit action. |
| Remaining | The owner must confirm the actual opening (including an explicit zero if true) on the existing card review, then classify the two card credits under F11, then commit. A general historical-anchor rule for bank gaps was explicitly deferred to keep this fix local. |

## F11/F12 local deployment and review payload check (6 November 2026)

F11/F12 are deployed to the local api. This was a read-only verification: no owner-data write,
import retry, approval, cancellation, or commit was performed, and no signed-in browser UI check
is claimed.

| Item | Result |
|---|---|
| Deployment | `docker compose build api` then `docker compose up -d --no-deps api` with the existing project configuration (`compose.yaml;compose.gpu.yaml`) and no profile change. Only api was recreated (container `917350a65a3a`, image `ghcr.io/joy9001/fin-agent:0.1.2`); `db`, `backup`, `phoenix`, and `speaches` kept their previous uptime and volumes. No Compose file was edited. |
| Health | `GET /api/health` -> `{"status":"ok","database":"ok","schema_version":12}`; the container is healthy and the recent api log has no errors or tracebacks. |
| Source identity | HEAD `db86223`; the working tree still holds the uncommitted F11/F12 changes. Container `/app` sources and the installed package under `.venv/.../site-packages` match the working tree by SHA-256 for `importers/pipeline.py`, `checks.py`, `commit.py`, `reconcile.py`, `monty.py`, and `api/imports.py`. |
| Active work | Read-only check before reload: no pending/running job and no conversation with `run_started_at` or `active_run_id`. |
| Review payload | `import_payload` read inside `BEGIN READ ONLY` (`transaction_read_only=on`). One import `imp_01M6PXJGRJFBE83FDGDHG0MHER`, status `review`, `committed_at` null, 8 lines all `new`, no movement IDs. Account is the Slice card (`acc_01M6NVSZMAEZCTVCN6S2RG6X43`). |
| Opening evidence | `opening_required=true`, `opening_evidenced=false`, and the review has no manual opening. The stored `check_result` still carries the old parser label `opening_source=printed` with the parser fallback value; the payload does not treat it as established evidence, so the commit guard stays active. The review bundle keys the opening input off `opening_required` and does not present the old label as a printed fact. |
| Card credits | Both card credits are already classified by the user (`credit_classified_by=user`, updated 06 Nov 07:23 and 07:25 UTC): one repayment with a funding account and one merchant refund with a category. `credit_decision_required` is false for every line, so no unresolved credit decisions remain. This differs from the brief's expectation of two unresolved decisions; this check did not alter the rows. |
| Web bundle | `/app/web/dist/index.html` references `assets/index-DKfpKnBX.js`, which contains `Opening balance needed`, `Card credits`, `Merchant refund`, `Card repayment from my account`, and `Other income`. This is bundle verification only, not signed-in UI acceptance. |
| Remaining human steps | In Activity -> Imports, open this review, enter the real card opening (explicit zero only if true), review the two already-classified credits, and commit. If either classification is wrong, change it before committing. |
