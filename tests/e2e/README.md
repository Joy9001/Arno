# The end-to-end driver

`tests/e2e/driver.py` runs real user journeys against a real API process on a scratch database.
The terminal `arno chat` runs the agent inside the caller's process, so it cannot do what a
browser does: upload a statement, answer a confirm card, review and commit an import, or show the
cards and tool calls a turn produced. This driver speaks the same HTTP the web app speaks, to a
server it starts itself.

It is for later end-to-end agents and for a developer checking a change by hand. It is not part of
`pytest`; the model-free part is `tests/test_e2e_driver.py`.

## Start and stop

```bash
export DATABASE_URL=postgresql://app:<pw>@127.0.0.1:5433/arno_e2e_test
export AGENT_RO_PASSWORD=...  BACKUP_PASSWORD=...   # what the app needs to bootstrap
uv run python -m tests.e2e.driver up --port 8123 --seed demo
uv run python -m tests.e2e.driver down
```

`up` refuses any database whose name does not end in `_test`. It runs the migrations, seeds the
synthetic persona when asked, starts `uv run arno serve` on `127.0.0.1:<port>` with jobs on
and the passkey origin at `http://localhost:<port>`, waits for `/api/health`, then enrolls a
software passkey through the real setup token and WebAuthn ceremony. `--empty` drops the schema
first instead of seeding.

The session, base URL, current thread, and the last turn's confirm cards live under `$E2E_STATE`
(default `<tempdir>/arno-e2e-<database>`), as `state.json` plus `server.log`. So separate
shell invocations continue the same conversation.

**`--seed demo` re-seeds and truncates the ledger**, so it is for a first start only. To restart a
server on the same database — a new port, or a different `CHAT_BASE_URL` read from the shell — run
`up --keep`: it stops the server the state file records, leaves every row alone, and reuses the
session. Without `--seed` or `--keep`, `up` only migrates and keeps the ledger too; `--keep` also
stops the previous process so the port is free.

```bash
uv run python -m tests.e2e.driver up --port 8125 --keep
```

## Commands

| Command | What it does |
| --- | --- |
| `up --port N [--seed demo] [--empty] [--keep]` | migrate, start the server, enroll a passkey |
| `down` | stop the server it started |
| `chat "text" [--file PATH]... [--thread ID] [--new] [--json]` | one user turn, as the composer sends it |
| `approve TOOL_CALL_ID... yes\|no [--thread ID]` | answer the last turn's confirm cards; a two-card turn needs both ids (the server refuses a partial answer; the web never sends one) |
| `undo UNDO_ID` | undo a card's write |
| `imports [--limit N]` | list imports |
| `import ID` | one import's review state, lines, the closing adjustment it would book, and the command that clears each blocker |
| `password ID PASSWORD` | the import card's password path, never through chat |
| `opening ID RUPEES` | the opening the import card asks for, as printed |
| `confirm-transfer ID LINE_ID` | confirm a line is a transfer between your own accounts |
| `resolve ID LINE_ID RESOLUTION [--movement-id M] [--credit-kind K] [--category-id C]` | any other line resolution |
| `commit ID [--confirm-adjustment]` / `reject ID` / `undo-import ID` | finish an import; the flag confirms the closing adjustment the review states |
| `tasks [--limit N]` | imports with their parser task ids |
| `task ID` | a parser task: state, report, transcript, imports |
| `wait-task ID [--timeout S]` | poll a parser task until it finishes |
| `answer TASK_ID ACCOUNT` | answer the parser's "which account is this?" question |
| `state [--json]` | the ledger summary, straight from SQL |
| `invariants` | `tests.invariants` against the same database |

`chat` prints the thread id, the assistant's text, every tool call with its arguments and result,
every data part (cards, undo ids, import ids), every confirm card with its tool call id, any
number-check note, and the exact next command. `--json` prints the same as one object.

## The journeys it was proven on

Run against the seeded persona with a real model. Output is trimmed; the money is verbatim.

### A spending question

```
$ uv run python -m tests.e2e.driver chat "How much did I spend on food and dining last month?"
thread: e2e_148359a7deb1477f

assistant:
  ₹1,850 on Food & Dining in October 2026 — 4 entries, all Swiggy, all from HDFC Savings.

  The ₹800 team dinner on 2026-10-23 counts at your own share of ₹400; the other ₹400 was
  Ravi's and Amit's, still owed to you. Coverage for the month is complete, no gaps.

tool calls:
  - run_code({"code": "food = await summarize_spending(group_by=\"none\", period=\"last_month\", ...)"})
      -> {"period": {"from": "2026-10-01", "to": "2026-10-31"}, "total": {"paise": 185000, ...}}
```

`driver state` says the same thing from SQL:

```
  2026-10  Food & Dining: ₹1,850.00
```

### An expense, and its undo

```
$ uv run python -m tests.e2e.driver chat "I spent 450 rupees on a Swiggy lunch today from HDFC Savings. Log it."
tool calls:
  - record_movement({"movement": {"kind": "expense", "amount_rupees": 450, "payee": "Swiggy", ...}})
      -> {"status": "recorded", "undo_id": "und_01M714N02YF7E9VPVYW4WZH0R3", ...}
data parts:
  - data-card: {"card": "movement", "title": "Logged", "amount": {"paise": 45000, ...},
                "undo_id": "und_01M714N02YF7E9VPVYW4WZH0R3", ...}
next:
  undo: driver undo und_01M714N02YF7E9VPVYW4WZH0R3
```

```
$ uv run python -m tests.e2e.driver state | grep -E 'HDFC|2026-11  Food'
  HDFC Savings (bank, liquid)  ₹775,030.00
  2026-11  Food & Dining: ₹770.00
$ uv run python -m tests.e2e.driver undo und_01M714N02YF7E9VPVYW4WZH0R3
{"status": "undone", "undo_id": "und_01M714N02YF7E9VPVYW4WZH0R3",
 "undo_undo_id": "und_01M714NSSXFDA81CC6S5EMVEQ6", "summary": "Reverted 3 changes."}
$ uv run python -m tests.e2e.driver state | grep -E 'HDFC|2026-11  Food'
  HDFC Savings (bank, liquid)  ₹775,480.00
  2026-11  Food & Dining: ₹320.00
```

The account is back to its balance and the category back to its pre-write total, so the reversal
nets to zero on both paths.

### A confirm card, and its approval

Once the thread has read statement text, a lasting write asks first. Nothing is written until the
card is answered.

```
$ uv run python -m tests.e2e.driver chat "From now on, never show me rent reminders again. Remember that."
tool calls:
  - preference({"action": "remember", "text": "Never show rent reminders — don't surface the rent
                schedule (Ramesh, landlord) in reminders or upcoming lists.", "kind": "preference"})
confirm cards:
  - preference call_1ccf8077c0e949419d21dc9a
next:
  approve: driver approve call_1ccf8077c0e949419d21dc9a yes
```

`SELECT count(*) FROM preferences` was `0` at that point. Then:

```
$ uv run python -m tests.e2e.driver approve call_1ccf8077c0e949419d21dc9a yes
assistant:
  Saved — rent reminders stay off from here on. The ₹28,000 rent schedule itself stays in place,
  so it still counts in your projections and cash flow; I just won't surface it as a reminder.
tool calls:
  - preference(...) -> {"status": "remembered", "undo_id": "und_01M7154874F3T8JXRA9EP610VY", ...}
data parts:
  - data-card: {"card": "preference", "title": "Remembered", "undo_id": "und_01M7154874F3T8JXRA9EP610VY"}
```

The preference exists exactly once, with its undo id. `approve ... no` sends the same part with
`"approved": false`, and the write never lands.

### A statement attached in chat, through to review

```bash
uv run python -c "from tests.import_fixtures import sbi_csv; open('sbi-april-2026.csv','wb').write(sbi_csv())"
uv run python -m tests.e2e.driver chat "Please import the statement I just attached." \
    --file sbi-april-2026.csv
```

```
tool calls:
  - delegate_task({"agent_name": "parser", "task": "Import the statement files from this turn.",
                   "background": true})
      -> Task e562d2eb12f7452a8ad52a0113b41d62 is running in the background. ...
next:
  a subagent started: driver tasks
```

```
$ uv run python -m tests.e2e.driver wait-task e562d2eb12f7452a8ad52a0113b41d62 --timeout 180
task e562d2eb12f7452a8ad52a0113b41d62: finished ok
  import imp_01M714RYC5E9RRTK1NNZTF8PX5  review  sbi-april-2026.csv
report: {"status": "ready", "imports": [{"import_id": "imp_01M714RYC5E9RRTK1NNZTF8PX5",
         "status": "in_review", "rows": 3, "debits": 2, "credits": 1,
         "reason": "SBI savings statement (text export); opening 2,45,678.90 printed and matched,
         running balance chain and period verified"}]}

$ uv run python -m tests.e2e.driver import imp_01M714RYC5E9RRTK1NNZTF8PX5
status: review
account: SBI Salary
period: 2026-04-01 to 2026-04-30
check: VERIFIED
opening: ₹2,45,678.90 (printed, evidenced)
lines: 3 {"new": 3}
next:
  ready: driver commit imp_01M714RYC5E9RRTK1NNZTF8PX5
```

`driver task <id>` prints the parser's whole transcript (every `run_code` call with its result and
the final report), which is what a browser shows on the run card.

If the statement has no account the parser can infer, it asks; `driver task <id>` prints the
question and `driver answer <task> "SBI Salary"` answers it with an account id or exact name.

## The truth, read straight from SQL

`state` prints account balances (a card's statement balance says `unknown` until a statement sets
it), this and last month's spending by category, who owes what, active goals, active schedules, and
open imports. It does not call the app's own read paths, so comparing it with what the bot said is
a real cross-check. Spending leaves out reversed movements and the reversal rows themselves by
calling the ledger's own `_effectively_reversed_ids`, so the rule lives in one place: each
movement's sign is one minus its reversal children's, and a pair is cancelled when the signs cancel
(an undo of an undo restores the original).

`invariants` runs `tests.invariants` against the same database: `OK: 673 checks` on the persona
after the journeys above.

## How it works

- The chat body is what `web/src/chat/runtime.ts` sends: `{id, trigger, messageId, messages}`, with
  the last message's parts carrying `text` and `data-statement` refs. An attachment is uploaded to
  `POST /api/attachments` with `purpose=statement` first, and the part carries only the id.
- A confirm card is answered with an assistant message whose part has `state:
  "approval-responded"` and `approval: {id, approved}`, which is what the AI SDK sends. The driver
  remembers the card's tool name and input from the turn that offered it, so `approve` needs only
  the tool call id. `tests/test_e2e_driver.py` validates both shapes against
  `pydantic_ai.ui.vercel_ai.request_types.RequestData`, the model the server itself parses with.
- The stream is read as the AI SDK UI message stream: `text-delta`, `tool-input-available`,
  `tool-output-available`/`tool-output-error`, `tool-approval-request`, `data-*`, `error`.

## The smoke test

`tests/test_e2e_driver.py` runs in normal `pytest` with models blocked. It drives a real
enrollment and upload against an in-process app, validates both request bodies against the server's
own schema, and checks that a canned stream becomes tool calls, cards, confirm cards, and a
number-check note.

```bash
uv run pytest tests/test_e2e_driver.py -q -n 0
```

## Limits

- `tests/test_e2e_driver.py` empties every table in `DATABASE_URL`, like every test in this suite.
  Running it against the database you are driving leaves an empty ledger: `up --seed demo` again.
- Parser task files under `ARNO_TASKS_DIR` are never cleared, so a `--seed` run of a database
  that a previous run used keeps that run's task records. Only an id you ask for reaches them.
- `state` lists an active schedule with its rule, not its due dates: expanding an RRULE is the
  app's job, not SQL's.
- There is no browser, so nothing here checks rendering, keyboard, or the WebAuthn UI. It checks
  the wire, which is where the money paths are.
