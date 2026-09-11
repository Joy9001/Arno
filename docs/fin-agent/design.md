# Design

Detail behind [`plan.md`](plan.md) §2 and §4: the data model, the tool surface, and the
projection engine.

`plan.md` owns decisions. This file owns their shape. If the two disagree, `plan.md` wins
and this file is stale.

---

## Data model

Five tables. The agent cannot project accurately without all five — this is the "full
financial context" requirement.

Column lists below are the intended shape, not a migration. Firm them up at the milestone
that needs them (M3 creates the schema).

### `profile`

Single row. What the agent needs to know about my income and position.

| Field | Notes |
|---|---|
| `income_amount` | Post-tax — what actually lands |
| `income_cadence` | Monthly for now. Weekly/biweekly if it ever matters |
| `next_pay_date` | Anchors the projection's inflows |
| `balance` | Current, as last stated or reconciled |
| `balance_updated_at` | Staleness matters — a two-week-old balance makes projections fiction |
| `floor` | The emergency-fund number projections must not cross. See `plan.md` open question M13 |

### `obligations`

Fixed recurring outflows, one row each.

| Field | Notes |
|---|---|
| `name` | "Rent", "Bike EMI", "Netflix" |
| `amount` | |
| `frequency` | Monthly, quarterly, annual |
| `due_day` | Day of month. The projection subtracts on this date |
| `active` | Soft-delete, so a cancelled subscription doesn't vanish from history |

### `baselines`

Typical spend per category. This is what makes projection possible before there's much
logged history.

| Field | Notes |
|---|---|
| `category` | FK to `categories` |
| `monthly_amount` | |
| `source` | `estimated` (user said so at setup) or `computed` (derived from real rows) |
| `updated_at` | |

Baselines start as the user's estimate and are overwritten by computed actuals once a
category has enough real transactions. The `source` flag exists so the agent can say which
it's using — an estimate presented as fact is the failure mode.

### `transactions`

The ledger.

| Field | Notes |
|---|---|
| `amount` | |
| `merchant` | |
| `category` | FK to `categories` |
| `date` | |
| `source_method` | Which route produced this row: `chat`, or the extractor's name |
| `confidence` | Meaningful for extracted rows; 1.0 for anything the user typed |
| `content_hash` | Dedupe key across re-imports. See [`import.md`](import.md) |

### `categories`

A fixed list, injected into the system prompt so the model can't invent one. How many is an
open question — fewer is better for a small model.

---

## Tool surface — eight tools

The scope principle from `plan.md` §2: *"answers any question" is a property of the tool
surface, not a feature list.* Eight orthogonal tools over complete data compose into an
unbounded question space.

### Read

| Tool | Returns |
|---|---|
| `get_financial_context()` | Profile, balance, obligations, baselines — the whole picture in one call |
| `query_transactions(filters)` | Ledger rows by category / date range / merchant / amount |
| `summarize_spending(group_by, period)` | Aggregates, summed in Python |
| `project_forward(days, hypothetical=None)` | Day-by-day projection |

### Write

| Tool | Does |
|---|---|
| `log_expense(...)` | Writes a transaction |
| `correct_transaction(...)` | Conversational repair of a wrong row |
| `update_profile(...)` | Income, balance, obligations. Used by setup and by later edits |
| `import_document(path, password=None)` | See [`import.md`](import.md) |

### Design notes

**`get_financial_context` is deliberately fat.** Small models lose coherence after 2–3 tool
calls, so one call returning the whole picture beats four calls assembling it.

**There is no `can_i_afford`.** It's `project_forward` with a hypothetical purchase. One
tested function, more general, less code.

**There is no `compare_periods`.** It's two `summarize_spending` calls the model composes.
Same for most "arbitrary analysis" questions — composition is the point of an orthogonal
surface.

**Every read tool distinguishes zero from unknown.** A general query surface over incomplete
data will otherwise answer confidently with no basis. "No data for that period" is a
distinct return, not an empty list the model reads as ₹0. This is the `Honesty` requirement
in `plan.md` §2, and enforcing it is the tool layer's job.

---

## The projection engine

Everything predictive runs through one deterministic function. No LLM involvement — this is
where `plan.md` §4's "the LLM never does arithmetic" rule is actually enforced.

1. Start from `profile.balance`
2. Walk forward day by day to the horizon
3. Add income on its pay dates, from `income_cadence` and `next_pay_date`
4. Subtract each active obligation on its `due_day`
5. Subtract averaged variable spend per day, derived from `baselines`
6. If a `hypothetical` purchase is given, insert it at its date
7. Return the daily balance curve, its minimum point and date, and whether it ever crosses
   `profile.floor`

### Reading the curve

Affordability is an interpretation of the output, not a separate calculation:

| Verdict | Condition |
|---|---|
| **yes** | Curve never approaches the floor |
| **yes-but-tradeoff** | Dips near the floor, or delays something the user cares about |
| **no** | Crosses the floor, or an obligation can't be met on its due date |

The agent's job is to phrase this. The numbers come from the function.

### Why this is built, not bought

It's the one place "use a library" is the wrong answer. No package knows my obligations, my
baselines, or my floor. This function *is* the product — see the build-vs-buy table in
`plan.md` §3.

It also has the strongest case for tests in the codebase: pure function, no I/O,
deterministic, and money depends on it being right.
