# Case studies

Three org-scale accounts with real numbers. These carry more weight than the essays, because they
report what happened rather than what should happen — though all three are self-reported by the
teams involved.

---

## OpenAI — building a product with zero human-written code

[*Harness engineering: leveraging Codex in an agent-first world*](https://openai.com/index/harness-engineering/),
Ryan Lopopolo, 2026-02-11. The single richest primary source in this survey.

### The experiment

Five months, starting from an empty git repository in late August 2025. Every line — application
logic, tests, CI config, docs, observability, internal tooling — written by Codex. **No manually
written code**, as a hard constraint.

| Measure | Value |
|---|---|
| Lines of code | ~1,000,000 |
| Pull requests | ~1,500 opened and merged |
| Team | 3 engineers, growing to 7 |
| Throughput | **3.5 PRs per engineer per day** — and it *increased* as the team grew |
| Time vs hand-writing | Estimated **~1/10th** |
| Longest single agent run | **6+ hours** on one task |

Even the initial `AGENTS.md` was written by Codex.

### The role change

> The primary job of our engineering team became enabling the agents to do useful work.

Early progress was slow "not because Codex was incapable, but because the environment was
underspecified." Their diagnostic when something failed was never *try harder*, it was:
**"what capability is missing, and how do we make it both legible and enforceable for the
agent?"** That is the Skill Issue Reframe operationalised.

### AGENTS.md as table of contents, not encyclopedia

They tried one big `AGENTS.md`. It failed in four named ways:

1. **Context is scarce** — a giant instruction file crowds out the task, the code, and the docs
2. **Too much guidance becomes non-guidance** — when everything is important, nothing is, and
   agents pattern-match locally instead of navigating intentionally
3. **It rots instantly** — a monolithic manual becomes a graveyard of stale rules humans stop
   maintaining
4. **It is hard to verify** — a single blob resists mechanical checks for coverage, freshness,
   ownership, cross-links

The replacement: a **~100-line AGENTS.md acting as a map**, pointing into a structured `docs/`
directory that is the system of record — design docs with verification status and core beliefs,
execution plans (active / completed / tech-debt), generated schema docs, product specs, reference
material.

**Plans are first-class, versioned artifacts** checked into the repository, with progress and
decision logs, so agents operate without external context.

**Enforced mechanically:** linters and CI jobs validate the knowledge base is current,
cross-linked and correctly structured. A recurring **doc-gardening agent** scans for stale docs
that no longer reflect code behaviour and opens fix-up PRs.

### Agent legibility as the design goal

> From the agent's point of view, anything it can't access in-context while running effectively
> doesn't exist.

Knowledge in Google Docs, chat threads, or people's heads is invisible. The consequence: push
context *into the repository* continuously. A Slack thread that aligned the team on an
architectural pattern is, to the agent, as unknown as it would be to a new hire joining three
months later.

Two design consequences worth noting:

- **Boring technology wins** — composability, API stability, and representation in the training
  set make it easier for agents to model.
- **Sometimes reimplement rather than depend.** Rather than a generic `p-limit` package, they
  wrote their own map-with-concurrency helper, tightly integrated with their OpenTelemetry
  instrumentation, at 100% coverage. *This cuts against the usual buy-don't-build default* — the
  argument is that opaque upstream behaviour is illegible to the agent, and legibility can beat
  reuse when the agent must reason about it.

### Enforce invariants, not implementations

A rigid layered architecture per business domain: **Types, Config, Repo, Service, Runtime, UI**,
forward dependencies only, with cross-cutting concerns (auth, connectors, telemetry, feature
flags) entering through a single `Providers` interface. Enforced by custom linters and structural
tests — themselves Codex-generated.

> This is the kind of architecture you usually postpone until you have hundreds of engineers. With
> coding agents, it's an early prerequisite: the constraints are what allows speed without decay.

They specify *what*, not *how*: data must be parsed at the boundary, but the library is the
agent's choice.

**The sharpest single technique in the whole survey:** custom lint error messages are written to
**inject remediation instructions into agent context.** The linter doesn't just reject — it
teaches. Every rejection carries its own fix.

### Throughput changes the merge philosophy

Minimal blocking merge gates. Short-lived PRs. Test flakes handled with a re-run rather than
blocking indefinitely.

> In a system where agent throughput far exceeds human attention, corrections are cheap, and
> waiting is expensive.

Flagged explicitly as irresponsible in a low-throughput environment — a trade-off purchased by
throughput, not a general recommendation.

### Entropy and garbage collection

Codex replicates existing patterns, including uneven ones, so drift is structural. The team
initially spent **every Friday — 20% of the week — cleaning up "AI slop."** That didn't scale.

The replacement: **golden principles** encoded in the repo (prefer shared utility packages over
hand-rolled helpers; never probe data shapes speculatively — validate boundaries or use typed
SDKs), plus recurring background Codex tasks that scan for deviations, update quality grades, and
open targeted refactoring PRs. Most are reviewable in under a minute and automerged.

The analogy is deliberate: technical debt as a high-interest loan, paid continuously rather than
in painful bursts. **Human taste is captured once, then enforced continuously on every line.**

### The autonomy threshold

From a single prompt the system can now: validate codebase state, reproduce a reported bug, record
a video of the failure, implement a fix, drive the app to validate it, record a second video, open
a PR, respond to feedback, detect and remediate build failures, escalate to a human only when
judgment is required, and merge.

Supporting infrastructure that made this possible: the app is **bootable per git worktree** so
each change gets its own instance; Chrome DevTools Protocol wired into the agent runtime with
skills for DOM snapshots, screenshots and navigation; an **ephemeral per-worktree observability
stack** with logs and metrics queryable via LogQL and PromQL. That last one makes prompts like
*"ensure service startup completes in under 800ms"* tractable.

**Their own caveat, worth preserving:** this "depends heavily on the specific structure and
tooling of this repository and should not be assumed to generalize without similar investment."

### What they admit not knowing

How architectural coherence evolves over *years* in a fully agent-generated system; where human
judgment adds the most leverage; how any of it changes as models improve.

---

## Microsoft — Azure SRE Agent

The best-documented **non-coding** production harness, and the source of the survey's most-cited
consolidation numbers.

| Change | From | To |
|---|---|---|
| Tools | 100+ narrow | **5 core** (wide CLI: `az`, `kubectl`) |
| Agents | 50+ specialised, hierarchical | **A handful of generalists** |
| "Intent Met" on novel incidents | 45% | **75%** |

**The handoff ceiling:** problems requiring more than **four handoffs** almost always failed.

**Context techniques:** large tool outputs written as files in a sandbox for interactive querying
rather than loaded into context; the model writes analysis code instead of receiving data dumps
(extending analysable timeframes "an order of magnitude"); continuous compaction into "key
incident facts"; planned tool-call chaining projected at **60-70% context reduction**.

**The reasoning behind filesystem-as-context** is the transferable part: frontier models are
trained on developer workflows, so the filesystem "is not an abstraction layered on top of that
prior — it matches it."

**Caveat:** the Microsoft primary source returns HTTP 403 to automated fetching. These figures
come from a secondary write-up (ZenML LLMOps database) and Osmani's article. The ZenML version
does not itself confirm the "Intent Met" metric — treat 45% to 75% as the least-verified number
in this survey.

---

## Academic — 70 agent systems surveyed

*Architectural Design Decisions in AI Agent Harnesses* (arXiv 2604.18071). Notable as one of the
few **non-vendor** systematic looks at the field.

Method: source-code and technical-material reading across 70 public agent-system projects.

**Five recurring design dimensions:** subagent architecture, context management, tool systems,
safety mechanisms, orchestration.

**Findings across the corpus:**

- Context strategies favour **file-persistent, hybrid, and hierarchical** approaches — independent
  corroboration of filesystem-as-context
- Tool systems are predominantly **registry-oriented**, with MCP and plugin extensions emerging
- Safety: intermediate isolation is common; **high-assurance auditing remains rare**
- Deeper coordination correlates with more explicit context services; stronger execution
  environments correlate with more structured governance

**Five system archetypes:** lightweight tools, balanced CLI frameworks, multi-agent orchestrators,
enterprise systems, scenario-verticalized projects.

Read from the abstract only — the full paper was not retrieved.
