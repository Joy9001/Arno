# Tool contracts

This specification defines the contracts for Arno's 14 read and presentation tools, 7 direct write
tools, 5 delegation tools, 13 parser subagent functions, and 5 categorizer functions; for data-model
conventions, write tiers, and engine math see [data-and-engine.md](data-and-engine.md).

Tool summaries in this specification track the live docstrings in `src/arno/agent/toolset.py`, which
is the source of truth for exact model-facing wording. Tool names and field names are part of the
persisted contract because stored conversations replay historical tool calls.

![Scripts read and compute; every write is a direct call](../diagrams/sd-tool-surface.png)

_Figure: The read and write execution boundary. Scripts call the 14 read and presentation tools
through `run_code`, direct write tools execute with their own cards, and the 5 delegation tools
manage background parser tasks ([diagram note](../diagrams/sd-tool-surface.md))._

---

## Shared rules

Every tool in `src/arno/agent/toolset.py` follows the shared rules listed in the following table:

| Rule | Behavior |
|---|---|
| Money out | `Money = {"paise": 300000, "text": "₹3,000"}`. Unknown amounts are `null`, never `0` |
| Money in | Field names carry units: read tools accept `*_paise`, and write tools accept `*_rupees` (except `emi_fee_paise` and `min_due_floor_paise` in `update_setup`). Projection and money calculators accept magnitudes up to `MAX_PAISE` (`9,223,372,036,854,775,807`) |
| Name resolution | Accounts, payees, and categories resolve by case-insensitive name; movements take IDs. Matching an archived account refuses with that account's name and the active spendable accounts |
| Validation errors | Unknown or ambiguous names and invalid inputs raise `ModelRetry` listing valid candidates. Inside `run_code`, a nested `ModelRetry` arrives as a catchable `Exception` |
| Unexpected errors | Unexpected tool exceptions become structured error results (`{"status": "error", "tool", "message"}`) through `ToolErrorsToModel.on_tool_execute_error`, while spend-budget stops still halt the run. Read tools carry a 60-second timeout; write tools have no timeout |
| Uncertain commit | When a database commit's outcome is unknown (`CommitOutcomeUnknownError`), `_call_write` returns `{"status": "outcome_unknown", "committed": "unknown", "message": "..."}` instructing the model to ask the user to verify the ledger and never repeat the write |
| Two payloads | Compact JSON for the model; cards, curves, and source IDs for the UI in `ToolReturn` metadata (`data-card` chunks). Arno emits nested card metadata from `run_code` returns |
| Read return types | JSON-native values only (`dict`, `list`, `str`, `int`, `float`, `bool`, `None`), ISO date strings, and `as_of` dates on every balance |
| Script calls | Inside `run_code`, scripts invoke read helpers with `await` and keyword arguments (`await amortize(loan="Home loan")`) |
| Coverage | `coverage()` inspects committed, non-undone statements; gaps are reported rather than summed as zero. Without an account filter, every active account with any committed statement must cover the period |
| Outside text | `mark()` wraps outside text in `<outside>` and `</outside>`, strips closing markers and invisible characters, caps length at 200 characters, and sets `conversations.outside_text_at` ([data-and-engine.md](data-and-engine.md#trust-boundaries-and-write-tiers)) |
| Pagination | List tools return 50 rows by default with a keyset `cursor` |
| Write groups | `write_group()` wraps each write in one audited transaction returning `undo_id`; `message_id` and `ctx.tool_call_id` come from `RunContext`, never tool inputs |
| Confirm gate | `_gate()` raises `ApprovalRequired` in a flagged conversation when `ctx.tool_call_approved` is false. Approval resumes the call from the top, re-validates inputs, and applies once per pending `tool_call_id` |
| Compaction safety | Tools never depend on earlier IDs staying in chat history; `undo` reads `audit_log` directly and raises `ModelRetry` on unknown IDs |

The `run_code` tool comes from `pydantic-ai-harness`'s `CodeMode(tools={"code_mode": True})`
capability ([data-and-engine.md](data-and-engine.md#code-mode-boundary)).

---

## Read tools

All 14 read and presentation tools are exposed inside `run_code`.

### `get_financial_context`

**Purpose:** Returns the complete current snapshot: today's date, settings (with `floor_history`),
every account with known-balance status, balance age, card cycle dates and stored finance terms,
loan terms, and statement coverage, the next 30 days of upcoming events (with payment status and
weekday), active schedules (with `amount_history` and up to four upcoming unpaid `next_dates`),
goals (with `priority_history`), who owes what, active preferences (with `subject`, `changed_at`,
and `previous`), scheduled questions, open imports, setup gaps, and warnings. When the user states a
value contradicting an active memory, floor, schedule amount, or goal priority, the model names both
values and the earlier date. Because the sandbox has no clock, scripts read today's date here.

**Input:** None.

**Output:**

```python
{
  "today": "2026-10-31",
  "settings": {"currency": "INR", "floor": Money, "floor_history": ChangeRecord | None,
               "default_horizon_days": 90, "time_zone": "Asia/Kolkata"},
  "accounts": [{"id", "name", "kind", "liquid", "active", "known": bool, "balance": Money | None,
                "as_of", "age_days", "credit_limit", "statement_day", "due_day",
                "finance_rate_bps", "min_due_bps", "min_due_floor",
                "available_credit", "outstanding", "rate_bps", "tenure_months",
                "next_statement_close", "next_bill_due",
                "covered": {"from", "to", "gaps": [...]}}],
  "liquid_total": Money | None,
  "upcoming": [{"date", "weekday", "name", "kind", "subtype", "amount": Money | None, "note",
                "schedule_id", "payment_status", "paid": Money, "remaining": Money | None,
                "payment_movements": [{"movement_id", "amount": Money, "active"}],
                "confirmed", "source_id"}],
  "schedules": [{"id", "name", "kind", "subtype", "amount": Money | None,
                 "amount_history": ChangeRecord | None, "rule", "next_date",
                 "next_dates": [{"date", "weekday"}, ...],
                 "confirmed", "active"}],
  "goals": [{"id", "name", "target": Money, "saved": Money, "remaining": Money, "deadline",
             "priority", "priority_history": ChangeRecord | None, "status"}],
  "owed": [{"person", "payee_id", "amount": Money, "direction", "expected_date"}],
  "preferences": [{"id", "text", "kind", "subject", "changed_at", "previous"}],
  "scheduled_questions": [{"id", "prompt", "rule", "next_run"}],
  "imports": [{"id", "status", "account", "period": {"start", "end"} | None, "lines",
               "counts", "check"}],
  "setup_gaps": [...],
  "warnings": [...],
  "source_ids": {"accounts", "schedules", "goals", "payees"},
  "outside_text": bool,
  "ui_results": [...]
}
```

**Logic:**

- A balance is `known` only when an opening balance, user-stated balance, or committed statement sets
  both `statement_balance_paise` and `statement_balance_as_of` (including explicit zero). A `goal`
  or `person` account is always `known` because its ledger balance is the full truth. `liquid_total`
  is `null` if any active liquid account is unknown.
- `as_of` and `age_days` use `ledger.common.balance_anchor` (the later of the last movement and the
  stated balance date). Cards with both `statement_day` and `due_day` compute `next_statement_close`
  and `next_bill_due` via the projection engine's cycle helpers.
- `upcoming` expands active schedules over 30 days with holiday adjustment and appends generated
  card-due events; an unanchored card bill has `amount: null` and explains why in `note`.
- `schedules[].next_dates` expands 400 days ahead, skips fully paid occurrences, and returns up to
  four `{"date", "weekday"}` entries.
- `imports` includes only open imports (`status NOT IN ('committed', 'rejected') AND undone_at IS
  NULL`).
- `setup_gaps` checks all seven conditions: `"no accounts added"`, `"no income schedule"`, `"no
  obligations"`, `"no goals"`, `"no floor set"`, `"no estimated baselines"`, and `"an account has no
  stated balance"`.
- `mark()` applies only to names whose rows carry `outside_text = true`. The server also pins this
  function's output as each thread's opening snapshot; scripts call it to re-read live state.
- Emits separate `ui_results` descriptors for accounts, upcoming payments, goals, and owed balances.

### `query_movements`

**Purpose:** Lists individual movements with their transfers, category, payee, and evidence. Use
`summarize_spending` for totals. Corrected entries and their reversals are hidden by default.

**Input:**

```python
query_movements(
    filters: MovementFilters | None = None,
    limit: int = 50,
    cursor: str | None = None,
)

class MovementFilters:              # extra="forbid"
    period: str | None              # this_week, last_week, this_month, last_month, last_30_days,
                                    # this_year, "2026-09", "2026-11-10", "2026-09-01..2026-09-15"
    account: str | None
    category: str | None
    payee: str | None
    kind: str | None
    min_paise: int | None
    max_paise: int | None
    text: str | None                # literal substring search over descriptions and leg notes
    include_reversed: bool = False
```

**Output:**

```python
{
  "rows": [{"id", "date", "kind", "description", "payee", "category",
            "amount": Money, "amount_paise": int,
            "transfers": [{"from", "to", "amount": Money, "from_kind", "to_kind",
                           "amount_paise": int, "category_name", "payee_name", ...}],
            "evidence": ["chat" | "voice" | "photo" | "statement_line"],
            "undo_id": str | None}],
  "total_count": int,
  "next_cursor": str | None,        # "{date}|{id}"
  "coverage": {"complete": bool, "gaps": [...], "from": str | None, "to": str | None},
  "outside_text": bool,
  "ui_results": [...]
}
```

**Logic:** Resolves `period` on the host clock and resolves account, category, and payee names to
IDs. Filters non-effective movements in SQL through `v_movement_state` (unless `include_reversed` is
true) so `total_count`, page contents, and `next_cursor` always agree. Marks descriptions and payees
on statement-backed, photo-backed, or outside-payee rows.

### `recap`

**Purpose:** Returns the server-computed recap for one day, week, or month—identical to the Recaps
page ([data-and-engine.md](data-and-engine.md#recaps)).

**Input:** `recap(period: str)` accepts a recap ID (`month-2026-10`, `week-2026-11-02`,
`day-2026-11-08`) or a shorthand (`yesterday`, `this_week`, `last_week`, `this_month`, `last_month`,
`2026-10`).

**Output:** `{"period", "stories", "coverage", "outside_text"}`. Statement merchant names are marked
as outside text, flagging the conversation. `recap` does not register `ui_results`.

### `summarize_spending`

**Purpose:** Computes net spending (refunds offset purchases) grouped by category, payee, account,
month, week, or `none`. Loan principal, card bill payments, investments, and splits owed by others
never count as spending.

**Input:**

```python
summarize_spending(
    group_by: Literal["category", "payee", "account", "month", "week", "none"],
    period: str,
    filters: SpendFilters | None = None,
)

class SpendFilters:                 # extra="forbid"
    account: str | None
    category: str | None
    payee: str | None
    text: str | None
```

**Output:**

```python
{
  "period": {"from": str, "to": str},
  "groups": [{"key": str, "amount": Money, "count": int}],
  "total": Money | None,            # null only when no rows exist and coverage is incomplete
  "coverage": {"complete": bool, "gaps": [...], "from": str | None, "to": str | None},
  "outside_text": bool,
  "ui_results": [...]
}
```

**Logic:** Sums signed transfers on `spending` (so reversals net to zero) while counting distinct
effective movements through `v_movement_state`. Drops zero-sum, zero-count canceled buckets. Marks
outside payee names only when `group_by == "payee"`.

### `project`

**Purpose:** Forecasts liquid cash against the floor, computes `safe_to_spend`, evaluates 1 to 12
non-persistent dated what-if `changes`, and judges a `hypothetical` purchase (`yes`, `yes-but`, or
`no`) with verified tradeoffs. Verdicts come exclusively from the engine; when no liquid account has
a known balance, the model states what is missing instead of quoting balances or probabilities.

**Input:**

```python
project(
    horizon_days: int | None = None,             # 1 to 3,650 (minimum; engine extends as needed)
    hypothetical: ProjectHypothetical | None = None,
    changes: list[ProjectChange] | None = None,  # 1 to 12 dated what-if changes
)

class ProjectHypothetical:                       # extra="forbid"
    amount_paise: int
    date: str                                    # YYYY-MM-DD
    method: Literal["account", "card", "emi"] = "account"
    emi_months: int | None = None                # 2 to 48; defaults to 3
    account: str | None = None                   # card name; best card if omitted
    emi_rate_percent: float | None = None        # annual rate in percent
    emi_fee_paise: int | None = None
    no_cost: bool = False
```

Each item in `changes` is discriminated by `kind` (`stop_income`, `pause_schedule`, `change_amount`,
`add_recurring`, `one_time`) with `YYYY-MM-DD` dates, optional inclusive `until`, and positive
integer `amount_paise`.

**Output:**

```python
{
  "as_of": str, "horizon_days": int,
  "floor": Money, "floor_set": bool,
  "floor_probability": float, "floor_threshold": float,
  "median_low": {"date": str, "amount": Money} | None,
  "p10_low": Money, "first_floor_crossing": str | None,
  "goals": [{"id", "name", "target": Money, "saved": Money, "remaining_after_plan": Money,
             "deadline", "completes_on", "slips_days", "reached", ...}],
  "obligations_met": bool,
  "safe_to_spend": {"amount": Money, "binding": "floor" | "obligation",
                    "binding_date": str} | None,
  "data_quality": {"balance_ages", "baseline_sources", "gaps", "setup_gaps", "simulation", ...},
  "source_ids": {"accounts", "cards", "schedules", "payees", "goals", "categories"},
  "outside_text": bool,
  "optimistic": {"floor_probability": float, "median_low": {"date", "amount": Money},
                 "income": [...]} | None,
  # Present when hypothetical is passed:
  "verdict": "yes" | "yes-but" | "no", "verdict_reason": str,
  "tradeoffs": [...], "goal_slips": [...], "hypothetical": {...}, "baseline": {...} | None,
  # Present when changes is passed:
  "scenario": {"changes": [...], "assumptions": [...], "runway_days": int | None,
               "holds": bool, "reason": str, "baseline": {...}},
  "ui_results": [...]
}
```

**Logic:** Validates `horizon_days`, `hypothetical`, and `changes`, loads a `REPEATABLE READ` ledger
snapshot, resolves schedule, detected-series, and goal names in `src/arno/tools/projection.py`, and
runs the pure engine off the event loop via `run_compute`. Emits a projection `data-card` carrying
the daily curve, full sources, and echoed request arguments for workbook export. For simulation
mechanics, checkpoint optimization, affordability lever search, and scenario rules, see
[data-and-engine.md](data-and-engine.md#projection-engine) and
[data-and-engine.md](data-and-engine.md#affordability).

### `run_query`

**Purpose:** Read-only PostgreSQL escape hatch (2-second statement timeout) for questions typed tools
cannot answer, including full-text conversation search on `messages.search`. Its docstring directs
the model to typed tools first, lists the exact columns of the 6 `agent_ro` views (`v_legs`,
`v_spending`, `v_accounts`, `v_balances`, `v_owed`, `v_movement_state`), names all 18 readable
domain tables (including `series_detections`), and instructs the model to inspect table columns via
`information_schema.columns` rather than guessing.

**Input:** `run_query(sql: str)`

**Output:** `{"columns": [...], "rows": [[...]], "row_count": int, "truncated": bool, "sql": str,
"outside_text": True, "ui_results": [...]}`. UI receives the SQL and row metadata.

**Logic:** Connects as `agent_ro` ([data-and-engine.md](data-and-engine.md#database-roles)) inside a
read-only transaction, sets `LOCAL statement_timeout = '2s'`, prepares the single statement
(`prepare=True`, rejecting multi-statement strings), fetches up to 1,001 rows (capping output at
1,000 and setting `truncated`), and always rolls back. Converts whole `Decimal` values to `int`,
marks every text cell with `mark()`, always sets `outside_text: True`, and translates timeouts or
SQL errors into `ModelRetry`.

### `amortize`

**Purpose:** Computes loan EMI, total interest, total paid, payoff date, monthly schedule, upfront
fee APR, and optional lump-sum prepayment comparison using either a stored loan name or explicit
terms (capped at 600 months). When `complete_cost` is false, the model must not call the total the
full cost and must explain `not_included`. Does not render a card automatically; display via
`present`.

**Input:**

```python
amortize(
    loan: str | None = None,
    principal_paise: int | None = None,
    annual_rate: float | None = None,
    months: int | None = None,
    emi_paise: int | None = None,
    prepay: Prepay | None = None,                # {amount_paise, at_emi=1, reduce="tenure"|"emi"}
    conventions: LoanConventions | None = None,  # rest, day_count, interest/emi rounding units
    fee_paise: int = 0,
)
```

**Output:**

```python
{
  "emi": Money, "months": int, "total_interest": Money, "total_paid": Money,
  "payoff_date": str | None,
  "schedule": [{"n", "date", "interest_paise", "principal_paise", "balance_paise"}],
  "prepay": {"new_emi": Money, "new_months": int, "new_total_interest": Money,
             "interest_saved": Money, "months_saved": int} | None,
  "assumptions": [...],
  "complete_cost": bool, "not_included": [...],
  # Present when fee_paise > 0:
  "processing_fee": Money, "fee_gst": Money, "total_fee": Money,
  "net_disbursed": Money, "total_cost": Money,
  "apr": {"low_bps_x100", "high_bps_x100", "exact", "note"},
  "apr_percent": float,                          # omitted if either apr bound is null
  "ui_results": [...]
}
```

**Logic:** Loads stored loan terms via `loan_terms()` when `loan` is passed (treating stored rate 0
beside an EMI as an omitted rate), infers monthly-rest rates via `infer_rate` when `annual_rate` is
omitted, delegates to `engine/money.py` ([data-and-engine.md](data-and-engine.md#money-calculators)),
and registers a calculation result in the turn's result registry.

### `card_payoff`

**Purpose:** Computes months to clear, total finance charge, GST, total paid, `cost_of_carrying`
(finance charge plus GST), payoff date, and monthly schedule for carrying a revolving credit card
balance under minimum due or a fixed monthly payment. Refuses with a prompt to ask the user when no
rate is known. When `complete_cost` is false, the model must not call the total the full cost and
must explain `not_included`. Display via `present`.

**Input:**

```python
card_payoff(
    card: str | None = None,
    balance_paise: int | None = None,
    monthly_rate_percent: float | None = None,
    payment_paise: int | None = None,            # omit for minimum due
    minimum_percent: float | None = None,
    minimum_floor_paise: int | None = None,
)
```

**Output:**

```python
{
  "months": int, "total_finance_charge": Money, "total_gst": Money, "total_paid": Money,
  "cost_of_carrying": Money, "payoff_date": str | None,
  "schedule": [{"n", "date", "opening_paise", "finance_charge_paise", "gst_paise",
                "payment_paise", "closing_paise"}],
  "assumptions": [...], "complete_cost": bool, "not_included": [...],
  "balance": Money, "balance_source": "stored on the card" | "as you stated",
  "monthly_rate_percent": float, "annual_rate_bps": int,
  "rate_source": "stored on the card" | "as you stated",
  "payment_mode": "minimum" | "fixed", "fixed_payment": Money | None,
  "ui_results": [...]
}
```

**Logic:** Resolves active card account by name, applies explicit parameter overrides over stored
card terms (`finance_rate_bps`, `min_due_bps`, `min_due_floor_paise`), refuses missing rates or
non-decreasing debt via `ModelRetry`, delegates to `engine.money.card_payoff()`, and registers a
calculation result (`Summary` and `Table` views).

### `compare_offers`

**Purpose:** Compares two or more cash, card-EMI, or loan offers for one purchase side by side in
input order without choosing a winner. Quotes pairwise gaps via `difference_vs_offers` so the model
never subtracts by hand, refuses missing financing rates, and requires explaining `not_included`
when `complete_cost` is false.

**Input:**

```python
compare_offers(offers: list[Offer])
# Offer: {"label", "cash_price_paise", "discount_paise"?, "cashback_paise"?,
#         "financing"?: {"kind": "card_emi" | "loan", "annual_rate_bps", "months",
#                        "processing_fee_paise"?}}
```

**Output:**

```python
{
  "offers": [{"label", "cash_price_paise", "discount_paise", "cashback_paise",
              "interest_paise", "gst_on_interest_paise", "processing_fee_paise",
              "gst_on_fee_paise", "total_paid_paise", "net_cost_paise",
              "difference_vs_cash_paise",
              "difference_vs_offers": [{"label", "difference_paise"}],
              "instalments_paise": [int, ...],
              "apr": RateFit | None, "apr_percent": float | None,
              "complete_cost": bool, "not_included": [...]}]
}
```

**Logic:** Routes `card_emi` through `engine.money.card_emi` (18% GST on interest and fee) and
`loan` through `engine.money.amortize`, refusing missing rates via `ModelRetry`. `compare_offers`
does not register `ui_results`.

### `compound`

**Purpose:** Computes growth of a monthly investment at a stated assumed annual rate (capped at 600
months). The reply must state the assumed rate explicitly; display via `present`.

**Input:** `compound(monthly_paise: int, annual_rate: float, months: int, start_paise: int = 0)`

**Output:** `{"final": Money, "contributed": Money, "growth": Money, "yearly": [{"year",
"balance_paise"}], "assumptions": [...], "ui_results": [...]}`.

**Logic:** Iterates `balance = (balance + monthly) * (1 + annual_rate / 1200)` each month (Indian
SIP start-of-month convention) and registers a calculation result.

### `rupees`

**Purpose:** Formats integer paise using Arno's Indian rupee formatter (`12345678` → `"₹1,23,456.78"`)
so scripts never divide by 100 by hand.

**Input and output:** `rupees(paise: int) -> str`, backed by `india/formatting.format_paise`. Emits
no card and does not register `ui_results`.

### `show_chart`

**Purpose:** Renders a typed chart (`line`, `bar`, `stacked_bar`, or `donut`) from script variables.

**Input:** `show_chart(type, title, series: list[{"name": str, "points": [{"x": str, "y": int}]}],
unit: Literal["paise", "count", "percent"] = "paise", as_of: str | None = None)`.

**Output:** `{"chart_id": str, "shown": True, "ui_results": [descriptor]}` plus a `chart`
`data-card`.

**Logic:** Enforces 1 to 8 series (exactly 1 for `donut`), at most 400 points per series, and whole
numeric `y` values, then registers a `Chart`-only result for the turn.

### `show_table`

**Purpose:** Renders a typed table computed inside a script with explicit column types (`money`,
`number`, `text`, or `date`) and lineage to source `ui_results` IDs.

**Input:** `show_table(title: str, columns: list[{"label": str, "type": str}], rows: list[list[str |
int | float | None]], source_result_ids: list[str] = [], as_of: str | None = None)`. PydanticAI
flattens `TableInput` into these top-level keyword arguments inside `run_code`.

**Output:** `{"table_id": str, "shown": True, "ui_results": [descriptor]}` plus a `result`
`data-card`.

**Logic:** Enforces 1 to 12 typed columns, 0 to 50 rows, exact cell counts, integer paise (or `null`)
in `money` cells, and valid current-turn `source_result_ids`, registering `Table` and `Summary`
views.

### `present`

**Purpose:** Composes one structured `answer` card from the current turn's `ui_results` using a
bounded tree (`Stack`, `Comparison`, `Summary`, `Table`, `Chart`, `Receipt`).

**Input:** `present(display: AnswerNode)` where container nodes (`Stack`, `Comparison`) hold
`children` and leaf nodes (`Summary`, `Table`, `Chart`, `Receipt`) hold `result_id` only.

**Output:** `{"shown": True, "sections": int, "results": [{"id", "title"}]}` plus one `data-card`
with `{"card": "answer", "version": 1, "display", "results"}`.

**Logic:** Enforces at most 12 nodes, depth 3, no unknown or HTML/CSS/URL/action props, at least two
sections in `Comparison`, and valid view membership for every leaf. Deep-copies only referenced
result snapshots so the card reloads independently of the turn's registry.

---

## Result registry

Eleven of the 14 read and presentation tools register compact `ui_results` descriptors on
`AgentDeps.presentation`:

```python
{"id": "res_1", "title": "Spending by category, 2026-09-01 to 2026-09-30",
 "views": ["Summary", "Table", "Chart"]}
```

`recap`, `compare_offers`, and `rupees` do not register `ui_results`, and `present` consumes
existing `ui_results` rather than registering a new entry.

Each turn's registry carries a random scope prefix so stale IDs from earlier turns cannot resolve,
and caps storage at 64 results. Snapshots preserve period, filters, coverage gaps, source IDs, and
`as_of`; unknown money serializes as `{"type": "unknown"}`, never zero. Standard spending, movement,
and query reads also emit a fallback `card: "result"` collected into one closed **Supporting data**
disclosure on the assistant message; a valid `present` answer suppresses native or fallback cards it
already covers with the matching view, while capture, import, undo, movement, approval, chart, and
projection cards remain outside the disclosure.

Derived tables from `show_table` carry flat, deduplicated source lineage capped at 48 sources
(exceeding the cap raises `ModelRetry` rather than silently dropping provenance). Display rendering
strips internal `<outside>` and `</outside>` markers without rewriting stored records. Run
`pnpm --dir web test:answers` to verify the Python snapshot to TypeScript renderer contract.

---

## Write tools

All 7 write tools are direct model calls outside `run_code`.

### `record_movement`

**Tier:** Always instant, with an inline undo card.

**Purpose:** Records money that already moved (`expense`, `income`, `transfer`, `split`, `loan`,
`refund`, or `settlement`). Future dates raise `ModelRetry` directing the model to schedules.
Amounts are rupees in `*_rupees`; placeholders such as `"Unknown shop"` or category names are never
stored as payees.

**Input:**

```python
record_movement(
    movement: Expense | Income | Transfer | Split | LoanPayment | Refund | Settlement,
    not_duplicate: bool = False,
)
# Shared optional fields: date, note, description, upi_id, upi_ref, attachment_id
# Expense:     amount_rupees, payee=None, account=None, category=None, category_guess=None
# Income:      amount_rupees, payee=None, account=None
# Transfer:    amount_rupees, from_account, to_account
# Split:       total_rupees, my_share_rupees, people=[{name, amount_rupees, expected_date=None}],
#              payee=None, account=None, category=None, category_guess=None
# LoanPayment: amount_rupees, loan, account=None
# Refund:      amount_rupees, payee, account=None, category=None
# Settlement:  amount_rupees, person, direction="they_paid"|"i_paid", account=None,
#              expected_date=None
```

**Output:**

```python
{
  "status": "recorded", "movement_id": str, "undo_id": str, "date": str,
  "summary": str, "transfers": [...],
  # Expense also includes:
  "account_source": "stated" | "usual",
  "category_source": "stated" | "rule" | "guess" | "none",
  "card_bill": {"statement_close": str, "due_date": str} | None,
  # LoanPayment also includes:
  "principal_paise": int, "interest_paise": int, "note": str  # note only when loan rate missing
}
# Or on suspected 10-minute duplicate:
{"status": "possible_duplicate", "existing": {"movement_id", "date", "summary"}}
```

**Logic:** Computes idempotency key from `message_id` and `ctx.tool_call_id` (returning existing
transfers on retry), resolves account (stated → payee's usual → sole liquid → busiest everyday
liquid), checks 10-minute duplicates before creating any payee, applies category precedence (stated
→ user rule → llm rule → `category_guess` saved as llm rule → none), splits loan EMIs via
`loan_payment_parts`, writes balanced transfers and capture (including redacted `upi_ref` and any
validated `attachment_id` link) inside one `write_group`, and queues a risk check.

### `record_movements`

**Tier:** Always instant, with one inline `movements_batch` undo card covering the entire batch.

**Purpose:** Atomically logs 1–50 movements from one message or multi-row screenshot inside a single
`write_group` so one Undo reverses all movements and newly created payees together. Reuses the same
seven movement variants as `record_movement`.

**Input:**

```python
record_movements(
    movements: list[Movement],
    attachment_id: str | None = None,
    not_duplicate: bool = False,
)
```

**Output:**

```python
{
  "status": "recorded",
  "count": int,
  "undo_id": str,
  "total_outflow": {"paise": int, "text": str} | None,
  "total_inflow": {"paise": int, "text": str} | None,
  "movements": [...],
}
# The movements_batch card is emitted to tool metadata with title, totals, items, and undo_id.
# Or on suspected duplicate against an earlier ledger row (no writes performed):
{"status": "possible_duplicate", "duplicates": [{"index": int, "existing": {"movement_id", "date", "summary"}}]}
```

**Logic:** Pre-checks every batch entry inside the `write_group` transaction against pre-existing
effective movements (so two legitimately identical small transactions inside the same batch are both
preserved when `not_duplicate` is false, while any match against prior ledger state exits cleanly
without writing any rows). Executes each entry through `_record_one_in_group`, producing one
`movements_batch` card with per-row summaries, totals, and one undo button.

### `correct_movement`

**Tier:** Instant with an undo card for `scope="this"` only. In a flagged conversation,
`scope="future"` and `scope="all"` gate through `_gate()` and require confirmation via
`ApprovalRequired` (`src/arno/agent/toolset.py`).

**Purpose:** Fixes an existing movement's category, payee, note, expected date, business date,
`one_off` flag, amount, split shares, or account, or deletes it by appending a reversal. `scope`
controls how far a category or payee update reaches: `this`, `future` (upserts a user rule), or
`all` (upserts a user rule and relabels past movements for that payee).

**Input:**

```python
correct_movement(
    movement_id: str,
    changes: MovementChanges,
    scope: Literal["this", "future", "all"] = "this",
)

class MovementChanges:              # extra="forbid"
    category: str | None = None
    payee: str | None = None
    note: str | None = None
    expected_date: date | None = None
    one_off: bool | None = None
    date: date | None = None
    amount_rupees: Decimal | None = None
    my_share_rupees: Decimal | None = None
    shares: list[{"name": str, "amount_rupees": Decimal}] | None = None
    account: str | None = None
    delete: bool = False
```

**Output:** `{"status": "corrected" | "deleted", "undo_id": str | None, "summary": str, "rule": str
| None, "rows_changed": int, "movement": dict}`. UI receives a `correction` card naming any rule and
the row count.

**Logic:** Gates `scope != "this"` before writing. Rejects already-reversed movements. Label edits
update `movement_legs` without touching pgledger; `date` and `one_off` update `movements`; amount,
account, or delete operations append a reversal movement (and replacement transfers when editing) in
one atomic batch. Re-splits equal splits automatically or asks for explicit shares on unequal
splits, recomputes loan principal and interest via `loan_payment_parts`, upserts user rules and
payee defaults for `scope != "this"`, relabels past rows for `scope == "all"`, and warns on the card
if an amount edit changes a statement-verified movement.

### `update_setup`

**Tier:** Lasting. Instant in a clean conversation; requires confirmation in a flagged conversation.

**Purpose:** Adds, updates, or archives setup entities (never past money movements). Balances are
signed from the owner's perspective: a card or loan balance is the amount owed (positive when owed,
negative when a card is in credit; loans cannot be in credit). Adding a card requires positive
`credit_limit_rupees`, `statement_day`, and `due_day`; card EMI and finance terms are recorded only
when stated by the issuer.

**Input:** `update_setup(entity, action: Literal["add", "update", "archive"], data: SetupData)`. The
following table lists the nine supported entities and their `SetupData` fields
(`src/arno/agent/write_models.py`):

| Entity | Model fields |
|---|---|
| `account` | `name`, `new_name`, `kind` (`bank`, `card`, `cash`, `wallet`, `loan`, `asset`), `institution`, `last4`, `opening_balance_rupees`, `as_of`, `liquid`, `credit_limit_rupees`, `statement_day`, `due_day`, `emi_rate_percent`, `emi_fee_paise`, `finance_rate_percent` (percent per month, stored as annual bps), `min_due_percent`, `min_due_floor_paise`, `rate_percent`, `tenure_months`, `active` |
| `balance` | `account`, `balance_rupees`, `as_of` |
| `schedule` | `name`, `new_name`, `kind`, `subtype`, `amount_rupees`, `account`, `category`, `rule`, `starts`, `ends`, `confirmed`, `holiday_adjust`, `active` |
| `goal` | `name`, `new_name`, `target_rupees`, `deadline`, `priority` (1–5), `status` |
| `category` | `name`, `new_name`, `reducible` |
| `payee` | `name`, `new_name`, `kind`, `upi_id`, `default_category`, `create_person_account`, `active` |
| `settings` | `floor_rupees`, `base_currency`, `time_zone`, `default_horizon_days`, `notification_time`, `weekly_briefing_dow`, `reminder_lead_days`, `display_name` |
| `scheduled_question` | `id`, `prompt`, `rule`, `spend_limit_usd` |
| `baseline` | `category`, `amount_per_day_rupees` |

**Output:** `{"status": "saved", "undo_id": str | None, "summary": str, "result": dict}` for
`account`, `balance`, `schedule`, `goal`, `category`, `payee`, and `settings`; `scheduled_question`
and `baseline` return `{"status": "saved", "undo_id": str | None, "summary": str}` without
`"result"`. UI receives a `setup` card with undo.

**Logic:** Gates through `_gate()`, validates entity fields and RRULE syntax, applies the write
inside one `write_group` (creating paired pgledger accounts and equity adjustments for `account`,
`balance`, and `goal`; soft-archiving via `active = false`), clears `outside_text` on confirmed
names when approved on a confirm card, and queues a risk check when balances or plans change.

### `schedule_payment`

**Tier:** Always instant and undoable. Associates existing ledger money with one schedule occurrence
without creating new money. If the returned occurrence or linked movement descriptions contain
outside text, the tool marks them and flags the conversation for later lasting writes.

**Input:** `schedule_payment(schedule: str, due_date: date, movement_id: str, action: Literal["link",
"unlink"] = "link", amount_rupees: Decimal | None = None)`.

**Output:** `{"status", "payment_id", "undo_id", "occurrence": {...}}` plus a `setup` undo card.

**Logic:** Resolves unique schedule ID or name, requires `due_date` to fall within 31 days after the
movement date (refusing far dates with the nearest open occurrence), validates capacity and account
direction, applies idempotent link or unlink inside an audited `write_group`, and returns updated
occurrence payment evidence ([data-and-engine.md](data-and-engine.md#planning-and-schedule-payment-evidence)).

### `preference`

**Tier:** Lasting. Every action (`remember`, `forget`, `classify`) gates through `_gate()`.

**Purpose:** Remembers, forgets, or reclassifies a lasting `preference` (how Arno behaves), `fact`
(the user's world), or `rule` (how money data is treated). Supports a stable `subject` key for a
topic so contradicting facts replace the active value under the same `(kind, subject)` while
preserving the superseded value in `audit_log`. Never stores numbers owned by the ledger.

**Input:** `preference(action: Literal["remember", "forget", "classify"], text: str | None = None,
id: str | None = None, kind: Literal["preference", "fact", "rule"] | None = None, subject: str |
None = None)`.

**Output:** `{"status": "remembered" | "forgotten" | "classified", "id": str, "undo_id": str | None,
"text": str, "kind": str}` plus a `preference` undo card.

**Logic:**

- `remember` with `subject`: Normalizes `subject` (lowercased, collapsed whitespace), acquires a
  transaction-scoped advisory lock on `f"{kind or 'preference'}:{subject}"`, locks any active
  `(kind, subject)` row with `FOR UPDATE`, returns unchanged if `body` matches, updates `body` and
  `updated_at` in place if different, or inserts a new row.
- `remember` without `subject`: Matches any active row by case-insensitive `body` (updating `kind`
  if changed) or inserts a new row (defaulting `kind` to `preference`).
- `forget`: Looks up by `id` first, then falls back to matching an active row by case-insensitive
  `text`; if neither matches, raises `ModelRetry` listing active preferences. Sets `active = false`
  so the entry stays visible in Settings and remains undoable.
- `classify`: Requires both `id` and `kind`; updates `kind` and `updated_at`.
- Permanent deletion is Settings-only (`delete_preference_for_good` blanks `body` in `audit_log` and
  deletes the row) so every agent write remains undoable.

### `undo`

**Tier:** Always instant.

**Purpose:** Reverts a write group by `undo_id`, or the latest non-undone write in the current
conversation when omitted.

**Input:** `undo(undo_id: str | None = None)`

**Output:** `{"status": "undone" | "already_undone" | "conflict", "summary": str, "conflicts":
[...], "undo_undo_id": str | None}` plus an `undo` card.

**Logic:** Refuses committed imports (directing the user to the Imports screen), returns
`already_undone` if `undone_at` is set, returns `conflict` without mutating rows if a later write
modified any row in the group, otherwise reverses ledger transfers and restores non-ledger `before`
snapshots in one audited transaction. The card's **Undo** button calls this same service over REST.

---

## Delegation tools

Five tools from `pydantic-ai-harness`'s `SubAgents` and `TaskControl` capabilities manage the
background `parser` subagent (`src/arno/agent/builder.py`). None of them write to the ledger. The
following table describes each tool:

| Tool | Purpose |
|---|---|
| `delegate_task(agent_name, task, background, resume)` | Hands statement files to the `parser` subagent in the background; `resume=<task_id>` continues a paused or interrupted task with its saved history and a fresh step budget |
| `list_tasks()` | Lists tasks started in this conversation with statuses and IDs |
| `message_task(task_id, message)` | Steers a running task; delivered before its next model request |
| `answer_task(task_id, answer)` | Answers a question raised by the subagent via `ask_parent` |
| `cancel_task(task_id)` | Cancels a running task |

**Delegation rules:**

- **Never include attachment IDs in `task` text:** The host injects the turn's validated statement
  attachments directly into `AgentDeps.statement_attachments` (`src/arno/agent/builder.py`). The
  main agent delegates with plain prose—`delegate_task("parser", "Import the statement files from
  this turn.", background=True)`—and ends its turn.
- **Taint on every report or question:** `OutsideTextTaint` flags the conversation on every
  delivered parser report or question, regardless of whether raw statement text was quoted
  ([data-and-engine.md](data-and-engine.md#outside-text-and-taint-tracking)).
- **Backgrounding, budgets, and passwords:** `TaskControl(auto_background_after=15.0)` moves any
  blocking delegation to the background after 15 seconds. Each parser run carries
  `PARSE_USAGE_LIMITS` (60 requests, 120 tool calls). Statement passwords never travel through
  delegation or `ask_parent`; they enter on the import card's REST endpoint into an in-memory vault.
  If a background task settles while nobody is chatting, the server runs a wake turn
  ([harness.md](harness.md)). Committing an import stays a REST action on the review screen.

---

## Parser subagent functions

The `parser` subagent (`src/arno/agent/parser.py`) runs on its own model role (`PARSE_*`, falling
back to the chat model) with `run_code`, `ask_parent`, and `Planning` tools—never write or ledger
tools. Inside `run_code`, scripts invoke 13 host functions (180-second default timeout;
`wait_for_password` carries a 630-second timeout). Extracted rows stay on the host while `run_code`
returns counts, status, and a sample. The subagent finishes with a `ParseReport` whose `status` is
`ready`, `not_a_statement`, `blocked`, or `gave_up`.

The following table lists all 13 parser functions:

| Function | Purpose | Input | Output |
|---|---|---|---|
| `open_imports` | Creates import rows for the turn's host-supplied attachments and inspects each file. Refuses multi-file archives (`archive_files`, marks `failed`), PDFs exceeding 150 pages (`MAX_DOC_PAGES = 150`, `too_large: true`, marks `failed`), and image or zero-text-layer PDFs when `VISION` is unconfigured (`vision_required: true`, marks `needs_input`). Marks locked files `needs_password` | None | `[{"import_id", "kind", "locked", "institution", "variant", ...}]` |
| `wait_for_password` | Waits up to 600 seconds for a password submitted on the import card without exposing it to the model; stores an unlocked copy on success and re-runs archive, page-limit, and vision checks | `import_id` | `{"status": "unlocked" \| "timed_out" \| "failed", ...}` |
| `saved_parser` | Returns the bank layout's active parser script and known header variants, or `null` when none exists or `helper_version` is stale | `import_id` | `{"version", "script", "variants", "helper_version"}` or `null` |
| `doc_info` | Returns file type, page count, text-layer pages, sheets, delimiter, and encoding | `import_id` | `{"kind", "pages", "text_layer_pages", "sheets", "delimiter", "encoding"}` |
| `doc_words` | Returns redacted positioned words on one PDF page | `import_id`, `page=0` | `[{"text", "x0", "x1", "top", "bottom"}]` |
| `doc_words_range` | Returns redacted positioned words across a page range in one step | `import_id`, `first=0`, `last=0` | List of word lists, one per page |
| `doc_cells` | Returns redacted spreadsheet or text rows by sheet index or name | `import_id`, `sheet=0` | `[[cell, ...], ...]` |
| `import_state` | Returns host-written import state (never statement text), including `earlier_imports` sharing `file_hash` so undone or rejected uploads need no user question | `import_id` | `{"import_id", "status", "check", "account", "period", "reason", "counts", "lines", "debits", "credits", "earlier_imports"}` |
| `ledger_opening` | Returns the account's ledger balance before the statement period, or `null` on a first import | `import_id`, `account=None` | `{"paise", "as_of"}` or `null` |
| `check_statement` | Previews verification checks against host-owned period, opening, and expected header fingerprint | `import_id`, `rows`, `opening=None`, `closing=None`, `headers=None`, `empty_statement=False` | `{"status", "reason", "computed_closing", "chain_breaks", ...}` |
| `transcribe_page` | Runs an isolated tool-less `VISION` call on a page without a text layer and redacts returned text | `import_id`, `page=0` | `{"is_statement", "opening_paise", "closing_paise", "rows", "note"}` |
| `submit_parser` | Gate that reruns `code` in a fresh sandbox with prepended helpers, verifies each import, and replays committed non-undone statements of that layout | `import_ids`, `code` | `{"status", "reason", "counts", "sample", "checks", "replay", "imports"}` |
| `suggest_transfers` | Lists active own accounts (`bank`, `card`, `wallet`, `cash`, excluding the statement's account) and records validated transfer suggestions on `statement_lines.match_detail` for review-screen confirmation | `import_id`, `suggestions=None` (`[{"row" \| "line_id", "account_id"}]`) | `{"status": "suggested" \| "error", "suggested", "accounts", ...}` |

**`check_statement` verification rules:** Fails (`FAILED`) on non-integer or negative amounts,
non-ISO dates, missing opening or closing balances, or zero rows unless `empty_statement=True`.
Flags `DISCREPANCY` when `opening + credits − debits != closing`, when a printed running balance
chain breaks, when a date falls outside the host-derived statement period, when `headers` disagrees
with the expected layout fingerprint, or when a printed opening disagrees with `ledger_opening`.
Otherwise returns `VERIFIED`.

**`submit_parser` gate flow:** Reruns `code` in a fresh sandbox with the current helper block
prepended (clock and random blocked), runs `check_statement` on every import, replays the script
against every committed, non-undone statement for that bank layout to confirm exact row
reproduction, and moves passing imports to `review` (saving the parser version only upon commit).
See [import.md](import.md) for full reader, check, and reconciliation details.

---

## Categorizer functions

The categorizer agent (`src/arno/agent/categorizer.py`) runs as a background job after statement
commit (and again when a batch completes). It uses the chat model (or optional `BATCH` model for
large backfills), never interacts with the user, and has no ledger write tools other than
`save_categories`. Rules have already labeled matching rows ([import.md](import.md)).

The following table lists all 5 categorizer functions:

| Function | Purpose | Input | Output |
|---|---|---|---|
| `pending_payees` | Lists payees in the import not covered by any rule (with count, total, sample narration, and direction) plus active category names | `import_id` | `{"payees": [...], "categories": [...]}` |
| `categorize_now` | Sends payees to the labeling model concurrently in chunks of 12 (concurrency 8); host injects known rule-covered check payees to measure agreement and keeps `guess_reason` off the agent context. Refuses if a batch is pending | `keys` | `{"answers": {key: category \| null}, "agreement": {"rate", "checked"}, "failed": [key]}` |
| `submit_batch` | Available only when `BATCH` is configured. Pre-accounts a conservative priced estimate against the shared daily budget, records durable intent, submits chunks as one batch, and queues `collect_batch` | `keys` | `{"batch_id", "requests", "status": "pending"}` |
| `batch_results` | Reads collected answers from a completed batch in the same shape as `categorize_now` | `batch_id` | `{"answers": {key: category \| null}, "agreement": {"rate", "checked"}, "failed": []}` |
| `save_categories` | Writes one import-owned `write_group`: trusted answers label movements and upsert `llm` rules; keys in `distrust`, `null`, unknown categories, and `"Other"` stay uncategorized with a stored `category_hint` | `distrust` (optional list of keys) | `{"undo_id", "labeled", "uncategorized", "rules"}` |

Undoing the parent import reverts the categorizer's `write_group`, rolling back both labels and
`llm` rules together. Generic payment apps (Paytm/ONE97, PhonePe, Google Pay, CRED, Amazon Pay) are
never matched by name.
