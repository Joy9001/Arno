# Tool design

Anthropic calls tool definitions the **agent-computer interface** and treats it as a first-class
design surface, on par with a human UI. The consistent message across sources: tool design is one
of the highest-leverage and most under-invested parts of a harness.

## The headline finding — fewer tools win

The strongest empirical result in the survey, and it runs against most people's instinct.

**Microsoft Azure SRE Agent:** reduced from **100+ narrow tools to 5 core tools**, primarily wide
CLI-shaped ones (`az`, `kubectl`). Two effects:

1. **Massive context compression** — tool definitions themselves consume context, and 100
   definitions is a large permanent tax on every turn.
2. **Expanded capability** — the model reached the entire CLI surface rather than whatever subset
   someone had pre-wrapped.

Combined with the filesystem-as-context change, "Intent Met" rose **45% to 75%** on novel
incidents.

**Anthropic** names the anti-pattern directly: *bloated tool sets that cover too much
functionality or lead to ambiguous decision points.* The diagnostic test is memorable —

> If an engineer can't definitively say which tool applies in a given scenario, the agent will
> fail the same way.

**Osmani** lists tool menu bloat as a named failure mode: fifty overlapping tools underperform ten
focused ones.

### Why this happens

Every tool definition is permanent context, and every additional tool adds a discrimination
problem. Overlapping tools force the model into a choice with no clear right answer, and that
choice is made on every relevant turn. The cost compounds; the benefit does not.

## Granularity — workflows, not API endpoints

**Do not simply wrap an existing API.** The recommendation is to implement a few thoughtful tools
targeting specific high-impact *workflows*.

The worked example: rather than exposing `list_users`, `list_events`, and `create_event`
separately, expose **`schedule_event`**. One tool that completes an intent beats three that
compose into one.

This is the CLI consolidation instinct from a different direction — fatter tools that do more per
call, rather than thin tools the model must chain. Chaining costs round trips, and each round trip
is a chance to lose coherence.

Each tool should have *a clear, distinct purpose*, with no functional overlap against any other.

## Naming and namespacing

- **Unambiguous parameter names.** `user_id`, not `user`.
- **Prefix related tools.** `asana_search`, `asana_projects_search` — grouping helps the model
  select correctly and makes the menu legible.

## Descriptions

Treated as the highest-leverage text in the harness:

> Small refinements to tool descriptions can yield dramatic improvements.

The framing is that a description makes implicit team knowledge explicit — what the tool expects,
what it returns, and any specialised terminology the model wouldn't otherwise know.

**Measured result:** Anthropic reports Claude-optimised tool definitions outperformed
human-written ones on Slack and Asana MCP servers, with gains holding against held-out test sets.
The implication is that tool descriptions are a tunable artifact, not documentation — optimisable
the way a prompt is.

## Return shapes

**Semantic over technical.** Return natural-language names, terms, and identifiers rather than
UUIDs. A UUID costs tokens and tells the model nothing it can reason about.

**Optional verbosity control.** A `response_format` enum offering concise vs detailed output lets
the model trade tokens against completeness per call, rather than the harness guessing once for
all cases.

**Distinguish empty from unknown.** Not stated as a slogan in the sources but implied throughout
the eval material: a tool returning nothing for "no data" and nothing for "no matches" gives the
model no way to tell those apart, and it will confabulate the difference.

## Error handling

Error messages are prompts. The guidance is to make them actionable:

- Communicate specific, actionable improvements rather than a code
- **Include examples of correctly formatted input** in the error itself

An error that teaches the model how to retry correctly converts a failure into a self-repair. One
that just says `400` burns a turn.

## Token efficiency

Apply **pagination, range selection, filtering, and truncation with sensible defaults.** Truncate
strategically rather than arbitrarily, and steer the agent with instructions toward efficient
search strategies.

The related pattern from [`context-engineering.md`](context-engineering.md): for genuinely large
outputs, don't return them at all — write to the filesystem and return a path.

## Structured output and validation

Named in the awesome-list taxonomy under tool design: `outlines`, `instructor`, and
provider-native structured output / function calling. The pattern is constraining generation to a
schema rather than parsing free text and hoping.

The harness-engineering framing: **validation is a deterministic layer catching a probabilistic
failure** — the same argument as environment-first security. Put the reliable mechanism
underneath the unreliable one.

## Bash and code execution as the general-purpose tool

A recurring pattern with real tension in it.

**The case for:** rather than pre-building a tool for every operation, give the agent a shell and
let it construct what it needs. Microsoft's 5-tool consolidation was exactly this — `az` and
`kubectl` are gateways to hundreds of operations, not tools. Anthropic's *Code Execution with MCP*
applies the same idea to protocol tooling.

**The case against:** it is the widest possible attack surface, and it discards the tested
semantics purpose-built tools provide. See [`security.md`](security.md) — the entire containment
literature exists because of this pattern.

The field has not resolved this. Practice appears to be general execution inside a hard sandbox,
with deterministic policy checks on destructive operations.

## Open research

From the awesome-list taxonomy — active work rather than settled practice:

- **AutoHarness**, **SkillTester** — automated generation and benchmarking of tool/skill utility
  and security
- **Scaling Parallel Tool Calling** — many tools invoked concurrently rather than sequentially
- **Tool Annotations as Risk Vocabulary** (MCP team) — standardised metadata describing what a
  tool can *do to the world*, so the harness can gate on it
- **TopoCurate** — modelling the interaction topology between tools

The risk-vocabulary work is the most practically interesting: it moves "is this tool dangerous"
from a hand-maintained allowlist into a declared property of the tool itself.
