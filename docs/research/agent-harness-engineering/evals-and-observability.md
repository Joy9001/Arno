# Evals and observability

The consistent claim across sources: **adding evaluation infrastructure early is the
highest-leverage change most teams can make after getting a working agent loop.** Without it,
teams sit in reactive loops, catching issues only in production.

## Why agent evals differ from chat evals

A chat response is one prompt, one output, one grade. An agent:

- acts over many turns
- calls tools
- **mutates external state**
- adapts on intermediate results

So mistakes propagate and compound, and a whole class of failure is **invisible if you only grade
the final message**. The agent can report success while having done nothing, or the wrong thing.

Three tiers of complexity: single-turn, multi-turn, agent. Only the third has to account for tool
orchestration and environmental state change.

## Vocabulary

Anthropic's terms, worth adopting because the field is sloppy here:

| Term | Meaning |
|---|---|
| **Task** | One test: defined inputs plus success criteria |
| **Trial** | One attempt at a task. Multiple per task, because of non-determinism |
| **Grader** | The scoring logic |
| **Transcript** | Full record — outputs, tool calls, reasoning, interactions |
| **Outcome** | Final environmental state, *distinct from what the agent claims* |
| **Eval harness** | Infrastructure that runs the evaluation |
| **Agent harness** | The system that makes the model an agent |

Note the two different "harnesses." They are routinely confused.

## Trajectory vs outcome

**Trajectory** is the path: tool-call sequence, turn count, token usage, reasoning quality.

**Outcome** is the final state of the world, independent of claims. The canonical example: the
agent says *"Your flight has been booked"* — the eval checks whether a reservation exists in the
database.

**The guidance is to resist grading the process.** Measure what the agent produced, not the steps
it took, because agents find valid approaches the designer didn't anticipate. Grading trajectory
punishes creativity and locks in the designer's assumptions.

## Grader types

**Code-based** — exact/regex/fuzzy string matching, binary pass-fail, static analysis, outcome
verification, tool-call verification. Fast, cheap, objective, reproducible. Brittle against valid
variation; useless for subjective quality.

**Model-based** — rubric scoring, natural-language assertions, pairwise comparison,
reference-based, multi-judge consensus. Flexible, handles open-ended tasks. Non-deterministic,
costs more, needs human calibration.

**Human** — expert review, crowdsourcing, spot-check sampling, A/B tests, inter-annotator
agreement. Gold standard, scales badly.

Real suites combine all three. From a separate source: **evaluator model capability matters** — a
large judge model caught all known failures where smaller ones missed 4-5 cases. A weak judge
produces confident, wrong quality signals.

## Building a suite

### Start small — 20-50 tasks

Not hundreds. Early-stage agent changes produce large, easily-detected effects, so small samples
suffice. Mature agents need larger and harder sets to detect incremental change.

**Source tasks from three places:** manual checks already being done during development; the bug
tracker and support queue; behaviours users actually attempt. The important part is that these are
**real failures**, not imagined ones.

### Task quality

- Two domain experts should independently reach the **same** pass/fail conclusion
- The task must be passable by an agent following instructions correctly
- Every grader check must be evident from the task description
- Write a **reference solution** — it proves the task is solvable and validates the grader

Diagnostic heuristic: **"0% pass@100" almost always means a broken task specification**, not an
incapable model.

### Balance the set

Test presence *and* absence of behaviour. The example given: verify the agent searches the web
when it should **and refrains when it shouldn't**. A one-sided suite produces one-sided
optimisation — you get an agent that searches constantly and scores well for it.

### Harness stability

**Isolate trial environments.** Shared state between runs — leftover files, cached data, resource
exhaustion — creates correlated failures unrelated to agent quality, and artificially inflates
scores.

## pass@k vs pass^k

The two metrics for non-determinism. Choosing wrong misrepresents your system:

| Metric | Measures | With larger k | Fits |
|---|---|---|---|
| **pass@k** | At least one of k trials succeeds | Rises — more shots on goal | Proposing options where one good answer suffices |
| **pass^k** | **All** k trials succeed | Falls — consistency bar rises | User-facing agents needing reliability every time |

## Per-domain approaches

**Coding agents** — deterministic graders dominate. Unit tests pass or they don't. SWE-bench
Verified requires fixing failing tests without breaking existing ones.

**Conversational agents** — multidimensional: resolution (outcome), turns-to-completion
(trajectory), tone (rubric). Often use simulated users to stress-test long interactions.

**Research agents** — hardest, because ground truth is unstable and "comprehensive" is contextual.
Combine groundedness (claims traceable to sources), coverage (required facts present), and source
quality.

**Computer-use agents** — verify backend consequences (the order actually placed), not the
confirmation page. Noted trade-off: DOM interaction is fast but token-hungry; screenshots are
slower but more token-efficient.

## The cautionary tale

Opus 4.5 initially scored **42%** on CORE-Bench. Investigation found rigid grading that rejected
`96.12` when expecting `96.124991...`, ambiguous specs, and irreproducible tasks. After fixing the
*eval*, the score was **95%**.

The lesson: a bad eval measures eval quality, not agent quality — and it does so confidently.
**Read the transcripts.** Metrics alone hide this.

## Layered evaluation

No single method catches everything:

| Method | When | Strength |
|---|---|---|
| Automated evals | Pre-launch, CI/CD | Fast iteration, reproducible |
| Production monitoring | Post-launch | Ground truth, drift detection |
| A/B testing | With real traffic | Real user outcomes |
| User feedback | Ongoing | Unanticipated failures |
| Transcript review | Continuous sampling | Intuition, subtle quality issues |
| Human studies | Calibration phases | Gold-standard judgments |

## Observability

The runtime counterpart to evals.

**What to capture:** structured tracing across LLM calls, tool invocations, and memory operations;
**nested spans preserving parent-child relationships** across multi-agent handoffs; and an
evaluation layer that scores production traces.

**Replay-grade capture at the tool boundary** is the stated bar — enough structure to **fail CI
when behaviour changes dangerously**, e.g. an exact lookup silently becoming a broad query, or an
external write happening without approval. A notably higher bar than "we log things."

**The opaque-harness problem** (2026): when the harness is third-party, closed-source, hosted, or
spawns arbitrary subprocesses, application-level telemetry cannot be the sole source of truth —
the framework trace may simply be incomplete. This motivates independent OS-level observation
(eBPF is the named technique) to detect discrepancies between what the harness reports and what
actually executed.

**Cost and latency metering** appear in every component taxonomy as first-class, not an
afterthought.
