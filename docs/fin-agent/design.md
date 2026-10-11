# Design

Detail behind [`plan.md`](plan.md) §2 and §4: the data model, the agent's tool surface, the
projection and affordability engine, and pattern detection.

`plan.md` owns decisions. This file owns their shape. If the two disagree, `plan.md` wins
and this file is stale.

---

## Data model

One Postgres database. Column lists are the intended shape, firmed up into the first migration
at L0 (milestone 3).

**Conventions**

- Money is `BIGINT` minor units (paise). `Decimal` at the Python boundary, never `float`
- Signed amounts: negative = money leaves that account. A card purchase is negative on the card
- Dates are `date`, moments are `timestamptz`
- Kinds are `text` with a `CHECK` constraint, not Postgres enums — enums are awkward to change
- Every row the agent can write has `created_at`, and every write goes through the audit log
- "Unknown" is `NULL`, never `0`

### Roles

| Role | Can |
|---|---|
| `app` | Owns the schema; everything the API does |
| `agent_ro` | `SELECT` on ledger, plan, people, categorisation and conversation tables. No auth tables. `default_transaction_read_only` and a `statement_timeout`, so `run_query` can neither write nor run away |

### Instance

| Table | Holds |
|---|---|
| `settings` | Single row: base currency, timezone, **floor** (fixed amount), default horizon (90 days), notification times |
| `users`, `webauthn_credentials`, `sessions` | Single-user auth (`user` is reserved in Postgres). See [`platform.md`](platform.md) |

### Money and where it sits

**`accounts`** — one table, a `kind` column, kind-specific nullable fields.

| Field | Notes |
|---|---|
| `kind` | `bank`, `card`, `cash`, `wallet`, `loan`, `asset` |
| `name`, `institution` | "SBI savings", "Slice" |
| `balance`, `balance_as_of`, `balance_source` | Last known balance, its date, and where it came from (`statement`, `user`, `computed`). Staleness is always reported |
| `liquid` | Counts toward spendable cash in the projection. True for bank/cash/wallet |
| card: `credit_limit`, `statement_day`, `due_day` | Card spend is a liability until the due date |
| loan: `principal`, `rate`, `emi_schedule_id` | Outstanding principal; the EMI lives in `schedules` |
| asset | No extra fields — the balance is updated by hand (EPF, FD, PPF, NPS, gold, funds). Statement-driven holdings are parked ([`parked.md`](parked.md)) |
| `active` | Soft-delete keeps history |

**`transactions`** — the ledger.

| Field | Notes |
|---|---|
| `account_id`, `date`, `amount` | |
| `description_raw`, `merchant` | Raw text as captured; normalised merchant for rules and grouping |
| `category_id` | Nullable until categorised |
| `kind` | `expense`, `income`, `transfer`, `refund`, `fee`, `interest`, `adjustment` |
| `transfer_group` | Links the two legs of a transfer — including paying a card bill. Transfers are never spend |
| `status` | `posted`, `pending` |
| `source`, `source_ref` | `chat`/`voice`/`photo`/`import`/`ui`/`system`, and the message, import or attachment that produced it |
| `upi_ref` | UPI reference number when known — from a screenshot or the statement narration. The strongest reconciliation key |
| `confidence` | Extractor-reported for imported and photo rows; 1.0 for typed |
| `content_hash` | Dedupe key across re-imports. See [`import.md`](import.md) |
| `attachment_id` | Receipt photo or UPI screenshot |

### The plan: what's coming

**`schedules`** — every recurring or expected future cash event, one table.

| Field | Notes |
|---|---|
| `kind` | `income_fixed`, `income_variable`, `income_oneoff`, `obligation`, `instalment`, `goal_contribution` |
| `subtype` | For obligations: `rent`, `emi`, `subscription`, `insurance`, `family_transfer`, `other` |
| `name`, `amount`, `account_id`, `category_id` | |
| `rule` | iCalendar RRULE via `python-dateutil` — "monthly on the 5th", "last weekday", "yearly 14 Mar", `COUNT=3` for "slice in 3". Holiday adjustment from the India calendar |
| `confirmed` | For variable income: confirmed amounts count toward verdicts, expected ones only show as an optimistic case |
| `starts`, `ends`, `active` | |

**`goals`** — `name`, `target`, `deadline`, `priority`, `saved` (fenced, excluded from spendable),
`contribution_schedule_id`, `status`.

### People

**`counterparties`** — people and organisations (an employer for reimbursements).

**`claims`** — money owed between me and a counterparty.

| Field | Notes |
|---|---|
| `counterparty_id`, `amount` | Positive = they owe me |
| `kind` | `split`, `lend`, `borrow`, `reimbursement` |
| `source_txn_id`, `settled_txn_id` | A ₹1200 dinner split 3 ways is one −1200 transaction plus two +400 `split` claims. My spend is the remainder |
| `expected_date` | Only claims with a date enter the projection |

### Categorisation

| Table | Holds |
|---|---|
| `categories` | Seeded with Indian defaults, editable. `reducible` (eligible for "cut a category" tradeoffs), `active` |
| `category_rules` | `merchant_pattern → category_id`, created from user corrections, with hit counts. **Rules always win**; the LLM only sees merchants no rule matches |
| `baselines` | Per-category typical spend: `amount_per_day`, `source` (`estimated` at setup, `computed` from real rows), `window`, `updated_at` |

Baselines are recomputed nightly once a category has enough real rows. The `source` flag exists
so the agent can say which it's using — an estimate presented as fact is the failure mode.

### Memory, conversations, provenance

| Table | Holds |
|---|---|
| `preferences` | Typed facts and rules ("Mom's monthly transfer is not discretionary spend"). `created_by` agent/user, `source_message_id`, `active`. The agent saves these unprompted when it notices a lasting fact, shown as a "Remembered" card with undo. Never a number the ledger owns. Visible and editable in settings |
| `conversations`, `messages` | Full chat history. `messages.search` is a generated `tsvector` column with a GIN index, using the `simple` configuration — no stemming, so Hinglish isn't mangled |
| `audit_log` | Every write: actor, `message_id`, table, row id, before/after JSON, `undone_at`. Powers undo |
| `imports` | File hash, parser, account, period, opening/closing, invariant result, status (`review`/`committed`/`rejected`) |
| `attachments` | Receipt photos and UPI screenshots as `bytea`. Statement files are not kept — only their hash |
| `agent_runs` | Trace: model, tokens, cost, tool calls and results, duration, error |
| `notifications`, `push_subscriptions` | Inbox items and web-push endpoints |

---

## Tool surface — eleven tools

*"Answers any question" is a property of the tool surface.* Few wide tools over complete data;
a 12th tool is the trigger to reconsider Tool Search ([`harness.md`](harness.md)).

### Read

| Tool | Returns |
|---|---|
| `get_financial_context()` | **Deliberately fat.** Settings, accounts with balances and ages, data coverage per account, next 30 days of scheduled events, goals and progress, open claims, active preferences, staleness warnings |
| `query_transactions(filters)` | Rows by account / category / merchant / date / amount / kind / counterparty |
| `summarize_spending(group_by, period, filters)` | Aggregates, summed in code. Transfers excluded; split shares netted |
| `project(horizon=None, hypothetical=None)` | Curve, floor probability, verdict and tradeoffs. See below |
| `run_query(sql)` | Rows via the read-only `agent_ro` role, including conversation search on `messages.search`. The agent shows the SQL it ran |

### Write

| Tool | Does |
|---|---|
| `record_transaction(...)` | Expense, income, transfer, split or loan in one call. Returns an inline card with undo |
| `edit_transaction(id, changes \| delete)` | Conversational repair. A category change creates or updates a rule |
| `update_setup(entity, action, data)` | Accounts, balances, schedules, goals, categories, counterparties. Discriminated union — or split per entity if evals say small models choke (`plan.md` §6, L2) |
| `preference(action, text \| id)` | Remember or forget a typed preference. Called unprompted when the agent notices a lasting fact |
| `import_document(attachment_id, password=None)` | Parses into an import in `review` state. Committing is a separate, explicit step |
| `undo(audit_id=None)` | Reverts the last (or a given) write |

Voice and photos are not tools. They're input preprocessing — STT text, or a typed photo
extraction — handed to the agent as a user turn. That keeps rule 2 in `plan.md` §4.

### Design notes

**Tool returns carry two payloads.** Content for the model — short, with ages and sources — and
metadata for the UI: the full projection curve, a breakdown table, and the **ids of the rows and
schedules behind every number**. The UI renders the metadata as a card and the ids as citations
that open the underlying rows; the model never sees the bulky part. PydanticAI's `ToolReturn`
metadata becomes a custom data part in the Vercel AI stream ([`platform.md`](platform.md)).

**There is no `can_i_afford`.** It's `project` with a hypothetical. **There is no
`compare_periods`.** It's two `summarize_spending` calls the model composes.

**Every read tool distinguishes zero from unknown.** Outside an account's data coverage the
answer is "no data", not ₹0. Every balance carries its `as_of`.

---

## The projection engine

One function in code, seeded so the same inputs always give the same output. No LLM involvement
— this is where "the LLM never does arithmetic" is actually enforced.

**Inputs**

- Liquid balance: sum of `liquid` accounts at their `balance_as_of`, plus logged transactions since
- Horizon: max(90 days, furthest goal deadline, the asked horizon)
- Scheduled events expanded from `schedules` over the horizon. Unconfirmed variable income is
  shown as an optimistic case only, never counted toward a verdict
- Card cycles: card outstanding plus projected card spend, paid in full from a bank account on each
  card's due date
- **Variable spend, simulated.** Each run fills the horizon by resampling real past days of spend
  per category, excluding categories already covered by obligations. A thousand runs give the
  spread. Until a category has enough real days, its setup estimate is used as a flat rate and
  `data_quality` says so. Days vs whole weeks is an open question (`plan.md` §6, L5)
- Goal contributions move money into fenced savings; fenced savings are not spendable
- Claims count only when they have an `expected_date`

**Walk** day by day in each run, applying events in a fixed order: income, obligations and
instalments, card dues, goal contributions, variable spend.

**Output**

- Daily liquid and spendable balance: the median, and a P10–P90 band (in 8 of 10 simulated
  futures the balance stays inside it)
- **Floor probability:** the share of runs that never cross the floor
- Minimum point and date on the median path; first date the median goes below the floor, if any
- Goal completion dates
- Source ids: the balances, schedules and baselines that fed the run, for citations
- `data_quality`: balance ages, baseline sources, coverage gaps. The agent must state these

### Affordability

`project(hypothetical={amount, date, method})`, where `method` is `account`, `card`, or
`emi(n)` (instalments on a card).

| Verdict | Condition |
|---|---|
| **yes** | Paid as asked, the floor probability stays at or above the threshold, and no goal slips |
| **yes-but** | Not as asked, but at least one tradeoff below gets it there |
| **no** | No tradeoff within the horizon does, or an obligation can't be met on its date |

The threshold (90% or 95%) is an open question (`plan.md` §6, L5).

**Tradeoffs**, each computed by rerunning the projection:

| Tradeoff | How |
|---|---|
| Wait until date X | Earliest purchase date within the horizon that meets the threshold |
| Pay by card / EMI | Card float to the next due date, or `n` instalments, within the card's available limit |
| Delay a goal | Goal completion dates before vs after — "Goa slips 3 weeks" |
| Cut a category | Shortfall spread over `reducible` categories' baselines until next payday; feasible only below a sane cut ratio |

The agent phrases the verdict and picks which tradeoffs to mention. The numbers come from the
function.

### Measuring it

Both numbers are run locally against real or licence-restricted data and published in the README.

| Measure | How |
|---|---|
| **Backtest** | For each month in the 12-month backfill, run `project()` as of its first day using only what was known then, and compare with the real statement balance 30 days later. Report the median error, and how often the real balance landed inside the P10–P90 band — close to 80% means the band is honest |
| **HackerRank "Buy or Wait"** | Score on the 25 labelled sample requests ([research](../research/personal-agent-platforms-2026-09-23.md#4-the-affordability-cluster--hackerrank-orchestrate-sept-2026)). Externally labelled, so it checks the affordability logic against someone else's answers. Used locally, never committed — the repo has no licence |

### Why it's built, not bought

No package knows my obligations, card cycles, goals or floor. This function *is* the product. It
has the strongest case for tests in the codebase: pure, seeded, and money depends on it.

---

## Pattern detection

Rule-based, in Python, run nightly and after imports. Everything surfaces as a **proposal** in the
inbox or chat — "Looks like Netflix ₹649 monthly. Add it as an obligation?" — never a silent write.

| Pattern | Rule (thresholds tuned on real data at L6) |
|---|---|
| Recurring charge | Same normalised merchant, ≥3 occurrences, interval ~7/30/365 days ±3, amount ±10% |
| New subscription | A recurring charge with no matching schedule |
| Missed recurring | An expected occurrence is 3+ days late |
| Risk | After any write and nightly, rerun the projection. If the floor probability drops below the threshold, or a crossing moves earlier, warn once per episode |

Large-transaction and category-spike detection are parked ([`parked.md`](parked.md)).
