# Context engineering

The largest and most developed sub-area. Anthropic's definition: *optimising the utility of
tokens against the inherent constraints of LLMs.*

## Context rot — the premise

As token volume grows, recall accuracy falls. Models show attention degradation analogous to
human working-memory limits.

The architectural cause is stated plainly: n tokens create n² pairwise attention relationships.
Position-encoding interpolation permits longer sequences at the cost of degraded positional
understanding. The result is a **performance gradient, not a hard cliff** — long contexts stay
usable but lose precision on retrieval and long-range reasoning.

Practical consequence: a filled context window is not a used resource, it's a degraded one.
Osmani lists **context anxiety** — models wrapping up work prematurely as the window fills — as
an observed failure mode.

## System prompt structure

Two failure modes bracket the good zone:

| Failure | Shape |
|---|---|
| **Too brittle** | Hardcoded if-else logic in prose. Fragile, unmaintainable |
| **Too vague** | High-level guidance with no concrete signal. Doesn't steer |

Recommended structure: distinct sections marked with XML tags or Markdown headers —
`<background_information>`, `<instructions>`, `## Tool guidance`, `## Output description`.

The target is *the minimal set of information that fully outlines expected behaviour.* Minimal
means non-redundant, not short.

**Few-shot over exhaustive rules.** Rather than documenting every edge case, supply a set of
diverse canonical examples that portray the expected behaviour. Examples do the work that
enumerated rules do badly.

**Size datapoints:** HumanLayer keeps `AGENTS.md` under **60 lines**. OpenAI settled on
**~100 lines** after trying a monolithic version — see *table of contents, not encyclopedia* below.

### The monolithic-instructions failure

OpenAI's team tried one large `AGENTS.md` and documented four distinct failure modes
([case-studies.md](case-studies.md)):

1. **Context is scarce** — a giant instruction file crowds out the task, the code, and the docs
2. **Too much guidance becomes non-guidance** — when everything is important, nothing is; agents
   pattern-match locally instead of navigating intentionally
3. **It rots instantly** — a monolithic manual becomes a graveyard of stale rules
4. **It is hard to verify** — a single blob resists mechanical checks for freshness and coverage

The replacement is a **map, not a manual**: a short stable entry point pointing into a structured
`docs/` tree that holds the real content. This is progressive disclosure applied to project
knowledge rather than to tools.

The mechanical half matters as much as the structure: linters and CI validate that the knowledge
base is current and cross-linked, and a recurring **doc-gardening agent** opens PRs against docs
that no longer match code behaviour. Without that, the map rots exactly like the manual did.

## Just-in-time vs pre-loaded context

A genuine shift in the field, away from stuffing everything in up front.

**Just-in-time**: hold lightweight identifiers — file paths, queries, links — and load data via
tools at runtime. Mirrors how humans use external organisation systems rather than memorising.
Enables **progressive disclosure**: the agent discovers context incrementally by exploring.

Claude Code is the worked example — targeted queries, filesystem navigation, `head`/`tail`,
never loading whole objects.

Metadata carries signal cheaply: folder hierarchies, naming conventions, and timestamps tell an
agent about purpose and relevance without spending tokens on content.

**The trade-off is real.** Runtime exploration is slower than pre-computed retrieval and needs
careful engineering to stop agents misusing tools, chasing dead ends, or missing key
information.

**Hybrid** — retrieve some data up front for speed, permit autonomous exploration beyond it — is
called out as optimal for *less dynamic* domains, with legal and financial work named
specifically.

## Filesystem as context substrate

The strongest single result in the survey.

Microsoft's Azure SRE agent exposed everything — source code, runbooks, query schemas, past
investigation notes — as **files**, and let the agent use `read_file`, `grep`, `find`, and shell.
This outperformed the specialised tooling it replaced: "Intent Met" went **45% to 75%** on novel
incidents.

The stated reason is the interesting part:

> Frontier models are trained on developer workflows: navigating repositories, grepping logs,
> patching files, running commands. The filesystem is not an abstraction layered on top of that
> prior. **It matches it.**

This reframes context engineering as *aligning with the model's training distribution* rather
than building bespoke scaffolding around it. LangChain's anatomy independently places the
filesystem as a first-class harness layer.

### Corollary: if the agent can't reach it, it doesn't exist

OpenAI's formulation:

> From the agent's point of view, anything it can't access in-context while running effectively
> doesn't exist.

Knowledge in chat threads, shared documents, or people's heads is invisible to the system. The
practice that follows is to migrate context *into the repository* continuously — versioned,
cross-linked, mechanically checked. Independent corroboration from the 70-system survey (arXiv
2604.18071): context strategies across the corpus favour **file-persistent, hybrid, and
hierarchical** approaches.

## Long-horizon techniques

Three approaches to outlasting the context window, with different fits.

### Compaction

Summarise conversation history before hitting the limit; continue with minimal degradation.
Preserve architectural decisions, unresolved bugs, and implementation details; discard redundant
outputs.

Tuning method: **maximise recall first** so the compaction prompt captures everything relevant,
then iterate on precision to cut the superfluous. Tune against complex real traces.

Cheapest win named: clear tool results from deep history — agents rarely need raw results once
the call has completed.

Microsoft used continuous summarisation into "key incident facts" to hold context as a small
working set.

### Structured note-taking

The agent writes notes persisted *outside* the context window and retrieves them later — to-do
lists, `NOTES.md`, progress and dependency tracking. Persistent memory at minimal overhead.

The Pokémon example is the vivid one: agents spontaneously maintained tallies, maps, strategic
notes and achievements across thousands of steps, sustaining multi-hour coherence across context
resets — without explicit memory prompting.

### Sub-agent architectures

Specialised sub-agents work in clean context windows while a lead agent coordinates from a
high-level plan. Sub-agents may burn tens of thousands of tokens exploring but return condensed
summaries of **1,000-2,000 tokens**.

The value is separation of concerns: detailed search context stays isolated in the sub-agent;
the lead agent only synthesises.

### Choosing between them

| Task shape | Technique |
|---|---|
| Extensive back-and-forth interaction | Compaction |
| Iterative development with clear milestones | Note-taking |
| Complex research benefiting from parallel exploration | Sub-agents |

## Other context-reduction techniques

- **Tool-call offloading** — write large outputs (a 2,000-line log) to the filesystem instead of
  into context; the agent queries the file.
- **Code interpretation over data dumping** — rather than putting structured data in context, have
  the model write analysis code against it. Microsoft reported this extended analysable
  timeframes by "an order of magnitude."
- **Tool-call chaining** — collapse sequential model-to-tool round trips into one script
  execution. Microsoft projected **60-70% context reduction** from converting three round trips
  into one.
- **Prompt caching** — reuse of stable prefixes; standard across vendors.
- **Full context resets** — for very long tasks, rebuild from a compact hand-off file rather than
  compacting repeatedly.

## Prompt caching as a context constraint

Rarely discussed as context engineering, but it shapes context layout decisively.

Caching requires **exact prefix matches**, so the standard guidance is: static content
(instructions, examples, tool definitions) at the **front**, variable content at the **end**.
Images and tool definitions must be byte-identical between requests.

The architectural consequence, from Codex: **the old prompt must remain an exact prefix of the new
prompt.** With cache hits, sampling cost goes from quadratic to linear over a conversation.

What breaks it: changing available tools mid-conversation, switching models, or changing sandbox
config / approval mode / working directory. The discipline that follows is to **append a new
message describing the change rather than editing an earlier one** — never rewrite history you
have already sent.

MCP is a specific hazard: servers may change their tool list mid-session via
`notifications/tools/list_changed`, and honouring it forces an expensive miss. Anthropic reports
server-side compaction reducing tokens **84%** in web search; OpenAI's `/responses/compact`
endpoint returns an opaque `type=compaction` item whose encrypted content preserves the model's
latent understanding — more than a text summary can carry.

Full mechanics in [`loop-and-orchestration.md`](loop-and-orchestration.md).

## Memory and continual learning

Memory is where context engineering becomes persistent.

**Memory file standards.** `AGENTS.md` / `CLAUDE.md`-style files injected at session start are the
dominant convention — the simplest possible durable memory, and the one that actually shipped.

**Filesystem as primary knowledge store.** Consistent with the above; memory is files, not a
special subsystem.

**Anthropic's file-based memory tool** lets agents build knowledge bases over time and hold
project state across sessions.

**Where research is heading.** Agent memory became a central focus through 2025-26 — hierarchical
and episodic memory, memory-as-skills (MemSkill represents memory operations as an evolving skill
bank), self-evolving agents, and "agent dreaming." Letta's framing is the notable strategic claim:

> As agents become capable of continual learning, memory will be a more valuable component than
> the underlying model — it is specific to the user and compounds in value over time.

**The security counterpart**, from Anthropic: persistent memory is persistent attack surface.
`CLAUDE.md`, product memory, and state directories enable post-exploitation persistence
injection. Memory that survives sessions survives compromise. See [`security.md`](security.md).

## Direction of travel

Harnesses are moving toward **dynamic, just-in-time assembly of tools and context** rather than
static configuration — Osmani's analogy is that harnesses start to resemble compilers.

The counterweight, stated by Anthropic: as models improve they need less prescriptive
engineering, but treating context as a precious finite resource stays central regardless.
