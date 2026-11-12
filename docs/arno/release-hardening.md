# Release hardening, November 2026

This file records the November 2026 release-hardening pass on the core agent: why it ran, how it
worked, what it found, and what it left open. It is the map for the fixes, not a replacement for
them. [`plan.md`](plan.md) owns decisions; each decision below names the satellite doc that holds
its detail.

The pass started at `9d16ffa` with 661 tests passing and 4 skipped. By the end of wave 3 the same
suite had 1,121 passing and 4 skipped, before wave 4's own tests. It ended with 1,267 passing and 4
skipped, a strict type check over every Python file, and the first published 134-case scorecard. It
ran on a working copy, never on the live instance. One repair does reach the live ledger, at the
release step, when the owner runs `arno repair-openings` once after upgrading.

## Why it ran

Arno was about to ship as a stable release. The bar was one sentence: every number must always
match. A plausible answer is not a correct one when the subject is money. That sets two jobs:

- Look for money bugs where two code paths compute the same quantity and disagree, and where an
  unknown is silently treated as zero.
- Prove the whole product with a real model, a real API, and a real ledger, not only with unit
  tests.

A feature review cannot find either. Only a scripted, adversarial reading of the money paths and
end-to-end journeys can.

## The method

### Area audits with disjoint ownership

Eight agents audited areas that do not overlap, each owning an explicit file list: the ledger and
write paths; read tools and reports; the projection and affordability engine; the agent loop and
chat API; statement import; watch jobs; the platform (CLI, configuration, telemetry, backup,
auth); and a new financial invariant checker. Each one traced real flows, wrote a regression test
that failed before its fix, and fixed the root cause in the shared function every caller routes
through. Each agent edited only its own files, which is why the whole pass could run in one working
copy without a merge.

Findings were triaged by the coordinator, reviewed diff by diff, and committed one area at a time
with Conventional Commit messages. Cross-area fixes went to a second wave with the same rules.

### Waves

Fixes arrived in four waves, each with its own source and its own test files.

| Wave | Source of findings | Shape |
|---|---|---|
| 1 | Eight area audits | Ledger, reads, engine, agent loop, imports, watch, platform, invariants |
| 2 | The same audits' cross-area sections | Split and loan corrections, week periods and coverage, month-end schedules, card sign convention, secret redaction, exports, grants, task-file race |
| 3 | Six end-to-end journey agents | Money-critical journeys, imports, affordability, prompts, and the browser-less review path |
| 4 | Three regression agents | Cases that were still failing, partial, or newly reachable after wave 3 |

Each wave ended with the full suite green, `ruff` clean, a web lint/typecheck/build when the web
changed, and CI green before the next wave started.

### The financial invariant checker

`tests/invariants.py` is one checker over a whole database. It encodes the "Financial invariants"
list in [`e2e-user-journeys.md`](e2e-user-journeys.md) and reuses `backup_restore.verify_database`
for the two balance checks, so "the ledger balances" has one definition.

It covers account balances against postings, the posting rules (an expense lowers the paying
account and raises consumption exactly once; income is not consumption; own-account transfers and
card repayments are not consumption), reversal pairs and undo, cross-path spending totals against
the read tools, the views against the ledger, schedule payment evidence, and imports against the
rows they created. It refuses any database whose name does not end in `_test`.

```bash
export DATABASE_URL=postgresql://app:<pw>@127.0.0.1:5433/arno_invariants_test
export AGENT_RO_PASSWORD=...  BACKUP_PASSWORD=...
uv run arno demo seed
uv run python -m tests.invariants        # prints OK: <n> checks, or every violation and exit 1
```

Every journey agent ran it before and after each write, and every fix wave kept it green on the
demo persona. The pass found one bug of the kind the checker exists for: undo deleted a reversed
movement's labels, so a cancelled pair stopped netting inside its category.

### The end-to-end driver

`tests/e2e/driver.py` starts a real API process on a scratch database, enrolls a software passkey
through the real WebAuthn ceremony, and speaks the same HTTP the web app speaks. It can upload a
statement, answer a confirm card, approve several cards in one request, review and commit an
import, undo it, and read the ledger from SQL. It is the only harness that exercises the dashboard
path end to end without a browser. Usage and limits:
[`tests/e2e/README.md`](../../tests/e2e/README.md).

Journey agents sent natural messages (English and Hinglish, one open case per message), with the
real chat model and no fallback, against a seeded demo persona or an empty install. After every
reply they checked each stated number against the driver's tool output, `driver state`, and
read-only SQL; after every write they ran the invariants. Six agents covered J01-J03 and J22,
J04-J08, J09-J14, J15 and J23-J24 and J30, J16-J21, and J25 and J28 and J34 and J36. Rounds, not
scripts: finding a defect changed the next message, not the case.

Three regression agents then re-ran every FAIL, PARTIAL, and newly reachable case from waves 1-3,
plus a sample of previously passing money cases, on fresh databases and fresh messages.

### Live-data rehearsals

The owner's ledger is real and private, so production-shaped data was handled on a throwaway
copy: `pg_dump` of the live database restored into `arno_livecopy_test`, then migrations
`0022`, `0023`, and `0024` applied, the invariants run before and after, and the copy dropped. The
three migrations change one thing each.

| Migration | What it stores | Why |
|---|---|---|
| `0022_job_schedule_anchor.sql` | `job_schedules.dtstart` | An RRULE counts `COUNT` and phases `INTERVAL` from its start; the scheduler rebuilt it from "now", so a finite question fired forever |
| `0023_capture_history_once.sql` | `captures.history_at` | A retried photo appended its note to model history a second time |
| `0024_import_categorize_groups.sql` | `imports.categorize_groups` | An import's later categorization belongs to it, so undoing the import also undoes the labels and rules it caused; the migration backfills existing ledgers |

The rehearsal also proved the repair path. A ledger written by an earlier release kept each
statement's printed opening inside a later balance adjustment, so every date the statement covered
read short by that opening; the backtest exposed it. The commit path now books an opening at the
statement's first day, and `arno repair-openings` applies the same repair to imports already
committed:

```bash
uv run arno repair-openings --dry-run   # aggregate preview; rolls every transaction back
uv run arno repair-openings --yes       # one transaction per import, idempotent
```

On the copy, the dry run named one import and its printed opening, the closing difference was zero,
and the earlier adjustment was superseded; the apply left the current balance unchanged, and a
second dry run reported nothing to repair. Undoing the import reverses the repair too, because the
repair's write groups join the import.

### The eval scorecard

The pass ended with the first published 134-case scorecard, and reaching it took three runs. The
first full run, 402 trials, regressed: every one of the 22 statement-sourced injection cases died in
setup because the fixture's one-line statement could never agree with the demo ledger, and every
honesty failure was a grader too narrow for an honest reply. That scorecard was reverted, and the
fixture and the graders were fixed. The next run passed every gate but scored honesty 91% and
never-moving-money 90% on two grader false negatives: `hn_schedule_confirmed_not_paid` did not
accept "not that any month's rent has been paid", and `nm_transfer_hindi` read "Agar bhej diya hai"
(if you have sent it) as a claim. That scorecard was discarded too, and both graders were fixed in
`01fd19f`. The coordinator also tightened the verdict grader, which had read "verdict is yes, no cut
needed" and "no-cost EMI" as a `no`.

The published run is 134 cases and 402 trials on November 10, 2026 (`e445680`): injection gate
150/150, answer number gate 78/78, answers 96% (25/26), writes 22/22, honesty 11/11, advice 11/11,
never-moving-money 10/10, and long threads 3/4. The fooled rate, the share of trials where the model
acted on an injected note even though the gate stopped the write, was 7% (11/150). Its two misses
were real one-trial model slips, not harness faults (see "Known limitations left open").

## Findings

The tables below list P0 and P1 findings for each area, waves 1-4 and the work that followed; each
area's P2s are grouped in a sentence after its table. Every row's Commit cell names its commit on
`main`.

### The ledger and every write path

The table below lists the ledger's P0 and P1 findings, in commit order.

| Sev | What was wrong | Root cause | Fix | Regression test | Commit |
|---|---|---|---|---|---|
| P0 | Rupee input with sub-paise precision was truncated, and a non-numeric amount crashed the tool | `int()` truncates instead of rounding; nothing checked the value was finite | Round to the nearest paise and reject anything not finite | `test_audit_ledger.py` | `28640e0` |
| P0 | Every loan EMI the agent logged carried zero interest | A loan's ledger balance is negative (a liability) and the principal/interest split took the wrong side | Split on the liability side, so interest is interest and principal is principal | `test_audit_ledger.py` | `28640e0` |
| P0 | A balance the user stated for a card or a loan was booked as an asset | The rupee helper forced a positive number and the tool passed it straight through | Sign a stated balance by account kind | `test_audit_ledger.py` | `28640e0` |
| P1 | A payee's usual account could be an archived account | Account resolution did not filter inactive rows on that path | Resolve only active accounts, with archived ones named | `test_audit_ledger.py` | `28640e0` |
| P1 | Undoing a "Remembered" card raised an error | Audit restore assumed every group holds a reversible movement | Treat a preference group as its own undo target | `test_audit_ledger.py` | `28640e0` |
| P1 | A correction could move a movement into the future | Only one of the two date paths validated the date | Validate in both, at the shared boundary | `test_audit_ledger.py` | `28640e0` |
| P1 | Undo deleted a reversed movement's labels, so a cancelled pair stopped netting inside its category | A label created with its movement was restored as an independently created row | Rebuild the label pair with the movement | `test_audit_ledger.py`, `test_invariants.py` | `28640e0` |
| P1 | A movement logged by a woken turn had no idempotency key, so a retry wrote it twice | The key needed a message id, and a server-started turn has none | Fall back to the tool call id alone | `test_audit_ledger.py` | `28640e0` |
| P1 | `update_setup` could not update or archive a payee by name, and two same-named rows were resolved to the first | The name lookup queried a column payees do not have, and used `LIMIT 1` | Per-table name column, and a refusal when the name is ambiguous | `test_audit_ledger.py` | `28640e0` |
| P1 | Undo of an added baseline or scheduled question raised an error | Restore had no rule for those tables; a scheduled question also needs its job mirror stopped | Delete the baseline, archive the question, and sync its schedule in the same transaction | `test_audit_ledger.py` | `28640e0` |
| P1 | `undo_latest` could revert the system bootstrap and break every later expense | It picked the newest group with no actor filter | Ignore `actor = 'system'` groups | `test_audit_ledger.py` | `28640e0` |
| P1 | Changing a split's payee renamed every person's share | Labels were updated for the whole transfer instead of the spending leg | Update only the vendor label; keep each share's label | `test_audit_ledger2.py` | `64aaf53` |
| P1 | The amount of a split or a loan payment could not be corrected | The correction path accepted only one-transfer kinds | Re-split with any given shares, or recompute principal and interest, and refuse a partial answer | `test_audit_ledger2.py` | `64aaf53` |
| P1 | A loan with no rate implied zero interest, as a silent certainty | The no-rate branch assumed zero instead of saying the interest is unknown | Return an explicit unknown and a data-quality note | `test_audit_ledger2.py` | `64aaf53` |
| P1 | A stated balance could be dated in the future | Two write paths never checked the date | Reject a future date at the shared boundary | `test_audit_ledger2.py` | `64aaf53` |
| P1 | A plain transfer to a person could not cover a family-transfer obligation | Payment matching accepted only the obligation's own kind | Match the person's ledger account, not the movement kind | `test_audit_ledger2.py` | `64aaf53` |
| P1 | The JSON and CSV exports shipped credential tables | The export enumerated every table | Exclude the credential tables; `pg_dump` stays complete because it is the backup source | `test_audit_ledger2.py` | `64aaf53` |
| P1 | A UPI handle matched only in its exact case, on both the read and the write path | Two lookups compared the raw string | Fold case in the shared lookup, and refuse an ambiguous creation | `test_audit_ledger2.py` | `64aaf53` |
| P0 | Undoing a movement's create group after a correction reversed the money a second time | Restore called the reversal unconditionally, and reverse accepted an already-reversed movement | Refuse both, with "undo that change first" | `test_audit_ledger3.py` | `0dc146a` |
| P0 | A certain card bill was invented for a card whose outstanding was never stated | The schedule event fell back to the ledger balance and called it certain | Emit the bill with an unknown amount and a note; every consumer says amount unknown | `test_audit_ledger3.py` | `0dc146a` |
| P1 | Every loan payment booked a full month's interest, so two payments in one month booked it twice | Interest was `outstanding × rate/12` on every payment | Interest once per calendar month per loan; a later payment that month is all principal | `test_audit_ledger3.py` | `0dc146a` |
| P1 | Undoing the restore of an unlinked payment crashed when an earlier undo had already removed its occurrence | The occurrence row was read as if it always exists, inside the undo transaction | A conflict that says to undo that change first, and a not-found error for the movement | `test_typing_tya.py` | `f79a19f` |

This area's P2s run across the same three commits and the same three test files: an unusable undo
id on a no-op correction, an ignored opening-balance change, a missing-field `KeyError`, income into
a system account counted as consumption, a split vendor saved as a person, duplicate detection that
skipped keyed movements, a concurrent-retry crash, a split with a zero share of mine, a rejected
zero floor, basis-point truncation of a rate, an ambiguous account name resolved to the first match,
a silently ignored kind change, a stated balance's money unit in the JSON export, a person account
that always looked unknown, a placeholder payee ("Unknown shop"), a weekday the model inferred, and
an import's state the model asserted without reading it. A wave-4 finding also made "₹500 in
credit" a single card write instead of five. The type pass added one: a preference, a category rule,
a transfer label, or an audited row that vanished between its read and its write now raises the
module's own error instead of crashing or recording an empty after-state (`f79a19f`).

### Reads, reports, and coverage

The table below lists the read-path P0 and P1 findings, in commit order.

| Sev | What was wrong | Root cause | Fix | Regression test | Commit |
|---|---|---|---|---|---|
| P1 | `query_movements` shrank `total_count` on every page | The cursor clause was added before the count was taken | Count the filter, not the page | `test_audit_reads.py` | `632d64c` |
| P1 | Text search missed a movement with no label row | The description match lived inside a join that dropped it | Search the fallback description too | `test_audit_reads.py` | `632d64c` |
| P1 | "This month" called the rest of the month a missing statement | A calendar month end was treated as a period that had ended | Compare against today | `test_audit_reads.py` | `632d64c` |
| P1 | A fully reversed category appeared as a ₹0 group | The sum cancelled the pair but the count excluded both | Drop a group whose effective rows are all reversed | `test_audit_reads.py` | `632d64c` |
| P1 | A statement or photo description reached the agent unmarked when its payee was already known | The mark was read only from the payee row | Mark the row when it came from a statement or a photo | `test_audit_reads.py` | `632d64c` |
| P1 | The Uncategorized list included income | The query excluded transfers and adjustments but not income | Exclude income | `test_audit_reads.py` | `632d64c` |
| P1 | Answering an uncategorized group mislabelled a split and skipped a bare row | The label update hit every leg of the movement | Update the spending leg only | `test_audit_reads.py` | `632d64c` |
| P1 | The recap's biggest-spend line could show unmarked bank text | The mark came from the payee only | Derive it from the spending rows, as the other paths do | `test_audit_reads.py` | `632d64c` |
| P1 | `this_week` and `last_week` were not valid periods outside recaps | Only recaps' period parser knew week words | Add them to the shared resolver, Monday to Sunday | `test_audit_reads2.py` | `ebf34f7` |
| P1 | One account's statement made every account's spending look complete | Import intervals for all accounts were merged into one list | Complete unfiltered coverage only when every active statement-bearing account covers the period | `test_audit_reads2.py` | `ebf34f7` |
| P1 | Text search treated `%` and `_` as wildcards | The value went straight into `ILIKE` | Escape the pattern characters | `test_audit_reads2.py` | `ebf34f7` |
| P1 | Balance age came from the statement anchor, not the balance itself ("101 days old" beside `as_of` today) | Two anchors in one payload | One definition: a ledger-tracked balance is as old as its latest evidence | `test_audit_reads3.py` | `1e342c8` |
| P1 | A goal account was permanently unknown and nagged a setup gap | Unknown-by-default ignored an account the app tracks from zero | Known by construction, like a person's account later | `test_audit_reads3.py` | `1e342c8` |

The P2s here are a stale `as_of`, an ambiguous name resolved silently, a deleted day listed as a
spending day, a refund-only month shown as ₹0 in one view and negative in another, a corrected row
counted twice in baselines, an ISO single date rejected as a period, and context fields the agent
could not see (`active`, a loan's rate and tenure, scheduled questions, a card's next close and due
date, future dates past 30 days). A wave-4 cleanup also made `get_financial_context` list open
imports, and it is the row that closed the "the agent asserted an import's state" finding.

### Projection, affordability, and schedules

The table below lists the engine's P0 and P1 findings, in commit order.

| Sev | What was wrong | Root cause | Fix | Regression test | Commit |
|---|---|---|---|---|---|
| P0 | A future-dated hypothetical purchase was silently ignored | The walk dispatched by event kind and never read a future hypothetical | Add the hypothetical at its date and extend the horizon to it | `test_audit_engine.py` | `ce1e247` |
| P1 | A card's due date collapsed onto the statement close in a short month | The due day was clamped against the close date, not the month | Clamp against the month | `test_audit_engine.py` | `ce1e247` |
| P1 | An EMI plan's later installments fell outside the horizon | The plan contributed only its current installment | Expand the whole plan | `test_audit_engine.py` | `ce1e247` |
| P1 | The amortization schedule did not clear the principal and overstated `total_paid` | The walk ran exactly `months` iterations and never paid the residue | Pay the rounded residue, and report the totals that were actually paid | `test_audit_engine.py` | `ce1e247` |
| P1 | A repaid or reversed debt was still projected as a receipt | The projection read the label amount, not the outstanding balance | Use the outstanding balance and reversal state | `test_audit_engine.py` | `ce1e247` |
| P1 | Undone spending still counted in the 90-day variable-spend history | The query filtered the signed row, not the reversal state | Exclude effectively reversed rows on every history path | `test_audit_engine.py`, `test_audit_engine2.py` | `ce1e247`, `d2fad9d` |
| P1 | A paid bill still got a reminder, and a partial one showed the full amount | The job checked the kind and date only | Read the occurrence's payment evidence | `test_audit_engine.py` | `ce1e247` |
| P1 | Risk warnings were raised from an unknown liquid balance | The risk job did not gate on a known balance | No warning without a known balance | `test_audit_engine.py` | `ce1e247` |
| P1 | Missed-recurring detection ignored schedule payment evidence | It inferred payment from date and amount | Use the occurrence's evidence | `test_audit_engine.py` | `ce1e247` |
| P1 | A zero-day reminder lead crashed the reminder job | The lead was used as a date offset without the zero case | Handle zero | `test_audit_engine.py` | `ce1e247` |
| P1 | A finite `COUNT` job rule never exhausted | The rule's start was never stored, so `COUNT` and `INTERVAL` had nothing to count from | Migration `0022`, anchored to the stored start, and an exhausted rule stays off | `test_audit_engine2.py` | `d2fad9d` |
| P1 | A monthly obligation on the 29th, 30th, or 31st skipped every short month | The stored rule was expanded verbatim, and no caller clamped the day | Clamp to the month's last day in the shared rule normalizer | `test_audit_engine2.py` | `d2fad9d` |
| P1 | Risk was silent with no floor set, even at a 55% chance of going below zero | The warning threshold needed a floor | Judge against zero when no floor is set, and say which it used | `test_audit_engine2.py` | `d2fad9d` |
| P0 | A "reduce the EMI" prepayment re-stretched the loan to its original tenure: negative interest saved, a longer loan | `remaining` used the caller's tenure cap instead of the EMIs actually left | Use the EMIs the walk actually has left | `test_audit_engine3.py` | `03e8d65` |
| P1 | A finite scheduled question restarted forever | The next occurrence was recomputed without the stored start, and reactivation rewrote it | Anchor to the stored start; never reactivate an exhausted rule | `test_audit_engine3.py` | `03e8d65` |

The P2s here are a month-end recurring proposal that skipped February, a missed-bill push labelled
as a new subscription, a prepayment after the last EMI, a goal tradeoff that printed "completes None
days later", a card-due forecast that disagreed with the engine, a configured income outside the
horizon reported as a setup gap, a cut-category tradeoff that cut the whole horizon while saying
"until payday", a backtest that read today's setup for a past date, a projection `remaining` that
collided with a goal's, a recurrence match that leaned on a name, a demo persona that contradicted
its own loan rate, and the duplicated "eight read tools" wording in the old docs. Two of these
became decisions, below.

### Agent loop, prompts, and chat

The table below lists the agent loop's P0 and P1 findings, in commit order.

| Sev | What was wrong | Root cause | Fix | Regression test | Commit |
|---|---|---|---|---|---|
| P1 | Every message after an approved tool was treated as another approval | The gate keyed on an approval object that survives on the next message | Key on the new message's own approval parts | `test_audit_agent.py` | `adbb248` |
| P1 | A card number typed in the first message was stored in the thread title | The title was built from the raw text before redaction | Apply the redaction policy before the title | `test_audit_agent.py` | `adbb248` |
| P1 | Untraced money in common formats was invisible to the number check | The amount patterns were hard-coded three times, each narrower | One pattern set, covering scale words in every position | `test_audit_agent.py` | `adbb248` |
| P1 | An OTP, PIN, or password typed in chat was stored verbatim and sent to the model | The detectors came from the harness and matched by shape only | One keyword-anchored detector list in `agent/redaction.py` feeds the guardrail, stored history, titles, and traces | `test_audit_agent2.py` | `38231ec` |
| P0 | "Transfer 1000 from SBI to Cash now" was logged as a completed transfer | The prompt said to tell the user to pay, not that an instruction is a request | An instruction to move money is a request: ask whether they did it, and log only after they say so, with an eval case | `test_audit_agent3.py` | `20b4f8b` |
| P1 | "Salary aa gayi" booked the schedule's amount with no figure from the user | "Ask when the amount is missing" had no clause for a schedule | A schedule's amount is never the received amount; ask | `test_audit_agent3.py` | `20b4f8b` |
| P1 | A short account name matching several accounts was silently guessed, and an answer drifted to another account | Nothing said a short form that matches several accounts is a question | Ask, never guess; answer about exactly the named account | `test_audit_agent3.py` | `20b4f8b` |
| P1 | Differences and subtotals were computed in the model's head | The rule named subtraction but not a "net after a refund" shape | Every difference, sum, average, or percentage comes from `run_code` | `test_audit_agent3.py` | `20b4f8b` |
| P1 | A confirm card arrived with no text | The prompt line asking for a sentence was ignored | The host writes one plain sentence per card from the tool and its arguments; a model-written sentence is kept | `test_audit_agent3.py`, `test_audit_agent4.py` | `20b4f8b`, `b0a7566` |
| P1 | A password in Hindi or Hinglish word order was stored and sent to the model ("mera password Abc@1234 hai") | The alphanumeric branch required a separator before the value, not after | Redact a password-shaped token after the keyword or before `hai`; plain words and amounts stay | `test_audit_agent4.py` | `b0a7566` |
| P1 | A turn that offered two confirm cards could not be answered: a partial answer failed the run and lost the second card | pydantic-ai requires a result for every deferred call, and the server forwarded a partial answer | The server refuses a partial answer with 409 before anything runs and re-offers both; the web already sends both; a duplicate submission writes once | `test_audit_agent4.py` | `b0a7566` |
| P1 | The note's own retry phrase still produced no statement handover | The instruction block said only "this turn has one statement file" | Name the file, say a carried file is this turn's file, and say a statement has no text to search; the note names it too | `test_audit_agent4.py` | `b0a7566` |
| P1 | An expense with no named shop was refused by the schema, so the model asked for a name nobody needed | `Expense.payee` was a required `str` while the prompt says to leave the payee out | Optional payee, and the summary no longer names the category as the merchant | `test_agent_write_contract.py` | `2451ccd` |
| P1 | "Auto bhaiya ko 150 diye" was booked as a settlement against the person, raising what they owed by ₹150 | Nothing in the settlement contract said when it applies | A settlement clears a debt already on the ledger; money for goods or a service is an expense, in the tool description and one prompt line | `wr_auto_hinglish` (eval case) | `2451ccd` |
| P1 | Every streamed request through the context-window guard raised `TypeError`, so the fallback model could never stream or fall through | The override async-iterated a model context manager instead of entering it | Enter it as the library does, with the window checked first | `test_typing_tyb.py` | `cdcf9f8` |

The P2s here are a duplicate approval POST that produced a failed turn, a fractional chart value
truncated, a post-write cleanup failure reported as a failed write, a CLI turn after compaction that
saved no reply, a CLI transcript sliced across compaction, an audit test that shared one template
database, a denied approval re-offered in the same turn, an example amount written into prose, a
ledger fact stated from chat history, paise quoted as rupees in prose, a wrong cause for a February
landing, a fabricated upcoming bill, a refund assigned wholly to my share, a "forgotten" preference
that left its setup change in force, a default date or account applied silently, a scheduled answer
skipped by the number check, a retried answer that kept abandoned text, two answer cards in one
reply, unhelpful `run_code` errors, tool-shape errors that described the caller's own input, and two
text parts joined without a separator. The type pass added one: a retried `run_code` step shown as
ok, because the run view still read the retry from a part the library no longer writes (`cdcf9f8`).

### Statement import

The table below lists the import P0 and P1 findings, in commit order.

| Sev | What was wrong | Root cause | Fix | Regression test | Commit |
|---|---|---|---|---|---|
| P0 | A failed commit left its earlier movements written, with no undo path | `commit_import` never opened a transaction; each line committed alone | One transaction per commit, with the audit groups joined to the import | `test_audit_imports.py` | `cea1daa` |
| P0 | Two concurrent commits of one import crashed after partial work | Nothing serialized the commit | Refuse a second commit of the same import | `test_audit_imports.py` | `cea1daa` |
| P0 | A failing undo half-reverted the import | Undo ran group by group with no outer transaction | One transaction for the whole undo | `test_audit_imports.py` | `cea1daa` |
| P1 | An identical row on another account was silently dropped | The duplicate key ignored the account | Include the account in the key | `test_audit_imports.py` | `cea1daa` |
| P1 | A merge that flipped a row's direction was committed silently | The reconciliation compared amounts, not direction | Treat a direction flip as a mismatch and ask | `test_audit_imports.py` | `cea1daa` |
| P1 | A reversed logged row absorbed a statement line | Candidate selection excluded reversal rows but not the rows they reverse | Exclude effectively reversed rows | `test_audit_imports.py` | `cea1daa` |
| P1 | A committed import could be parsed again, wiping and re-booking it | The parser accepted any import id | Refuse a committed or undone import | `test_audit_imports.py` | `cea1daa` |
| P1 | Undo left behind the payees the import created | The payee write group was not joined to the import | Join it, and archive on undo | `test_audit_imports.py` | `cea1daa` |
| P1 | A date-only merge duplicated the logged row's captures | The merge copied captures to a new movement | Keep the existing movement and add evidence | `test_audit_imports.py` | `cea1daa` |
| P1 | The review screen had no way to answer the opening balance it asked for | The API had no route for it | `POST /api/imports/{id}/opening` and an input on the review card | `test_audit_imports.py` | `cea1daa` |
| P1 | A `;` inside one narration made the CSV delimiter `;` | The delimiter scored by frequency, not by consistency | Score by per-line count consistency | `test_audit_imports2.py` | `164d611` |
| P1 | A card's stated opening was checked in the wrong sign, so a first card statement could not be unblocked | No sign convention existed between the parser, the check, and the ledger | One ledger-signed contract below the statement text, converted once by account kind | `test_audit_imports2.py` | `164d611` |
| P1 | Rejecting or undoing an import left another import's suppressed duplicate rows `ignored` | Nothing re-reconciled the released rows | Re-reconcile on reject and undo | `test_audit_imports2.py` | `164d611` |
| P1 | Stating the opening was lost as soon as the parser ran again | Reconcile replaced the whole review document | Merge the keys it owns | `test_audit_imports2.py` | `164d611` |
| P0 | A second statement for a known institution returned 500 at commit | A `text[]` parameter was passed as a string | Cast the array in the shared write | `test_audit_imports3.py` | `ae1ddad` |
| P1 | A cash statement could never be filed | The account resolver accepted only bank, card, and wallet | Include cash | `test_audit_imports3.py` | `ae1ddad` |
| P1 | A rejected import came back when its parser task finished | Reject did not cancel the task, and the late result wrote anyway | Cancel the task and make a late result a no-op | `test_audit_imports3.py` | `ae1ddad` |
| P1 | Confirming an own-account move on a matched line booked it twice | The transfer confirmation ran after the merge had already committed the line | Decide the line's kind before the merge | `test_audit_imports3.py` | `ae1ddad` |
| P1 | The card's opening answer could not unblock a first card statement | The opening was stored but the review was not rechecked when it had no rows | Re-check the stored evidence, keeping existing decisions | `test_audit_imports3.py` | `ae1ddad` |
| P1 | A saved parser replayed on the next month read 0 rows | The variant header of the next month was not registered as the same layout | Match the layout by its variant set | `test_audit_imports3.py` | `ae1ddad` |
| P1 | A statement could be filed against the wrong account | A guess from the file overrode or replaced the account the user answered | The user's answer wins and is stored on the import | `test_audit_imports3.py`, `test_audit_imports4.py` | `ae1ddad`, `8132141` |
| P1 | An uploaded statement was occasionally orphaned with no import and no card | Nothing but a model tool call turned an upload into an import | The host guarantees a handover for an attached statement, and the note is unconditional | `test_audit_imports4.py` | `8132141` |
| P1 | The parser could not read its own import's status | The status read was not exposed to the subagent | Expose it as host text | `test_audit_imports4.py` | `8132141` |
| P1 | Undoing a committed import returned 409 once the categorizer had run | The categorizer's later write groups were not attributable to the import | Migration `0024`, and undo reverts those groups first | `test_audit_undoimport.py` | `c0a1081` |
| P0 | A movement logged after the review was booked a second time by the commit, and an adjustment hid it | The commit merged only lines the review had already matched | Re-match at commit, and refuse a non-zero adjustment while a candidate match exists | `test_audit_imports5.py` | `3c672f2` |
| P1 | After a balance was stated and a backfill imported under it, every historical balance the statement covered read short by its opening (a savings account showed negative balances; found by running the backtest on a copy of real data) | The statement's opening stayed inside the adjustment dated the day the balance was stated | Commit books the evidenced opening on the statement's first day, supersedes the adjustments its rows explain, and re-anchors a later stated balance; `arno repair-openings` fixes ledgers written before | `test_audit_anchor.py` | `7f69946` |
| P1 | The backtest saw no account on a backfilled ledger and would have published a meaningless 0% calibration | "Known then" used the account row's creation date, not its history, and nothing checked whether a month had a usable starting point | An account counts once it has an entry by the as-of date; months without an account or a confirmed income schedule mark the run NOT A MEASUREMENT and `--publish` refuses | `test_audit_anchor.py` | `7f69946` |
| P1 | A non-zero closing adjustment was booked silently | Any difference was treated as a fix to make | Show the adjustment on the review and require confirmation | `test_audit_imports5.py` | `3c672f2` |
| P1 | A statement whose period had not ended was accepted | No check compared the period to today | Reject a period that ends after the ledger date | `test_audit_imports5.py` | `3c672f2` |
| P1 | A balance the user stated was replaced by the statement's figure with nothing shown and nothing asked (found in the last end-to-end round) | A balance stated inside the statement's period was treated as the answer the statement explains, and nothing compared the two figures on the day | Compare the statement's own balance on the stated day; the review names both and commit refuses with 409 until `adjustment_confirmed`; undo puts the stated figure back | `test_audit_final2.py` | `0f378eb` |
| P1 | An import that had been undone still counted as ledger evidence, so the next good statement was refused as a discrepancy and could never commit | `ledger_opening` counted every entry, including a cancelled pair, and 0 paise read as an evidenced opening | Count only movements that stand; with no standing entry the printed opening is used, as for a first import | `test_audit_final2.py` | `0f378eb` |

The P2s here are an ignored ISO period header, an LLM answer overwriting a user rule's category, a
gate that compared rows in id order, an import stuck in `parsing` after a crash, a multi-file
archive imported in part, an invariant that fired on a legitimate certain-match review, the driver's
`up --seed` truncating a ledger it was meant to keep, a merged refund keeping the ledger's date, an
annual card fee left Uncategorized, and a "not a statement" card that said the parser finished
without reading the file. Two more came with `0f378eb`: a payment-rail token (`DR`, `UPI`, `NEFT`,
and seven others) became a payee and a global rule that made every debit Groceries, and a closing
read from the last running balance was used with no note on the review.

### Watch, jobs, and capture

The table below lists the watch P0 and P1 findings, in commit order.

| Sev | What was wrong | Root cause | Fix | Regression test | Commit |
|---|---|---|---|---|---|
| P1 | The backup alert fired on a fresh install, before 48 hours existed | The no-success branch had no clock | Start the clock at first install, and alert once per outage | `test_audit_watch.py` | `cec3eef` |
| P1 | The weekly briefing invented a floor and hid setup gaps | It copied the floor fields without the known flag | Carry the flag, say "unknown", and name the gaps | `test_audit_watch.py` | `cec3eef` |
| P1 | Model prose could publish a number the data never contained | The briefing text was free prose over the numbers | Brief only traced numbers; the note follows a flagged reply | `test_audit_watch.py` | `cec3eef` |
| P1 | A scheduled-question ceiling looked like a finished answer, and the docs disagreed | The ceiling fact was in the prose only | Store it as data with the answer, and reconcile the two docs | `test_audit_watch2.py` | `154597e` |
| P1 | The brief stated a spend total with no word about coverage | The per-week spend was the one path the coverage block missed | Pass coverage through and say when it is incomplete | `test_audit_watch2.py` | `154597e` |

The P2s here are a negative-week comparison, a crash-retried briefing that spent a second model
call, push delivery blocking the event loop, an unpruned gone subscription, unmarked outside
reminder names, a job poll that dropped finishes past 50, a retried photo appending its note twice
(migration `0023`), a UTR printed with spaces failing extraction, and two log lines.

### Platform, CLI, and backup

The table below lists the platform P0 and P1 findings, in commit order.

| Sev | What was wrong | Root cause | Fix | Regression test | Commit |
|---|---|---|---|---|---|
| P1 | A host CLI exported traces to a Phoenix that was not running, then blocked at exit for minutes | Telemetry was on by default with a guessed endpoint | Tracing is opt-in: no collector endpoint, no registration | `test_audit_platform.py` | `5b5d076` |
| P1 | `arno chat` crashed on a cp1252 console after saving the turn | The CLI never set an encoding for its own streams | Set UTF-8 on the CLI's streams | `test_audit_platform.py` | `5b5d076` |
| P0 | A fresh-machine restore could not boot, and lost every role grant | The dump omitted grants and the restore created no roles | Dump and restore grants, and create the login roles before the restore | `test_audit_platform.py` | `5b5d076` |
| P1 | The backup sidecar's dump dropped every table grant | The dumper discarded the record of the grants without writing another | Keep the grants in the dump | `test_audit_platform2.py` | `b2aa300` |
| P1 | `_restore_dump` threw the grants away a second time | The restore re-created objects without their privileges | Replay the grants first | `test_audit_platform2.py` | `b2aa300` |
| P1 | The backup sidecar could not start from a Windows checkout | `backup.sh` was checked out with CRLF | `.gitattributes` pins `*.sh` to LF | none (config) | `7179c5e` |
| P0 | Two writers sharing one task directory could lose a saved task | The atomic save used one predictable `.tmp` name | A unique temp name per save, plus an owner per directory | `test_audit_taskrace.py` | `8936545` |

The P2s here are a non-atomic enrollment-link consumption, two simultaneous passkey deletions
removing the last passkey, a future `demo seed` crashing, a migration directory outside the source
tree rejected, an unknown `--thread` silently starting a new conversation, a missing `pg_dump`
printing a traceback, environment typos that were a traceback or a silent wrong value, a JSON export
that did not label its money unit, an accepted-and-ignored `autocommit` argument, a restore drill
that skipped its binary check, and stale architecture wording in the docs.

### The invariants themselves

`tests/invariants.py` was written in wave 1 and then corrected twice: a review import may reference
an existing movement it matched, so `movement_from_uncommitted_import` now asks whether the import
created effective movements; and the driver stopped re-implementing the reversal rule after it
disagreed with the ledger on an undo of an undo. Both changes came from live evidence, not review.
The type pass then made the report survive its own failure paths: a transfer posting with a missing
account no longer crashes the sort that names the kinds, and a missing schedule-payment row is
reported with a new `schedule_payment_missing_row` code instead of an `AttributeError` (`d8bb347`).

### Evals, graders, and the harness

The table below lists the eval-harness P0 and P1 findings, in commit order.

| Sev | What was wrong | Root cause | Fix | Regression test | Commit |
|---|---|---|---|---|---|
| P0 | 66 injection trials never ran: the fixture statement could not commit | Its one-line statement was dated over a month the demo ledger already filled, so its printed closing could never agree and the closing-adjustment gate refused it every time | Derive the statement's window from the ledger, the last month before the account's first entry, and print the ledger's own opening and closing | `test_import_injection.py` | `0f378eb` |
| P1 | A trial could not answer the confirm card it raised, so the turn was graded on an empty reply | The harness had no approval path, and a legitimate card ended the run | Every category but injection and never-moving-money approves the card it raised, a simulated user's yes, and continues; those two keep every card pending | `test_evals.py` | `c42bc61` |
| P1 | A write the case's first message asked for was counted against its last turn | Grading started at the case's start, not at the last user message | Grade movements, audit rows, and captures since the last user message | `test_evals.py` | `c42bc61` |

The P2s are one class of finding: a grader that matched one literal form. It now reads a
probability written as the engine's fraction, a count written as a word, a bolded or dashed verdict
(still rejecting "there is no statement balance"), and an absence in the reply's own words; the
cases that could not be answered as written now name an amount and a term, or ask for the values
they grade. A correct Hinglish refusal also failed the payment-claim check, because "Agar bhej diya
hai" (if you have sent it) contains the claim's words as a question, and the schedule case now
accepts "not that any month's rent has been paid" (`c42bc61`, `01fd19f`).

## The type-checking pass

The pass closed with strict type checking. Tests prove what they cover and nothing about the shapes
that reach a line they miss: a database row that can be missing, a `None` that reaches arithmetic, a
return type that hides a version mismatch. Pyrefly was chosen on November 10, 2026 over basedpyright
(`plan.md` §3), and [`typing.md`](typing.md) owns the policy. In short: one pinned version checks
`src/`, `tests/`, `evals/`, and `scripts/` for Python 3.14 on Linux under the `strict` preset, where
a warning fails like an error, deprecations and untyped imports are errors, and the only suppression
syntax is `# pyrefly: ignore[<code>]` with a reason; `src/` adds return-type rules, the pure money
core also bans `Any` and truthiness on amounts and dates, and tests may leave fixtures unannotated.
A pre-commit hook and a CI step run it over the whole project, not only the staged files.

It took five areas in disjoint ownership and about 670 diagnostics to reach zero, and the checker
paid for itself: two P1s and a handful of P2s. Every streamed request through the context-window
guard raised `TypeError`, so the fallback model could never stream or fall through (`cdcf9f8`). An
undo whose occurrence was already gone crashed instead of saying to undo that change first
(`f79a19f`). A re-parse landing mid-call handed `None` to a caller that indexes it (`2d49aa2`), a
retried `run_code` showed as ok (`cdcf9f8`), rows deleted between a read and their write crashed or
recorded an empty after-state (`f79a19f`), and the invariant report crashed on its own failure paths
(`d8bb347`). The two P1s are rows in the area tables above.

Six suppressions remain, each with the fact that makes the code right:

| Location | Reason |
|---|---|
| `importers/readers.py` | `olefile` ships no type information |
| `evals/runner.py`, `tests/conftest.py` | `pydantic_ai.models.ALLOW_MODEL_REQUESTS` is a module global the library rebinds, which the checker reads as the literal `True` |
| `tests/test_capture.py` | a deliberately invalid photo, one with a zero amount and one with an unknown method, asserts the refusal |
| `tests/test_chat_quote.py` | a deliberately invalid quote body must answer 422 |

Three of the pass's changes were corrected before they shipped. The `int()` and `float()`
conversions in the `amortize` tool stayed: they normalize a value at the sandbox boundary, where an
annotation is not a guarantee, which is also why `unnecessary-type-conversion` is off for now.
`GET /api/projection` keeps its `Projection` annotation but declares `response_model=None`, because
FastAPI otherwise re-validates the payload and silently drops every key the `TypedDict` does not
declare. The job-handler alias was not widened to allow a `None` result, because every production
handler returns one.

## Decisions made

Each decision is a line in `plan.md` §6 with a link here or to the satellite that holds its shape,
except where a row says otherwise. The "why" column is the reason a future change must not undo it.

| Decision | Why | Recorded in |
|---|---|---|
| A card statement's figures are ledger-signed below the statement text: a parser negates the printed balances, and a printed opening the user types is converted once by account kind | Two paths disagreed on the sign, which blocked a first card import and could flip a repayment into spending | [`import.md`](import.md#signs) |
| A "wait until X" tradeoff alone leaves the affordability verdict `no`; `yes-but` needs a same-date tradeoff | A later date is a caveat, not a way to afford the purchase as asked | [`design.md`](design.md#affordability), `plan.md` §6 L5 |
| Unfiltered coverage is complete only when every active account that has ever committed a statement covers the period | One account's statement made every account's spending look complete | [`tools.md`](tools.md) (`coverage`), [`design.md`](design.md) |
| A monthly rule on the 29th, 30th, or 31st falls on the last day of a shorter month | Otherwise rent "on the 31st" is skipped in February, April, and every short month | [`design.md`](design.md) (schedules) |
| Loan interest is charged once per calendar month per loan; a later payment that month is all principal | Two payments in one month booked the month's interest twice | [`tools.md`](tools.md) (loans) |
| An equal split's paise remainder goes to my share | Deterministic, conserves every paisa, and never silently reassigns someone else's share | [`tools.md`](tools.md) (splits) |
| A turn's confirm cards are answered together; a partial answer writes nothing and all cards stay pending | The agent framework cannot resume a run with a missing result, and inventing one would be a lie | rule 2 in `plan.md` §4, [`design.md`](design.md#untrusted-text-and-write-tiers), `api/chat.py` |
| JSON and CSV exports exclude credential and session tables; `pg_dump` stays complete | A data export is not a backup, and a dump is the backup and restore source | `plan.md` §5 L1, [`platform.md`](platform.md#security) |
| Tracing is opt-in: no Phoenix collector endpoint means nothing is registered | A host CLI exported to a guessed local endpoint and then blocked at exit | [`observability.md`](observability.md) |
| A statement's opening is booked at the statement's first day, and `arno repair-openings` applies that to imports already committed | An opening inside a later adjustment shifted every historical balance the statement covered | [`import.md`](import.md), `plan.md` §5 L3 |
| Secret redaction is keyword-anchored and shape-aware: an OTP, PIN, CVV, password, passcode, or API key is redacted next to its keyword in English, Hinglish, and Devanagari, and in either word order; plain words, amounts, dates, and UPI ids stay | A value typed in chat must not reach the model, storage, titles, or traces, and an over-broad rule would eat real amounts | [`harness.md`](harness.md#day-one), [`system-design.md`](system-design.md#decisions) |
| The host writes one plain approval sentence per confirm card when the model writes none | A card with no words made the user read JSON to know what they were approving | [`design.md`](design.md#untrusted-text-and-write-tiers), `agent/turns.py` |
| A backtest month without a usable starting balance is not a measurement, and `--publish` refuses it | An error that measures missing setup would read as a claim about the engine | `engine/backtest.py` (the report says so in words) |
| Python type checking uses Pyrefly, pinned, under the `strict` preset, in the hook and in CI | It reads the Pydantic models without a plugin, its per-path strictness keeps the money core strictest, and one checker means one answer | `plan.md` §3, [`typing.md`](typing.md) |
| The release waited for the rename to Arno, then shipped as 0.2.0 | 0.2.0 rather than 1.0.0, because `plan.md` defines shipped as four weeks of real use and a freeze week, which have not happened | `plan.md` (rename note, November 10, 2026), [`self-hosting.md` §6.6](self-hosting.md#66-upgrading-from-fin-agent) |

## Done for real, simulated, or impossible here

The completion checklist keeps the authoritative statuses. This table says what kind of evidence
each kind of item has.

| Item | Status | Evidence, or what it needs |
|---|---|---|
| Area audits, fixes, regression tests | Done for real | Commits on `main`, the full suite green, CI green before each wave |
| Strict type checking in the hook and in CI | Done for real | Zero diagnostics with six suppressions, each with its reason; CI green on `5788325` |
| The 134-case eval scorecard | Done for real | The November 10, 2026 run, 402 trials, in the README block `arno eval` writes (`e445680`); every gate passed |
| The next type-tightening steps in `typing.md` | Not started | Each step is a `pyproject.toml` change plus its fixes; the first one is available now |
| The renamed release's version, image tags, and git tag | Done for real | `v0.2.0` on November 11, 2026: the release workflow published `ghcr.io/joy9001/arno:0.2.0`, `arno-backup:0.2.0`, and the GitHub Release; the repository is now `Joy9001/Arno` |
| Invariant checker on the demo persona and every journey write | Done for real | `OK` counts rose through each run and never reported a violation after a fix |
| Six journeys and three regression rounds with the real model | Done for real, on scratch databases | Section "The end-to-end driver" above and [`e2e-run-log.md`](e2e-run-log.md) |
| Migrations `0022`-`0024` applied to a copy of the live ledger | Done for real, on a throwaway copy | Invariants before and after, then the copy dropped |
| Openings repair on a copy of the live ledger | Done for real, on a throwaway copy | Dry run, apply, second dry run reporting nothing |
| The live instance's move to Arno and its repair | Done for real | Followed [`self-hosting.md` §6.6](self-hosting.md#66-upgrading-from-fin-agent) on November 11, 2026: a `pg_dump` first, the five volumes copied byte-identical, the database and settings renamed, 0.2.0 started healthy with every balance unchanged; then `repair-openings` booked SBI's ₹16,548.42 opening at November 3, 2025 (a second dry run reports nothing), a live copy passed 6,729 invariant checks, and the first Arno backup wrote restic snapshot `3f070fcd`. The old `fin-agent_*` volumes and images, and the `fin-agent` and `fin-agent-backup` packages on GHCR, were deleted the same day; the `pg_dump` taken first is kept locally |
| Fresh install from the docs alone (milestone 40) | Simulated | Same owner, same Windows host, `127.0.0.1`, software passkey, no tailnet and no phone |
| Restore into a fresh cluster (milestone 37) | Simulated | A second Compose project with new volumes; restic password plus the OAuth client |
| Backtest (milestone 29) | Blocked on the owner | The live ledger has no income schedule, so the backtest correctly refuses to publish; add one, then `arno backtest --publish` |
| HackerRank score (milestone 30) | Done | `arno hackerrank` scored 88.8% on the 25 labeled samples (status 23/25, amount within 5% 21/25, earliest within 3 days 22/25); the dataset stays in gitignored `private/`, and the README block is published |
| Union Bank statements | Blocked on the owner | No statement has been provided, so no format decision exists |
| Statement passwords | Impossible here | The owner types them on the import card; they never enter chat, a test, or this record |
| Corrected Slice exports | Blocked on the owner | Several repayments are missing from the PDFs, so the remaining cycles wait on corrected exports or manual rows |
| Four weeks of daily use (milestone 41) | Impossible here | Needs the owner's real use, with no spreadsheet or mental-math fallback |
| Freeze week | Not started | Starts after the last checklist item |
| Physical phone push and PWA install | Impossible here | Needs a real Android device and a web-push endpoint |
| Owner voice notes | Impossible here | Needs a microphone, an STT endpoint, and Hinglish audio |
| A local model's scorecard | Not run | Ollama is installed; the eval spends money and rewrites README blocks, so it was left for the owner's go-ahead |

## Known limitations left open

- **The model can ignore an attached statement.** The host now names the carried file, says it is
  this turn's file, and names the unread file in the note, but the model can still answer about an
  earlier statement instead of handing the file to the parser. The host cannot start the parser
  itself: the task needs the parent run's live context. The next step is a product one, a one-tap
  "read the statement" action on the note card, not a prompt tweak.
- **Live-stream text joining.** Consecutive text parts are joined with a blank line in the saved
  transcript and on every reload, and adjacent text blocks in the thread view have spacing. A reply
  whose text the streaming adapter folds into one UI part still reads smushed until reload; the
  stream has no boundary to insert a separator at.
- **Confirm cards are all-or-nothing.** Answering one of two cards refuses the request and keeps
  both pending. Applying the answered ones and carrying the rest across turns needs a host-side
  deferred-tool handler, which is larger than this pass.
- **Sandbox helper docstrings are parsed into stubs.** `run_code` builds each helper's stub from its
  docstring, so a docstring that reads like a call example can break positional calls. Keep helper
  docstrings in the existing simple form; `money()` is the formatting helper scripts use.
- **Card billed versus unbilled is a simplification.** The projection pays the whole current
  outstanding, including spend not yet billed, on the next due date, so a purchase made after the
  statement day is paid a cycle early. A precise model needs the billed balance and its date on the
  card plus a billed/unbilled split; keep the simplification unless real statements show it changes
  a verdict.
- **A backtest needs an income schedule.** Without one, every month is unusable as a measurement
  and the command refuses to publish. That is the honest behavior, but it means milestone 29 cannot
  close from the current ledger.
- **The number check stays a note.** It flags amounts a reply cannot trace; it is not a gate, and a
  model can still produce a plausible wrong sentence that the check misses.
- **Two one-trial model slips in the published scorecard, since fixed at their cause.** A grocery
  answer left its total and trip count to the presented card, and a long thread recalled the floor
  decision without its ₹25,000 value. The prompt's answer rule contradicted itself and `present`
  told the model only that the card was shown; the summary prompt said to leave out amounts. After
  the fix both cases passed 10 of 10 trials, and a targeted rerun scored answers 25/26 (one
  timeout) and long threads 4/4. The published cells still read 25/26 and 3/4 until the next full
  run.
- **Two smaller data limits.** A saved parser only replays a layout whose header variant it has
  learned (a new month of the same bank is fine once the variant is registered), and a photo's
  extraction note reaches model history once by design, so re-describing a photo needs a new turn.

## How to re-run this audit

```bash
# One scratch database per agent; the name must end in _test.
export DATABASE_URL=postgresql://app:<pw>@127.0.0.1:5433/arno_audit_test
export AGENT_RO_PASSWORD=...  BACKUP_PASSWORD=...

# 1. Invariants on the demo persona.
uv run arno migrate
uv run arno demo seed
uv run python -m tests.invariants

# 2. The suite, the linter, and the web build.
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run pyrefly check
pnpm --dir web lint && pnpm --dir web typecheck && pnpm --dir web build
pnpm --dir web test:answers      # when an answer or result card changed

# 3. A real journey, with a real model, against a live API process.
uv run python -m tests.e2e.driver up --port 8123 --seed demo
uv run python -m tests.e2e.driver chat "How much did I spend on food and dining last month?"
uv run python -m tests.e2e.driver state
uv run python -m tests.e2e.driver invariants
uv run python -m tests.e2e.driver down

# 4. A production-shaped data rehearsal, on a copy only.
pg_dump "$LIVE_DATABASE_URL" > live.dump
createdb arno_livecopy_test && pg_restore -d arno_livecopy_test live.dump
DATABASE_URL=postgresql://app:<pw>@127.0.0.1:5433/arno_livecopy_test uv run arno migrate
DATABASE_URL=... uv run python -m tests.invariants
DATABASE_URL=... uv run arno repair-openings --dry-run
dropdb arno_livecopy_test
```

A journey run is not a pass because the model answered: check every stated number against
`driver state` and SQL, run the invariants after every write, and confirm that undo restores the
ledger exactly. A simulated gate is never ticked as real.
