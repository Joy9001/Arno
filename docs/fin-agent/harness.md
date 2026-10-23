# The harness layer

This file holds the detail behind [`plan.md`](plan.md) §3: why PydanticAI, which harness
capabilities to adopt and when, and the agent-behavior disciplines that aren't code.

This file is grounded in the
[agent harness engineering survey](../research/agent-harness-engineering/). That survey
describes the field; this file records what Fin-Agent takes from it.

The `plan.md` file owns decisions, and this file owns their shape. If the two disagree, the plan
wins and this file is stale.

---

## Why PydanticAI: The real reason

The plan originally justified it as "structured output validation." True but thin, and an equally
good argument for three other frameworks.

The actual reason: _Pydantic ships an official harness capability library_
(`pydantic-ai-harness`) implementing most of the survey's checklist as supported code. Building a
custom loop means hand-building that list. The harness is still on 0.x releases, so pin it.

The following table maps each survey finding to the capability that implements it:

| Survey finding | Capability |
|---|---|
| Prompt-cache prefix discipline | Warn On Cache Busts: detects prefix collapse from the provider's own numbers |
| Offload large tool outputs | Tool Output Limits: truncate, spill to a queryable file, or summarize at source |
| Compaction for long runs | Compaction: the provider's built-in compaction, plus model-independent strategies |
| Progressive tool disclosure | Tool Search: load tool definitions on demand |
| Hooks as deterministic enforcement | Guardrails: block or redact on tool calls _and_ results |
| Cost metering as a core concern | Spend Limits: USD or token budgets, per-response cost |
| Human-in-the-loop approval | Tool approval and deferred tool calls (core) |
| Sandboxed code execution | Code Mode: one script calling many tools in a Monty sandbox |
| Instruction fade on long runs | System Reminders: cache-safe mid-run reinjection |
| Durable, resumable runs | Step Persistence (SQLite backend) and core durable execution |

Provider switching is core: `OllamaProvider(base_url=...)` and
`OpenAIProvider(base_url=..., api_key=...)` are both fully supported, so the one-code-path
requirement for local and cloud in `plan.md` §3 holds exactly as written.

**Second reason, since the web app became the main face:** PydanticAI ships `VercelAIAdapter`, which
turns an agent run into the Vercel AI UI stream that the `useChat` hook consumes—text, tool calls,
custom data parts for cards, and tool-approval requests. The chat endpoint is one call
(`VercelAIAdapter.dispatch_request(request, agent=agent)`), not a streaming protocol that Fin-Agent
has to write.

**Why not LangChain (rechecked October 15, 2026).** LangChain v1's `create_agent` is the right
LangChain layer for one tool loop. Deep Agents puts file tools beside the ledger, and a raw
LangGraph graph would have one node. Its middleware covers compaction, tool approval, tool
selection, and guardrails, but it leaves three gaps that PydanticAI fills:

- Spend limits in USD: LangChain caps the number of calls, not their cost.
- A Vercel AI stream from Python: `@ai-sdk/langchain` is TypeScript only, and its browser
  transport needs the licensed LangGraph Agent Server.
- Cache-bust warnings.

Rule 3 keeps the switch a thin rewrite if this changes.

**Why the tools aren't hosted in Hermes or OpenClaw.** Considered in the
[personal-agent platforms research](../research/personal-agent-platforms-2026-09-23.md#5-could-hermes-or-openclaw-be-fin-agent):
they'd replace this harness, need 64k tokens of context, and ship shell and browser toolsets
beside the ledger. Rule 3 in `plan.md` §4 keeps the tools framework-free, so exposing them to such
a host later (through MCP) stays a thin adapter—see [`parked.md`](parked.md).

## What Fin-Agent adopts, and when

**The menu is a trap.** The survey's headline finding is Microsoft cutting more than 100 tools to 5.
Adopting 10 capabilities because they exist is the same mistake in a different costume. Each row
that follows stays off until its trigger fires.

### Day one

The following capabilities are on from the start:

| Capability | Why now |
|---|---|
| Core agent + tools + structured output | The product |
| Spend Limits | Development runs against a cloud API. A runaway loop costing real money is the one failure that bites immediately, and this is a few lines |
| Trace logging (Fin-Agent's own, to `agent_runs`) | Not a harness capability—every run's model, tokens, cost, tool calls, and results, inside the database on the encrypted host disk. Costs nothing and is the precondition for evals |
| Code Mode, over the read tools only | Multistep modeling from day one. The design target is a frontier model (`plan.md` §3), and a script is how the model computes without doing arithmetic in its head. Configuration in [`design.md`](design.md#code-mode) |

### Later, with triggers

The following capabilities wait for their triggers:

| Capability | Adopt when |
|---|---|
| Compaction | A single conversation actually approaches the context window. Conversations are kept forever, but only the current one is sent—total history is not the trigger |
| Tool Output Limits | A tool return is genuinely large—most likely a wide `run_query` result |
| Guardrails | Untrusted text has to reach the agent directly (for example, a chat-app bridge forwarding other people's messages), or a tool gains a destructive mode undo can't reverse. Today rule 2 in `plan.md` §4 keeps untrusted text out structurally |
| Tool approval | Write-show-undo proves insufficient. The plan chose undo for chat writes and a review screen for imports and photos. If adopted, the Vercel AI adapter already renders approval requests |
| Tool Search | The tool count grows past roughly a dozen. At 11, it costs more than it saves |
| Memory | Not adopted. Memory is the typed `preferences` table (`plan.md` §2): the agent saves to it unprompted, with a "Remembered" card and undo—visible, editable, and audited, which a notebook isn't |
| System Reminders | Long multiturn sessions show instruction drift |
| Step Persistence and durable execution | Runs get long enough that losing one hurts. A chat turn is seconds |

## The tool surface is deliberately wide

The survey's strongest single result: fewer, wider tools beat many narrow ones. Microsoft's
consolidation from more than 100 tools to 5 moved "Intent Met" from 45% to 75%, and Anthropic
explicitly warns against bloated tool sets and offers a sharp test—_if an engineer cannot say which
tool applies, the agent cannot either._

Two consequences are already in the design:

- **The `get_financial_context` tool is comprehensive on purpose.** One call returns settings,
  balances, the next 30 days of scheduled events, goals, open claims, and preferences. The model
  gets the whole picture before it plans anything, and every turn saves round trips.
- **The `run_query(sql)` tool applies the wide, shell-shaped pattern to a ledger.** It was
  parked; the evidence reversed that. A read-only Postgres role (`agent_ro`, which has a statement
  timeout) means the database does the arithmetic deterministically, so rule 1 in `plan.md` §4
  holds. The agent shows the SQL it ran, so a wrong filter is visible rather than silent.

**The counterargument, kept on the record:** the
[archived prior-art research](../archive/prior-art/research-2026-05-24.md) says "do not give the
LLM raw SQL access." That warning was about SQL as the _primary_ interface. As a read-only
escape hatch beside 10 tested tools, it is a different risk—a wrong query, not wrong
arithmetic. Different, not zero. If the `run_query` tool starts getting chosen over the typed
tools for questions the typed tools already handle, that is the signal the tool descriptions are
wrong.

## Instructions as a map, not a manual

OpenAI tried one large `AGENTS.md` file and documented four failure modes—it crowds out context,
dilutes priority, rots, and resists verification. They settled on about 100 lines pointing into a
structured store; HumanLayer independently landed under 60.

This applies to the Fin-Agent system prompt as follows:

- Keep it short. Name the tools, the category list, and the boundaries—do not enumerate
  rules.
- The category list is injected, because every plain-text-accounting project that skipped this
  learned the same lesson about invented categories. It's the user's edited list, not a constant.
- Two small injected blocks, each owned by data rather than prose: the India prompt notes
  (Hinglish examples, currency) and an instruction to reply in the user's own language mix.
  Active preferences arrive through `get_financial_context`, not the prompt.
- The advice boundary is one line: facts and tradeoffs, never investment or product
  recommendations.
- Behavior that must _always_ hold belongs in code, not prose. Rule 1 in `plan.md` §4 is
  enforced structurally: the model computes by calling tools or by writing a Code Mode script,
  so every number it states arrives as a tool return.

## Evals are a milestone, not an afterthought

The most repeated claim in the survey: adding evaluation infrastructure early is the
highest-leverage change after a working loop. Without it, you catch problems only in
production—and here, "production" means wrong answers about your own money.

**Note the naming collision.** The `evals/` directory in this repository is a _model-selection_
harness: it tests whether a given local model can hold a tool-calling conversation. That is not an
_agent eval_, which asks whether _this agent_ picks the right tool and refuses when data is missing.
Both are useful; they measure different things.

The suite takes its shape from the survey:

- **Use at least 50 tasks.** Draw them from real usage and real failures rather than imagined ones,
  including modeling questions that need a script, Hinglish, voice-transcript, and
  prompt-injection inputs (a statement row or
  screenshot whose text tries to instruct the agent). Run them against _every_ configured model,
  so the suite is also how a self-hoster learns whether their local model is good enough.
- **Grade the outcome, not the trajectory.** The question is whether the row landed in the
  ledger.
- **Balance the set.** Test that it answers when it has data _and refuses when it does not_. A
  one-sided suite produces a one-sided agent.
- **Isolate trial environments.** Use a fresh database for each trial, cloned from the
  demo-persona template (`CREATE DATABASE … TEMPLATE`). Shared state inflates scores.
- **Publish the results.** The `pass^k` score for each model goes in the README—the scorecard a
  self-hoster reads to pick a local model.
- **Prefer `pass^k` over `pass@k`.** This answers questions about money; it needs to be right
  every time, not once in five.
- **Read a 0% `pass@100` score as a diagnostic.** It almost always means a broken task spec, not an
  incapable model.

The cautionary tale worth remembering: Opus 4.5 scored 42% on CORE-Bench until researchers found
the _grader_ rejecting `96.12` when it expected `96.124991...`. After the fix, it scored 95%. A bad
eval measures eval quality, confidently.
