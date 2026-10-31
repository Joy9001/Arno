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
| 4. Ground truth | The ledger every answer must agree with: what a record means, how spending is counted | [Below](#step-4-ground-truth); [`design.md`](design.md#the-ledger) | Agreed October 30, 2026 |
| 5. Tools | What the agent can read and write, through which contracts | [Below](#step-5-tools); [`tools.md`](tools.md) | Agreed October 31, 2026 |
| 6. Context and memory | What the model sees each turn: prompt, context, history, memory, cache order | [Below](#step-6-context-and-memory); [`harness.md`](harness.md#context-and-memory) | Agreed October 31, 2026 |
| 7. Safety and trust | Trust boundaries, prompt injection, data leaving the box, privacy, the advice boundary | [Below](#step-7-safety-and-trust); [`design.md`](design.md#untrusted-text-and-write-tiers) | Partly decided; next |
| 8. Reliability | What fails, and how it recovers | [Below](#step-8-reliability) | Partly decided |
| 9. Evals and observability | How it's measured, traced, and scored for each model | [`harness.md`](harness.md) | Not started; questions parked |
| 10. Interfaces and operations | API and streaming contract, auth, deployment, cost and latency | [Below](#step-10-interfaces-and-operations); [`platform.md`](platform.md) | Not started; an API draft and a deployment check are parked |

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

![The agent loop runs for you and for new layouts](diagrams/sd-agent-vs-workflow.png)

*Figure: your chat and the questions you schedule drive the agent loop, and a new statement layout
starts a parse run with no write tools. Built-in jobs and known layouts run as code with at most
one LLM call each, and still run when the LLM is down ([note](diagrams/sd-agent-vs-workflow.md)).*

- **Built-in jobs are workflows.** Warnings, reminders, and the briefing run as fixed code: the
  engine computes, rules decide whether to warn, and at most one LLM call writes the prose. They
  work when the LLM is down and can't go wrong silently. What we give up: they can't improvise.
- **Your own scheduled questions run the agent, read-only.** "Every Sunday, tell me what I spent on
  food and whether it's up" runs the agent on a schedule, the way Claude Cowork's scheduled tasks
  do. No write tools—nobody is there to see an undo card or answer a confirm card—so the answer
  lands in the inbox, and each run has its own spend limit.
- **Imports are code, until a layout is new.** A known layout reruns its saved parser with no LLM
  call. A new layout starts a parse run: the same agent, with no write tools, and it waits if the
  LLM is down. This replaced "at most one LLM call" on October 31, 2026
  ([step 5](#imports-the-agent-writes-the-parser)).
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
  review is a deterministic check ([step 9](#step-9-evals-and-observability)). A parse run is this
  agent with fewer tools ([step 5](#imports-the-agent-writes-the-parser)).
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
| `CHAT` | The agent loop, your scheduled questions, parse runs, and the one-shot calls: the merchant categorizer and briefing prose |
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
| The context grows too large: a long conversation, or a wide `run_query` result | Compaction keeps the history inside the window, and tool output limits trim a big `run_code` return. Both are on from day one and configured in [step 6](#step-6-context-and-memory) |
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

What goes in the instructions and how the prompt is ordered are in
[step 6](#step-6-context-and-memory). Parked: what happens when the client disconnects
([step 8](#step-8-reliability)).

---

## Step 4: Ground truth

The ledger every answer must agree with. Agreed October 30, 2026. The full data model is in
[`design.md`](design.md#the-ledger).

![The ledger, built on pgledger](diagrams/sd-ledger-model.png)

*Figure: one movement is one pgledger call of balanced transfers; labels hold the editable category
and payee, and captures link the evidence ([note](diagrams/sd-ledger-model.md)).*

### Double-entry, on pgledger

- **Double-entry.** Every movement goes from one place to another, and spending is one rule: money
  that went into the Spending account. The card bill, the SIP, a friend's repayment, and loan
  principal never touch it, so they can't be miscounted. This replaces the old rulebook of
  `transfer_group`, `claims`, and `kind`.
- **The core is vendored, not built.** Against the plan's constraints—Postgres holds all state,
  plain SQL, a permissive license, one process—only [pgledger](https://github.com/pgr0ss/pgledger)
  fits. Beancount has the most mature accounting, but it's GPL and keeps state in text files.
  python-accounting and django-hordak need an ORM. Formance and Blnk are separate services.
- **Categories are editable labels, not ledger accounts.** pgledger only appends, so a category
  stored in the ledger would turn every recategorization into a reversal. As a label, a payee
  correction never touches the ledger. Only undo and amount fixes append reversals, which keeps
  the history honest.

### The fit check, October 30, 2026

Read from pgledger's source, license, and README at commit `fee1aa0`.

| Need | pgledger | Verdict |
|---|---|---|
| Permissive license | MIT, plus BSD-3-Clause for its ID helpers | Fits |
| Postgres only, plain SQL, one process | SQL functions and views, no extensions; commits in the app's own transaction | Fits; pin Postgres 18 |
| Every movement balances | Each transfer has one `from` and one `to`, written atomically | Fits |
| Splits and EMIs | One call writes several transfers, all or nothing | Fits; one call for each movement |
| The retry key | A built-in idempotency key for the whole call | Fits |
| Statement dates vs logging time | `event_at`, separate from `created_at`, but fixed once written | Adapted: `movements.date` is authoritative and editable |
| The agent's read-only role | Functions run with the caller's rights | Fits |
| A balance on a past date | Stored running balances follow write order, and a backfill arrives out of order | Adapted: `v_balances` sums entries by each movement's date |
| Edits and undo | Append-only, and retries compare `metadata` | Adapted: reversals, and labels in our own table |
| Account details | Name, currency, and balance flags only | Adapted: our `accounts` table links one-to-one |
| IDs | Prefixed text ULIDs | Adopted throughout |

Risks: one maintainer and no tagged releases. Vendoring a pinned commit of a 375-line file limits
both.

### Credit

pgledger (MIT, © 2025 Paul Gross) and `scoville/pgsql-ulid` (BSD-3-Clause, © 2021 Scoville) are
copied unmodified, with both LICENSE files; `THIRD_PARTY_NOTICES.md` ships in the Docker image; and
the README credits pgledger under "Built on," with no wording that implies endorsement
([`design.md`](design.md#the-ledger)).

### Other decisions

- **Captures link to movements.** Every chat message, screenshot, and statement line is kept as
  evidence for one movement, and unmatched ones are visible.
- **Payees are an entity,** with a display name, a UPI ID, a hashed match key, and a default
  category. A person who owes you, or whom you owe, also gets a ledger account.
- **Loans:** the ledger balance is the outstanding principal. The engine's amortization splits each
  EMI, and a loan statement corrects any drift. That's what a prepayment what-if needs.
- **Coverage comes from imports,** so a missing month is visible and answers say "no data."
- **Duplicates:** the retry key is the message ID plus the tool call ID. A suspected duplicate is
  the same account, amount, and payee, logged by chat within 10 minutes. Statement lines dedupe by
  a content hash.
- **The agent's SQL** reads views for the common questions (`v_spending`, `v_cashflow`,
  `v_balances`, `v_owed`) that the typed tools share, so there's one definition of each number.
  The raw tables stay readable, because with double-entry they hide no double-counting trap.

---

## Step 5: Tools

What the agent can read and write, through which contracts. Agreed October 31, 2026. Each tool's
description, input, output, and logic are in [`tools.md`](tools.md).

![Scripts read and compute; every write is a direct call](diagrams/sd-tool-surface.png)

*Figure: the model sees seven tools. A script reaches the eight read tools through `run_code`, and
every write is a direct call with its own card ([note](diagrams/sd-tool-surface.md)).*

### Decisions on the surface

- **One word: movement.** `query_movements`, `record_movement`, and `correct_movement` replace the
  `transaction` names, matching the ledger.
- **Every tool is a normal PydanticAI tool.** Code Mode changes how the model calls the eight read
  tools, not what they are. Writes stay direct calls, because an approval-gated tool can't pause
  inside a script, and a script could otherwise write many rows with no card for each.
- **Two tested money functions.** `amortize` and `compound` cover loans and compounding, so standard
  maths isn't rewritten in each script. The rate in `compound` is an assumption the reply must state.
- **The unit is in the field name.** Read tools take `*_paise`, because scripts compute in paise.
  Write tools take `*_rupees`, as you said it, so the model never multiplies by 100.
- **`category` or `category_guess`.** `category` means you named it, and it wins. `category_guess` is
  used only when no rule exists, then saved as an `llm` rule. This keeps "your rules win" and saves a
  separate categorizer call on chat.
- **`scope` on a correction:** `this` (no rule), `future` (a rule), or `all` (a rule and past rows).
- **Charts.** A script calls `show_chart` with a small typed spec (line, bar, stacked bar, donut) built
  from data it computed. A fixed component draws it, and the script is its citation. There's no
  model-written HTML, and no Vega-Lite. Fixed cards stay for the core views, and rendering belongs to
  [step 10](#step-10-interfaces-and-operations).
- **Passwords never reach the model.** A statement password goes in a field on the import card, over
  REST. It stays in memory and is never stored.
- **Preferences: off, or deleted.** Forgetting sets `active = false`, so a preference stays listed in
  Settings and can be turned back on. Delete is separate, in Settings only, with a confirm, and it
  also blanks the text in the audit rows. The agent has no delete, so everything it writes stays
  undoable. The audit log keeps powering undo for every write.
- **Marks on outside text can clear.** Otherwise a schedule name copied from a statement would flag
  every conversation that reads the financial context. A marked name you confirm on a card loses its
  mark, and a new name in a flagged conversation stays unmarked if it appears in your own message
  ([`design.md`](design.md#untrusted-text-and-write-tiers)).
- **Not taken:** a dry-run mode, because confirm cards and undo cover it, and an MCP server, which
  stays parked. Tool Search's trigger is now confusion seen in evals, not a tool count.

### Imports: the agent writes the parser

![The agent learns each statement layout once](diagrams/sd-import-pipeline.png)

*Figure: a known layout reruns its saved parser with no LLM call. A new layout starts a parse run,
checks gate both paths, and the parser is saved only after you commit
([note](diagrams/sd-import-pipeline.md)).*

- **No parser for each bank.** A small set of readers turns any file into cells or positioned words,
  and the agent writes a parser for each new layout. A typed layout spec would need new schema and
  code for every oddity, such as wrapped narrations, which is per-format logic under another name.
- **A parse run is the same agent with different tools.** It has `run_code` and a skill, and no write
  or ledger tools, the way scheduled questions have no write tools. To write a parser the model must
  read sample rows. In the chat run, that would flag the conversation and put a crafted payee note
  beside the write tools. In its own run, the worst case is wrong rows, and the chat stays free. The
  cost is that its questions come back through the import card. It uses the chat turn's ceilings.
- **Monty is the sandbox.** It's already in the stack, runs in the API process, and has no filesystem,
  environment, or network. Local sandboxes remain the fallback ([parked](parked.md#a-separate-code-sandbox)).
- **The model submits a script, never rows.** The server reruns the script from its text in a fresh
  sandbox. Saved after you commit, it parses later statements with no LLM call.
- **A vision model reads pages with no text.** OCR waits for the L3 spike
  ([parked](parked.md#ocr-for-scanned-statements)).

The following table records the other options that were considered:

| Option | Verdict | Why |
|---|---|---|
| A library for each bank (`xfina`, `bankstatementparser`) | Optional | The L3 spike keeps one only if it beats a written parser on real statements |
| `FileUnderstanding`, the harness capability | No | It describes files a model can't read, as free text. A frontier model gets the PDF unchanged, in the run that holds the write tools, and it leaves files alone under `FallbackModel` |
| The model reads the whole file every time | Last layer only | Every import would send the whole statement to the provider and stop when the LLM is down. It stays for pages with no text |
| Docker, BoxLite, Smol, Bubblewrap sandboxes | Fallback | Docker needs the host's Docker socket from inside Compose, and its docs say not to mount it. BoxLite and Smol need `/dev/kvm`. Bubblewrap's docs say it isn't a boundary against a hostile agent |
| E2B, boxd | No | Cloud-hosted, so statements would reach another third party |

### The Monty test, October 31, 2026

A subagent ran parsers in Monty against synthetic files for every document type. The full results
are in the [report](../research/monty-document-parsing/README.md).

| Documents | Result |
|---|---|
| PDFs with text, 9 layouts | All rows right, verified |
| `.xlsx`, `.xls`, `.ods`, CSV, TSV, TXT, `.docx`, HTML, RTF, OFX, MT940, CAMT, JSON | All rows right. Some need host-side conversion, and statements with no printed opening need one from the ledger |
| 182 pages, 9,996 rows | 5.6 s. Fails Code Mode's 100-call cap at one call per page, so `doc_words_range` batches them |
| Clean scan, moderate phone photo | Works through RapidOCR, after 1-2 repairs |
| Harsh phone photo | 0 rows; needs a vision model |
| Salary slip, loan letter, mutual fund statement | Correctly identified as not bank statements |
| `.doc`, `.xlsb` statements, vision models, real Indian bank files, running on Linux | Not tested |

What it changed:

- **The balance check isn't enough.** A day/month swap came out verified with 17 of 18 dates wrong,
  and a new column came out verified with 0 of 22 narrations right. The checks now add the period,
  taken from outside the parser, a column-header fingerprint, a row count, and the ledger's opening
  balance. The review screen stays the last guard, and a parser is saved only after you commit.
- **Cards are Fin-Agent's job.** Metadata from a tool called inside a script doesn't reach the
  `VercelAIAdapter` stream, so Fin-Agent emits those cards from the `run_code` return.
- **The host layer is about 400 lines,** plus a versioned 100-line helper block, written once.
- **Scripts and replay share one form.** Scripts use `await` and keyword arguments, host functions
  return JSON-native types, and replay runs in `AsyncMonty` with `os_policy` and a sleep limit set.

**The real test is still to come.** The agent wrote the fixtures, the parsers, and the helper block,
so these results lean toward success, and it found no real Indian bank PDF or XLS. A fresh model
writing a parser for real statements is the gate, at the [L3 spike](import.md#the-spike-milestone-21-in-the-plan).

---

## Step 6: Context and memory

What the model sees each turn, and what it remembers. Agreed October 31, 2026. The capability
configuration and the first instructions are in [`harness.md`](harness.md#context-and-memory).

![What the model sees each turn, from unchanging to changing](diagrams/sd-context-stack.png)

*Figure: six blocks, ordered so that what rarely changes comes first and stays cached. The reminder
and your turn come after the cache ([note](diagrams/sd-context-stack.md)).*

### Threads are yours

You see every thread, as in ChatGPT or Claude. You open an old one or start a new one whenever you
want, and no time-based rule splits them. Nothing limits how much you chat except the
[spend ceilings](#the-loop-context-is-managed-runaways-are-stopped).

- **How a message finds its thread.** A thread is one `conversations` row, and each message stores
  its thread's ID. The app sends that ID with your message, or none for a new chat, and the server
  creates the row.
- **The model sees only the open thread.** If you mention something from another one, it searches
  `messages` through `run_query`.
- **The cost of a long thread.** Its outside-text flag lasts as long as the thread, so a flagged
  thread asks for a tap on every lasting write until you start a new chat. Long threads also get
  compacted more often. Both are accepted: the tap applies only to lasting writes, and compaction
  has the guards below.

What the app opens to and how threads get their titles belong to
[step 10](#step-10-interfaces-and-operations).

### What goes in each request

- **The financial snapshot loads itself.** When a thread starts, the server runs
  `get_financial_context` and adds the result as a pinned part (`pin()`), which compaction keeps.
  Preferences are the agent's only memory, so a model that forgot to load them would have none. The
  [harness survey](../research/agent-harness-engineering/context-engineering.md) also calls this
  hybrid—some data up front, explore the rest—optimal for financial work. It isn't in the
  instructions, because they sit before the history, so a changed balance would break the cache for
  the whole thread. It isn't a `SystemPromptPart`, because PydanticAI warns against building one from
  content you didn't write. Scheduled questions get a snapshot; parse runs don't.
- **The flag follows what the model read.** The snapshot goes through `mark()` like any read, so a
  new thread starts flagged only if a name in its snapshot still carries a mark
  ([write tiers](design.md#untrusted-text-and-write-tiers)).
- **A fresh snapshot when you come back.** Before a turn, if `prompt_cache_outlook` says the cache
  has expired and something has changed since `conversations.snapshot_at`, the server swaps in a new
  snapshot. The history has to be cached again anyway, so the swap costs nothing extra. The rest of
  the thread stays as it was.
- **A short reminder each turn.** `SystemReminders` adds a line such as "Today is 2 November. Since
  your snapshot: 3 writes; re-read before stating a balance." It counts writes from any source in
  `audit_log`: other threads, imports, and Settings. It sits after the cache, and only the newest
  reaches the model. While the cache is live or its state is unknown, this is how the model learns
  the snapshot is old.

### Two records, both on the server

![One thread keeps two records](diagrams/sd-thread-records.png)

*Figure: the client sends a thread's ID and a new message. When the run ends, the model's history and
your transcript are saved separately ([note](diagrams/sd-thread-records.md)).*

- **`messages` is what you saw.** It's filled from `new_messages()`, never edited, and used for
  search, export, and citations.
- **`conversations.model_history` is what the model gets.** It's replaced from `all_messages()` in
  `on_complete`, stored as JSON through `ModelMessagesTypeAdapter`. The two must be separate because
  compaction's edits persist in the stored history.
- **The adapter's own option owns the history.** `manage_message_history="server"` uses only the
  client's new turn, so a client can't inject history or fake tool results, and a retried message
  isn't added twice.
- **The thread's ID is the `conversation_id`,** so traces and summary calls are recorded under it.
- **Not `conversation=`.** PydanticAI recommends it for saved sessions, but it carries usage across
  turns, which would turn [step 3](#step-3-agent-core)'s per-turn ceilings into per-thread ones. The
  adapter doesn't accept it either.

### Output limits apply to `run_code` only

Applied globally, `ToolOutputLimits` also hits tools called inside a script: a nested 2,000-row list
reached the script as a 4,000-character string, so the script would compute on the wrong data. With
`tool_filter=["run_code"]`, the nested list arrives whole, and only what returns to the model is cut,
at 10,000 characters.

Nothing spills to a file. The data is already in Postgres, script variables last for the turn so the
model can slice them, and the next turn queries again. Spilling would add an eighth tool
(`read_tool_result`), a workspace, and statement data in temp files.

### Compaction

`TieredCompaction` starts when the history passes one target, and stops as soon as it fits:

| Order | Strategy | Does |
|---|---|---|
| 1 | `ClearToolResults` | Clears old tool returns and keeps the newest 5. Write returns stay |
| 2 | `SummarizingCompaction` | Summarizes older messages. Keeps the newest fifth of the target whole, plus up to 20 older turns of yours word for word |
| 3 | `SlidingWindowCompaction` | Runs only if the summary call fails, through `FallbackCompaction`, so the turn still answers. Keeps the same newest fifth |

- **The target is half the main model's window, capped at 100,000 tokens.** It's a starting value,
  tuned from traces. It follows the main model, because a `FallbackModel` reports its smallest
  window, so `target_fraction` would make a small local backup compact frontier threads far too
  early. What happens when a backup can't fit a long history belongs to [step 8](#step-8-reliability).
- **`keep_tokens` is required with `keep_user_messages`.** With `keep_messages` alone, the newest
  exchanges vanished: they were neither sent nor summarized.
- **The summary keeps your intent, decisions, and open questions.** It drops amounts and statement
  text, so a number can't outlive the tool return behind it, and a payee note can't lose its mark.
  The flag lives on the conversation row, so compaction can't clear it.
- **`receipts=True` tells the model what's gone,** so it re-verifies rather than pretending to
  remember.
- **IDs don't survive every tier.** Write returns survive clearing, but not a summary or the fallback.
  The summary keeps IDs where it can, but nothing relies on that: `undo` reads `audit_log`, and the
  model queries for an ID before using an old one.

### Caching

`Caching()` and `WarnOnCacheBusts()` are on from day one. The warning only observes, and one right
after a compaction is expected. Chats come in bursts, so a 5-minute cache may often expire; L2 traces
decide whether a 1-hour cache is worth it.

### Memory: no memory library

| What | Where it lives | How it reaches the model | Written by |
|---|---|---|---|
| This thread | `conversations.model_history` and the snapshot | Every request | Each turn |
| Facts about you | `preferences` | Always, in the snapshot | The agent, unprompted, with a card |
| Past conversations | `messages` | When needed, by keyword search through `run_query`, which flags the thread | Automatically |
| How to behave | Instructions, category rules, the parse skill, saved parsers | Instructions always; the rest in code or parse runs | Releases, your corrections, and committed imports |
| Money | The ledger | Through tools; never memorized | Write tools |

Mem0, Graphiti, Letta, Cognee, Memobase, and Supermemory were considered and rejected
([research](../research/agentic-system-design/retrieval-and-memory.md)):

- Full context beat every memory system on accuracy in Mem0's own published table, and vendor scores
  for the same product differ by more than 40 points.
- Each LLM merge can silently corrupt a correct memory, and poisoned memory persists.
- A hidden store would be a second source of truth beside the ledger.

When facts conflict, the agent forgets the old one and remembers the new one, and both show as cards.
Semantic search stays [parked](parked.md#hidden-memory-extraction-and-semantic-search-mem0-style) with
its trigger.

### The instructions are tuned during the build

The [first version](harness.md#the-instructions-first-version) is a starting point, tuned by trial and
error. The fixed rules each get an eval case; wording, order, and length are free to change.
Instructions aren't stored in history, so a change reaches existing threads on the next turn, with no
migration.

### Not taken

The following table records the other options that were considered:

| Option | Why not |
|---|---|
| Starting a new thread after 6 idle hours | A hidden rule on top of threads you already control. The swap and reminder keep the snapshot fresh without it |
| Dynamic instructions for balances | They sit before the history, so each change would break the cache for the whole thread |
| Reloading the whole context every turn | Uncached on every request, for data that rarely changes between turns |
| `Spill` for big outputs | Adds a tool, a workspace, and statement data in temp files, when the data is already in Postgres |
| Provider-native compaction | Opaque, and tied to one provider while a backup model can take over |
| `RelevanceCompaction`, `CompactOnRequest`, `LLMReminder` | Each adds a model call or a model decision to a problem the tiers already handle |
| Compacting while the cache is expired (`prompt_cache_outlook`, `compact_now`) | Compaction is rare enough that the saving is tiny. Revisit if traces show otherwise |
| `ClampOversizedMessages` | A model's output cap should already bound a single response; this isn't tested. Add it if a local model hits the problem |
| Structured note-taking, sub-agents | Turns are short, and the ledger and `preferences` already hold what lasts |

### The tests, October 31, 2026

Every test ran with `FunctionModel`, with no real LLM, on PydanticAI 2.64.0 and `pydantic-ai-harness`
0.64.0. The docs don't cover how these features interact.

| Test | Result |
|---|---|
| `ToolOutputLimits` applied globally | A nested list of 2,000 rows reached the script as a 4,000-character string |
| The same, with `tool_filter=["run_code"]` | The list arrived whole; the return to the model was cut to 10,000 characters |
| Script variables | Last between snippets in one run, not into the next turn |
| The reminder | Lands last in the request. The last turn's stays in stored history, but isn't sent again |
| `conversation=` with a per-turn limit | The limit stopped the third turn. With `message_history=`, usage restarted each turn |
| `pin()` | Survived JSON storage, the adapter, and all three strategies |
| Swapping the snapshot | The model saw only the new one; pins didn't pile up; earlier chat stayed; the new snapshot survived compaction |
| `manage_message_history="server"` | A forged history and a fake tool result never reached the model; a retried message wasn't added twice |
| `new_messages()` when compaction fires | Held only the new question and answer |
| Clearing is enough | No summary call |
| A long chat | Clearing wasn't enough, so a summary ran; the snapshot and newest message stayed |
| `keep_user_messages=True` with `keep_messages` alone | **A trap:** the newest 3 of your turns and the newest 4 replies were neither sent nor summarized |
| The final configuration, with `keep_tokens` and tool calls | Every turn was either sent or summarized, and tool calls stayed paired with their returns |
| The summary call fails | The sliding window ran, and the turn still answered |
| Write IDs | Survived clearing; gone after a summary or the fallback |
| Typing past an open confirm card | The write didn't run, the call closed as `interrupted`, and the model saw the new message |
| Changed instructions | Applied to a stored thread on its next turn |
| Two threads | Neither saw the other's messages |

**Not tested:** real token counts (an estimate of 8,000-10,000 at the start of a thread, measured at
L2), summary quality (evals, [step 9](#step-9-evals-and-observability)), and `dispatch_request` itself,
because the tests drove the adapter's `run_stream`. The docs say that moving a history between
providers is handled, with thinking sent as text and signed data filtered, but no real provider has
tried it.

---

## Step 7: Safety and trust

Decided so far; the step itself hasn't been walked.

- **Outside text and write tiers.** Payee names and UPI notes reach the agent marked as data, and
  lasting or wide writes need a tap in a conversation that has read them
  ([`design.md`](design.md#untrusted-text-and-write-tiers)).
- **Exfiltration.** A content security policy allows images only from the app's own origin, and
  links in replies render as plain text ([`platform.md`](platform.md#security)).
- **Documents.** Statements are parsed in a run with no write or ledger tools
  ([step 5](#imports-the-agent-writes-the-parser)), and photos by a tool-less call into a typed
  schema.

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
  2026), so this binding is Fin-Agent's to add. Client tool results are already dropped unless they
  answer a pending call ([step 6](#two-records-both-on-the-server)).
- Typing past an open confirm card closes the pending write as `interrupted`, and the write doesn't
  run (tested in [step 6](#the-tests-october-31-2026)). Should the server send a clearer denial, and
  how does the old card show that it's closed?
- Photo and voice enter through their own endpoints, so the server—never the client—inserts the
  extracted fields and sets the outside-text flag.
- Limiting where the agent can connect: which hosts the API container may reach beyond the LLM
  endpoints.

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
- What gets saved when a run fails or is stopped. `on_complete` saves both records, and the adapter
  also has `on_cancel`.
- A backup model whose window can't fit a long history. Compaction's target follows the main model
  ([step 6](#compaction)).
- The scheduler's job store, and how parsing moves off the chat path. A candidate for both, and for
  runs that survive a crash or deploy: DBOS, a library that needs only Postgres and has a native
  PydanticAI integration, so no extra server.

---

## Step 9: Evals and observability

Not started. Parked here:

- An output check that every number in a reply matches a tool return, and how it fits streaming:
  stream progress first and check the finished draft, or have the model write value placeholders
  that the UI fills from tool returns.
- The success measures from step 1 become this step's targets.
- Eval cases for the fixed rules in the [instructions](harness.md#the-instructions-first-version),
  summary quality, and real token counts ([step 6](#the-tests-october-31-2026)).

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
- The thread list ([step 6](#threads-are-yours)). What the app opens to: the default is your last
  thread, with New chat one tap away, so logging chai six times a day doesn't create six threads.
  Thread titles: the default is your first message, trimmed.
- **Deployment, checked October 31, 2026: Docker Compose stays, on its merits.** Its original reason
  was only that traditional backends deploy this way, so the options were explored. Most sources are
  vendor blogs, with no head-to-head comparison, and Podman Quadlet wasn't evaluated. What sets an
  agent apart isn't its packaging: durable runs, code isolation, spend, and traces are handled in
  steps 8, 5, 3, and 9. The following table records the options:

  | Option | Verdict | Why |
  |---|---|---|
  | Docker Compose | Yes | One command on Linux, Docker Desktop, or a VPS; a pinned Postgres; the agent's container can't see your other files; backups stay independent of the app |
  | Native install with systemd | Close second | Lightest, and common for OpenClaw and Hermes, but Postgres must be installed per OS, and the agent runs beside your files |
  | Coolify, Dokploy | Optional for self-hosters | Dashboards that run Compose files, so nothing extra to build |
  | One all-in-one container | No | Backups could no longer run when the app is broken |
  | Kamal, Dokku | No | For a developer shipping to their own server; neither runs Compose files |
  | Managed runtimes: AWS AgentCore, Azure Foundry, Vertex Agent Engine, Claude Managed Agents, Cloudflare Agents | No | They solve scale for many users; your ledger would leave your machine, and others couldn't self-host |
  | Kubernetes, Ray | No | One user for each instance |

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
| Double-entry legs for each movement; EMI interest and principal as separate legs | Taken in [step 4](#step-4-ground-truth), on pgledger |
| A PII scrubber before cloud LLM calls | Decided in [step 7](#identifiers-at-import-not-a-scrubbing-layer): identifiers are handled at import instead |
| Typed query plans or views for model-written SQL | Views taken in [step 4](#step-4-ground-truth) |
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
