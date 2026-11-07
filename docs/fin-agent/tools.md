# Tools

This file holds the detail behind [system design, step 5](system-design.md#step-5-tools): each
tool's name, description, input, output, and logic. Each description is the literal text the model
reads, tuned later by evals. Field names and tool names are part of the contract, because stored
conversations replay old tool calls.

The `plan.md` file owns decisions, and this file owns their shape. If the two disagree, the plan
wins and this file is stale.

![Scripts read and compute; every write is a direct call](diagrams/sd-tool-surface.png)

*Figure: the read/write execution boundary. Scripts reach read and presentation tools through
`run_code`; every write is a direct call with its own card. The original diagram omits the
presentation-only `show_table` and `present` tools described below ([note](diagrams/sd-tool-surface.md)).*

---

## Rules every tool shares

| Rule | Detail |
|---|---|
| Money out | `Money = {"paise": 300000, "text": "₹3,000"}`. Unknown is `null`, never `0` |
| Money in | The field name carries the unit. Read tools take `*_paise`, because scripts compute in paise. Write tools take `*_rupees`, as you said it, and code converts, so the model never multiplies by 100 |
| Names | Accounts, payees, and categories go in by name, and `resolve()` finds them. Movements go in by ID |
| Errors teach | An unknown or ambiguous name raises `ModelRetry`, listing the candidates. Inside a script it arrives as a catchable `Exception` with the same message; uncaught, the model gets the message and retries |
| Unexpected errors | A tool that throws anything else becomes a failed result the model can read, through `on_tool_execute_error`, and the real error goes to the trace. Read tools have a 60-second timeout, as a starting value; write tools have none ([step 8](system-design.md#when-a-tool-fails)) |
| Two payloads | Short content for the model; cards, curves, and source IDs for the UI, as `ToolReturn` metadata. Metadata from a tool called inside a script isn't streamed, so Fin-Agent emits those cards itself from the `run_code` return |
| Read returns | Only JSON-native types: `dict`, `list`, `str`, `int`, `float`, `bool`, `None`. Dates are ISO strings, and every balance has an `as_of` |
| Calling from a script | `await` and keyword arguments only: `await amortize(loan="Home loan")` |
| Coverage | `coverage()` reports which dates have statements. A gap is reported, never summed as zero |
| Outside text | `mark()` wraps the text in markers, strips any closing marker and invisible character inside it, caps it at 200 characters, and sets `conversations.outside_text_at`. The model can't set or clear this. The flag is the boundary, and the markers are only a hint ([write tiers](design.md#untrusted-text-and-write-tiers)) |
| Lists | 50 rows by default, with a cursor |
| Writes | `write_group()` is one short transaction whose audit rows share a group, returned as `undo_id`. The message ID and `ctx.tool_call_id` come from context, so they're never inputs |
| Confirm | `gate(change)`: if the conversation is flagged and `ctx.tool_call_approved` is false, raise `ApprovalRequired`. Approval ends the run and the tool runs again from the top, so it validates again. Stored history binds the answer: it counts once, only for a pending call, with the stored arguments ([step 7](system-design.md#decisions)) |
| Tier | Each tool declares its [write tier](design.md#untrusted-text-and-write-tiers) in its tool metadata |
| Old returns | Compaction can remove an old return, so no tool relies on an ID staying in the chat. `undo` reads `audit_log`, and an unknown ID raises `ModelRetry` ([step 6](system-design.md#compaction)) |

**`run_code`** comes from the harness, so Fin-Agent configures it rather than builds it. The model
writes Python that calls the read tools inside Monty. See [Code Mode](design.md#code-mode).

---

## Read tools: called from scripts

### `get_financial_context`

**Description:** "The user's whole picture: today's date, settings, accounts with balances and
ages, the next 30 days of scheduled events, goals, who owes what, remembered preferences, what setup
is missing, and data warnings. The sandbox has no clock, so get today's date here."

**Input:** none

**Output**

```python
{
  "today": "2026-10-31",
  "settings": {"currency": "INR", "floor": Money, "default_horizon_days": 90},
  "accounts": [{"name", "kind", "liquid", "known": bool, "balance": Money, "as_of",
                "statement_age_days", "covered": {"from", "to"}, "gaps": [...]}],
  "liquid_total": Money,  # null when any active liquid account is not known
  "upcoming": [{"date", "name", "kind", "amount": Money}],
  "goals": [{"name", "target": Money, "saved": Money, "deadline", "priority"}],
  "owed": [{"person", "amount": Money, "expected_date"}],  # positive: they owe you
  "preferences": [{"id", "text"}],                         # active ones only
  "setup_gaps": ["no income schedule"],
  "warnings": ["SBI balance is 23 days old"]
}
```

UI: source IDs for citations. The turn also gets separate `ui_results` descriptors for accounts,
upcoming payments, goals, and owed, without a wall of context cards.

A balance is known only when an explicit opening balance, a user-stated balance, or a committed
statement anchors it. Those sources all persist `statement_balance_paise` with a valid
`statement_balance_as_of`, including an explicit zero. A ledger movement changes a known balance;
it never establishes one. An account whose first entry is an expense or income therefore keeps
`known: false`, `balance: null`, and `as_of: null`, while its real net change remains visible in
the account payload and Ledger. `liquid_total` is null when any active liquid account is unknown;
it is not a complete current cash claim.

**Logic**

```
settings   = read settings
accounts   = active accounts joined to v_balances as of today
for each account: known = statement_balance_paise and statement_balance_as_of are present
                  balance = ledger balance only when known
                  coverage from imports, statement age
upcoming   = expand schedule rules over 30 days, adjust for holidays, add card due dates
goals      = goals plus the balance of each goal account
owed       = v_owed
setup_gaps = no income schedule? no floor? an account with no stated balance?
warnings   = stale balances + coverage gaps
mark() only the names whose row still has outside_text = true
```

The server also runs this function when a thread starts, and pins the result as the thread's snapshot
([step 6](system-design.md#what-goes-in-each-request)). A script calls it to re-read.

### `query_movements`

**Description:** "List individual movements with their transfers, category, payee, and evidence. Use
it to find a row or see what's behind a number. For totals, use summarize_spending."

**Input**

```python
query_movements(filters: MovementFilters | None = None, limit: int = 50, cursor: str | None = None)

class MovementFilters:
    period: str | None          # "this_month", "last_month", "2026-09", "2026-09-01..2026-09-15"
    account: str | None
    category: str | None
    payee: str | None
    kind: str | None
    min_paise: int | None
    max_paise: int | None
    text: str | None            # searches descriptions and notes
    include_reversed: bool = False
```

`MovementFilters` is a typed model with `extra="forbid"`, so a script sees its keys and an unknown
key, such as `categories` or `date_from`, fails with the list of real ones. A dropped key would
answer a different question: a silently ignored `categories` filter once returned the whole month
as food spending.

**Output**

```python
{"rows": [{"id", "date", "kind", "description", "payee", "category", "amount": Money,
           "transfers": [{"from", "to", "amount": Money}], "evidence": ["chat", "statement_line"]}],
 "total_count": int, "next_cursor": str | None,
 "coverage": {"complete": bool, "gaps": [...]}}
```

UI: row IDs for citations.

**Logic**

```
period = resolve_period(filters.period, today)     # on the host, which has a clock
names  = resolve() to IDs
rows   = movements + transfers (by movement_id in metadata) + labels + captures,
         filtered, newest first, paged
hide reversed pairs unless include_reversed, in SQL, so total_count, the page, and
  next_cursor agree
mark() the description and payee on outside rows
return rows, total_count, next_cursor, coverage(account, period)
```

### `summarize_spending`

**Description:** "Totals of money spent, net of refunds, grouped however you need. Loan principal,
card bills, investments, and money others owe never count as spending. To compare periods, call it
twice."

**Input**

```python
summarize_spending(group_by: Literal["category", "payee", "account", "month", "week", "none"],
                   period: str, filters: SpendFilters | None = None)

class SpendFilters:                 # what the sum honors; any other key is an error
    account: str | None
    category: str | None
    payee: str | None
    text: str | None
```

**Output**

```python
{"period": {"from", "to"}, "groups": [{"key", "amount": Money, "count": int}],
 "total": Money | None, "coverage": {"complete": bool, "gaps": [...]}}
```

UI: breakdown card and source row IDs.

**Logic**

```
period = resolve_period(period, today)
cov    = coverage(filters.account, period)
if nothing logged and no statement covers the period: return total = null with the gaps
groups = SELECT key, SUM(amount), COUNT(effective movements) FROM v_spending ... GROUP BY key
         # the sum nets a reversal against its original; the count leaves the pair out
if group_by == payee: mark() outside names         # grouping by category flags nothing
return groups, total, cov                          # complete = false when part has no statement
```

### `project`

**Description:** "Forecast the user's cash against their floor (the minimum balance they want to
keep). With a hypothetical purchase, also returns the yes, yes-but, or no verdict and the tradeoffs.
Never give your own verdict. State the data_quality notes in your answer."

**Input**

```python
project(horizon_days: int | None = None, hypothetical: Hypothetical | None = None)

class Hypothetical:
    amount_paise: int
    date: str
    method: Literal["account", "card", "emi"]
    emi_months: int | None = None
    account: str | None = None
```

**Output**

```python
{"floor_probability": 0.93, "median_low": {"amount": Money, "date"},
 "first_floor_crossing": str | None,
 "optimistic": {"floor_probability": 0.97, "income": [...]} | None,
 "goals": [{"name", "completes_on", "slips_days"}],
 "verdict": "yes" | "yes-but" | "no" | None,
 "tradeoffs": [{"kind", "detail"}],
 "data_quality": {"balance_ages", "baseline_sources", "gaps", "setup_gaps"},
 "source_ids": {"accounts", "cards", "schedules", "goals", "categories", "payees"}}
```

The daily curve, the full source list, and the verdict card travel as UI metadata; the model sees
the compact payload above. `optimistic` is the unconfirmed-income case and never feeds a verdict.
`data_quality.balance_ages[].known` uses the same anchor rule as the account summary. An unknown
liquid account keeps `known: false` and no balance date; the gap says the projection starts from
its recorded net change, not a verified bank balance. That provisional input is never presented as
known cash.

UI: daily curve (median and P10-P90), source IDs, projection card.

**Logic**

```
horizon = max(90 days, furthest goal deadline, asked horizon)
inputs  = liquid balances, expanded schedules, card cycles, goal contributions,
          money owed with an expected date, baselines
runs    = 1,000 seeded walks; each day apply income, obligations, card dues,
          goal contributions, resampled variable spend
curve   = median and P10-P90 for each day
floor_probability = share of runs that never cross the floor
if hypothetical:
    rerun with it
    if threshold met, no goal slips, no obligation missed: verdict = yes
    else:
        tradeoffs = rerun for: wait until date X, card or EMI, delay a goal, cut a category
        verdict   = yes-but if any works, otherwise no
```

"Wait until date X" is already a tradeoff, so a script doesn't need to sweep dates. The engine is
detailed in [The projection engine](design.md#the-projection-engine).

### `run_query`

**Description:** "Read-only SQL for questions the typed tools can't answer, including conversation
search. Try query_movements and summarize_spending first. Prefer the views v_spending, v_cashflow,
v_balances, and v_owed. Schema: …"

**Input:** `run_query(sql: str)`

**Output**

```python
{"columns": [...], "rows": [[...]], "row_count": int, "truncated": bool, "sql": str}
```

UI: the SQL, shown next to the answer.

**Logic**

```
connect as agent_ro                    # SELECT only, no auth tables
in a read-only transaction, under a client-side deadline:
    SET LOCAL statement_timeout = '2s'
    run the SQL with prepare=True      # the extended protocol rejects a second statement
    fetch up to 1,001 rows
    always roll back                   # undoes anything the statement set
truncated = got 1,001
whole NUMERIC values → int             # Code Mode would turn a Decimal into a string
mark() every text value; always set the flag
on SQL error: ModelRetry(Postgres's message)
```

The grants are the boundary. Everything else here is an extra layer, because the role can change its
own settings ([step 7](system-design.md#decisions)). `mark()`'s cap applies here too, so a message
search returns snippets, and the model can use `substr` to read further.

### `amortize`

An EMI is the fixed monthly loan payment. Amortization is how each EMI splits into interest and
principal.

**Description:** "Loan maths: EMI, total interest, payoff date, and the monthly schedule. Pass a
loan's name or its terms. Add prepay to compare a lump-sum prepayment. Use this instead of writing
the formula."

**Input**

```python
amortize(loan: str | None = None, principal_paise: int | None = None,
         annual_rate: float | None = None, months: int | None = None,
         prepay: Prepay | None = None)

class Prepay:
    amount_paise: int
    at_emi: int = 1                                   # before which EMI, counting from the next one
    reduce: Literal["tenure", "emi"] = "tenure"       # finish sooner, or pay less each month
```

**Output**

```python
{"emi": Money, "months": int, "total_interest": Money, "payoff_date": str | None,
 "schedule": [{"n", "date", "interest_paise", "principal_paise", "balance_paise"}],
 "prepay": {"new_emi": Money, "new_months": int, "new_total_interest": Money,
            "interest_saved": Money, "months_saved": int} | None,
 "assumptions": ["8.5% as you entered it; a floating rate can change"]}
```

**Logic**

```
if loan: P = loan_terms(): the outstanding balance (the loan account's negated ledger balance),
         its rate, its tenure as the cap, and its EMI schedule's amount. The eval grader reads
         the same terms
prepay is a typed Prepay; an unknown key is an error, never ignored
else:    r = annual_rate / 1200; emi = P·r·(1+r)^n / ((1+r)^n − 1); if r == 0: emi = P / n
if emi <= first month's interest: ModelRetry("this EMI never repays the loan")
schedule = each month: interest = round(balance·r)
                       principal = min(emi − interest, balance)
                       balance −= principal
if prepay:
    subtract amount before EMI at_emi
    reduce = tenure: same EMI, walk to zero
    reduce = emi:    recompute EMI over the remaining months
    compare totals
```

This runs on the host with `Decimal`. `record_movement` uses the same function to split an EMI.

### `compound`

**Description:** "Growth of a monthly investment at an assumed annual rate. The rate is an
assumption, not a prediction or advice: state it in your answer."

**Input:** `compound(monthly_paise: int, annual_rate: float, months: int, start_paise: int = 0)`

**Output**

```python
{"final": Money, "contributed": Money, "growth": Money,
 "yearly": [{"year", "balance_paise"}],
 "assumptions": ["12% a year, assumed", "paid at the start of each month"]}
```

**Logic**

```
r = annual_rate / 1200
balance = start
each month: balance = (balance + monthly) · (1 + r)    # how Indian SIP calculators count it
contributed = start + monthly · months
growth = final − contributed
```

### `show_chart`

**Description:** "Show the user a chart: line, bar, stacked_bar, or donut. Take values from variables
in your script, never typed by hand. Use one when a shape says more than a sentence."

**Input**

```python
show_chart(type: Literal["line", "bar", "stacked_bar", "donut"], title: str,
           series: list[Series], unit: Literal["paise", "count", "percent"] = "paise",
           as_of: str | None = None)

class Series:
    name: str
    points: list[Point]    # Point = {"x": str, "y": int}
```

**Output:** Model: `{"chart_id", "shown": True, "ui_results": [descriptor]}`. UI: the checked spec,
`as_of`, and the script as its citation.

**Logic**

```
validate: series and point limits, a donut has one series, values are numbers
attach the script that made it
register a Chart-only result for this turn
after run_code returns: read the spec from its metadata and emit it as a data part,
                        stored with the message so it survives a reload
no ledger write, no audit
```

### `show_table`

**Description:** "Show a table you computed in your script. Pass columns with an explicit type
(money, number, text, or date), rows of cells, and optionally the ui_results ids it came from.
Money cells are integer paise. At most 12 columns and 50 rows; a comparison is better as a table
than as prose."

**Input**

```python
show_table(title: str, columns: list[TableColumn], rows: list[list[Any]],
           source_result_ids: list[str] = [], as_of: str | None = None)

class TableInput:
    title: str
    columns: list[{"label": str, "type": "money" | "number" | "text" | "date"}]
    rows: list[list[str | int | float | None]]   # at most 50 rows, 12 columns
    source_result_ids: list[str] = []            # current-turn ui_results ids
    as_of: str | None = None
```

Pydantic AI flattens the tool's single `TableInput` model into these named arguments in the
CodeMode catalog. Scripts call `await show_table(title=..., columns=..., rows=...)`, without a
`table=` wrapper.

**Output:** Model: `{"table_id", "shown": True, "ui_results": [descriptor]}`. UI: a readable
result card with a caption, typed cells, and any source result titles.

**Logic**

```
validate: title, 1-12 typed columns, 0-50 rows, each row has one cell per column
money cells must be integer paise or null; number cells numeric or null; text cells not objects
resolve every source_result_ids against this turn's registry (unknown is ModelRetry)
register a Table/Summary result; unknown is not zero
```

### `present`

**Description:** "Compose the answer from this turn's ui_results. display is a bounded tree:
{'_type': 'Stack' | 'Comparison' | 'Summary' | 'Table' | 'Chart' | 'Receipt', 'children': [...],
'result_id': 'res_1'}. Containers have children; leaves have a result_id and no other props. Use
only a view the result offers. Comparison aligns sections and never computes a difference."

**Input**

```python
present(display: AnswerNode)

class AnswerNode:
    _type: Literal["Stack", "Comparison", "Summary", "Table", "Chart", "Receipt"]
    children: list[AnswerNode] | None
    result_id: str | None       # leaves only
```

**Output:** Model: `{"shown": True, "sections": int, "results": [{"id", "title"}]}`. UI: one
`data-card` with `{card: "answer", version: 1, display, results}`. `results` holds only the
referenced snapshots, deep-copied from the registry, so the card survives a reload without the
turn's registry.

**Logic**

```
validate: at most 12 nodes and 3 container levels, no unknown props, no HTML/CSS/URL/action props,
          containers have children and no result_id, leaves have a result_id and no children,
          Comparison has at least two leaf sections
for each leaf: the id is current for this turn and the view is in the result's offered views
                (missing/stale/unsupported -> ModelRetry with the offered views)
copy only the referenced snapshots, in tree order
```

The views are deliberately narrow. `Summary` is headline values plus coverage; `Table` is the
saved typed table; `Chart` is a saved chart spec; `Receipt` is the original projection card with
its curve, sources, and evidence control. A spending result offers Summary, Table, and Chart; a
movements or query result offers Summary and Table; a projection offers Receipt, Summary, and
Table. The frontend resolves every leaf from the enclosing saved `results`; the model cannot pass
a number.

### Result registry

Every read tool adds a compact `ui_results` descriptor to its return:

```python
{"id": "res_1", "title": "Spending by category, 2026-09-01 to 2026-09-30",
 "views": ["Summary", "Table", "Chart"]}
```

The registry lives on `AgentDeps`; each ID includes a random registry scope so a stale ID cannot
resolve to a different result in another turn. A turn keeps at most 64 results. Snapshots carry the
period, filters, coverage gaps, source IDs that the tool actually returned, and `as_of` where it
applies. Unknown money is `{"type": "unknown"}`; it is never filled with zero. Ordinary
spending, movement, and query reads also emit a readable `card: "result"` fallback, so a turn that
does not call `present` still shows its data. Native chart, projection, amortize, and compound
cards keep their existing controls and gain a `result_id`; a composed answer hides a native card
only when the same assistant message covers it with the matching view.

Read results in one assistant message collect into one closed **Supporting data** disclosure with a
read count, rather than one top-level card per read. A valid composed answer removes the result
views it already shows; if there is no valid answer, or the stream failed, the same disclosure keeps
the readable data. Legacy `card: "query"` records are deduplicated when an equivalent saved query
result carries the same SQL. Capture, import, undo, movement, approval, chart, and projection cards
stay outside the disclosure. Empty results show one concise empty line plus coverage, never both a
metrics block and a second empty table. Displayed titles, cells, notes, and Markdown strip only the
internal `<outside>` and `</outside>` envelope markers; saved messages and result snapshots are not
rewritten.

Computed table columns carry stable positional keys. Their source lineage retains each source's
period, filters, coverage, assumptions, SQL, and ledger IDs, including when one derived table feeds
another. Lineage is flat, deduplicated, and capped at 48 sources; exceeding the cap is a retry rather
than silently losing evidence. Incomplete coverage stays visible beside the derived table.
Source preview limits remain attributed to the source; they never describe a derived table's rows.

Projection summaries show an unknown probability until a floor is configured, matching the native
receipt and its optimistic variants. Summary percentages use the same rounding as the receipt.
Saved answer data, including legacy standalone charts and receipts, is validated through every
nested chart/receipt field that a renderer
uses. Invalid data cannot suppress a valid fallback card, and a widget error boundary isolates an
unexpected rendering failure from the conversation. Run `pnpm test:answers` in `web` for the Python
snapshot to TypeScript validation/rendering contract checks (no database or model required).

---

## Write tools: normal tool calls

### `record_movement`

**Tier:** always instant, with an undo card.

**Description:** "Log money that moved: expense, income, transfer between your own accounts (card
bill, SIP, saving to a goal), split (whole bill and your share; each person and their share), loan
EMI (total paid), refund, or settlement (direction says who paid whom). Say what happened; code
works out the transfers. Ask first if the amount is missing or a split isn't clear. `date` is ISO,
omit for today, never a future date. `account` is optional; code uses the payee's usual account, or
the busiest/default one. Pass `category` only if the user named one; otherwise pass
`category_guess`. Amounts are rupees in `amount_rupees`, never paise."

**Input**

```python
record_movement(movement: Expense | Income | Transfer | Split | LoanPayment | Refund | Settlement,
                not_duplicate: bool = False)

# All kinds also take: date=None, note=None, description=None, upi_id=None
Expense:     amount_rupees, payee, account=None, category=None, category_guess=None
Income:      amount_rupees, payee, account=None
Transfer:    amount_rupees, from_account, to_account
Split:       total_rupees, my_share_rupees, people=[{name, amount_rupees, expected_date=None}],
             payee, account=None, category=None, category_guess=None
LoanPayment: amount_rupees, loan, account=None
Refund:      amount_rupees, payee, account=None, category=None
Settlement:  amount_rupees, person, direction=Literal["they_paid", "i_paid"], account=None,
             expected_date=None
```

**Output**

```python
{"status": "recorded", "movement_id", "undo_id", "date",
 "summary": "₹450 to Swiggy from SBI savings, Food",
 "transfers": [...], "account_source": "stated" | "usual",
 "category_source": "stated" | "rule" | "guess" | "none"}
# or
{"status": "possible_duplicate", "existing": {"movement_id", "date", "summary"}}
```

UI: undo card, with the screenshot beside the fields if it came from a photo.

**Logic**

```
key = message_id + ctx.tool_call_id
if a movement has this key: return it                    # a retry
if date > today: ModelRetry("use a schedule for future payments")
if a split's parts don't add up to the total: ModelRetry(...)

account  = stated, else this payee's usual account, else the only liquid account,
           else ModelRetry("which account?")
payee    = find or create
           outside_text = came from a photo, or (flagged and name not in your message)
category = stated, else your rule, else llm rule, else guess (saved as an llm rule), else none

if same account, amount, payee logged by chat within 10 min and not not_duplicate:
    return possible_duplicate                            # writes nothing; the model asks

transfers = by kind:
    expense     account → Spending, labeled
    income      Income → account
    transfer    from → to
    split       account → Spending (your share); account → each person
    loan        account → Loan (principal); account → Spending (interest), split by amortize
    refund      Spending → account, labeled
    settlement  they_paid: person → account; i_paid: account → person

write_group:
    pgledger_create_transfers(transfers, key, event_at = noon India time)
    insert movement, labels, capture (upi_ref from the server's extraction, never typed by the model)
queue the risk check
```

### `correct_movement`

**Tier:** always instant, with an undo card, including `scope="all"`
([decided in step 1](system-design.md#retroactive-payee-corrections)).

**Description:** "Fix a logged movement: category, payee, note, expected date, date, amount, account,
or delete it. For a category or payee, scope sets how far it goes: this row, this row and future
ones, or all rows past and future ('ramesh@okaxis is my landlord')."

**Input**

```python
correct_movement(movement_id: str, changes: Changes,
                 scope: Literal["this", "future", "all"] = "this")

class Changes:
    category: str | None
    payee: str | None
    note: str | None
    expected_date: str | None
    date: str | None
    amount_rupees: Decimal | None
    account: str | None
    delete: bool = False
```

**Output:** `{"status", "undo_id", "summary", "rule": "Swiggy → Groceries" | None, "rows_changed":
int}`. UI: undo card naming the rule and the row count.

**Logic**

```
m = load(id), else ModelRetry
if m is already reversed: ModelRetry
write_group:
    category, payee, note, expected date → edit transfer_labels   # ledger untouched
    date → edit movements.date                                    # ledger untouched
    amount or account → new movement, reverses_id = m:
                        opposite transfers + corrected transfers, one batch
    delete → new movement, reverses_id = m: opposite transfers only
    if scope != "this":
        upsert rule (payee → category, source = user), replacing any llm rule
        set the payee's default category
    if scope == "all":
        relabel the payee's past transfers; rows_changed = count
queue the risk check if amount, account, date, or delete changed
if amount changed on a movement a committed statement verified:
    the card warns that the ledger no longer matches the bank,
    and that the next import's opening check will fail                # allowed, not blocked
```

### `update_setup`

**Tier:** lasting. Instant in a clean conversation, confirm card in a flagged one.

**Description:** "Change setup: accounts, balances, schedules, goals, categories, payees, settings
(the floor), scheduled questions, and baselines. Not for money that moved. Amounts are rupees in
`*_rupees` fields. A schedule uses an iCalendar RRULE; `last4` is only the last four digits; the
floor is the minimum balance to keep; `rate_percent` is annual; `as_of` dates a stated balance; a
baseline is typical daily spend.
For action "add" on a card, a positive credit limit, a statement day, and a due day are required.
If the user does not know one, leave the card pending instead of guessing. A bank needs only a
name, and its balance and last4 are optional. An update changes only the fields you pass.
Example: {"entity": "settings", "action": "update", "data": {"floor_rupees": 30000}}."

**Input**

```python
update_setup(entity: Literal["account", "balance", "schedule", "goal", "category",
                             "payee", "settings", "scheduled_question", "baseline"],
             action: Literal["add", "update", "archive"],
             data: ...)  # a typed model for each entity, for example:

# balance:  {account, balance_rupees, as_of}
# schedule: {name, kind, subtype, amount_rupees, account, category, rule, starts, ends, confirmed}
# goal:     {name, target_rupees, deadline, priority}
# scheduled_question: {prompt, rule, spend_limit_usd}
```

**Output:** `{"status": "saved", "undo_id", "summary", "before", "after"}`. UI: undo card, or the
confirm card first.

**Logic**

```
validate data for this entity; resolve names; parse the schedule rule
change = the exact before and after
gate(change)
write_group:
    account: create the pgledger account and accounts row; opening balance from equity
    balance: diff = stated − ledger; if diff: adjustment movement against equity
             set statement_balance and as_of
    goal:    create the goal account and goals row
    other:   insert or update; archive sets active = false, never deletes
    if approved: clear outside_text on the names shown on the card
queue the risk check if the money picture changed
```

### `preference`

**Tier:** lasting.

**Description:** "Remember a lasting fact or rule about the user, or forget one. Call it unprompted
when you notice one, such as 'Mom's monthly transfer isn't discretionary.' Never store a number the
ledger owns."

**Input:** `preference(action: Literal["remember", "forget"], text: str | None = None, id: str |
None = None)`

**Output:** `{"status": "remembered" | "forgotten", "id", "undo_id", "text"}`. UI: "Remembered" card
with undo, or the confirm card first.

**Logic**

```
gate(the exact text)                  # both actions: forgetting a rule lasts too
remember:
    same text already active: return it
    insert(created_by = agent, source_message_id, active = true)
forget:
    find by id, else ModelRetry listing active preferences
    set active = false                # stays listed in Settings, and can be turned back on
```

**Delete is not in this tool.** Settings can delete a preference for good, with a confirm. That also
blanks its text in the audit rows, or it wouldn't really be deleted, and it can't be undone. Keeping
it out of the tool means every write the agent makes stays undoable, and a tool with a mode undo
can't reverse would fire the [Guardrails trigger](harness.md#later-with-triggers). In chat, the agent
turns the preference off and points you to Settings.

### Delegation: `delegate_task` and the task tools

**Tier:** none of these writes to the ledger. They come from the harness's `SubAgents` capability
with `TaskControl`, so Fin-Agent configures them rather than builds them.

| Tool | Use |
|---|---|
| `delegate_task(agent_name, task, background, resume)` | Hand an upload to the `parser` subagent, in the background. `resume=<task_id>` continues a finished or interrupted task with its history |
| `list_tasks()` | The tasks started in this conversation, with status and IDs |
| `message_task(task_id, message)` | Steer a running task; it reads the message at its next model request |
| `answer_task(task_id, answer)` | Answer a question the task asked through `ask_parent` |
| `cancel_task(task_id)` | Stop a task |

The parser's description in the main prompt: "Imports uploaded statements. Give it the attachment
IDs. It reports a status, counts, and anything blocking; it never writes to the ledger, and you
can't commit an import."

**Logic**

```
upload: the main agent calls delegate_task("parser", "<attachment ids>", background=True)
    one upload is one task; the turn ends at once
the task runs under the DelegationTasks owner opened at startup; history is saved after each step
a report or a question waits for the conversation's next turn (DelegationReports)
    a report carries statuses and counts; statement text in it goes through mark()
a password never passes through these tools or ask_parent: the subagent's wait_for_password
    host tool waits on the vault, and the import card's endpoint fills it
after a restart the task is cancelled; you ask, and the main agent resumes it
committing is a REST endpoint on the review screen; no tool does it
```

### `undo`

**Tier:** always instant.

**Description:** "Undo a write. With no ID, it undoes the last write in this conversation. Use the
undo_id from a write's result for an earlier one."

**Input:** `undo(undo_id: str | None = None)`

**Output:** `{"status": "undone" | "already_undone" | "conflict", "summary", "conflicts": [...]}`.
UI: a card saying what was reverted.

**Logic**

```
group = undo_id, or the last un-undone group in this conversation, else ModelRetry
if group is a committed import: ModelRetry("undo imports from the Imports screen")
if group.undone_at: return already_undone
if a later write changed any of its rows: return conflict, naming them   # changes nothing
write_group:
    ledger rows: a reversal movement with opposite transfers
    other rows:  restore the before JSON
    set undone_at
queue the risk check
```

The undo button on a card calls this same function over REST, so undo works without the LLM.

---

## Parser subagent functions

The `parser` subagent is its own agent: its own instructions (the statement know-how that was the
`parse-statement` skill), its own model setting (`PARSE_*`, falling back to the chat model), and a
different toolset: `run_code`, `ask_parent`, and `Planning`'s plan tools. It has no write tools and
no ledger tools. A script reaches the functions below. Rows stay on the host, and `run_code` returns
counts, status, and a sample, so a statement's text doesn't fill the context.

The main agent's deps are passed to it, so the file isn't in its deps. Each function takes the
`import_id`, which one task may hold several of, and loads the file itself.

The task ends with a report: `ready` for review, `not_a_statement` with the kind it found,
`blocked` with what it needs, or `gave_up` with the reason and what it tried.

| Function | Description | Input | Output |
|---|---|---|---|
| `open_imports` | Create the import rows for this turn's uploaded files, which the host passes in, so no model copies an ID, and read each file | None | `[{"import_id", "kind", "locked", "institution", "variant"}]` |
| `wait_for_password` | Wait until the password typed on the import card unlocks the file. The model never sees it | `import_id` | `{"status": "unlocked" \| "timed_out"}` |
| `saved_parser` | The bank's active parser version and its known header variants | `import_id` | `{"version", "script", "variants"}` or `null` |
| `doc_info` | What the file is | `import_id` | `{"kind", "pages", "text_layer_pages", "sheets", "delimiter", "encoding"}` |
| `doc_words` | One page's words with positions | `import_id`, `page` | `[{"text", "x0", "x1", "top", "bottom"}]` |
| `doc_words_range` | Many pages at once; use this past a few pages | `import_id`, `first`, `last` | The same words, for each page |
| `doc_cells` | A sheet's rows. Text formats arrive as one-line cells | `import_id`, `sheet=0` | `[[cell, ...], ...]` |
| `ledger_opening` | The account's ledger balance before this statement, for statements that print none | `import_id`, `account` | `{"paise", "as_of"}` or `null` on a first import |
| `check_statement` | The checks, on rows a script produced. Call it as often as useful | `import_id`, `rows`, `opening`, `closing` | `{"status", "reason", "computed_closing", "chain_breaks", ...}` |
| `transcribe_page` | An isolated, tool-less `VISION` call, for a page with no text layer | `import_id`, `page` | Typed rows |
| `submit_parser` | The gate. Submit one bank layout's script for its imports in this upload; an upload mixing banks gets one submit per bank. The model never submits rows | `import_ids`, `code` | `{"status", "counts", "sample", "reason", "replay"}` |

**`check_statement`**

```
FAILED if an amount isn't a non-negative integer, a date isn't ISO, or opening or closing is missing
FAILED if zero rows, unless the statement says there were none
DISCREPANCY if opening + credits − debits != closing
DISCREPANCY if a printed running balance doesn't follow from the row before it
DISCREPANCY if a date falls outside the period
    # the period comes from outside the parser: a month name in the header,
    # or the day after the ledger's last row for this account
DISCREPANCY if the printed opening differs from ledger_opening
VERIFIED otherwise
```

**`submit_parser`**

```
rerun the script's text in a fresh AsyncMonty           # only this proves it stands alone
    prepend the helper block at its current version
    os_policy blocks the clock and random; sleep limit set
run check_statement on what it returned, for each import
replay it on every stored statement from that bank whose import is committed and not undone;
    each must reproduce its saved statement_lines exactly
keep the rows on the host; return counts, a five-row sample, and the first replay mismatch
on any failure: return the reason; the subagent repairs and submits again
if everything passes: each import moves to review, holding the script and its header variant
the version is saved only when you commit
```

Details of the readers and the checks are in [`import.md`](import.md).

---

## Categorizer functions

The categorizer is an agent the job runner starts after a commit, and again when a batch ends. It
uses the chat model and has no ledger tools except `save_categories`. Rules have already labeled
what they match ([`import.md`](import.md#categorizing-after-commit)).

| Function | Description | Input | Output |
|---|---|---|---|
| `pending_payees` | Payees in this import no rule covers, with their row count, total, and a sample narration, plus your categories | `import_id` | `{"payees": [...], "categories": [...]}` |
| `check_payees` | Payees a rule already covers, without their category, for measuring agreement | `import_id`, `count` | `[{"key", "narration"}]` |
| `categorize_now` | Ask the model now, in chunks sent at the same time | `keys` | `{"answers": {key: category}, "agreement": {"rate", "checked"}, "failed": [key]}`; the host keeps each reason for the hint; a failed chunk's keys can be asked again once |
| `submit_batch` | Only when `BATCH` is set. Submit the keys as one batch; the job runner checks it and starts a fresh run when it ends | `keys` | `{"batch_id", "requests"}` |
| `batch_results` | The ended batch's answers, in a run started for it | `batch_id` | The same shape as `categorize_now` |
| `save_categories` | One write group over every answer the host holds for the run: labels the trusted ones and writes `llm` rules, and stores a hint (guess and reason) on the rest, which stay uncategorized. An answer is untrusted when its key is in `distrust`, or its category is null, unknown, or "Other" | `distrust`: keys | `{"undo_id", "labeled", "uncategorized"}` |

**Logic**

```
commit queues a categorize job for the import
rules label every row they match; the agent sees the rest
agent: check_payees; ask about them with the pending ones
host: agreement = share of check payees the model got right, returned to the agent and saved with the run
    a normal month: categorize_now
    a big backfill with BATCH set: submit_batch, and the run ends
        the job runner checks the batch; when it ends, a fresh run reads batch_results
agent checks a sample of answers against the narrations
save_categories: trusted answers → labels + llm rules; the rest → uncategorized, with a hint
payment apps (ONE97, PhonePe, Google Pay, CRED, Amazon Pay) are never matched by name
```
