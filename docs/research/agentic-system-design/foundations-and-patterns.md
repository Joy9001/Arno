# Foundations & Design-Pattern Taxonomies

Part of the agentic system design field survey, written **2026-10-18**. This is a survey of how
vendors and practitioners define agentic systems and classify architecture patterns. It is not
tailored to any project and makes no recommendations.

Scope boundary: harness internals (the agent loop, context engineering, tool design, evals, security)
are covered in [`../agent-harness-engineering/`](../agent-harness-engineering/README.md), especially
[`loop-and-orchestration.md`](../agent-harness-engineering/loop-and-orchestration.md) and
[`foundations.md`](../agent-harness-engineering/foundations.md). This file stays at the level of
*which architecture shape to pick and how sources name it*.

**Source-reading notes.** Several vendor pages carry no visible publication date; where a date is
inferred it is marked "(inferred)". Vendor self-interest is flagged inline with **[vendor]**. Numbers
are quoted with attribution; secondary-source numbers are marked as such.

---

## 1. Definitions

### 1.1 Workflow vs agent

The dominant definition comes from Anthropic's
[*Building effective agents*](https://www.anthropic.com/engineering/building-effective-agents)
(originally 2024-12-19; the page has since been updated and now names Claude Haiku 4.5 / Sonnet 4.5):

- **Workflow**: "systems where LLMs and tools are orchestrated through predefined code paths."
- **Agent**: systems where LLMs "direct their own processes and tool usage, keeping control over how
  they accomplish tasks." Operationally, "just LLMs using tools based on environmental feedback in a
  loop," with stopping conditions such as a max-iteration cap.
- Both are grouped under **"agentic systems."**

Other sources converge on the same line, with different wording:

| Source | Definition | Where the line is |
|---|---|---|
| [Anthropic](https://www.anthropic.com/engineering/building-effective-agents) (2024-12, updated) | Workflow = predefined code paths; agent = LLM directs its own process | Who decides the next step: code or model |
| [LangGraph docs](https://docs.langchain.com/oss/python/langgraph/workflows-agents) (undated) | "Workflows have predetermined code paths... Agents are dynamic and define their own processes and tool usage." | Same split, adopted nearly verbatim |
| [OpenAI practical guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf) (PDF created 2025-04-07) | Agents are "systems that independently accomplish tasks on your behalf." Needs (1) an LLM that manages workflow execution and decisions, including knowing when the workflow is complete, and (2) tools it selects dynamically within guardrails. Simple chatbots, single-turn LLMs, and sentiment classifiers "are not agents" | LLM must *control workflow execution*, not merely appear in it |
| [Hugging Face smolagents](https://huggingface.co/docs/smolagents/conceptual_guides/intro_agents) (undated) | "AI Agents are programs where LLM outputs control the workflow." | Continuous spectrum, not binary |
| [MongoDB](https://www.mongodb.com/resources/basics/artificial-intelligence/agentic-systems) (undated, ~2025 inferred) **[vendor]** | Systems that "use LLMs to decide the execution flow of an application with varying degrees of autonomy"; cites Andrew Ng that systems are "agent-like to different degrees" | Degree of decision authority delegated to the LLM |
| [Databricks](https://www.databricks.com/blog/agentic-systems-guide) (undated, 2026 footer) **[vendor]** | Agentic AI "autonomously plans, executes, and adapts multi-step work toward a goal"; distinguishes *AI agent* (goal-directed entity), *AI system* (wider architecture), and *agentic AI system* (agents plus infrastructure to run independently) | Goal-driven, acts through tools; contrasted with generative AI that only produces content |
| [IBM](https://www.ibm.com/think/topics/agentic-architecture) (undated) **[vendor]** | "Agentic architecture" = structure/design of agentic frameworks, modeled loosely on human agency (planning, action, memory, reflection). Notes "not all AI agents are agentic"; depends on orchestration framework sophistication | Architecture/orchestration capability, not the model |
| [HumanLayer 12-Factor Agents](https://github.com/humanlayer/12-factor-agents) (2025) | Many "AI Agents" are "mostly deterministic code with LLM steps inserted at key points" | Rejects "loop until done" as the production model |

### 1.2 The agentic spectrum

Three independent framings of "degrees of agency":

**(a) Code-level agency ladder** ([smolagents](https://huggingface.co/docs/smolagents/conceptual_guides/intro_agents)):

| Stars | What the LLM output controls | Name |
|---|---|---|
| none | Nothing about program flow | Simple processor |
| 1 | An if/else branch | Router |
| 2 | Which function runs, with what arguments | Tool call |
| 2 | Loop iteration and continuation | Multi-step agent |
| 3 | Starting another agentic workflow | Multi-agent |
| 3 | Writing its own tools / code | Code agent |

**(b) Pattern-ordered spectrum** ([MongoDB](https://www.mongodb.com/resources/basics/artificial-intelligence/agentic-systems)),
least to most autonomous: controlled flows, then routing and parallelization, then reflect/critique and
human-in-the-loop, then single agents, then multi-agent systems. MongoDB states fully autonomous
multi-agent workflows mean "higher costs, latency, and a system that is hard to debug."

**(c) Complexity-level ladder** ([Microsoft Azure Architecture Center](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns),
`ms.date` 2026-02-12, metadata updated 2026-09-21): direct model call, then single agent with tools,
then multi-agent orchestration. Guidance: "Use the lowest level of complexity that reliably meets your
requirements." Single agent with tools is called "often the right default for enterprise use cases."

### 1.3 Autonomy levels (user-role framings)

The most-cited explicit level framework is Feng, McDonald & Zhang,
[*Levels of Autonomy for AI Agents*](https://arxiv.org/abs/2506.12469) (arXiv 2025-06-14, revised
2025-07-28; also in the Knight First Amendment Institute essay series). It defines five levels by the
**role the human plays**: **L1 Operator** (user directs, agent acts on command), **L2 Collaborator**
(plan/delegate/execute jointly), **L3 Consultant** (agent leads, asks user for expertise/preferences),
**L4 Approver** (agent involves the user only in risky or pre-specified cases), **L5 Observer** (full
autonomy, user monitors). Per the abstract's framing, autonomy is "a deliberate design decision,
separate from its capability" and the operating environment; the paper floats "autonomy certificates"
for governance. (Level descriptions come from a search summary of the Knight/arXiv pages, not the full
paper.)

Other level-like framings:
- [arXiv 2601.03624](https://arxiv.org/abs/2601.03624) (Milosevic & Rabhi, Jan 2026, rev. May 2026)
  proposes three tiers: *LLM Agents* (task-specific automation), *Agentic AI* (adaptive goal-seekers),
  *Agentic Communities* (agents and humans coordinating via formal roles, protocols, governance).
- Databricks describes a progression traditional model, then generative AI, then agentic, and advises
  that a human "watch the first several hundred workflow executions" before autonomy is enabled, and
  that irreversible actions (deleting records, moving money) need explicit human approval **[vendor]**.
- No source reviewed gives a self-driving-car-style numbered scale endorsed by a major vendor. The
  Feng et al. levels are academic, and the vendor pages use spectra without numbering.

---

## 2. Core components

Sources list components at different granularities. A normalized view:

| Component | Anthropic | OpenAI | Google Cloud | IBM | Exabeam **[vendor]** | Databricks **[vendor]** |
|---|---|---|---|---|---|---|
| Model / reasoning | "Augmented LLM" | Model | AI model | Reasoning | Cognitive module (reasoning engine) | Reasoning layer |
| Tools / actions | Tools | Tools (data, action, orchestration) | Tools | Tool calling | Action module | Execution layer |
| Memory | Memory | (via state/context) | Session state and memory | Memory | Short-term + long-term memory, state management (Redis, Firestore) | Perception and memory layer |
| Retrieval / perception | Retrieval | Data tools | (tools) | Perception | Perception module | Perception layer |
| Planning | Agent plans (loop) | Instructions / routines | Model plans steps | Planning | Cognitive module | Reasoning layer |
| Instructions / policy | Prompt | **Instructions** (explicit guidelines and guardrails) | System prompt | (agency factors) | Goals, scope, guardrails | Governance |
| Orchestration | Orchestrator patterns | Run loop; manager/handoffs | Workflow agents, coordinator | Architecture type | Orchestration layer | Orchestration layer |
| Feedback / learning | Environment "ground truth" | Evals | (iteration patterns) | Learning, reflection | Feedback loop | Perceive-reason-act-learn loop |
| Human oversight | Return to human at checkpoints | Human intervention, guardrails | External human-review system | (not emphasized) | Human-in-the-loop pattern | Escalation |
| Security / identity | Sandbox + guardrails | Layered guardrails | Agent Gateway / Identity / Registry | (not emphasized) | (not emphasized) | Minimal permissions |

Notable framings:
- **OpenAI's minimal agent** = Model + Tools + Instructions
  ([guide](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)).
  Tools are split into **data**, **action**, and **orchestration** (agents as tools). The guide recommends
  prototyping with the most capable model everywhere to set a baseline, then swapping in smaller models
  "where possible."
- **Anthropic's building block** is the augmented LLM (LLM + retrieval + tools + memory), and its agents
  need environmental "ground truth" at each step.
- **IBM's agency factors** come from social-cognitive psychology (Bandura 2001): intentionality, forethought,
  self-reactiveness, self-reflectiveness. IBM also splits reasoning styles into reactive, deliberative,
  cognitive, and BDI (belief-desire-intention), an older multi-agent-systems lineage that no other source
  here uses.
- **Exabeam** has the richest classical decomposition (perception, cognition, short/long memory, action,
  orchestration, feedback, state management; three logical layers: reasoning, tool use, action).
- **Environment** is rarely a named component in the vendor lists. Anthropic (ground truth from tool
  results/code execution) and IBM (connecting to data sources and APIs) come closest.

Depth on how context, tools, and the loop are engineered is in [`../agent-harness-engineering/`](../agent-harness-engineering/).

---

## 3. Pattern catalogs

### 3.1 Per-vendor catalogs

**Anthropic** ([2024-12](https://www.anthropic.com/engineering/building-effective-agents)): five workflows plus the autonomous agent.
- *Prompt chaining* (fixed subtasks, gates between steps; "trades latency for accuracy"),
- *Routing* (classify, dispatch; example: easy queries to a small model),
- *Parallelization* (sectioning and voting),
- *Orchestrator-workers* (subtasks determined at runtime by the orchestrator),
- *Evaluator-optimizer* (generate/critique loop with clear criteria),
- *Agent* (LLM + tools + environment feedback in a loop).

**OpenAI** ([2025-04](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)): single-agent loop, then two multi-agent shapes.
- *Single-agent* with a "run" loop and exit conditions (final-output tool, no tool calls, error, max turns);
- *Manager (agents as tools)*: a central agent calls specialists via tool calls and keeps the user interface;
- *Decentralized (handoffs)*: peers pass one-way control, with full conversation state transferring.
- Includes a guardrails taxonomy (relevance, safety, PII, moderation, tool risk ratings, rules-based, output validation).

**Google Cloud** ([updated 2026-05-28](https://docs.cloud.google.com/architecture/choose-design-pattern-agentic-ai-system)): the broadest named list (ADK-oriented; **[vendor]** for ADK and Google's agent-infra products).
- Single agent;
- Multi-agent: sequential, parallel, loop, review-and-critique (generator/critic), iterative refinement, coordinator, hierarchical task decomposition, swarm;
- Plus ReAct, human-in-the-loop, and custom logic.
- Sequential/parallel/loop are *workflow agents* with "no model orchestration"; coordinator, hierarchical, and swarm are model-orchestrated.

**Microsoft** ([Azure Architecture Center](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)): five multi-agent orchestrations, each with aliases.
- *Sequential* (a.k.a. pipeline, prompt chaining),
- *Concurrent* (fan-out/fan-in, scatter-gather, map-reduce),
- *Group chat* (roundtable, multi-agent debate; includes maker-checker loops),
- *Handoff* (routing, triage, dispatch),
- *Magentic* (task-ledger-based adaptive planning).
- The page maps each to classical cloud design patterns (Pipes and Filters, Fan-out/Fan-in). **[vendor]** for Microsoft Agent Framework / Foundry.

**AWS** ([Prescriptive Guidance, dated July 2025 per search snapshot](https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-patterns/introduction.html)) **[vendor]**: three axes rather than one list.
- *Agent patterns* (reasoning, retrieval-augmented, coding, voice, workflow orchestrator, multi-agent),
- *LLM workflows* (prompt chaining, routing, parallelization, agent orchestration, evaluator reflect-refine loop),
- *Agentic workflow patterns* (task orchestration, subagent delegation, event-based coordination).
- It contrasts **workflow orchestration agents** (centralized coordinator, predefined sequence, enterprise process automation) with **multi-agent collaboration** (decentralized peers, "emergent" task distribution, negotiation), and ties each to AWS services (Bedrock, Step Functions, EventBridge, SQS). Takeaway text: "Agent patterns are composable... Agent design is contextual."

**MongoDB** ([undated](https://www.mongodb.com/resources/basics/artificial-intelligence/agentic-systems)) **[vendor]**: seven patterns: controlled flows, LLM as router, parallelization, reflect-and-critique, human-in-the-loop, agents, multi-agent (supervisor / network / custom). Advice: "Start with the simplest architecture that could work... add additional components only if there is clear evidence that they are needed."

**IBM** ([undated](https://www.ibm.com/think/topics/agentic-architecture)) **[vendor]**: classifies by *agent count* (single vs multi) and *control structure* (vertical/hierarchical, horizontal/peer, hybrid), plus reasoning styles. Names ReAct and ReWOO as linked topics only, without describing them.

**Exabeam** ([undated, 2026 footer](https://www.exabeam.com/explainers/agentic-ai/agentic-ai-architecture-types-components-best-practices/)) **[vendor, security product]**: five architecture types (single, multi, hierarchical, decentralized, hybrid) and four design patterns (ReAct, reflection/self-correction, multi-agent, human-in-the-loop).

**Databricks** ([undated](https://www.databricks.com/blog/agentic-systems-guide)) **[vendor]**: perceive-reason-act-learn loop, multi-step tool chaining, hierarchical orchestration, decentralized orchestration, hybrid, human-in-the-loop escalation, layered architecture.

**LangGraph** ([docs](https://docs.langchain.com/oss/python/langgraph/workflows-agents), undated) **[vendor]**: re-implements Anthropic's list (prompt chaining, parallelization, routing, orchestrator-worker with a `Send` API, evaluator-optimizer, agent). Largely a code-level mirror of Anthropic's taxonomy.

**Andrew Ng's four patterns** (2024; summarized via [DeepLearning.AI](https://www.deeplearning.ai/the-batch/check-out-our-course-on-how-to-build-ai-agents) and secondary posts): **Reflection, Tool Use, Planning, Multi-agent collaboration.** In his
[2024-03-20 Batch letter](https://www.deeplearning.ai/the-batch/how-agents-can-improve-llm-performance/),
HumanEval: GPT-3.5 zero-shot 48.1%, GPT-4 zero-shot 67.0%, GPT-3.5 inside an agent loop up to 95.1%; he says the gain from an iterative workflow "dwarfs" the GPT-3.5 to GPT-4 jump. (Ng's vantage: this is a *capability-primitive* taxonomy, not an architecture-shape taxonomy.)

**HumanLayer 12-Factor Agents** ([2025](https://github.com/humanlayer/12-factor-agents), by Dex Horthy) is a principles list rather than a pattern catalog: natural language to tool calls; own your prompts; own your context window; tools are structured outputs; unify execution and business state; launch/pause/resume APIs; contact humans with tool calls; own your control flow; compact errors into context; small focused agents; trigger from anywhere; stateless reducer. Author's claims (his observations, no data): teams reach "roughly 70-80% quality quickly" then find that insufficient for customer-facing use and must reverse-engineer their framework; few frameworks power production customer-facing agents.

**Academic catalogs.**
- [Liu et al., *Agent Design Pattern Catalogue*](https://arxiv.org/abs/2405.10467) (arXiv 2024-05, rev. 2024-11): 18 architectural patterns from a systematic literature review, with context/forces/trade-offs and a pattern-selection decision model.
- [Milosevic & Rabhi, arXiv 2601.03624](https://arxiv.org/abs/2601.03624): distributed-systems-derived patterns for "agentic communities."
- [arXiv 2601.19752](https://arxiv.org/abs/2601.19752v1) (Jan 2026): a systems-theoretic framework for agentic design patterns, arguing existing taxonomies are "convenience-based" (abstract seen via search snippet only).
- Practitioner roundups from 2026 (e.g. [SitePoint](https://www.sitepoint.com/the-definitive-guide-to-agentic-design-patterns-in-2026/), [Augment Code](https://www.augmentcode.com/guides/agentic-design-patterns), [aiagentrank](https://aiagentrank.io/blog/ai-agent-design-patterns-2026)) merge Ng + Anthropic lists into 9-12 pattern sets. These were seen only as search snippets and are SEO-adjacent; no independent evidence.

### 3.2 Side-by-side mapping

Rows are conceptual patterns; cells give each source's name. "-" means no equivalent named pattern.

| Concept | Anthropic | OpenAI | Google Cloud | Microsoft | AWS | MongoDB | IBM / Exabeam / Databricks | Ng |
|---|---|---|---|---|---|---|---|---|
| No agent; one call | "optimize single LLM calls" | "deterministic solution may suffice" | non-agentic for summarization/classification | Direct model call | - | - | - | - |
| Fixed pipeline | Prompt chaining | - | Sequential | Sequential | Prompt chaining (LLM workflow) | Controlled flows | Multi-step tool chaining (Databricks) | - |
| Classify and dispatch | Routing | (triage agent via handoff) | Coordinator (model-routed) | Handoff (alias: routing, triage) | Routing | LLM as router | - | - |
| Run branches at once | Parallelization (sectioning, voting) | - | Parallel | Concurrent (fan-out/fan-in) | Parallelization | Parallelization | Multi-agent parallel (IBM) | - |
| Generate, critique, repeat | Evaluator-optimizer | - | Review-and-critique; Iterative refinement; Loop | Group chat (maker-checker) | Evaluator reflect-refine | Reflect and critique | Reflection/self-correction (Exabeam) | Reflection |
| Single LLM + tools in a loop | Agent | Single-agent system (run loop) | Single agent; ReAct | Single agent with tools | Reasoning / coding agents | Agents | Single-agent (IBM/Exabeam) | Tool use |
| Central LLM decomposes, delegates | Orchestrator-workers | Manager (agents as tools) | Coordinator; Hierarchical task decomposition | Magentic (task ledger) | Workflow orchestration agents | Multi-agent: supervisor | Vertical/hierarchical | Planning + multi-agent |
| Peers pass control | (not a named pattern) | Decentralized (handoffs) | Swarm (dispatcher + any-to-any) | Handoff; Group chat | Multi-agent collaboration | Multi-agent: network | Horizontal/decentralized | Multi-agent collaboration |
| Mixed leader/peer | - | - | Custom logic | "Combining orchestration patterns" | "Composable" patterns | Custom control flow | Hybrid | - |
| Human checkpoint | "return to human for input" | Human intervention + tool safeguards | Human-in-the-loop | HITL gates in each pattern | (event-driven approvals) | Human in the loop | Human-in-the-loop escalation (Databricks, Exabeam) | - |
| Hand-written control flow | Use APIs, understand framework | Code-first vs declarative graphs | Custom logic | Deterministic routing (SDK vs no-code) | Step Functions | - | - | - |

Reading the table:
- **Prompt chaining = Sequential = Controlled flows.** Three names for the same thing. Microsoft lists "prompt chaining" as an alias.
- **Orchestrator-workers = Manager = Coordinator.** Same shape, three vendors. The distinguishing property
  (Anthropic): subtasks are *determined by the orchestrator at runtime* rather than pre-defined, unlike parallelization.
- **Routing is split.** Anthropic treats it as a workflow (one classification, no loop). Microsoft's "handoff" lists
  "routing" as an alias but means *ongoing* control transfer among agents, and OpenAI's decentralized pattern is the
  same mechanism. Google's "coordinator" is model-routed and can recur.
- **Group chat** (Microsoft) and **swarm** (Google) have no Anthropic/OpenAI analog. Anthropic and OpenAI do not
  catalog free-form many-agent debate as a pattern.
- **Magentic** (task ledger, plan-build-execute) is Microsoft-specific naming, close to Google's hierarchical
  decomposition and Anthropic's orchestrator-workers with persisted plans.

### 3.3 Reasoning-level patterns (inside a single agent)

These operate within one model's loop and are referenced by the vendor catalogs above.

| Pattern | Idea | Reported evidence | Notes |
|---|---|---|---|
| **ReAct** ([arXiv 2210.03629](https://arxiv.org/abs/2210.03629), 2022-10) | Interleave reasoning traces and actions (Thought/Action/Observation) | Abstract: beats imitation and RL methods "by an absolute success rate of 34% and 10%" on ALFWorld and WebShop with 1-2 in-context examples; less hallucination than CoT on HotpotQA/Fever with a Wikipedia API | Google Cloud: simpler and cheaper than multi-agent, reasoning trace aids debugging, but loop adds latency and "a bad tool result can propagate" |
| **Plan-and-Execute** ([LangChain, 2024-02-13](https://www.langchain.com/blog/planning-agents)) **[vendor]** | Planner LLM writes multi-step plan; executors run steps; re-plan | LangChain: avoids calling the big planner per tool call, "faster and cheaper" in principle | Tool calls remain serial |
| **ReWOO** (Reasoning WithOut Observations) | Planner emits plan with variable placeholders (#E2); worker substitutes results; solver composes answer | Per LangChain: no LLM call per task, each task gets only needed context | Still sequential |
| **LLMCompiler** ([arXiv 2312.04511](https://www.arxiv.org/pdf/2312.04511v3)) | Planner streams a DAG of tasks; scheduler runs independent tool calls in parallel; joiner decides finish/replan | Paper (via search summary): up to 3.7x latency speedup, 6.7x cost saving, ~9% accuracy gain, all versus ReAct (LangChain's blog cites 3.6x) | A third-party summary claiming 30-60% token savings for plan-and-execute could not be traced to LangChain's post; treat as unverified |
| **Reflexion** ([arXiv 2303.11366](https://arxiv.org/pdf/2303.11366)) | Agent stores verbal self-feedback in episodic memory across trials | NeurIPS 2023 poster: 91% pass@1 on HumanEval vs 80% for GPT-4 (replication paper reports 89.4% with GPT-4; secondary summary attributions conflict) | Requires a usable success/failure signal |
| **Tree of Thoughts** ([arXiv 2305.10601](https://arxiv.org/abs/2305.10601), 2023-05) | Explore and self-evaluate multiple reasoning branches; lookahead/backtrack | Abstract: Game of 24, GPT-4 CoT solves 4%, ToT 74% | Many model calls per problem; research-origin benchmark |

A neutral caveat: the Atlan comparison (2026-08, seen via snippet) states neither ReAct nor Plan-and-Execute
guarantees lower tokens, latency, or higher accuracy, since results are workload-dependent. Direct comparative
benchmarks across these patterns under identical conditions were not found in the sources reviewed (one candidate,
[arXiv 2601.02663](https://arxiv.org/html/2601.02663), compares one-shot vs plan-execute-replan on LangChain/LangGraph; results not read).

---

## 4. When-to-use guidance, by source

The consensus decision rule across sources is "simplest thing that works, escalate on evidence." Specific criteria differ.

| Source | Use a plain LLM call / workflow when... | Use an agent when... | Use multi-agent when... |
|---|---|---|---|
| **Anthropic** | Task decomposes into fixed subtasks; predictability needed; "many applications only need optimized single LLM calls with retrieval and in-context examples" | Open-ended; step count unpredictable; model decisions trusted; trusted environment; examples: SWE-bench coding agent, computer use | Orchestrator-workers when subtasks can't be predicted (multi-file code changes, multi-source research) |
| **OpenAI** | Rules suffice: "a deterministic solution may suffice" | Prior automation struggled: (1) complex decision-making (refund approval), (2) hard-to-maintain rule sets (vendor security reviews), (3) heavy unstructured data (insurance claims) | Only after maximizing single agent: complex branching logic in prompts, or "tool overload" (similarity/overlap, not raw count; "more than 15 well-defined, distinct tools" worked for some, "fewer than 10 overlapping" failed for others) |
| **Google Cloud** | Predictable or one-shot (summarization, translation, classification) | Single agent for multi-step tasks needing external data; start here | When one agent's tool count, complexity, or responsibilities become a bottleneck. Assess four dimensions: task characteristics, latency/performance, cost, human involvement |
| **Microsoft** | Direct model call when prompt engineering suffices | Single agent with tools, with iteration limits | Justified when single agent "can't reliably handle certain tasks due to prompt complexity, tool overload, or security requirements" (also distinct security boundaries) |
| **MongoDB** **[vendor]** | Controlled flows when deterministic, reliable outcomes are required | Unstructured tasks; high latency tolerance; non-deterministic output acceptable | Open-ended, experimental; unpredictable outcomes acceptable. Selection factors: reliability, workflow structure, task complexity, cost of bad outcomes |
| **Databricks** **[vendor]** | Generative AI for content, summarization, classification | Multi-step execution, real-time retrieval, external-system action | Hierarchical for stable, well-understood workflows; decentralized "when resilience matters more than auditability"; pilot a high-impact, low-risk, reversible workflow |
| **HF smolagents** | "Regularize towards not using any agentic behaviour" if deterministic workflow fits all queries (100% reliable) | When the predetermined workflow "falls short too often" | - |
| **Anthropic research (2025-06)** | - | - | Valuable tasks, heavily parallelizable, information exceeding one context window, many complex tools; **not** when all agents need the same context, many dependencies, or most coding tasks |
| **Cognition (2025-06)** | - | Single-threaded linear agent with continuous context as default; add a compression model for long tasks | Argues multi-agent is currently "fragile" |

Additional specifics:
- **Pilot sizing** (Databricks): a pilot at 100 executions/day may hit new failure modes at 10,000/day.
- **Model tiering** (Anthropic, OpenAI, Microsoft, MongoDB all agree): route easy work to smaller, cheaper models;
  Microsoft: "Not every agent requires the most capable model."
- **Maturity of the market** (Gartner, June 2025, via press coverage): over 40% of agentic AI projects will be
  canceled by end of 2027 citing cost, unclear value, weak risk controls; only ~130 of thousands of "agentic AI
  vendors" are real ("agent washing"). Analyst forecast, not a measured result.

---

## 5. Trade-offs

| Dimension | What the sources say |
|---|---|
| **Latency** | Chaining "trades latency for accuracy" (Anthropic). Parallel patterns cut wall-clock time but not cost (Google, Microsoft). Loops and ReAct add latency per iteration (Google). Anthropic's research system reports parallel subagents and tool calls "cut research time by up to 90% on complex queries." Microsoft warns of multi-hop latency and slow-to-converge magentic runs |
| **Cost** | Anthropic (2025-06): agents "use about 4x more tokens than chat," multi-agent "about 15x"; token usage explained 80% of variance on BrowseComp in their analysis. Microsoft: magentic is "most variable" and hard to predict; concurrent can spike; recommends per-agent token monitoring. Google: coordinator, hierarchical, and swarm each raise calls and cost. Anthropic's own multi-agent post is **[vendor]** evidence (Anthropic sells the models consumed) |
| **Quality / performance** | Anthropic: Opus 4 lead + Sonnet 4 subagents "outperformed single-agent Claude Opus 4 by 90.2%" on an internal research eval (company-run eval, not independent). Ng: iterative agent loops can exceed a model-generation jump on HumanEval. Google: iterative refinement/critique improves reliability at the cost of cycles |
| **Reliability / compounding error** | Anthropic: autonomy brings "compounding errors"; recommends sandboxed testing and guardrails. Microsoft: multi-agent reintroduces distributed-systems failures (node failure, message loss, cascading errors); advises timeouts, retries, circuit breakers, output validation between hops, checkpointing. Google: loops risk infinite cycles without exit conditions; swarm may fail to converge. Microsoft handoff risk: "infinite handoff loops" |
| **Controllability / auditability** | Hierarchical: predictability and clear accountability but leader as bottleneck/single point of failure (IBM, Exabeam). Decentralized: resilience but harder oversight (Databricks), coordination overhead (IBM). Microsoft group chat: transparency and auditability via single accumulating thread |
| **Debuggability** | Single agent "simpler to debug and test" (Microsoft). ReAct traces aid debugging (Google). MongoDB: autonomous multi-agent is "hard to debug." Anthropic: frameworks "can hide prompts and responses" |
| **Context management** | Google calls per-agent context isolation/compression "context engineering." Microsoft: context windows "can grow rapidly" across agents; compact between hops. Cognition: share full traces, not messages. (Deep treatment in the [harness survey](../agent-harness-engineering/context-engineering.md).) |
| **Security / governance** | Microsoft: least privilege per agent, security trimming in every agent, guardrails at input/tool call/tool response/output. Google: Agent Gateway, Identity, Registry **[vendor]**. OpenAI: layered guardrails, tool risk ratings (low/medium/high by read/write, reversibility, financial impact) |

---

## 6. Where the sources agree and disagree

### Strong agreement
1. **Start simple.** Every major source (Anthropic, OpenAI, Google, Microsoft, MongoDB, smolagents) says to use
   the lowest-complexity architecture that works and escalate on measured evidence.
2. **Single agent first.** OpenAI ("maximize a single agent's capabilities first"), Google ("start here"),
   Microsoft ("often the right default"), and Anthropic's "simplest solution possible" all converge.
3. **The workflow/agent distinction is "who controls the next step."** Even sources using other vocabulary
   (HF's "LLM outputs control the workflow", MongoDB's "degree of agenticity") map onto it.
4. **The core workflow set is shared.** Prompt chaining, routing, parallelization, orchestrator/manager, and
   generate-critique loops appear in nearly every catalog under different names (section 3.2).
5. **Human checkpoints are a first-class pattern** in six of the catalogs reviewed.
6. **Patterns compose.** Microsoft ("Don't try to make one workflow fit into a single pattern"), AWS
   ("composable"), MongoDB, and Anthropic (patterns can be combined) agree.
7. **Frameworks are optional and can obscure.** Anthropic (start with APIs), 12-Factor ("own your control flow"),
   OpenAI (code-first over declarative graphs), Exabeam (understand limits).

### Disagreements and tensions
1. **Is the autonomous loop the production default?** Anthropic defines agents as the loop and positions them for
   open-ended problems. 12-Factor Agents argues the "loop until done" pattern is *not* what good production agents
   use, preferring deterministic code with embedded LLM steps (author observation, no data). OpenAI/Microsoft
   treat the loop as the standard single-agent baseline.
2. **Multi-agent: when and how much.** Anthropic (2025-06) reports a large gain for orchestrator-worker research
   but excludes shared-context and coding tasks. Cognition (2025-06) argues multi-agent collaboration is currently
   fragile because actions carry implicit decisions, and recommends single-threaded context. Both are consistent
   if read as domain-dependent (parallelizable read-heavy research vs write-heavy coupled coding), but the
   headline messages differ. OpenAI is more neutral and offers two multi-agent shapes.
3. **Graphs vs code.** LangGraph models flows as explicit state graphs; OpenAI's guide criticizes declarative
   graphs as "cumbersome" as workflows grow dynamic, favoring code-first. Both are **[vendor]** positions
   aligned with their SDKs.
4. **Peer-to-peer patterns: first-class or niche?** Google (swarm), Microsoft (group chat), AWS (multi-agent
   collaboration with "emergent" intelligence) present decentralized collaboration as a pattern; Anthropic does
   not catalog it; Google itself calls swarm "the most complex and costly."
5. **Naming collisions.** "Routing" (workflow step vs inter-agent control transfer), "orchestrator" (model-driven
   vs deterministic code, since Google's "workflow agents" orchestrate without a model), "agent" (IBM: not all
   agents are agentic), "multi-agent" (OpenAI counts a manager with agents-as-tools; Anthropic calls the same
   shape orchestrator-workers inside "workflows").
6. **Autonomy taxonomy.** Only the academic Feng et al. work gives numbered, human-role-based levels. Vendors
   use spectra, and the Databricks piece declines to define levels at all.
7. **Evidence quality.** Almost none of the vendor catalogs provide benchmarks for pattern selection. Numeric
   evidence comes from research papers (ReAct, ToT, Reflexion, LLMCompiler), Anthropic's internal evals, and
   Ng's HumanEval letter, none of which compare the vendor patterns head to head.

---

## 7. Caveats on this survey

- OpenAI's guide was read from the PDF (created 2025-04-07); later OpenAI guidance (e.g. newer SDK docs) was not reviewed.
- The Anthropic essay predates later Anthropic writing on harnesses and long-running agents (see the sibling survey).
- AWS, Ng's original posts, Feng et al.'s level descriptions, the reasoning-pattern papers other than ReAct/ToT/arXiv catalogs, and the 2026 practitioner roundups were consulted via search summaries or abstracts rather than full text.
- Undated vendor pages (IBM, Exabeam, MongoDB, Databricks, smolagents, LangGraph) may have been revised since indexing.

---

## Sources

- [read] https://www.anthropic.com/engineering/building-effective-agents
- [read] https://docs.cloud.google.com/architecture/choose-design-pattern-agentic-ai-system
- [read] https://www.ibm.com/think/topics/agentic-architecture
- [read] https://www.exabeam.com/explainers/agentic-ai/agentic-ai-architecture-types-components-best-practices/
- [read] https://www.mongodb.com/resources/basics/artificial-intelligence/agentic-systems
- [read] https://www.databricks.com/blog/agentic-systems-guide
- [read] https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns
- [read] https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf
- [read] https://docs.langchain.com/oss/python/langgraph/workflows-agents
- [read] https://github.com/humanlayer/12-factor-agents
- [read] https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-patterns/introduction.html
- [read] https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-patterns/workflow-orchestration-agents.html
- [read] https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-patterns/multi-agent-collaboration.html
- [read] https://huggingface.co/docs/smolagents/conceptual_guides/intro_agents
- [read] https://www.deeplearning.ai/the-batch/how-agents-can-improve-llm-performance/
- [read] https://www.anthropic.com/engineering/multi-agent-research-system
- [read] https://cognition.com/blog/dont-build-multi-agents
- [read] https://www.langchain.com/blog/planning-agents
- [read] https://arxiv.org/abs/2506.12469
- [read] https://arxiv.org/abs/2305.10601
- [read] https://arxiv.org/abs/2210.03629
- [read] https://arxiv.org/abs/2405.10467
- [read] https://arxiv.org/abs/2601.03624
- [summary] https://knightcolumbia.org/content/levels-of-autonomy-for-ai-agents-1
- [summary] https://www.deeplearning.ai/the-batch/check-out-our-course-on-how-to-build-ai-agents
- [summary] https://www.arxiv.org/pdf/2312.04511v3
- [summary] https://arxiv.org/pdf/2303.11366
- [summary] https://arxiv.org/html/2601.02663
- [summary] https://arxiv.org/abs/2601.19752v1
- [summary] https://www.sitepoint.com/the-definitive-guide-to-agentic-design-patterns-in-2026/
- [summary] https://www.augmentcode.com/guides/agentic-design-patterns
- [summary] https://aiagentrank.io/blog/ai-agent-design-patterns-2026
- [summary] https://atlan.com/know/ai-agent/react-vs-plan-and-execute-agent-architecture/
- [summary] https://www.outlookbusiness.com/deeptech/artificial-intelligence/over-40-of-agentic-ai-projects-will-be-scrapped-by-2027-says-gartner (press coverage of Gartner, June 2025)
- [summary] https://neurips.cc/virtual/2023/poster/70114
- [indexed] https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-patterns/llm-workflows.html
- [indexed] https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-frameworks/frameworks.html
- [indexed] https://www.fast.io/resources/agentic-ai-design-patterns.md
- [indexed] https://arxiv.org/pdf/2607.09586
- [indexed] https://www.aigl.blog/levels-of-autonomy-for-ai-agents/
