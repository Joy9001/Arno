# The loop and orchestration

## The agent loop

The irreducible core, per Simon Willison: an agent "runs tools in a loop to achieve a goal."
Everything else in a harness exists to make that loop terminate correctly.

**ReAct** (arXiv 2210.03629) is the ancestor of most production loops — interleaving reasoning
traces with actions so each informs the other. Nearly every current harness is a hardened
descendant.

Contemporary loop concerns named across sources: what gets re-injected each iteration, how
completion is detected, what happens on tool error, when to compact, and when to stop.

Two loop-adjacent findings worth noting:

- **Agents learn their runtime** (arXiv 2603.01209) — agents adapt behaviour to the harness they
  find themselves in, which is part of why harness changes move benchmarks so much.
- **Real-time deadlines reveal temporal awareness failures** (arXiv 2601.13206) — agents reason
  poorly about elapsed and remaining time unless the harness supplies it explicitly.

## Workflows vs agents — the foundational taxonomy

Anthropic's distinction, and the most useful framing for deciding how much autonomy to give:

| | Definition |
|---|---|
| **Workflow** | LLMs and tools orchestrated through **predefined code paths** |
| **Agent** | LLMs **dynamically direct their own processes** and tool usage |

The practical implication: most problems people reach for an agent to solve are workflows, and a
workflow is cheaper, faster, and far more debuggable.

### The five workflow patterns

**1. Prompt chaining** — decompose into sequential steps, each LLM call processing the prior
output, with programmatic checks between. *Use when* subtasks are fixed and decomposition is
clean. Trades latency for accuracy by simplifying each call.

**2. Routing** — classify the input, dispatch to a specialised handler. *Use when* categories are
distinct and classification is reliably accurate.

**3. Parallelisation** — two variants: *sectioning* (independent subtasks run concurrently) and
*voting* (the same task run repeatedly for diverse outputs). *Use when* speed matters, or when
multiple perspectives raise confidence.

**4. Orchestrator-workers** — a central LLM decomposes dynamically, delegates to workers,
synthesises results. *Use when* subtasks cannot be predicted in advance. Differs from
parallelisation by being dynamic rather than predefined.

**5. Evaluator-optimiser** — one LLM generates, another critiques, iterate. *Use when* clear
evaluation criteria exist and iteration demonstrably improves output.

### The agent pattern

LLMs using tools against environmental feedback in a loop, planning independently under user
direction. *Use when* the problem is open-ended and the required steps genuinely cannot be
hardcoded.

### The simplicity guidance

Unusually blunt for vendor documentation:

> Optimising single LLM calls with retrieval and in-context examples is usually enough.

Agentic systems **trade latency and cost for task performance**. Increase complexity only when
simpler approaches demonstrably underperform. The three success principles: maintain simplicity,
prioritise transparency by showing planning steps, and carefully craft the agent-computer
interface.

## The Codex loop in detail

The most thoroughly documented production loop, from OpenAI's *Unrolling the Codex Agent Loop*
(Michael Bolin, 2026-01-23). Worth reading closely because it makes the mechanics concrete.

### Turn structure

A **turn** runs from user input to assistant message, and may contain many
inference-then-tool-call iterations inside it. The assistant message *is* the termination signal —
the loop ends when the model stops emitting tool calls. A **thread** is the conversation; each new
turn re-sends the entire prior history.

Note the consequence: for a software agent the assistant message is not the real output. The real
output is the mutated filesystem. The message only signals that control returns to the user.

### Prompt construction

Items carry a **role**, in decreasing priority: `system`, `developer`, `user`, `assistant`.

The request has three parts the client controls — `instructions` (system/developer message),
`tools` (definitions from the harness, the API, and MCP servers), and `input` (the item list).

Codex prepends four things before the user's message:

1. A `developer` message describing **the sandbox**, built from bundled Markdown snippets —
   file permissions, network access, when to ask for approval, writable folders
2. Optional `developer` message with user config instructions
3. Optional `user` message of aggregated **user instructions**: `AGENTS.override.md` and
   `AGENTS.md` from `$CODEX_HOME`, then every folder from the git root down to the cwd
   (**32 KiB limit**, more specific instructions later), plus skill metadata if configured
4. A `user` message with `<environment_context>` — cwd and shell

The layered `AGENTS.md` walk with a size cap is a concrete answer to "how do project instructions
compose," and the ordering rule — more specific later — is how conflicts resolve.

### Prompt caching is a first-class architectural constraint

The loop is **quadratic** in JSON sent over a conversation. Prompt caching makes *sampling* linear,
and sampling cost dominates network cost. Caching requires **exact prefix matches**, so:

> The old prompt is an exact prefix of the new prompt. This is intentional.

**What causes a cache miss:**

- Changing the tools available mid-conversation
- Changing the model (model-specific instructions sit near the front)
- Changing sandbox configuration, approval mode, or cwd

**The discipline that follows:** when configuration changes mid-conversation, **append a new
message rather than modify an earlier one.** A sandbox change inserts a fresh `developer`
permissions message; a cwd change inserts a fresh `<environment_context>`.

**A real bug worth knowing:** early MCP support enumerated tools in inconsistent order, silently
destroying cache hits. MCP is especially hazardous here because servers can change their tool list
mid-conversation via `notifications/tools/list_changed` — honouring that notification during a long
conversation forces an expensive cache miss.

### Statelessness as a deliberate cost

The Responses API offers `previous_response_id` to avoid resending history. **Codex does not use
it** — to keep requests fully stateless and support Zero Data Retention. ZDR customers still
benefit from prior-turn reasoning because `encrypted_content` is decrypted server-side (OpenAI
persists the key, not the data).

An explicit efficiency-for-privacy trade, made in the opposite direction from what pure
performance would suggest.

### Compaction

Evolved from a manual `/compact` command (re-query with summarisation instructions, use the
resulting summary as new input) to a dedicated `/responses/compact` endpoint that returns a
replacement item list. That list includes an opaque `type=compaction` item whose
`encrypted_content` **preserves the model's latent understanding** of the original conversation —
strictly more than a text summary could carry. Triggered automatically at `auto_compact_limit`.

### The sandbox boundary

**Only Codex's own shell tool is sandboxed.** Tools from MCP servers and other APIs are *not*, and
must enforce their own guardrails. This is stated plainly in the docs and is a significant, easily
missed security property — see [`security.md`](security.md).

### Endpoint portability

The Responses API endpoint is configurable, including `http://localhost:11434/v1/responses` for
local models via ollama or LM Studio. The same harness drives hosted and local inference with a
config change.

## Planning and task decomposition

**Planning as a first-class artifact.** Decompose goals into step sequences **stored on disk**,
not held in context. This survives compaction and context resets, which is the point.

**Sprint contracts** — negotiate done-conditions before implementation begins. An agent that
agreed what "finished" means is measurably less likely to stop early.

**Planner / generator / evaluator splits** — separate generation from evaluation into distinct
agents to prevent **self-evaluation bias**. An agent grading its own work is not a check.

Research directions: LATS (Language Agent Tree Search), Plan-and-Act, task-decoupled planning for
long-horizon agents, task-adaptive multi-agent orchestration.

## Long-horizon execution

The problem: work that outlasts a context window, sometimes many of them.

**Ralph loops** — intercept the agent's attempt to declare completion, re-inject the original
prompt into a fresh context, repeat across sessions. Crude and effective; it directly attacks
early stopping.

**Compaction and hand-off files** — covered in [`context-engineering.md`](context-engineering.md).
For very long tasks, a full context reset rebuilt from a compact hand-off file beats repeated
compaction.

**Named failure modes** in long-horizon work:

- Early stopping
- Poor problem decomposition
- Incoherence across context windows
- Commented-out test code shipping (the agent "passing" tests by disabling them)
- Context anxiety — wrapping up prematurely as the window fills
- Self-evaluation bias

The last two are the subtle ones, because both produce output that *looks* like success.

## Hooks and middleware

Deterministic code at lifecycle points. This is where a harness stops being advisory and starts
being enforcing.

Documented uses:

- Pre-tool-call validation
- Post-file-edit verification — typecheck, lint, tests run automatically
- Blocking destructive commands: recursive deletes, force-pushes, table drops
- Pre-commit approval gates
- Auto-formatting on write

The principle: anything that must always happen should not be a prompt instruction. A hook always
fires; a prompt is a suggestion the model may or may not follow. This is the **Ratchet Principle**
made mechanical — each discovered failure becomes a hook rather than another line of prompt.

## Multi-agent — and its limits

The most important negative result in the survey.

**Microsoft's Azure SRE agent** started with **50+ specialised agents** in a hierarchy. After
observing coordination failures they consolidated to **a handful of generalist agents**. The
specific finding:

> Problems requiring more than **four handoffs** almost always failed.

GitHub's *Multi-Agent Workflows Often Fail* points the same direction. LangChain's *Choosing the
Right Multi-Agent Architecture* treats architecture selection as the actual decision, rather than
assuming more agents help.

This sits in tension with the sub-agent pattern in
[`context-engineering.md`](context-engineering.md), which genuinely works. The reconciliation
appears to be: **sub-agents for context isolation are good; agent hierarchies for task
decomposition are fragile.** A sub-agent that explores and returns a summary is a context
management technique. A network of agents handing work to each other is a distributed system with
an unreliable transport.

Where multi-agent machinery does appear in production harnesses, it's narrow: sub-agent spawning,
mailboxes, an FSM protocol, and worktree isolation — bounded coordination, not open delegation.

## Model routing

Sending different steps to different models by capability and cost. Named as a harness primitive
by both Osmani and LangChain. LangChain's *Tuning the Harness, Not the Model* is the strongest
evidence for the economics: harness-only tuning brought a much cheaper model within one point of a
frontier model at roughly one-tenth the cost.
