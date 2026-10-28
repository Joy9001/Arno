# System design

This file tracks Fin-Agent's agentic system design walk: ten steps, taken in order, each agreed
before the next one starts. It records where each step's decisions live, which questions wait for
which step, and what outside reviews contributed.

The decisions themselves live in [`plan.md`](plan.md) and its satellites, and this file points to
them. If the two disagree, the plan wins and this file is stale.

![Agentic system design in ten steps](diagrams/sd-framework.png)

*Figure: the ten steps in four groups—decide, the agent's world, earn trust, run it—with evals
sending the walk back to the requirements ([note](diagrams/sd-framework.md)).*

The framework is a synthesis, not a published standard: the research found no settled framework
for agentic design. It draws on the interview template in
[`genai-system-architecture.md`](../research/agentic-system-design/genai-system-architecture.md)
§9, the System Design Handbook's agentic steps, and how the
[harness survey](../research/agent-harness-engineering/) is organized. The walk began on October
25, 2026, with a generic backend framework; on October 28 it switched to this one, and every
earlier decision was re-filed under the step it belongs to.

---

## The walk

The following table lists the steps, in order:

| Step | The question it answers | Recorded in | Status |
|---|---|---|---|
| 1. Requirements and success | What must it do, how is "it works" measured, and how much autonomy and write risk is involved? | [`plan.md`](plan.md) §1-2; [below](#step-1-requirements-and-success) | Agreed October 28, 2026 |
| 2. Control and autonomy | For each path, does code or the model pick the next step, and where does a human step in? | [Below](#step-2-control-and-autonomy) | Partly decided; next |
| 3. Agent core | One agent or several; models for each role; the loop, stopping rules, budgets | [Below](#step-3-agent-core); [`harness.md`](harness.md) | Partly decided |
| 4. Ground truth | The ledger every answer must agree with: what a record means, how spending is counted | [`design.md`](design.md) | Not started; questions parked |
| 5. Tools | What the agent can read and write, through which contracts | [`design.md`](design.md#tool-surface-11-tools) | Not started; the tool surface predates the walk |
| 6. Context and memory | What the model sees each turn: prompt, context, history, memory, cache order | [`harness.md`](harness.md), [`design.md`](design.md) | Not started; questions parked |
| 7. Safety and trust | Trust boundaries, prompt injection, data leaving the box, privacy, the advice boundary | [Below](#step-7-safety-and-trust); [`design.md`](design.md#untrusted-text-and-write-tiers) | Partly decided |
| 8. Reliability | What fails, and how it recovers | [Below](#step-8-reliability) | Partly decided |
| 9. Evals and observability | How it's measured, traced, and scored for each model | [`harness.md`](harness.md) | Not started; questions parked |
| 10. Interfaces and operations | API and streaming contract, auth, deployment, cost and latency | [`platform.md`](platform.md) | Not started; an API draft is parked |

Decisions made before or outside their step—rule 1 (the model decides what to compute; code
computes it), Code Mode, the write tiers, and the old trade-offs step—are filed under their step
and re-checked when that step is walked.

---

## Step 1: Requirements and success

The requirements are recorded in [`plan.md`](plan.md) §1-2: the product, functional scope,
non-functional targets, and constraints. This section adds how success is measured and how much
autonomy each kind of action gets.

### Success measures

The following targets say what "it works" means. Step 9 refines how each is measured. `pass^3`
means a task passes in all three trials, not just once.

| What | Measure | Target |
|---|---|---|
| Answers and writes | The agent eval suite (50+ tasks from real use), `pass^3` on the configured model | 90% or more—the research's launch gates sit at 80-90% |
| Honesty | Tasks where the data is missing: it says "no data" and never invents | `pass^3` of 95% or more |
| Injection | Planted payee notes: no lasting write lands without its confirm card | 100%, a hard gate |
| Numbers | Every number in a reply traces back to a tool return | 100%; step 9 decides how it's enforced |
| Forecast honesty | In the backtest, the real balance 30 days later lands inside the P10-P90 band | About 8 months in 10 |
| Forecast accuracy | Median 30-day error in the backtest | Published; a target once the first backtest shows what's realistic |
| Affordability engine | The 25 labeled HackerRank "Buy or Wait" samples | Published; a target after the first run |
| Categorizer | Share of its answers you don't correct | 90% or more |
| Daily use | Four weeks without falling back to a spreadsheet or mental math | Already in "Done is" (`plan.md` §1) |

### Autonomy for each kind of action

The following table sets who starts each kind of action and what the agent may do on its own:

| Action | Who starts it | What the agent may do on its own |
|---|---|---|
| Answering a question, modeling a what-if | You | Everything: it reads, computes, and explains |
| Logging, editing, correcting | You | Writes at once; you can undo |
| Remembering a lasting fact | The agent, when it notices one | Saves at once with a "Remembered" card; asks first only in a conversation that has read outside text |
| Setup changes: the floor, schedules, goals | You | Writes at once; asks first in a conversation that has read outside text |
| Statement imports | You, by uploading | Parses and matches; nothing is written until you approve the review screen |
| Reminders, warnings, the weekly briefing | The schedule | Informs only |
| A detected subscription | The schedule | Proposes; writes only when you accept |
| Moving money, investment advice | — | Never |

The agent never starts a write on its own, with one exception: saving a memory, which you can
always see and undo.

### Scale assumptions

The following table lists the scale assumptions that step 10 re-checks:

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

---

## Step 2: Control and autonomy

Decided so far; the step itself hasn't been walked.

### Agent or workflow, for each path

![Only chat runs an agent loop](diagrams/sd-agent-vs-workflow.png)

*Figure: your chat drives the only agent loop; scheduled jobs and imports run as code with at most
one LLM call each, and still run when the LLM is down ([note](diagrams/sd-agent-vs-workflow.md)).*

The merchant categorizer writes its answer through as a category rule (source `llm`), so each new
merchant costs one LLM call, once. User rules win, and a correction replaces an `llm` rule
([`design.md`](design.md#categorization)). What we give up: background jobs can't improvise. In
return, they're predictable, cheap, and testable.

### Autonomy for writes

- Everyday writes land instantly, with an undo card.
- A confirm card appears only for lasting or wide writes in a conversation that has read outside
  text ([write tiers](design.md#untrusted-text-and-write-tiers)).
- Background jobs propose; they never write silently. Recurring-charge detection, for example,
  suggests a schedule and waits for you.

---

## Step 3: Agent core

Decided so far; the step itself hasn't been walked.

- **One agent.** The work is sequential and write-heavy—where the research found multi-agent
  setups lose.
- **Code Mode gives it computing power, and the engine gives verdicts.** Scripts orchestrate:
  standard money maths (EMI and amortization, prepayment, compounding) comes from tested engine
  functions, and free-form arithmetic covers the rest, with the script shown
  ([`design.md`](design.md#code-mode)). The yes, yes-but, or no verdict comes only from the engine.
- **Answer quality over cost.** A frontier model, metered by Spend Limits, with no routing to
  cheaper models. Prompt caching is the main cost lever.
- **A backup chat model.** Each role may name an optional backup endpoint, and PydanticAI's
  `FallbackModel` switches to it when the main one fails—typically cloud first, a local model
  second. Replies the backup produced carry a badge, so a weaker answer is never silent. Tools
  aren't restricted on the backup: its eval scorecard already says what it handles, and
  `FallbackModel` switches for each request inside a run. `plan.md` §3 records the switch.

---

## Step 4: Ground truth

Not started. Parked here:

- How spending is counted: the classification rules, and whether `run_query` reads views that
  apply them.
- One record for each movement: single rows with transfer pairs and claims (the current design),
  or double-entry legs. How a loan EMI's interest and principal are held for prepayment what-ifs.
- The keys behind duplicate protection: the retry key (message and tool call), and the
  suspected-duplicate window and fields ([step 8](#step-8-reliability)).
- ID types.

---

## Step 5: Tools

Not started. The tool surface in [`design.md`](design.md#tool-surface-11-tools)—11 tools, with
the read tools behind Code Mode—predates the walk; this step re-checks it.

---

## Step 6: Context and memory

Not started. Decided so far: prompt caching orders content from unchanging to changing—
instructions, tools, and schema first; the date, balances, and the turn last. Parked here:

- Loading the financial context automatically at the start of a conversation.
- The server owns the conversation history: the client sends only the new message, so it can't
  inject fake tool results or drop the outside-text marks. PydanticAI's adapter accepts server
  history as the trusted part (checked October 27, 2026).

---

## Step 7: Safety and trust

Decided so far; the step itself hasn't been walked.

- **Outside text and write tiers.** Payee names and UPI notes reach the agent marked as data, and
  lasting or wide writes need a tap in a conversation that has read them
  ([`design.md`](design.md#untrusted-text-and-write-tiers)).
- **Exfiltration.** A content security policy allows images only from the app's own origin, and
  links in replies render as plain text ([`platform.md`](platform.md#security)).

### Identifiers at import, not a scrubbing layer

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

Parked here:

- Confirm answers are bound on the server: each pending confirmation is recorded with its
  conversation, tool call ID, and arguments, and an answer counts once, only for a pending call.
  PydanticAI's adapter trusts confirm answers from the request by default (checked October 27,
  2026), so this binding is Fin-Agent's to add.
- Photo and voice enter through their own endpoints, so the server—never the client—inserts the
  extracted fields and sets the outside-text flag.

---

## Step 8: Reliability

Decided so far; the step itself hasn't been walked.

### Idempotency, not a workflow engine

A chat turn takes seconds, so there's no workflow engine. Instead, a write can't land twice:

![A chat write can't land twice](diagrams/sd-duplicate-writes.png)

*Figure: a retried tool call returns the row already written; a near-identical entry from minutes
ago makes the agent ask in words; statement reconciliation is a third, separate layer
([note](diagrams/sd-duplicate-writes.md)).*

- Scheduled jobs carry a key for the job and its period, so a restart can't send a briefing twice.
- Job state lives in Postgres, so a run missed while the machine slept fires once on wake
  ([`platform.md`](platform.md#notifications-and-scheduler)).
- No database transaction stays open across an LLM call; each write and its audit entry commit in
  one short transaction.
- Heavy statement parsing runs off the chat request path.

Parked here:

- A turn finishes even if the client disconnects. By default PydanticAI's adapter cancels the run
  on a disconnect (checked October 27, 2026), so the run has to be detached from the HTTP stream.
  A separate stop endpoint handles the Stop button.
- The scheduler's job store, and how parsing moves off the chat path.

---

## Step 9: Evals and observability

Not started. Parked here:

- An output check that every number in a reply matches a tool return, and how it fits streaming:
  stream progress first and check the finished draft, or have the model write value placeholders
  that the UI fills from tool returns.
- The success measures from step 1 become this step's targets.

---

## Step 10: Interfaces and operations

Not started. Decided so far, from the old trade-offs step:

- **Consistency over availability:** one Postgres database; when the machine is down, so is the
  app, and backups keep the data.
- **Simplicity over scale:** one process, one database, the scheduler inside the API process; no
  queue, cache, or vector database.
- **Depth over speed:** replies stream, so the first output arrives in about 2 s, and a what-if can
  take about 15 s.

Parked here, from an API draft of October 27, 2026 that was never agreed:

- An endpoint map: chat with a stop endpoint; conversations; ledger reads and writes; imports that
  return `202` and are polled; photo and voice endpoints; inbox; instance and auth.
- The undo button calls REST, not a chat turn, so undo works without an LLM.
- Conventions: integer paise; `null` means unknown, never `0`; balances carry `as_of`; RFC 9457
  errors; cursor pagination.
- The CLI uses a named, revocable token created on the host; admin commands never go over HTTP; a
  fresh passkey check only for export and passkey changes.
- No `/v1` in URLs while the web app and CLI ship in the same image. Tool names and argument shapes
  are part of the contract, because stored conversations replay old tool calls.
- Rate limits, CSP, and other headers; MCP exposure stays parked.

---

## Outside reviews

### Requirements, October 27, 2026

An outside agent reviewed the step 1 draft. The following table records what was taken from it
and why:

| Point | Verdict |
|---|---|
| Savings and investment outflows (SIPs, FDs) aren't spending | Taken into `plan.md` §2 |
| Refunds offset the purchase; reimbursements aren't income | Taken into `plan.md` §2. Claims already model reimbursements; the requirement is now explicit |
| A payee or UPI ID correction applies to past and future rows | Taken into `plan.md` §2; stays instant with undo ([decided](#retroactive-payee-corrections)) |
| Useful from a spoken snapshot on day one | Taken into `plan.md` §2. The setup conversation already lands balances; now it's a requirement |
| Higher transaction volume, and context size as a scale dimension | Taken into the [scale assumptions](#scale-assumptions) |
| Separate latency targets for lookups and what-ifs | Taken into `plan.md` §2 as targets that step 10 re-checks |
| Account Aggregator is closed to self-hosted apps | Taken into `plan.md` §2 constraints, checked against Sahamati's FIU page |
| Never moving money is a hard invariant | Taken into `plan.md` §2 |
| Coverage tracking, so "no data" isn't ₹0 | Already covered: data coverage for each account in every read tool ([`design.md`](design.md)) |
| Balance checksum on imports | Already covered: the invariant in [`import.md`](import.md). It catches most bad extractions, not all—swapped rows or wrong dates can still pass |
| Three reconciliation states | Already covered: certain, ambiguous, and none, plus "logged but not on the statement" ([`import.md`](import.md)) |
| Double-entry legs for each movement; EMI interest and principal as separate legs | Parked to [step 4](#step-4-ground-truth) |
| A PII scrubber before cloud LLM calls | Decided in [step 7](#identifiers-at-import-not-a-scrubbing-layer): identifiers are handled at import instead |
| Typed query plans or views for model-written SQL | Parked to [step 4](#step-4-ground-truth) |
| Writes only from direct user turns | Rejected: the write tiers replace it and keep everyday edits instant |
| Merge matching within ±5 minutes | Rejected: statements carry dates, not times. Matching uses the UPI reference, or the amount within ±3 days |
| Candidate merges surfaced in chat or the weekly briefing | Rejected: they stay on the import review screen, with no new chat surface |
| Extraction is "provably trustworthy" once balances reconcile | Rejected as stated; see the checksum row |
| A `upi://pay` link to settle up with a friend | Not now: outside the current scope. Noted in case settling up comes back |

### Trade-offs, October 27, 2026

An outside agent reviewed the old trade-offs step. The following table records what was taken
from it, and the step each point now sits under:

| Point | Verdict |
|---|---|
| Never hold a database transaction open across an LLM call | Taken; [step 8](#step-8-reliability) |
| Scheduler state survives restarts; a missed run fires on wake | Taken; [step 8](#step-8-reliability) and [`platform.md`](platform.md#notifications-and-scheduler) |
| Statement parsing runs off the chat request path | Taken as a principle; [step 8](#step-8-reliability) |
| Prompt order for caching: stable content first, changing content last | Taken; [step 6](#step-6-context-and-memory) |
| Code Mode orchestrates tested engine functions instead of writing money formulas from scratch | Taken; [step 3](#step-3-agent-core) and [`design.md`](design.md#code-mode) |
| Handle identifiers at import instead of a scrubbing layer | Taken, with a keyed one-way hash for identifiers used only for matching; [step 7](#identifiers-at-import-not-a-scrubbing-layer) |
| Hash phone numbers in UPI narrations | Adapted: UPI IDs stay readable because payee corrections name them; hashing applies to identifiers used only for matching |
| The categorizer writes through to a lookup table | Taken: answers are saved as `llm`-sourced category rules; [step 2](#agent-or-workflow-for-each-path) |
| Separate retry protection from suspected-duplicate protection | Taken; a suspected duplicate gets a question in words, not a confirm card; [step 8](#step-8-reliability) |
| Badge replies from the backup model | Taken; [step 3](#step-3-agent-core) |
| Restrict the backup model to simple capture | Rejected: the scorecard shows what the backup handles, and restricting tools mid-run needs a separate health-check path |
| Commit a turn's changes together at the end of the turn | Rejected: each write commits on its own, so its undo card shows at once |
| Stream progress first, or have the model write value placeholders the UI fills from tool returns | Parked to [step 9](#step-9-evals-and-observability) with the number check |
