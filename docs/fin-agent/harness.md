# The harness layer

Detail behind [`plan.md`](plan.md) §3. Why PydanticAI, which harness capabilities to adopt and
when, and the agent-behaviour disciplines that aren't code.

Grounded in [`../research/agent-harness-engineering/`](../research/agent-harness-engineering/).
That survey describes the field; this file records what we take from it.

`plan.md` owns decisions. This file owns their shape.

---

## Why PydanticAI — the real reason

The plan originally justified it as "structured output validation." True but thin, and an equally
good argument for three other frameworks.

The actual reason: **Pydantic ships an official harness capability library**
(`pydantic-ai-harness`) implementing most of the survey's checklist as supported code. Rolling our
own loop, or picking a framework without this, means hand-building that list.

| Survey finding | Capability |
|---|---|
| Prompt-cache prefix discipline | **Warn On Cache Busts** — detects prefix collapse from the provider's own numbers |
| Offload large tool outputs | **Tool Output Limits** — truncate, spill to a queryable file, or summarize at source |
| Compaction for long runs | **Compaction** — provider-native, plus model-agnostic strategies |
| Progressive tool disclosure | **Tool Search** — load tool definitions on demand |
| Hooks as deterministic enforcement | **Guardrails** — block/redact on tool calls *and* results |
| Cost metering as first-class | **Spend Limits** — USD/token budgets, per-response cost |
| Human-in-the-loop approval | **Tool approval** / deferred tool calls (core) |
| Sandboxed code execution | **Code Mode** — one script calling many tools in a Monty sandbox |
| Instruction fade on long runs | **System Reminders** — cache-safe mid-run re-injection |
| Durable/resumable runs | **Step Persistence** (SQLite backend) and core durable execution |

Provider switching is core: `OllamaProvider(base_url=...)` and
`OpenAIProvider(base_url=..., api_key=...)` are both first-class, so the one-code-path local/cloud
requirement in `plan.md` §3 holds exactly as written.

**Second reason, since the web app became the main face:** PydanticAI ships `VercelAIAdapter`,
which turns an agent run into the Vercel AI UI stream that `useChat` consumes — text, tool calls,
custom data parts for cards, and tool-approval requests. The chat endpoint is one call
(`VercelAIAdapter.dispatch_request(request, agent=agent)`), not a streaming protocol we write.

**Why not host the tools in Hermes or OpenClaw instead.** Considered in the
[platform research](../research/personal-agent-platforms-2026-09-23.md#5-could-hermes-or-openclaw-be-fin-agent):
they'd replace this harness, need 64K context, and ship shell and browser toolsets beside the
ledger. Rule 3 in `plan.md` §4 keeps the tools framework-free, so exposing them to such a host
later (via MCP) stays a thin adapter — see [`parked.md`](parked.md).

## What we adopt, and when

**The menu is a trap.** The survey's headline finding is Microsoft cutting 100+ tools to 5.
Adopting ten capabilities because they exist is the same mistake in a different costume. Each row
below stays off until its trigger fires.

### Day one

| Capability | Why now |
|---|---|
| Core agent + tools + structured output | The product |
| **Spend Limits** | Development runs against a cloud API. A runaway loop costing real money is the one failure that bites immediately, and this is a few lines |
| **Trace logging** (ours, to `agent_runs`) | Not a harness capability — every run's model, tokens, cost, tool calls and results, inside the encrypted DB. Costs nothing and is the precondition for evals |

### Later, with triggers

| Capability | Adopt when |
|---|---|
| **Compaction** | A single conversation actually approaches the context window. Conversations are kept forever, but only the current one is sent — total history is not the trigger |
| **Tool Output Limits** | A tool return is genuinely large — most likely a wide `run_query` result |
| **Guardrails** | Untrusted text has to reach the agent directly (e.g. a chat-app bridge forwarding other people's messages), or a tool gains a destructive mode undo can't reverse. Today rule 2 in `plan.md` §4 keeps untrusted text out structurally |
| **Tool approval** | Write-show-undo proves insufficient. The plan chose undo for chat writes and a review screen for imports and photos. If adopted, the Vercel AI adapter already renders approval requests |
| **Tool Search** | The tool count grows past roughly a dozen. At eleven it would cost more than it saves |
| **Memory** | Not adopted. Memory is the typed `preferences` table (`plan.md` §2): the agent saves to it unprompted, with a "Remembered" card and undo — visible, editable and audited, which a notebook isn't |
| **System Reminders** | Long multi-turn sessions show instruction drift |
| **Step Persistence / durable execution** | Runs get long enough that losing one hurts. A chat turn is seconds |
| **Code Mode** | See [`parked.md`](parked.md) |

## The tool surface is deliberately wide

The survey's strongest single result: fewer, wider tools beat many narrow ones. Microsoft's
100-plus to 5 consolidation moved "Intent Met" from 45% to 75%, and Anthropic names bloated tool
sets as an explicit anti-pattern with a sharp test — *if an engineer cannot say which tool
applies, the agent cannot either.*

Two consequences already in the design:

- **`get_financial_context` is fat on purpose.** One call returns profile, balance, obligations
  and baselines. Small models lose coherence after 2-3 chained calls, so one fat call beats four
  thin ones.
- **`run_query(sql)` is the wide shell-shaped tool applied to a ledger.** It was parked; the
  evidence reversed that. A read-only Postgres role (`agent_ro`, with a statement timeout) means the
  database does the arithmetic deterministically, so `plan.md` section 4's rule holds. The agent shows the SQL it ran, so a
  wrong filter is visible rather than silent.

**The counter-argument, kept on the record:** the
[archived prior-art research](../archive/prior-art/research-2026-05-24.md) says *"do not give the
LLM raw SQL access."* That warning was about SQL as the **primary** interface. As a read-only
escape hatch beside ten tested tools it is a different risk — a wrong query, not wrong
arithmetic. Different, not zero. If `run_query` starts getting chosen over the typed tools for
questions the typed tools already handle, that is the signal the tool descriptions are wrong.

## Instructions as a map, not a manual

OpenAI tried one large `AGENTS.md` and documented four failure modes — it crowds out context,
dilutes priority, rots, and resists verification. They settled on ~100 lines pointing into a
structured store; HumanLayer independently landed under 60.

Applied to our system prompt:

- Keep it short. Name the tools, the category list, and the boundaries — do not enumerate
  rules
- The **category list is injected**, because every plain-text-accounting project that skipped this
  learned the same lesson about invented categories. It's the user's edited list, not a constant
- Two small injected blocks, each owned by data rather than prose: the **India prompt notes**
  (Hinglish examples, currency) and an instruction to reply in the user's own language mix.
  Active preferences arrive through `get_financial_context`, not the prompt
- The **advice boundary** is one line: facts and tradeoffs, never investment or product
  recommendations
- Behaviour that must *always* hold belongs in code, not prose. "The LLM never does arithmetic" is
  enforced structurally: the model is given no way to compute, because every number arrives as a
  tool return

## Evals are a milestone, not an afterthought

The most repeated claim in the survey: adding evaluation infrastructure early is the
highest-leverage change after a working loop. Without it you catch problems only in production —
and here, "production" means wrong answers about your own money.

**Note the naming collision.** `evals/` in this repo is a *model-selection* harness: can a given
local model hold a tool-calling conversation. That is not an **agent eval**, which asks whether
*this agent* picks the right tool and refuses when data is missing. Both are useful; they measure
different things.

Shape, from the survey:

- **50+ tasks**, drawn from real usage and real failures rather than imagined ones, including
  Hinglish, voice-transcript and **prompt-injection** inputs (a statement row or screenshot whose
  text tries to instruct the agent). Run against **every configured model**, so the suite is
  also how a self-hoster learns whether their local model is good enough
- Grade the **outcome** (did the row land in the ledger), not the trajectory
- **Balance the set**: test that it answers when it has data *and refuses when it does not*. A
  one-sided suite produces a one-sided agent
- **Isolate trial environments** — a fresh database per trial, cloned from the demo-persona
  template (`CREATE DATABASE … TEMPLATE`). Shared state inflates scores
- **Publish the results.** `pass^k` per model goes in the README — the scorecard a self-hoster
  reads to pick a local model
- Prefer `pass^k` over `pass@k`. This answers questions about money; it needs to be right every
  time, not once in five
- Diagnostic: **0% pass@100 almost always means a broken task spec**, not an incapable model

The cautionary tale worth remembering: Opus 4.5 scored 42% on CORE-Bench until researchers found
the *grader* rejecting `96.12` when it expected `96.124991...`. After fixing the eval, 95%. A bad
eval measures eval quality, confidently.
