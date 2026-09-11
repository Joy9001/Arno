# Research: Agent Harness Engineering

Independent survey of agent harness engineering as a discipline, conducted **2026-09-07**.

**This is deliberately unfiltered by any project's requirements.** It records what exists in
the field, not what any particular system should adopt. Applying it to a specific build is a
separate exercise — do not read these files as recommendations.

Starting point was [Addy Osmani's *Agent Harness Engineering*](https://addyosmani.com/blog/agent-harness-engineering/);
the survey went outward from there to primary vendor engineering writing, the
`awesome-harness-engineering` taxonomy, and current research.

## Index

| File | Covers |
|---|---|
| [`case-studies.md`](case-studies.md) | **OpenAI's zero-human-code experiment**, Microsoft's Azure SRE agent, and a 70-system academic survey — the hard numbers |
| [`foundations.md`](foundations.md) | What a harness is, `Agent = Model + Harness`, evidence the harness dominates, component taxonomies, convergence |
| [`context-engineering.md`](context-engineering.md) | Context rot, system-prompt structure, just-in-time retrieval, compaction, note-taking, sub-agents, filesystem-as-context, memory |
| [`tool-design.md`](tool-design.md) | Naming, granularity, return shapes, errors, token budget, and the strong empirical case for *fewer* tools |
| [`loop-and-orchestration.md`](loop-and-orchestration.md) | The agent loop, workflow-vs-agent taxonomy, planning, long-horizon execution, hooks, multi-agent limits |
| [`evals-and-observability.md`](evals-and-observability.md) | Why agent evals differ, grader types, `pass@k` vs `pass^k`, dataset sizing, tracing |
| [`security.md`](security.md) | Containment layers, sandboxing, permission fatigue, prompt injection, egress control |
| [`ecosystem.md`](ecosystem.md) | Frameworks, MCP and protocols, notable harnesses, where the field is heading |
| [`sources.md`](sources.md) | Full bibliography |

## Executive summary

1. **`Agent = Model + Harness`.** The harness is all code, config, and execution logic around
   the model. The field's organising claim: *a decent model with a great harness beats a great
   model with a bad harness.*

2. **The strongest single account: OpenAI shipped ~1M lines with zero human-written code.** Five
   months, ~1,500 PRs, 3 engineers growing to 7, ~3.5 PRs per engineer per day, estimated 1/10th
   the time. Their engineering job became designing environments, not writing code. Their own
   caveat: it "should not be assumed to generalize without similar investment."

3. **The evidence is unusually strong for a young field.** Moving a model from Top 30 to Top 5
   on Terminal Bench 2.0 required only harness changes. LangChain reports harness-only tuning
   brought a cheaper model within one point of a frontier model at roughly one-tenth the cost.
   Anthropic's 2026 agentic coding report treats harness setup as worth 5+ benchmark points.

4. **Context is a finite resource, and the discipline is subtraction.** "Context rot" —
   accuracy degrading as the window fills — is treated as a given. The recurring instruction is
   to find *the smallest set of high-signal tokens* that produce the behaviour.

5. **Fewer, more general tools beat many specific ones.** Microsoft's Azure SRE agent went from
   100+ narrow tools to **5** CLI-shaped ones, and from 50+ specialised agents to a handful of
   generalists. Anthropic names bloated tool sets as an explicit anti-pattern.

6. **The filesystem is the dominant context substrate.** Frontier models are trained on
   developer workflows — grep, read, patch, run. Exposing state as files matches that prior
   instead of fighting it.

7. **Evals are the load-bearing quality layer, and they differ from chat evals.** Agents mutate
   state across many turns; grading the final message misses most failure. Start with 20-50
   tasks drawn from real failures. Grade the **outcome**, not the trajectory.

8. **Security belongs at the environment layer, not the model layer.** Permission prompts fail
   to human fatigue (~93% approval rates). OS-level sandboxing with default-deny egress cut
   prompts 84%. Probabilistic defences sit *behind* deterministic boundaries, never in front.

9. **Convergence is real.** Independent top coding agents have arrived at near-identical harness
   patterns despite different underlying models, which suggests load-bearing architecture rather
   than fashion.

10. **Instructions should be a map, not a manual.** Monolithic instruction files fail four ways —
    they crowd out context, dilute priority, rot, and resist verification. Both OpenAI (~100 lines)
    and HumanLayer (<60 lines) converged on short entry points pointing into structured, mechanically
    validated stores.

## Reading note

Numbers are quoted as reported by their sources, and [`sources.md`](sources.md) logs three
corrections made after verification — two figures this survey originally overstated, and one soft
attribution. Vendor engineering blogs describing their own
products are the bulk of the primary material, and they have an obvious interest in the results
they publish — treat specific figures as directional. Where a claim is contested or a source is
self-interested, [`sources.md`](sources.md) says so.
