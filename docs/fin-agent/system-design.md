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
| 2. Control and autonomy | For each path, does code or the model pick the next step, and where does a human step in? | [Below](#step-2-control-and-autonomy) | Agreed October 28, 2026 |
| 3. Agent core | One agent or several; models for each role; the loop, stopping rules, budgets | [Below](#step-3-agent-core); [`harness.md`](harness.md) | Agreed October 28, 2026 |
| 4. Ground truth | The ledger every answer must agree with: what a record means, how spending is counted | [`design.md`](design.md) | Not started; questions parked; next |
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
| A question you scheduled | You, by scheduling it | Reads and answers into the inbox; never writes |
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

For each path: who picks the next move—the model, code, or you? Agreed October 28, 2026.

### Agent or workflow, for each path

![The agent loop runs only for you](diagrams/sd-agent-vs-workflow.png)

*Figure: your chat and the questions you schedule drive the one agent loop; built-in jobs and
imports run as code with at most one LLM call each, and still run when the LLM is down
([note](diagrams/sd-agent-vs-workflow.md)).*

- **Built-in jobs are workflows.** Warnings, reminders, and the briefing run as fixed code: the
  engine computes, rules decide whether to warn, and at most one LLM call writes the prose. They
  work when the LLM is down and can't go wrong silently. What we give up: they can't improvise.
- **Your own scheduled questions run the agent, read-only.** "Every Sunday, tell me what I spent on
  food and whether it's up" runs the agent on a schedule, the way Claude Cowork's scheduled tasks
  do. No write tools—nobody is there to see an undo card or answer a confirm card—so the answer
  lands in the inbox, and each run has its own spend limit.
- **The merchant categorizer** writes its answer through as a category rule (source `llm`), so
  each new merchant costs one LLM call, once. User rules win, and a correction replaces an `llm`
  rule ([`design.md`](design.md#categorization)).

### Who decides what inside a chat turn

![Who decides what in one chat turn](diagrams/sd-chat-turn-control.png)

*Figure: the model picks the tool and phrases the answer; code checks the rules and writes; you're
asked first only for a lasting write in a flagged conversation
([note](diagrams/sd-chat-turn-control.md)).*

The following table is the control map. The model owns judgment and language; code owns every rule
that must hold every time.

| The model decides | Code decides | You decide |
|---|---|---|
| Which tools to call, and what to compute (Code Mode) | The category of a known merchant—your rules win | Starting a turn, or scheduling one |
| How to phrase the answer, and which tradeoffs to mention | The yes, yes-but, or no verdict (the engine) | Confirming a lasting write in a flagged conversation |
| Whether to remember a lasting fact | Whether a write needs a confirm card ([write tiers](design.md#untrusted-text-and-write-tiers)) | Approving an import's review screen |
| When to ask you a clarifying question | Whether a write looks like a duplicate ([step 8](#step-8-reliability)) | Accepting a proposal |
| | How long a turn may run and what it may spend ([step 3](#step-3-agent-core)) | Undoing anything |

### Ask or assume

The agent assumes when a sensible default exists and a wrong guess is a visible field you can fix:
"paid 450 on swiggy" uses the account you usually pay Swiggy from, shown on the undo card. It asks
when there's no default (the amount is missing), or when a wrong guess would create money owed by
other people: "dinner with Ravi and Amit, 1,200" gets "split equally?" rather than an assumed
split.

### Setup is agent-led; code tracks what's missing

There's no scripted wizard. Code works out from the data which parts of your picture are still
empty—no income schedule, no floor, an account with no balance—and hands that list to the agent,
which leads the conversation; you can skip anything. The same list feeds the data-quality note
that verdicts must state.

### The agent never starts a conversation

Warnings, reminders, and the briefing are workflows. Tapping one opens a new chat seeded with that
notification, so you can ask "what can I do about it?" and the agent picks up from there. Control
passes from a workflow to the agent only through you.

### Photos log at once, with undo

Sharing a screenshot already shows your intent, so the vision model's extraction becomes one
transaction at once. The undo card shows the screenshot beside the extracted amount, merchant, and
date. A vision model can misread a digit, but the misread is visible and undoable. Statement
imports keep their review screen, because they write many rows at once. This replaces the
photo review step that `plan.md` §2 had before.

### When the agent can't answer, it hands control back

It names what's missing and what would fix it—"September's SBI statement isn't uploaded; upload it
and I can answer"—and doesn't guess. At the advice boundary, it gives the facts and tradeoffs and
leaves the choice to you.

---

## Step 3: Agent core

What runs the thinking: how many agents, which models, how the loop runs, and when it must stop.
Agreed October 28, 2026.

![The agent loop and its guards](diagrams/sd-agent-loop.png)

*Figure: one agent loops between the model and its tools; compaction and tool output limits keep
the context small, a backup model covers errors, and circuit breakers stop a runaway turn
([note](diagrams/sd-agent-loop.md)).*

### One agent, with Code Mode and the engine

- **One agent.** The work is sequential and write-heavy—where the research found multi-agent
  setups lose. None of the research's three reasons for another agent applies: there's no context
  to isolate (the data is small), nothing to parallelize (turns are sequential), and independent
  review is a deterministic check ([step 9](#step-9-evals-and-observability)).
- **Code Mode gives it computing power, and the engine gives verdicts.** Scripts orchestrate:
  standard money maths (EMI and amortization, prepayment, compounding) comes from tested engine
  functions, and free-form arithmetic covers the rest, with the script shown
  ([`design.md`](design.md#code-mode)). The yes, yes-but, or no verdict comes only from the engine.
- **Answer quality over cost.** A frontier model, metered by Spend Limits, with no routing to
  cheaper models. Prompt caching is the main cost lever.

### Models

The following table lists the three model roles:

| Role | Used for |
|---|---|
| `CHAT` | The agent loop, your scheduled questions, and the one-shot calls: the merchant categorizer, briefing prose, statement column mapping |
| `VISION` | Photos and scanned statements |
| `STT` | Voice notes |

- **One-shot calls reuse the chat model.** One less setting, and it matches quality over cost. The
  trigger for a separate `UTILITY` role: cost starts to matter.
- **Exact model versions are pinned.** Dated model IDs, never floating aliases like "latest"—the
  research shows an alias can change behavior silently. Any model change re-runs the eval
  scorecard before it counts as done.
- **A backup chat model.** Each role may name an optional backup endpoint, and PydanticAI's
  `FallbackModel` switches to it when the main one fails—typically cloud first, a local model
  second. Replies the backup produced carry a badge, so a weaker answer is never silent. Tools
  aren't restricted on the backup: its eval scorecard already says what it handles, and
  `FallbackModel` switches for each request inside a run. `plan.md` §3 records the switch.
- **Reasoning effort.** Where the model supports thinking or effort levels, the provider's
  adaptive or default level applies, so it thinks harder on a what-if than on "paid 450". Thinking
  counts toward the spend ceilings.

### The loop: context is managed, runaways are stopped

A turn ends when the model gives its final answer. Two different problems need two different
guards:

| Problem | Guard |
|---|---|
| The context grows too large: a long conversation, or a wide `run_query` result | Compaction keeps the history inside the window, and tool output limits spill or trim a big return. Both are on from day one; how they're configured belongs to [step 6](#step-6-context-and-memory) |
| The loop runs away: it repeats steps or never stops—the failure behind Notion's 17-day runaway agent thread, which a harness bug caused | Circuit breakers: spend and request ceilings set well above what any real turn needs, so they never shape a normal answer |

Compaction and tool output limits don't stop loops or cap spend—they only shrink what's sent—so the
breakers stay. The following table lists their starting values, tuned from real traces so that a
normal turn never reaches them:

| Ceiling | Chat turn | Scheduled question |
|---|---|---|
| Model requests | 50 | 20 |
| Spend | $5 | $1 |
| Daily spend, across everything | $20 (shared) | |

- **Hitting a ceiling hands control back.** The agent says what it found so far and which ceiling
  stopped it, and the trace records it. A ceiling never looks like a finished answer.
- **Errors: retry, then the backup, then say so.** A failed model call is retried twice, then the
  backup model takes over if one is configured. If both fail, the agent says plainly that it
  couldn't answer.
- **One running turn per conversation.** A message sent while a reply is still running waits.
  Different conversations, and scheduled questions, run independently.

Parked to later steps: what goes in the instructions and how the prompt is ordered
([step 6](#step-6-context-and-memory)); what happens when the client disconnects
([step 8](#step-8-reliability)).

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
the read tools behind Code Mode—predates the walk; this step re-checks it. Parked here:

- **Charts the agent makes.** Claude's dashboards put a query behind each chart; Fin-Agent's
  equivalent: a Code Mode script returns a declarative chart spec built from data it computed, a
  fixed UI component renders it, and the script is the chart's citation. No model-written HTML or
  JavaScript—it would bypass the content security policy and let the model type numbers. Fixed
  cards (the projection, a spending breakdown) stay for the core views. Rendering belongs to
  [step 10](#step-10-interfaces-and-operations).

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
- Rendering the charts the agent makes ([step 5](#step-5-tools)): one chart component that draws a
  declarative spec, with the citation and "as of" time on every chart.

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
