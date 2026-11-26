# Agent evals

`pydantic_evals` runs the real agent against the synthetic demo persona. Every trial gets its own
Postgres database cloned from a template, and every grade reads the outcome in that database or a
value computed by the tested engine. There is no model judge.

## Run it

The suite has no CLI command. Real runs call a model and spend money, so run one only on purpose,
through `evals.runner.run_eval(model_names=[...])`, which is the only code that turns
`models.ALLOW_MODEL_REQUESTS` on. `evals.runner.dry_run()` counts cases per category for free.

## What is in the suite

84 cases, all on the demo persona:

| Category | Cases | Graded on |
|---|---:|---|
| Answers and what-ifs | 32 | Expected values from the ledger and engine, plus the number check |
| Writes | 23 | Database state: the row, correction, preference, floor, or schedule |
| Honesty when data is missing | 15 | Says no data and writes nothing |
| Never moving money / passwords | 10 | No transfer or settlement, no secret stored or echoed |
| Long threads after a summary | 4 | A follow-up answer still carries the earlier decision |

Tags mark Hinglish (`hinglish`), Devanagari Hindi (`hindi`), and voice transcripts (`voice`).

## How a trial works

- `evals/lifecycle.py` builds a template from scratch, never from the self-hoster's ledger: an
  empty database, migrations, then the demo persona.
- Each trial clones that template with `CREATE DATABASE … TEMPLATE`; teardown drops it. A context
  variable points every connection in the trial at its own clone.
- A confirm card ends a turn in the app until the user answers it. A trial approves the cards the
  case asked for so the case can grade what the write did. Never-moving-money trials never
  approve a card.
- Graders run before teardown. A multi-message case grades the turn its last message started.

`pass^k` is computed per original case: a case passes only when all `k` trials pass. Failure and
number-flag transcripts go to `evals/results/` (gitignored).
