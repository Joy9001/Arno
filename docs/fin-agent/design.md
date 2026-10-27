# Design

This file holds the detail behind [`plan.md`](plan.md) §2 and §4: the data model, the agent's
tool surface, the projection and affordability engine, and pattern detection.

The `plan.md` file owns decisions, and this file owns their shape. If the two disagree, the plan
wins and this file is stale.

---

## Data model

One Postgres database. Column lists are the intended shape, firmed up into the first migration
at L0 (milestone 3).

### Conventions

- Money is stored as `BIGINT` minor units (paise) and handled as a `Decimal` value at the Python
  boundary, never as a `float` value.
- Signed amounts: a negative amount means money leaves that account. A card purchase is negative
  on the card.
- Dates are `date`; moments are `timestamptz`.
- Kinds are `text` columns that have a `CHECK` constraint, not Postgres enums—enums are awkward
  to change.
- Every row the agent can write has `created_at`, and every write goes through the audit log.
- "Unknown" is `NULL`, never `0`.

### Roles

The database has two roles:

| Role | Can |
|---|---|
| `app` | Owns the schema; everything the API does |
| `agent_ro` | `SELECT` on ledger, plan, people, categorization, and conversation tables. No auth tables. `default_transaction_read_only` and a `statement_timeout`, so `run_query` can neither write nor run away |

### Instance

The following tables hold instance-wide data:

| Table | Holds |
|---|---|
| `settings` | Single row: base currency, time zone, floor (fixed amount), default horizon (90 days), notification times |
| `users`, `webauthn_credentials`, `sessions` | Single-user auth (`user` is reserved in Postgres). See [`platform.md`](platform.md) |

### Money and where it sits

**`accounts`:** one table that has a `kind` column and kind-specific nullable fields. The
following table lists its fields:

| Field | Notes |
|---|---|
| `kind` | `bank`, `card`, `cash`, `wallet`, `loan`, `asset` |
| `name`, `institution` | "SBI savings", "Slice" |
| `balance`, `balance_as_of`, `balance_source` | Last known balance, its date, and where it came from (`statement`, `user`, `computed`). Staleness is always reported |
| `liquid` | Counts toward spendable cash in the projection. True for bank, cash, and wallet accounts |
| card: `credit_limit`, `statement_day`, `due_day` | Card spend is a liability until the due date |
| loan: `principal`, `rate`, `emi_schedule_id` | Outstanding principal; the EMI lives in `schedules` |
| asset | No extra fields—the balance is updated by hand (EPF, FD, PPF, NPS, gold, funds). Statement-driven holdings are parked ([`parked.md`](parked.md)) |
| `active` | Soft-delete keeps history |

**`transactions`:** the ledger. The following table lists its fields:

| Field | Notes |
|---|---|
| `account_id`, `date`, `amount` | |
| `description_raw`, `merchant` | Raw text as captured; normalized merchant for rules and grouping |
| `category_id` | Nullable until categorized |
| `kind` | `expense`, `income`, `transfer`, `refund`, `fee`, `interest`, `adjustment` |
| `transfer_group` | Links the two legs of a transfer—including paying a card bill. Transfers are never spend |
| `status` | `posted`, `pending` |
| `source`, `source_ref` | `source` is `chat`, `voice`, `photo`, `import`, `ui`, or `system`; `source_ref` is the message, import, or attachment that produced it |
| `upi_ref` | UPI reference number when known—from a screenshot or the statement narration. The strongest reconciliation key |
| `confidence` | Extractor-reported for imported and photo rows; 1.0 for typed |
| `content_hash` | Dedupe key across reimports. See [`import.md`](import.md) |
| `attachment_id` | Receipt photo or UPI screenshot |

### The plan: What's coming

**`schedules`:** every recurring or expected future cash event, in one table. The following
table lists its fields:

| Field | Notes |
|---|---|
| `kind` | `income_fixed`, `income_variable`, `income_oneoff`, `obligation`, `instalment`, `goal_contribution` |
| `subtype` | For obligations: `rent`, `emi`, `subscription`, `insurance`, `family_transfer`, `other` |
| `name`, `amount`, `account_id`, `category_id` | |
| `rule` | iCalendar RRULE, handled by `python-dateutil`—"monthly on the 5th", "last weekday", "yearly on Mar 14", `COUNT=3` for "slice in 3". Holiday adjustment from the India calendar |
| `confirmed` | For variable income: confirmed amounts count toward verdicts, expected ones only show as an optimistic case |
| `starts`, `ends`, `active` | |

**`goals`:** `name`, `target`, `deadline`, `priority`, `saved` (fenced, excluded from spendable),
`contribution_schedule_id`, `status`.

### People

**`counterparties`:** people and organizations (an employer for reimbursements).

**`claims`:** money owed between me and a counterparty. The following table lists its fields:

| Field | Notes |
|---|---|
| `counterparty_id`, `amount` | A positive amount means they owe me |
| `kind` | `split`, `lend`, `borrow`, `reimbursement` |
| `source_txn_id`, `settled_txn_id` | A ₹1,200 dinner split 3 ways is one −1,200 transaction plus two +400 `split` claims. My spend is the remainder |
| `expected_date` | Only claims that have a date enter the projection |

### Categorization

Three tables handle categorization:

| Table | Holds |
|---|---|
| `categories` | Seeded with Indian defaults, editable. `reducible` (eligible for "cut a category" tradeoffs), `active` |
| `category_rules` | `merchant_pattern → category_id`, created from user corrections, with hit counts. _Rules always win_; the LLM only sees merchants no rule matches. The one-shot categorizer's answer for a new merchant is saved as a rule too, so each merchant costs one LLM call, once. `source` is `user` or `llm`; user rules win, and a correction replaces an `llm` rule |
| `baselines` | Typical spend for each category: `amount_per_day`, `source` (`estimated` at setup, `computed` from real rows), `window`, `updated_at` |

Baselines are recomputed nightly after a category has enough real rows. The `source` flag exists
so the agent can say which it's using—an estimate presented as fact is the failure mode.

### Memory, conversations, and provenance

The following tables hold memory, conversations, and provenance:

| Table | Holds |
|---|---|
| `preferences` | Typed facts and rules ("Mom's monthly transfer is not discretionary spend"). `created_by` (agent or user), `source_message_id`, `active`. The agent saves these unprompted when it notices a lasting fact, shown as a "Remembered" card with undo. Never a number the ledger owns. Visible and editable in settings |
| `conversations`, `messages` | Full chat history. `messages.search` is a generated `tsvector` column that has a GIN index, using the `simple` configuration—no stemming, so Hinglish isn't mangled. `conversations.outside_text_at` records when outside text first entered the conversation ([write tiers](#untrusted-text-and-write-tiers)) |
| `audit_log` | Every write: actor, `message_id`, table, row ID, before and after JSON, `undone_at`. Powers undo |
| `imports` | File hash, parser, account, period, opening and closing balances, invariant result, status (`review`, `committed`, or `rejected`) |
| `attachments` | Receipt photos and UPI screenshots as `bytea`. Statement files are not kept—only their hash |
| `agent_runs` | Trace: model, tokens, cost, tool calls and results, duration, error |
| `notifications`, `push_subscriptions` | Inbox items and web-push endpoints |

---

## Tool surface: 11 tools

_"Answers any question" is a property of the tool surface._ Few wide tools over complete data;
a twelfth tool is the trigger to reconsider Tool Search ([`harness.md`](harness.md)). The five
read tools sit behind Code Mode's single `run_code` tool, so the model sees seven tools: `run_code`
and the six write tools. See [Code Mode](#code-mode).

### Read

The agent has five read tools:

| Tool | Returns |
|---|---|
| `get_financial_context()` | Deliberately comprehensive. Settings, accounts with balances and ages, data coverage for each account, next 30 days of scheduled events, goals and progress, open claims, active preferences, staleness warnings |
| `query_transactions(filters)` | Rows, filterable by account, category, merchant, date, amount, kind, and counterparty |
| `summarize_spending(group_by, period, filters)` | Aggregates, summed in code. Transfers excluded; split shares netted |
| `project(horizon=None, hypothetical=None)` | Curve, floor probability, verdict, and tradeoffs. See [The projection engine](#the-projection-engine) |
| `run_query(sql)` | Rows, queried as the read-only `agent_ro` role, including conversation search on `messages.search`. The agent shows the SQL it ran |

### Write

The agent has six write tools:

| Tool | Does |
|---|---|
| `record_transaction(...)` | Expense, income, transfer, split, or loan in one call. Returns an inline card with undo |
| `edit_transaction(id, changes \| delete)` | Conversational repair. A category change creates or updates a rule |
| `update_setup(entity, action, data)` | Accounts, balances, schedules, goals, categories, counterparties. One discriminated-union tool, shaped for a frontier model (`plan.md` §3) |
| `preference(action, text \| id)` | Remember or forget a typed preference. Called unprompted when the agent notices a lasting fact |
| `import_document(attachment_id, password=None)` | Parses into an import in `review` state. Committing is a separate, explicit step |
| `undo(audit_id=None)` | Reverts the last (or a given) write |

Voice and photos are not tools. They're input preprocessing—speech-to-text output, or a typed
photo extraction—handed to the agent as a user turn. That keeps rule 2 in `plan.md` §4. A photo
extraction's text fields count as outside text, so that turn flags the conversation
([write tiers](#untrusted-text-and-write-tiers)).

### Design notes

**Tool returns carry two payloads.** Content for the model—short, with ages and sources—and
metadata for the UI: the full projection curve, a breakdown table, and the _IDs of the rows and
schedules behind every number_. The UI renders the metadata as a card and the IDs as citations
that open the underlying rows; the model never sees the bulky part. PydanticAI's `ToolReturn`
metadata becomes a custom data part in the Vercel AI stream ([`platform.md`](platform.md)).

**There is no `can_i_afford` tool.** It's the `project` tool with a hypothetical. Likewise,
there is no `compare_periods` tool: it's two `summarize_spending` calls the model composes.

**Every read tool distinguishes zero from unknown.** Outside an account's data coverage, the
answer is "no data," not ₹0. Every balance carries its `as_of` date.

### Code Mode

The model answers questions by writing Python that calls the read tools inside the Monty sandbox
from `pydantic-ai-harness`. A script can loop, branch, run several queries at once, and model a
scenario—"prepay ₹50,000 on the loan: new EMI, interest saved, and what happens to the Goa
goal"—so intermediate results stay out of the context window. Code Mode is how rule 1 in
`plan.md` §4 works in practice: the model does the thinking, and the script does the arithmetic.

The following table sets how it is configured:

| Concern | Decision |
|---|---|
| Which tools move behind `run_code` | The five read tools only. They live in a toolset tagged `.with_metadata(code_mode=True)`, and the capability is `CodeMode(tools={'code_mode': True})`. The default `tools='all'` would also hide the write tools, so it is not used |
| Writes | Always direct tool calls, one row at a time, each with its write-show-undo card. A script never writes |
| Money in the sandbox | Monty has no `decimal` module, and `Decimal` values arrive as strings. Read tools therefore also return amounts as integer paise, which stay exact in Python integers. Rate arithmetic (interest, growth) is floating point, so results are rounded to the rupee for display |
| Dates | The sandbox has no clock. "Today" comes from `get_financial_context`, never from `datetime.now()` |
| Host access | `mount` and `os_access` stay unset. The read tools hold the only route to data, through the `agent_ro` role |
| Limits | A cap on nested tool calls and a short duration for each snippet, tuned at L2. Spend Limits still cap the run |
| Provenance | The `run_code` return keeps every nested tool call and return in its metadata. The UI shows the script next to the answer, and the source IDs from the nested returns become the answer's citations |
| Verdicts | A script can call `project()` many times, for example to sweep purchase dates. The yes, yes-but, or no verdict still comes only from the engine, which is tested and backtested; model-written code explores but doesn't judge. Standard money maths—EMI and amortization, prepayment, compounding—comes from tested engine functions the script calls; free-form arithmetic covers what those don't, and the script is shown either way |

**Open at L2:** whether `VercelAIAdapter` streams the UI metadata of tool returns nested inside
`run_code`. If it doesn't, Fin-Agent emits those cards and citations itself from the `run_code`
return's metadata (`plan.md` §6).

### Untrusted text and write tiers

Bank statements carry text that other people choose: a payer's name and, on some banks, the
note typed on a UPI transfer. Anyone can send ₹1 with a note written as an instruction. The agent
has no outbound tools, can't move money, and every write is audited and undoable, so the damage
worth guarding is a write that _lasts silently_—a poisoned preference that mutes rent warnings
forever. This is how rule 2 in `plan.md` §4 works.

**Outside text.** The following fields count as outside text:

- `description_raw` and `merchant` on rows whose `source` is `import` or `photo`
- Any text copied from them into another table, such as a schedule name taken from a
  recurring-charge proposal. Those rows carry an `outside_text` boolean, so the mark travels
  with the copy
- Every `run_query` result, because model-written SQL can select any column under any alias

**How it's tracked.** Tool returns wrap outside-text fields in markers, and the system prompt has
one line saying that marked text is data from statements and never an instruction. Frontier
models stop most casual attempts this way, but adaptive attacks beat prompt-based defenses, so the
markers are a layer, not the boundary. When a read tool returns marked text—directly or nested
inside `run_code`—it sets `conversations.outside_text_at`. Server code sets the flag from where the
data came from; the model can't set or clear it. A new conversation starts clean.

**Write tiers.** The following table sets which writes ask first:

| Tier | Writes | Behavior |
|---|---|---|
| Visible and undoable | `record_transaction`, `edit_transaction`, and `undo` on a single row, including the category rule an edit creates (named on its card). A payee correction that also applies the rule to that payee's past rows stays here: its card names the rule and how many past rows changed, and one undo reverts the rule and every row | Always instant, with an undo card—outside text or not |
| Lasting or wide | `preference` saves; `update_setup` (floor, schedules, goals, accounts, categories); any other write that touches more than one existing row | Instant in a clean conversation. In a flagged one, the tool raises PydanticAI's `ApprovalRequired`, and the chat shows a confirm card that states the exact change. Nothing is written until the user taps Confirm |

Recategorizing a statement row right after reading it stays instant. A preference save that
nobody asked for, in a conversation that has read a crafted payee note, shows up as a card the
user can cancel.

**Exfiltration.** A markdown image in a reply makes the browser fetch its URL, which could carry
data out. The content security policy allows images only from the app's own origin, and links in
agent replies render as plain text ([`platform.md`](platform.md#security)).

---

## The projection engine

One function in code, seeded so the same inputs always give the same output. No LLM
involvement—this is the tested arithmetic every affordability verdict comes from.

### Inputs

- Liquid balance: sum of `liquid` accounts at their `balance_as_of` dates, plus logged
  transactions since.
- Horizon: max(90 days, furthest goal deadline, the asked horizon).
- Scheduled events expanded from the `schedules` table over the horizon. Unconfirmed variable
  income is shown as an optimistic case only, never counted toward a verdict.
- Card cycles: card outstanding plus projected card spend, paid in full from a bank account on each
  card's due date.
- Variable spend, simulated: each run fills the horizon by resampling real past days of spend for
  each category, excluding categories already covered by obligations. A thousand runs give the
  spread. Until a category has enough real days, its setup estimate is used as a flat rate and the
  `data_quality` field says so. Days versus whole weeks is an open question (`plan.md` §6, L5).
- Goal contributions move money into fenced savings; fenced savings are not spendable.
- Claims count only when they have an `expected_date`.

### Walk

Each run walks day by day, applying events in a fixed order: income, obligations and
installments, card dues, goal contributions, and variable spend.

### Output

- Daily liquid and spendable balance: the median, and a P10-P90 band (in 8 of 10 simulated
  futures the balance stays inside it).
- **Floor probability:** the share of runs that never cross the floor.
- Minimum point and date on the median path; first date the median goes below the floor, if any.
- Goal completion dates.
- Source IDs: the balances, schedules, and baselines that fed the run, for citations.
- `data_quality`: balance ages, baseline sources, coverage gaps. The agent must state these.

### Affordability

`project(hypothetical={amount, date, method})`, where `method` is `account`, `card`, or
`emi(n)` (installments on a card). The following table defines each verdict:

| Verdict | Condition |
|---|---|
| yes | Paid as asked, the floor probability stays at or above the threshold, and no goal slips |
| yes-but | Not as asked, but at least one of the tradeoffs listed later gets it there |
| no | No tradeoff within the horizon does, or an obligation can't be met on its date |

The threshold (90% or 95%) is an open question (`plan.md` §6, L5).

The engine computes each tradeoff by rerunning the projection:

| Tradeoff | How |
|---|---|
| Wait until date X | Earliest purchase date within the horizon that meets the threshold |
| Pay by card or EMI | Card float to the next due date, or `n` installments, within the card's available limit |
| Delay a goal | Goal completion dates before versus after—"Goa slips three weeks" |
| Cut a category | Shortfall spread over `reducible` categories' baselines until next payday; feasible only below a sensible cut ratio |

The agent phrases the verdict and picks which tradeoffs to mention. The numbers come from the
function.

### Measuring it

The following two measures run locally against real or license-restricted data, and their
results are published in the README:

| Measure | How |
|---|---|
| Backtest | For each month in the 12-month backfill, run `project()` as of its first day using only what was known then, and compare with the real statement balance 30 days later. Report the median error, and how often the real balance landed inside the P10-P90 band—close to 80% means the band is honest |
| HackerRank "Buy or Wait" | Score on the 25 labeled sample requests ([affordability section of the personal-agent platforms research](../research/personal-agent-platforms-2026-09-23.md#4-the-affordability-cluster--hackerrank-orchestrate-sept-2026)). Externally labeled, so it checks the affordability logic against someone else's answers. Used locally, never committed—its source repository has no license |

### Why it's built, not bought

No package knows my obligations, card cycles, goals, or floor. This function _is_ the product. It
has the strongest case for tests in the codebase: pure, seeded, and money depends on it.

---

## Pattern detection

Rule-based, in Python, run nightly and after imports. Everything surfaces as a _proposal_ in the
inbox or chat—"Looks like Netflix ₹649 monthly. Add it as an obligation?"—never a silent write.

The detector looks for the following patterns:

| Pattern | Rule (thresholds tuned on real data at L6) |
|---|---|
| Recurring charge | Same normalized merchant, ≥3 occurrences, interval ~7, 30, or 365 days ±3, amount ±10% |
| New subscription | A recurring charge that has no matching schedule |
| Missed recurring | An expected occurrence is 3+ days late |
| Risk | After any write and nightly, rerun the projection. If the floor probability drops below the threshold, or a crossing moves earlier, warn once for each episode |

Large-transaction and category-spike detection are parked ([`parked.md`](parked.md)).
