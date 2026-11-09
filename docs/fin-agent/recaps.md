# Recaps

A recap tells the story of one day, week, or month of money, as a few short stories you step
through. Every figure is computed in code from ledger rows and opens to those rows; the sentences
are templates. The page, the agent's `recap` tool, and the ready notifications all read one engine,
`src/fin_agent/tools/recaps.py`, so they never disagree. Decided November 9, 2026 (plan.md,
item 23).

## Periods

| Kind | ID | Listed | Compared with |
| --- | --- | --- | --- |
| Day | `day-2026-11-08` | The last 7 days before today that have spending | The same weekday a week earlier |
| Week (Monday to Sunday) | `week-2026-11-02` | This week so far, then the last 4 complete weeks | The week before; a week so far uses the same days of last week |
| Month | `month-2026-10` | This month so far, then every complete month back to the first ledger row, at most 24 | The month before; a month so far uses the same days of last month |

- A period that ends before the ledger's first row is never listed.
- A week or month in progress keeps its ID and reads "so far" until it ends.
- **New** marks only the latest complete recap of each kind (yesterday, last week, last month)
  until it is opened. Opening a recap records it in `recap_views`; nothing else is stored. The
  figures are always computed fresh, so a later import or correction changes them.

## Stories

In this order. A story with nothing to say is left out, except the first.

| Key | Label | Figure | Line | Rows behind it |
| --- | --- | --- | --- | --- |
| `total` | You spent | Net spend (refunds offset purchases), from `summarize_spending` | Change against the compared period, or that nothing was recorded then | Every spending row in the period |
| `in_out` | Money in and out | What was left (in minus out) | In and out amounts; only when income was recorded | The income rows; the story also carries `in` and `out` |
| `where` | Where it went | The top category | Its share of the total, and its row count; bars for the top four plus everything else | That category's rows (each bar opens its own) |
| `change` | The change worth a look | The category whose spend moved the most, up or down | Both amounts and the compared period; only when both periods have spending | That category's rows in this period |
| `biggest` | The biggest single spend | Its amount | Payee and date | That one row |
| `frequent` | Your most frequent | The payee seen most often, at least twice | Times and total | That payee's rows |
| `recurring` | Repeat charges with no schedule | How many | Merchant, amount, and cadence of each, from the pattern detector as of the period's end, with a last charge inside the period | None; each offers "Track it" in chat |

A period with no spending at all has one story: ₹0 and "No spending recorded in this period."
Missing account coverage in the period is stated under the total, never hidden.

## API

| Route | Returns |
| --- | --- |
| `GET /api/recaps` | Every listed period: ID, kind, title, start, end, `complete`, `new`, and the spend figure; plus `unseen` (the count of new recaps) |
| `GET /api/recaps/{id}` | The period and its stories. Each story: `key`, `label`, `figure` (text and paise), `line`, optional `bars`, and the `rows` reference that opens it |
| `GET /api/recaps/{id}/rows?story=&key=` | The movements behind one figure, with their sum, which equals the figure |
| `POST /api/recaps/{id}/seen` | Records that the recap was opened |

## Notifications

A built-in `recaps` job runs daily at the notification time. It makes sure the latest complete
week and month each have an inbox item (`kind = recap`, deduplicated by period ID), but only for a
period with spending. A new month also sends a push; a week does not. The item opens the recap.

## The agent

`recap(period)` is a read tool. It takes a period ID or `yesterday`, `this_week`, `last_week`,
`this_month`, `last_month`, or `2026-10`, and returns the same stories, with every figure as a
money value the number check can trace. The agent uses it to tell a recap in its own words.
Statement merchant names come back marked as outside text, and the conversation is flagged.
