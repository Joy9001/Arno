# Foundations

## The definition

An **agent harness** is all code, configuration, and execution logic surrounding a model. The
model alone is not an agent; the harness makes it one by supplying state, tool execution,
feedback loops, and enforceable constraints.

> "Agent = Model + Harness. If you're not the model, you're the harness." — Viv Trivedy

Simon Willison's minimal reduction of the loop itself: an agent "runs tools in a loop to
achieve a goal." Everything that makes that loop reliable is harness.

The organising claim of the discipline:

> **A decent model with a great harness beats a great model with a bad harness.**

### Toward a precise definition

The term is "loose and polysemous" — used for complete products, evaluation scaffolds, and
frameworks alike. arXiv 2606.10106 proposes a **constitutive definition** giving necessary and
sufficient conditions, operationalised as an inclusion/exclusion test and validated against six
real systems (Claude Code, Codex CLI, Aider, Cline, OpenHands, SWE-agent) plus deliberate edge
cases. Its four necessary elements: **a loop, a tool interface, context, and controls.**

Note the collision this resolves: an **eval harness** and an **agent harness** are different
things that share a word. See [`evals-and-observability.md`](evals-and-observability.md).

### Six runtime responsibilities

A 2026 survey (arXiv 2606.20683) decomposes the execution harness into six coupled components:

**observation · context · control · action · state · verification**

Its central claim is that agent quality — success, efficiency, safety, generalisation — "emerges
from the interaction between model capability, runtime infrastructure, task structure, and
evaluation design," and that the useful diagnostic question is whether a given limitation lives in
the model, the harness, or their coupling.

## Why it emerged as a named discipline

The lineage runs prompt engineering → context engineering → harness engineering, each a wider
frame on the same problem. The survey above extends it one step further:

> prompt engineering → workflows → context engineering → harness engineering → **agent-native
> training with model-harness co-evolution**

- **Prompt engineering** asks: what words produce the behaviour?
- **Context engineering** asks: what configuration of context produces the behaviour?
- **Harness engineering** asks: what *system* around the model makes the wrong behaviour
  structurally impossible?

That last shift is the important one. The stated goal is not a smarter model but a system in
which **failures cannot repeat**, because each one becomes a permanent constraint.

## The evidence

This is what separates the field from vibes. Independent reports converge:

| Claim | Source | Confidence |
|---|---|---|
| **Claude Opus scores 93% on Terminal-Bench 2.0 in Cursor vs 77% in Claude Code — a 16-point gap from the harness alone** | Reported across practitioner write-ups | The single cleanest datapoint: same model, two harnesses, one number |
| A coding agent moved **Top 30 → Top 5** on Terminal Bench 2.0 by changing only the harness | Attributed to **Cursor's** team | Widely repeated; Osmani's summary attributes it differently, so attribution is soft |
| Harness-only tuning brought Nemotron 3 Ultra within **one point** of Opus 4.8 on Deep Agents at ~1/10 cost | LangChain / NVIDIA | Both parties benefit from the result |
| Infrastructure configuration can swing agentic coding benchmarks by **several percentage points — sometimes more than the leaderboard gap between top models** | Anthropic, 2026 Agentic Coding Trends Report | Verified wording. *An earlier draft of this survey said "5+ points" — that was too specific* |
| With a suitable harness, top models hit **70-90%** on the standard benchmark, up from **~4% in 2023** | Anthropic, same report | Conflates model and harness progress; directional only |
| **Self-Harness**: agents optimising their own harness produced relative gains **up to 132%** across 3 benchmarks and 3 model families | arXiv 2606.09498 | Academic, non-vendor |
| "Intent Met" rose **45% → 75%** after re-architecting context and tools | Microsoft, Azure SRE Agent | **Weakest number here.** Primary source is behind a bot check; second-hand only |

Caveat worth holding: most of these are vendors or practitioners reporting on their own systems.
The direction is consistent across independent teams; individual magnitudes are softer. The
Terminal-Bench 93-vs-77 split is the most useful single figure because it isolates the harness
while holding the model fixed.

Also worth noting the benchmark itself moves: Terminal-Bench is at 2.1 as of mid-2026, and a
dedicated leaderboard for harness-by-model comparison now exists (`TheLime1/harness-bench`).

## Component taxonomies

Three published decompositions. They differ in granularity, not in substance.

### Osmani's core primitives

- System prompts, `AGENTS.md`, skill files, sub-agent prompts
- Tools, skills, MCP servers — **and their descriptions**
- Bundled infrastructure: filesystem, sandbox, browser
- Orchestration logic: sub-agent spawning, handoffs, model routing
- Hooks and middleware for deterministic execution
- Observability: logs, traces, cost and latency metering

### LangChain's anatomy

1. System prompts and tool configuration, with progressive disclosure
2. Filesystem and storage — durable state, workspace, collaboration surface, git for
   versioning and rollback, memory standards like `AGENTS.md`
3. Code execution — bash as default tool, sandboxes, runtimes, CLI tools, browser
4. Observation and verification — logs, screenshots, test runners, self-verification loops
5. Context management — compaction, tool-output offloading, progressive disclosure
6. Long-horizon execution — planning, Ralph loops, sub-agent orchestration, model routing
7. Knowledge access — web search, MCP, memory injection across sessions

LangChain's framing of the purpose: *harness engineering helps humans inject useful priors to
guide agent behaviour.*

### Claude Code's layers (Fareed Khan's breakdown)

Input · Knowledge · Integration · Execution · Output · Observability · Multi-agent — with the
knowledge layer holding skill registry, context compressor, task graph, and memory store, and
the multi-agent layer holding sub-agent spawning, mailboxes, an FSM protocol, and worktree
isolation.

**What all three share:** prompts and tool definitions; a durable store (usually a filesystem);
an execution environment; a verification path; context management; and observability.

## Named principles

### The Ratchet Principle

Every agent mistake becomes a permanent rule. Failures drive harness improvements, and nothing
is written off as a transient error. This is the mechanism behind "make failure structurally
impossible" — the harness only moves one direction.

### The Skill Issue Reframe

HumanLayer's framing: most agent failures are **configuration problems, not model limitations**.
It is a diagnostic stance — before concluding the model can't, check whether the harness let it.
The Terminal Bench result above is the evidence offered for it.

### Environment before model

From Anthropic's containment work but general in application: design deterministic boundaries at
the environment layer first, then steer behaviour at the model layer. Probabilistic defences
belong behind deterministic ones. See [`security.md`](security.md).

### Minimal high-signal context

> "Find the smallest set of high-signal tokens that maximise the likelihood of your desired
> outcome."

Minimal does not mean short — it means no redundancy while remaining sufficient. See
[`context-engineering.md`](context-engineering.md).

## Convergence

Top coding agents — Claude Code, Cursor, Codex, Aider, Cline — have converged on very similar
harness patterns despite different underlying models and independent teams. The field reads this
as evidence that the patterns are load-bearing rather than stylistic.

## Two directional observations

**The model-harness training loop.** Harness primitives that get discovered and standardised
become training targets for the next generation of models. Models then overfit to those patterns,
which entrenches them. Harness design and model capability co-evolve rather than one leading.

**Harnesses don't shrink as models improve — they migrate.** Better models eliminate some
scaffolding and simultaneously unlock capabilities that need different scaffolding. The
expectation that harness work is a temporary crutch for weak models is not what practitioners
report.
