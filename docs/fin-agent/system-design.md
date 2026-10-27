# System design

This file tracks Fin-Agent's system design walk: eight steps, taken in order, each agreed before
the next one starts. It records where each step's decisions live, which questions wait for which
step, and what outside reviews contributed.

The decisions themselves live in [`plan.md`](plan.md) and its satellites, and this file points to
them. If the two disagree, the plan wins and this file is stale.

---

## The walk

The following table lists the steps, in order:

| Step | Covers | Recorded in | Status |
|---|---|---|---|
| 1. Requirements | Functional, non-functional, scale, constraints | [`plan.md`](plan.md) §1-2; scale [below](#scale-assumptions) | Agreed, October 27, 2026 |
| 2. Trade-offs | The tensions the requirements create, and which side wins | [Below](#step-2-trade-offs) | Recorded, October 27, 2026 |
| 3. API design | Endpoints, the chat stream, auth, versioning | [`platform.md`](platform.md) | Next |
| 4. Data model | Entities, schema, database | [`design.md`](design.md) | Not started |
| 5. High-level design | Layers and data flow | [`plan.md`](plan.md) §4 | Not started |
| 6. Deep dives | Agent loop, import, engine, memory, security | [`harness.md`](harness.md), [`design.md`](design.md), [`import.md`](import.md) | Not started |
| 7. Production readiness | Monitoring, security, backups, CI | [`platform.md`](platform.md) | Not started |
| 8. Capacity planning | Traffic, storage, memory, tokens; re-checks step 1 | This file | Not started |

Some decisions were committed before the walk began: rule 1 (the model decides what to compute;
code computes it), Code Mode, and the write tiers. Each is re-checked when its step arrives, not
treated as final.

---

## Step 1: Requirements

The requirements are recorded in [`plan.md`](plan.md) §1-2: the product, functional scope,
non-functional targets, and constraints.

### Scale assumptions

The following table lists the scale assumptions that step 8 re-checks:

| Dimension | Assumption |
|---|---|
| Users | One for each instance |
| Transactions | 100-500 a month—active UPI users run high (quick commerce, autos, chai, splits). About 60,000 rows over ten years, well under 50 MB |
| Chat | 10-50 turns a day; one conversation at a time |
| Statements | 3-5 files a month, plus a 12-month backfill at setup |
| Context per turn | Summaries and row IDs, never hundreds of raw rows |

The consequence: throughput doesn't shape the design. Correctness and trust do.

### Retroactive payee corrections

"ramesh@okaxis is my landlord" rewrites that payee's past rows, which would make it a _wide_
write under the [write tiers](design.md#untrusted-text-and-write-tiers). It happens most in a
conversation that has just read imported rows, so a confirm tap there would be everyday friction.

**Decided:** it stays instant. The undo card names the rule and how many past rows changed, and
one undo reverts the rule and every row.

### External review, October 27, 2026

An outside agent reviewed the step 1 draft. The following table records what was taken from it
and why:

| Point | Verdict |
|---|---|
| Savings and investment outflows (SIPs, FDs) aren't spending | Taken into `plan.md` §2 |
| Refunds offset the purchase; reimbursements aren't income | Taken into `plan.md` §2. Claims already model reimbursements; the requirement is now explicit |
| A payee or UPI ID correction applies to past and future rows | Taken into `plan.md` §2; stays instant with undo ([decided](#retroactive-payee-corrections)) |
| Useful from a spoken snapshot on day one | Taken into `plan.md` §2. The setup conversation already lands balances; now it's a requirement |
| Higher transaction volume, and context size as a scale dimension | Taken into the [scale assumptions](#scale-assumptions) |
| Separate latency targets for lookups and what-ifs | Taken into `plan.md` §2 as targets that step 8 re-checks |
| Account Aggregator is closed to self-hosted apps | Taken into `plan.md` §2 constraints, checked against Sahamati's FIU page |
| Never moving money is a hard invariant | Taken into `plan.md` §2 |
| Coverage tracking, so "no data" isn't ₹0 | Already covered: data coverage for each account in every read tool ([`design.md`](design.md)) |
| Balance checksum on imports | Already covered: the invariant in [`import.md`](import.md). It catches most bad extractions, not all—swapped rows or wrong dates can still pass |
| Three reconciliation states | Already covered: certain, ambiguous, and none, plus "logged but not on the statement" ([`import.md`](import.md)) |
| Double-entry legs for each movement; EMI interest and principal as separate legs | Parked to [step 4](#step-4-data-model) |
| A PII scrubber before cloud LLM calls | Decided in [step 2](#b-identifiers-at-import-not-a-scrubbing-layer): identifiers are handled at import instead |
| Typed query plans or views for model-written SQL | Parked to [step 4](#step-4-data-model) |
| Writes only from direct user turns | Rejected: the write tiers replace it and keep everyday edits instant |
| Merge matching within ±5 minutes | Rejected: statements carry dates, not times. Matching uses the UPI reference, or the amount within ±3 days |
| Candidate merges surfaced in chat or the weekly briefing | Rejected: they stay on the import review screen, with no new chat surface |
| Extraction is "provably trustworthy" once balances reconcile | Rejected as stated; see the checksum row |
| A `upi://pay` link to settle up with a friend | Not now: outside the current scope. Noted in case settling up comes back |

---

## Step 2: Trade-offs

Each trade-off names the side that wins, the step 1 requirement that decides it, and what Fin-Agent
gives up.

### The framework's four

The following table applies the framework's four trade-offs to Fin-Agent:

| Trade-off | Winner | Decided by | What we give up |
|---|---|---|---|
| Consistency vs availability | Consistency. One Postgres database: each write and its audit entry commit in one short transaction, and no transaction stays open across an LLM call | Correctness; durability | When the machine is down, so is the app; backups keep the data |
| Simplicity vs scalability | Simplicity: one process, one database, the scheduler inside the API process; no queue, cache, or vector database. Two guards: job state lives in Postgres, so a run missed while the machine slept fires once on wake, and heavy statement parsing runs off the chat request path | One user; about 60,000 rows a decade; one developer | Multiple users stays parked |
| Cost vs performance | Answer quality: a frontier model, metered by Spend Limits. Prompt caching is the main cost lever, so unchanging content goes first (instructions, tools, schema) and changing content last (the date, balances, the turn). No routing to cheaper models | Intelligence; LLM cost isn't a constraint | A higher cost for each question |
| Latency vs throughput | Throughput doesn't matter (one conversation at a time). Depth wins over speed: replies stream, so the first output arrives in about 2 s, and a what-if can take about 15 s | The latency targets | How the number check fits a streamed reply waits for step 6 |

### Re-checked, still holding

- **One agent.** The work is sequential and write-heavy—where the research found multi-agent
  setups lose. Code Mode gives the one agent its computing power.
- **Verdicts come from the engine.** Code Mode scripts orchestrate: standard money maths (EMI and
  amortization, prepayment, compounding) comes from tested engine functions, and free-form
  arithmetic covers the rest, with the script shown ([`design.md`](design.md#code-mode)).
- **Autonomy versus control:** everyday writes stay instant with undo; a confirm card appears only
  for lasting or wide changes.
- **Self-hosted versus hosted:** privacy wins over an easier install.
- **Freshness versus nagging:** the agent states how old a number is and never nags for a balance.

### A: A backup chat model

Panels are read-only, so an LLM outage would block logging. Each role may name an optional backup
endpoint, and PydanticAI's `FallbackModel` switches to it when the main one fails—typically cloud
first, a local model second. Replies the backup produced carry a badge, so a weaker answer is
never silent. Tools aren't restricted on the backup: its eval scorecard already says what it
handles, and `FallbackModel` switches for each request inside a run, so restricting tools would
need a separate health-check path. `plan.md` §3 records the switch.

### B: Identifiers at import, not a scrubbing layer

A reversible scrubber would hide identifiers, but the cloud model still sees amounts, merchants,
and salary; hiding those takes a local model, which is already an option. So the following rules
apply at import, in plain code:

| Identifier | Treatment |
|---|---|
| Your own account and card numbers | Last four digits only |
| UPI IDs | Kept as they are: payee corrections name them ("ramesh@okaxis") |
| Other identifiers used only for matching, such as another person's account number in a NEFT narration | A keyed one-way hash, so matching across statements still works without storing the number |
| Anything the product doesn't use | Dropped |

The L3 import spike records which identifiers each bank's statements actually carry.

### C: Agent or workflow, for each path

![Only chat runs an agent loop](diagrams/sd-agent-vs-workflow.png)

*Figure: your chat drives the only agent loop; scheduled jobs and imports run as code with at most
one LLM call each, and still run when the LLM is down ([note](diagrams/sd-agent-vs-workflow.md)).*

The merchant categorizer writes its answer through as a category rule (source `llm`), so each new
merchant costs one LLM call, once. User rules win, and a correction replaces an `llm` rule
([`design.md`](design.md#categorization)). What we give up: background jobs can't improvise. In
return, they're predictable, cheap, and testable.

### D: Idempotency, not a workflow engine

A chat turn takes seconds, so there's no workflow engine. Instead, a write can't land twice:

![A chat write can't land twice](diagrams/sd-duplicate-writes.png)

*Figure: a retried tool call returns the row already written; a near-identical entry from minutes
ago makes the agent ask in words; statement reconciliation is a third, separate layer
([note](diagrams/sd-duplicate-writes.md)).*

Scheduled jobs carry a key for the job and its period (for example, the briefing for one week), so a
restart can't send one twice. The exact keys and the duplicate window wait for step 4.

### External review, October 27, 2026

An outside agent reviewed the step 2 draft. The following table records what was taken from it
and why:

| Point | Verdict |
|---|---|
| Never hold a database transaction open across an LLM call | Taken into the consistency row |
| Scheduler state survives restarts; a missed run fires on wake | Taken into the simplicity row and [`platform.md`](platform.md#notifications-and-scheduler); mechanism in step 6 |
| Statement parsing runs off the chat request path | Taken as a principle; mechanism in step 6 |
| Prompt order for caching: stable content first, changing content last | Taken into the cost row |
| Code Mode orchestrates tested engine functions instead of writing money formulas from scratch | Taken: [`design.md`](design.md#code-mode); which functions, in step 6 |
| Lock decision B in | Taken, with a keyed one-way hash for identifiers used only for matching |
| Hash phone numbers in UPI narrations | Adapted: UPI IDs stay readable because payee corrections name them; hashing applies to identifiers used only for matching |
| The categorizer writes through to a lookup table | Taken: answers are saved as `llm`-sourced category rules |
| Separate retry protection from suspected-duplicate protection | Taken as decision D. A suspected duplicate gets a question in words, not a confirm card |
| Badge replies from the backup model | Taken into decision A |
| Restrict the backup model to simple capture | Rejected: the scorecard shows what the backup handles, and restricting tools mid-run needs a separate health-check path |
| Commit a turn's changes together at the end of the turn | Rejected: each write commits on its own, so its undo card shows at once |
| Stream progress first, or have the model write value placeholders the UI fills from tool returns | Parked to [step 6](#step-6-deep-dives) with the number check |

---

## Parked by step

The following questions wait for their step.

### Step 4: Data model

- How spending is counted: the classification rules, and whether `run_query` reads views that
  apply them.
- One record for each movement: single rows with transfer pairs and claims (the current design),
  or double-entry legs. How a loan EMI's interest and principal are held for prepayment what-ifs.
- Duplicate protection: the retry key (message and tool call), and the suspected-duplicate window
  and fields.

### Step 6: Deep dives

- An output check that every number in a reply matches a tool return, and how it fits streaming:
  stream progress first and check the finished draft, or have the model write value placeholders
  that the UI fills from tool returns.
- Loading the financial context automatically at the start of a conversation.
- Which tested engine functions Code Mode scripts call: EMI and amortization, prepayment,
  compounding.
- The scheduler's job store, and running statement parsing off the chat request path.
