# Agentic System Design: Research Frontier and Framework Landscape

Field survey conducted **2026-10-18**. One slice of a wider "Agentic / GenAI System Design" survey. It is deliberately independent of any project: it records what exists, not what anyone should adopt. Sibling files cover protocols in depth and, in [`../agent-harness-engineering/ecosystem.md`](../agent-harness-engineering/ecosystem.md), a shorter pass over frameworks; this file goes beyond those rather than repeating them.

**Source tags** (used in the Sources section): **[read]** = the page itself was fetched and read in this session; **[summary]** = only a search-engine or aggregator summary was seen, so numbers are secondhand; **[indexed]** = known from a listing (e.g. the awesome-list) but not opened. Claims resting on [summary] sources are marked "(secondhand)" in the text. Many leaderboards below are volatile; treat every leaderboard number as "as reported around the date shown."

---

# Part A: Research frontier

## A1. The Awesome-Agentic-System-Design taxonomy

[gtzheng/Awesome-Agentic-System-Design](https://github.com/gtzheng/Awesome-Agentic-System-Design) is a curated, interview-prep-flavoured index. The landing page is thin; the substance is in `resources/papers.md`, `resources/frameworks.md`, `docs/evaluation.md` and `UPDATES.md` (all read). Its structure:

| Layer | Contents |
|---|---|
| Learning paths | Fundamentals (architecture, context, memory) -> production (tools/protocols, evals, safety) -> interview prep -> recent developments -> implementation tools |
| Core topics (7) | (1) architecture and orchestration; (2) context, retrieval, memory; (3) tools, skills, protocols (MCP, A2A, Agent Skills); (4) evaluation and benchmarks; (5) production, reliability, safety (durable execution, isolation, approvals, injection resistance, latency, cost); (6) interview prep; (7) case studies (support, research, coding, incident-response agents) |
| Papers, grouped by *design question* | Foundations / Surveys / Orchestration / Context and harnesses / Evaluation / Safety and attribution |
| Frameworks | Grouped "by responsibility" (own the loop, explicit workflow control, delegate, run code/computer, operate a service, adopt a hosted runtime) |

Notable design choice: it classifies **by question asked** ("which workflow and topology should we use?") rather than by technique, and repeatedly stresses recovery semantics (checkpoint behaviour, approval persistence, duplicate-effect prevention) as the discriminator between frameworks.

**Most-cited papers per category on the list** (arXiv year in parentheses):

| Category | Papers |
|---|---|
| Foundations | [ReAct](https://arxiv.org/abs/2210.03629) (2022); [Reflexion](https://arxiv.org/abs/2303.11366) (2023) |
| Surveys | [Agentic LLMs survey](https://arxiv.org/abs/2503.23037); [LLM Agent: Methodology, Applications, Challenges](https://arxiv.org/abs/2503.21460); [Self-Evolving Agents](https://arxiv.org/abs/2507.21046); [Agentic RAG](https://arxiv.org/abs/2501.09136); [Multi-Agent Collaboration Mechanisms](https://arxiv.org/abs/2501.06322); [interoperability protocols MCP/ACP/A2A/ANP](https://arxiv.org/abs/2505.02279); [Agentic AI Frameworks: Architectures, Protocols, Design Challenges](https://arxiv.org/abs/2508.10146); [Architectures for Building Agentic AI](https://arxiv.org/abs/2512.09458); [Agentic Design Patterns: A System-Theoretic Framework](https://arxiv.org/abs/2601.19752) (2026) |
| Orchestration | [AFlow](https://arxiv.org/abs/2410.10762); [Multi-Agent Design (prompts + topologies)](https://arxiv.org/abs/2502.02533); [Weak-for-Strong meta-agent](https://arxiv.org/abs/2504.04785); [FlowReasoner](https://arxiv.org/abs/2504.15257); [MetaAgent (FSM-based)](https://arxiv.org/abs/2507.22606); [Feedback in test-time scaling of agentic workflows](https://arxiv.org/abs/2504.01931); [Budget-Aware Tool Use](https://arxiv.org/abs/2511.17006); [Towards a Science of Scaling Agent Systems](https://arxiv.org/abs/2512.08296) |
| Context and harnesses | [A-MEM](https://arxiv.org/abs/2502.12110); [Production-grade agentic workflows guide](https://arxiv.org/abs/2512.08769); 2026 harness-engineering papers (see caveat) |
| Evaluation | [GAIA](https://arxiv.org/abs/2311.12983); [Survey on Evaluation of LLM-based Agents](https://arxiv.org/abs/2503.16416); [MultiAgentBench](https://arxiv.org/abs/2503.01935); ClawBench, LoopsBench, PerspectiveGap (2026) |
| Safety / attribution | [AgenTracer](https://arxiv.org/abs/2509.03312); [Toward a Safe Internet of Agents](https://arxiv.org/abs/2512.00520); [Towards a Science of AI Agent Reliability](https://arxiv.org/abs/2602.16666) (2026: consistency, robustness, predictability, safety beyond mean accuracy) |

**Caveat:** several 2026 entries on the list carry arXiv IDs (e.g. 2609.xxxxx, 2610.xxxxx, "SWE-Bench Pro Verified", "HERMES") that I did not open; they are reproduced from the list only. The list itself warns not to conflate "SWE-Bench Pro Verified" with SWE-bench Verified. `UPDATES.md` (read) dates key platform events: Microsoft Agent Framework 1.0 GA 2026-04-02; OpenAI Agent Builder deprecated 2026-06-03 with shutdown 2026-11-30; an MCP spec revision 2026-07-28; A2A 1.0.

## A2. Systems research on serving agentic workloads

The core observation across this literature: request-level LLM serving (vLLM-style) discards program structure. An agent is a *program* of dependent LLM calls interleaved with tool latency, and its prefix grows monotonically, which makes KV-cache reuse and program-aware scheduling the main levers.

| Work | Lever | Reported result (authors' claims unless noted) |
|---|---|---|
| [SGLang / RadixAttention](https://arxiv.org/abs/2312.07104) (Dec 2023) | Radix-tree prefix cache across calls; structured-program runtime | Up to 6.4x throughput vs prior inference systems on agent control, JSON decoding, multi-turn chat |
| [LLMCompiler](https://arxiv.org/abs/2312.04511) (ICML 2024) | Planner emits a DAG of function calls; parallel executor | Up to 3.7x latency, 6.7x cost, ~9% accuracy vs ReAct |
| [Parrot](https://arxiv.org/abs/2405.19888) (OSDI 2024) | "Semantic Variables" expose data-flow between requests to the server | Up to an order of magnitude on LLM-app workloads |
| [Autellix](https://arxiv.org/abs/2502.13965) (Feb 2025) | Program-level scheduling: PLAS (single-threaded), ATLAS (multi-threaded/DAG, critical path) over vLLM; fights head-of-line blocking | 4-15x program throughput at equal latency vs vLLM (self-reported; no independent replication found) |
| [Continuum](https://arxiv.org/abs/2511.02230) (ICLR 2026) | Pins KV cache with a TTL sized to predicted tool-call duration; program-level FCFS to avoid "scheduling bubbles" | Lower average job completion time on SWE-Bench and BFCL with Llama-3.1 8B/70B (secondhand); authors criticise InferCept for ignoring multi-turn horizon |
| InferCept (2024) | Per-pause choice among GPU retention, CPU swap, recompute | Not verified here; characterised via Continuum/TraceLab text |
| [Sutradhara](https://arxiv.org/abs/2601.12967) (Jan 2026) | Overlaps tool execution with next prefill; dispatches tools during decoding | Authors report tool calls are 30-80% of first-response latency in their analysis (secondhand) |
| [AsyncFC / future-based async function calling](https://arxiv.org/pdf/2605.15077) (May 2026) | Model keeps decoding while calls run; unresolved results as symbolic futures; no fine-tuning | Lower end-to-end time, accuracy preserved (secondhand) |
| Partial-argument decoding (Google defensive publication, May 2026) | Start a tool once its first arguments are decoded | Not peer reviewed |

Practical shape of the findings, hedged: (i) gains scale with tool latency relative to decode time and vanish on strictly serial dependency chains; (ii) side-effecting tools make speculation unsafe; (iii) a Berkeley-led workload characterisation ([TraceLab](https://arxiv.org/pdf/2606.30560), 2026) exists for coding-agent traces (indexed only). Provider-side prompt caching (hosted APIs) is the commercial analogue of RadixAttention but its pricing/TTL terms are vendor-specific and out of scope here.

## A3. Automated agent design, prompt/program optimisation, self-improvement

| Work | Idea | Numbers / status |
|---|---|---|
| [ADAS](https://arxiv.org/abs/2408.08435) (Aug 2024) | "Meta Agent Search": a meta-agent writes new agents *as code*, keeps an archive; claims discovered agents beat hand-designed ones and transfer across domains and models | Programming languages are Turing-complete, so the search space includes prompts, tools, workflows |
| [AFlow](https://arxiv.org/abs/2410.10762) (Oct 2024) | MCTS over code-represented workflows with operator nodes | +5.7% avg over SOTA baselines on six benchmarks; smaller models beating GPT-4o at 4.55% of its inference cost on specific tasks |
| [DSPy](https://github.com/stanfordnlp/dspy) | "Programming, not prompting": declarative modules + compilers/optimisers over prompts and weights | MIT; optimiser research lineage includes MIPROv2 (2024) and GEPA |
| [GEPA](https://arxiv.org/abs/2507.19457) (ICLR 2026 oral) | Reflective, Pareto-based prompt evolution from natural-language trajectory diagnosis | v1 claimed +10% avg over GRPO (up to 20%) with up to 35x fewer rollouts; **v2 (Feb 2026) revised to +6% avg across six tasks**, still up to 35x fewer rollouts; >10% over MIPROv2 (secondhand). Cite the version. |
| [Darwin Gödel Machine](https://arxiv.org/abs/2505.22954) (May 2025) | Archive-based open-ended self-modification of a coding agent's own tools/workflow (frozen model) | SWE-bench 20.0% -> 50.0%, Polyglot 14.2% -> 30.7% over 80 iterations; ~2 weeks and significant API cost per run; still below best closed-source (secondhand) |
| [Huxley-Gödel Machine](https://proceedings.iclr.cc/paper_files/paper/2026/hash/821d20219c2f14850af1b5220f0ed13f-Abstract-Conference.html) (ICLR 2026, arXiv 2510.21614) | Selects which self-modification to expand by "clade metaproductivity" (descendants' performance) | Beats DGM-style baselines with fewer CPU-hours (secondhand) |
| Gödel Agent (2024) | Self-referential agent rewriting its own code | ~$15 vs ~$300 for Meta Agent Search on a GPT-3.5 variant (secondhand, via a 2026 survey) |
| [Self-Evolving Agents survey](https://arxiv.org/abs/2507.21046); [Self-Improvements in Modern Agentic Systems](https://arxiv.org/pdf/2607.13104) (2026) | Taxonomies of *what* changes (prompts, memory, tools, workflow, weights) and *when* | Indexed |

**Reading across:** automated design has moved from "search workflows for fixed benchmarks" (ADAS, AFlow) toward "evolve the harness of a deployed-style coding agent" (DGM, HGM) and "optimise instructions/tool descriptions from traces" (GEPA; also now productised, see B2 Optimization services at AWS and Microsoft). Recurrent caveats: search cost, benchmark overfitting (the optimiser sees the metric), and safety of self-modifying code. The ADAS authors explicitly flag safety.

## A4. Multi-agent evidence and surveys

**Is multi-agent worth it? The evidence is conditional, and the field disagrees.**

| Source | Position / finding |
|---|---|
| Anthropic, [multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) (2025-06-13) | Opus 4 lead + Sonnet 4 subagents beat single-agent Opus 4 by 90.2% on an internal research eval. Agents use ~4x the tokens of chat; multi-agent ~15x. On BrowseComp, three factors explain 95% of variance, token usage alone 80%. Engineering lessons: resumable state, rainbow deployments, tracing, synchronous lead-waits-for-workers bottleneck |
| Cognition, [Don't Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents) (2025-06-12) | Share full traces, not messages; actions carry implicit decisions that conflict across parallel agents. Default to a single-threaded agent; compress history for long tasks. Notes Claude Code subagents do not work in parallel with the main agent |
| Google/MIT et al., [Scaling Agent Systems](https://arxiv.org/abs/2512.08296) (Dec 2025) | 180 configurations on Finance-Agent, BrowseComp-Plus, PlanCraft, Workbench. All multi-agent variants degraded sequential-reasoning tasks by 39-70%; centralised coordination +80.9% on parallelisable tasks; returns diminish once single-agent baseline exceeds ~45%; independent agents amplify errors 17.2x vs 4.4x centralised; a predictive model picks the best strategy for 87% of held-out configs (R^2 = 0.513) (secondhand abstract) |
| Cemri et al., [MAST: Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657) (2025) | 14 failure modes in 3 clusters (system design, inter-agent misalignment, task verification), from 150 traces annotated (kappa 0.88), later 1,600+ traces over 7 frameworks (secondhand). Failures stem largely from design and coordination, not only model weakness |

The two camps reconcile on a read/write split: parallel *reading/exploration* scales; parallel *writing* needs one decision-maker. Treat "90.2%" and "15x" as vendor-internal evals, not benchmarks.

**Surveys, 2023-2026 (by usefulness as maps):**
- [A Survey on LLM-based Autonomous Agents](https://arxiv.org/abs/2308.11432) (Wang et al., Aug 2023; v7 Mar 2025): construction / applications / evaluation. [Rise and Potential of LLM-Based Agents](https://arxiv.org/abs/2309.07864) (Xi et al., 2023) and [LLM-based Multi-Agents: Progress and Challenges](https://arxiv.org/abs/2402.01680) (Guo et al., 2024) are the other early anchors (indexed, not opened).
- 2025: Agentic LLMs ([2503.23037](https://arxiv.org/abs/2503.23037)); LLM Agent methodology ([2503.21460](https://arxiv.org/abs/2503.21460)); Multi-Agent Collaboration Mechanisms ([2501.06322](https://arxiv.org/abs/2501.06322)); Self-Evolving Agents ([2507.21046](https://arxiv.org/abs/2507.21046)); Deep Research systematic survey ([2512.02038](https://arxiv.org/pdf/2512.02038)); Evaluation of LLM-based Agents ([2503.16416](https://arxiv.org/abs/2503.16416)).
- 2026: [Agentic Design Patterns](https://arxiv.org/abs/2601.19752); [From QA to Task Completion: Agent System and Harness Design](https://arxiv.org/pdf/2606.20683); The Agent Loop survey (SSRN, preprint). Preprint-grade; verify before citing.

## A5. Benchmarks for agentic capability

| Benchmark | What it measures | Reference points (date) | Caveats |
|---|---|---|---|
| **SWE-bench Verified** | Resolve real Python repo issues, executable tests | Aggregators in 2026 show 80%+ for top models; one reports Verified ~81% vs Pro ~46% for the same model (Mar 2026, secondhand) | Reported as contaminated/saturated by aggregators; always state scaffold. Not the same as SWE-bench Pro |
| **[SWE-Bench Pro](https://labs.scale.com/leaderboard/swe_bench_pro_public)** (Scale) | Long-horizon tasks; 1,865 tasks, 41 repos: 731 public (GPL repos), 276 private (18 startup codebases), 858 held-out | Launch: GPT-5 23.3%, Opus 4.1 23.1% public; private 14.9% / 17.8%. Leaderboard (fetched): Muse Spark 1.1 61.5 +/- 3.1, gpt-5.4 (xHigh) 59.1 +/- 3.56 | Entries use mini-swe-agent, not comparable to earlier SWE-Agent numbers. One aggregator cites a July 2026 OpenAI audit estimating ~30% of the public split broken (single secondary source; unverified) |
| **[tau2-bench](https://arxiv.org/abs/2506.07982)** (Sierra) | Tool-agent-user dialogue under domain policy; adds *dual-control* Telecom (user also has tools; Dec-POMDP) | Paper: large drops from no-user to dual-control mode. A "tau3-bench" with banking/voice reported (secondhand) | Simulated users; no reliable 2026 top score found, so none quoted |
| **[GAIA](https://arxiv.org/abs/2311.12983)** | General assistant, tool use; 466 questions (300 answers private) | At release (2023): humans 92% vs GPT-4+plugins 15%. Princeton HAL mirror: top ~74.5% (model unnamed; secondhand) | Dataset exposure, tool availability |
| **OSWorld / OSWorld-Verified** | Real-desktop computer use | Human baseline 72.36%; aggregators claim 83-90% top entries by mid/late 2026 (unverified; leader disagrees across sources). OSWorld 2.0 best entry ~44% (secondhand) | Not interchangeable with OSWorld-Verified; nearing saturation |
| **[WebArena](https://arxiv.org/abs/2307.13854)** | Self-hosted web tasks | Launch: best GPT-4 agent 14.41% vs human 78.24%. 2026 aggregators: ~65-74% (leader disagrees) | Scaffolded systems vs bare models mixed |
| **[TheAgentCompany](https://arxiv.org/abs/2412.14161)** | Simulated software company, real internal tools | Dec 2024 abstract: best agent completed 30% autonomously | Old number; superseded by newer models |
| **Terminal-Bench 2.0** | 89 Dockerised terminal tasks | Aggregators (Sep 2026): top ~82-83%. One secondary analysis: Opus 4.6 scored 58.0% in Claude Code's harness vs 74.7% in Terminus-KIRA vs 81.8% in ForgeCode, i.e. harness swings exceed model gaps | The tbench.ai site now shows a "Terminal-Bench 4.0" page; a successor exists |
| **METR time horizon** | Task length (human-expert time) at which an agent succeeds with 50% (or 80%) probability | See below | See below |

**METR time horizon** ([paper](https://arxiv.org/abs/2503.14499), NeurIPS 2025; [live page](https://metr.org/time-horizons/)):
- Original claim (Mar 2025): 50% horizon of Claude 3.7 Sonnet ~50 min; doubling about every **seven months since 2019**, "though the trend may have accelerated in 2024."
- **Time Horizon 1.1** (2026-01-29): suite 170 -> 228 tasks; 8h+ tasks 14 -> 31 (only 5 of the 31 have measured human baselines); moved to Inspect. Doubling: 196.5 days stitched since 2019; **130.8 days (CI 107-161) since 2023; 88.6 days since 2024**. Opus 4.5 = 320 min (CI 170-729), GPT-5 = 214 min.
- METR says measurements above 16 hours are unreliable with the current suite, and as of 2026-09-08 the page "is no longer actively updated." Later frontier figures (~12h public frontier, 16-20h best shared model) come from a Manifold summary of a METR risk report and are unverified here.
- **Hype guardrails (METR's own FAQ):** horizon is task *difficulty measured in human time*, not how long an agent runs autonomously; tasks are self-contained software/ML/cyber work and underestimate real context requirements; the human times likely overestimate expert time; 1,000 one-hour problems is not a 1,000-hour task; messy or holistically-scored work shows lower performance. Doubling-time estimates swing between ~3 and ~7 months depending on the fit window, so quoting a single number without the window is misleading.

---

# Part B: Framework and platform landscape (as of October 2026)

## B1. Open-source frameworks and SDKs

| Framework | Abstraction level | State / persistence | Multi-agent | Language | Notable trade-offs |
|---|---|---|---|---|---|
| **[LangGraph](https://github.com/langchain-ai/langgraph)** (LangChain Inc., MIT) | Low-level graph/state-machine runtime; LangChain 1.0's `create_agent` sits on top (secondhand) | Checkpointed durable execution, resume, human-in-the-loop state edit, short- and long-term memory; deployment via LangSmith Deployment | Subgraphs, supervisor and swarm patterns (library level) | Python, JS (LangGraph.js) | Explicit control and replay vs. boilerplate and state-schema design; observability/deploy path nudges toward LangSmith. Version 1.2.x by Aug 2026 (secondhand) |
| **[OpenAI Agents SDK](https://github.com/openai/openai-agents-python)** | Code-first loop with runner; agents, handoffs, agents-as-tools, guardrails | SDK sessions (e.g. SQLAlchemy/Redis backends) or Responses conversation state; you own storage; no durable-execution engine documented on the repo page | Handoffs and agents-as-tools | Python; JS/TS counterpart | Small surface, provider-agnostic (Responses and Chat Completions, 100+ LLMs); recovery semantics are yours. Sandbox agents, realtime/voice agents. Hosted sibling: managed **Agents API** (managed Codex harness). Visual Agent Builder deprecated 2026-06-03, shutdown 2026-11-30 |
| **[Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview)** (Anthropic) | Claude Code's loop, tools and context management as a library (it runs the Claude Code binary) | Sessions with resume/fork; project `.claude/` skills, memory, plugins loaded from disk | Subagents; hooks; MCP | Python, TypeScript (other languages via CLI subprocess `-p`) | Rich built-ins (file, shell, search, permissions) vs a heavy, coding-agent-shaped runtime tied to Claude models. Terms bar third-party claude.ai login. Hosted sibling: **Managed Agents** |
| **[Google ADK](https://adk.dev/2.0/)** 2.0 | Agent + graph workflow runtime; 2.0 turns agents, tools, functions into graph nodes; also "dynamic workflows" in plain code | Session/event store (custom SQL schemas need migration in 2.0); human-in-the-loop pause/resume | Coordinator + subagents; graph; Sequential/Parallel/Loop agents deprecated in TS | Python (2.0 GA 2026-05-19), Go (06-30), TypeScript (08-21); Java: no 2.0 GA date on the page | Breaking 1.x -> 2.0 changes (pin `google-adk~=1.0` to stay); a secondary source warns 2.0 code on 1.x databases risks data loss |
| **[Microsoft Agent Framework](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-at-build-2026-announce/)** (AutoGen + Semantic Kernel successor) | Agents + workflows + an "Agent Harness" layer (compaction, file memory, task tracking, plan/execute, skills, background subagents, tool-approval rules) | Workflows; hosted agents in Foundry get per-session persistent filesystems | Handoff orchestration (declared allowed transfers), background-agent fan-out | .NET, Python | **1.0 GA 2026-04-02**; AutoGen repo says maintenance mode. Shell execution .NET-only; CodeAct (Hyperlight micro-VM) is alpha with a vendor-benchmarked 52.4% time / 63.9% token reduction. AG2 is a separate fork, not the same project |
| **CrewAI** ([repo](https://github.com/crewAIInc/crewAI)) | Role-based **Crews** (autonomous teams) plus event-driven **Flows** (deterministic control, state) | Flow state; enterprise AMP adds deployment, observability, governance | Core feature: crews with delegation | Python | Fast to prototype; role/crew abstraction hides prompts. Secondary sources: v1.14.x (May 2026), four 2026 CVEs patched; adoption statistics are vendor-sourced and inconsistent |
| **LlamaIndex Workflows** ([workflows-py](https://github.com/run-llama/workflows-py)) | Event-driven async steps (emit/consume events); branching, loops, parallelism in plain Python | Persist/resume runs from file or database; `llama-agents-server` exposes workflows as REST with streaming, persistence, HITL; `llamactl` deploys to LlamaParse or AWS AgentCore | Composition of workflows | Python (page shows no TypeScript) | Strong document/RAG heritage; step/event model is flexible but less visual than graph tools |
| **[Pydantic AI](https://github.com/pydantic/pydantic-ai)** (MIT) | Typed agents: structured outputs, typed dependency injection, Pydantic Graph for typed control flow | Durable execution via Temporal, DBOS, Prefect, Restate, Lambda (repo page says "nine engines"; docs list four; discrepancy unresolved) | Via graph/agent delegation; no crew abstraction | Python | Validation-first and provider-neutral; durability is delegated to a workflow engine rather than built in. MCP, AG-UI, Pydantic Evals included |
| **[Mastra](https://github.com/mastra-ai/mastra)** | TS app framework: agents, graph workflows (`.then/.branch/.parallel`), model routing (40+ providers) | Suspend/resume HITL from stored execution state; conversation and "Observational Memory"; evals, MCP | Agents and workflows composed; supervisor-style | TypeScript | Natural fit for Next.js/Node stacks; Apache-2.0 core with source-available `ee/` code needing a licence for production. ~28.9k stars |
| **[smolagents](https://github.com/huggingface/smolagents)** (Hugging Face) | Minimal library (~1,000 lines core); `CodeAgent` emits actions as Python code, `ToolCallingAgent` JSON | Little persistence; stateless by design | Hierarchies of managed agents | Python | Code-as-action reduces call count (research-backed claim from CodeAct line, not verified here) but `LocalPythonExecutor` is explicitly *not* a sandbox; use E2B, Modal, Blaxel or Docker |
| **[DSPy](https://github.com/stanfordnlp/dspy)** (MIT) | Not an orchestrator: declarative signatures/modules compiled by optimisers (MIPROv2, GEPA); can build agent loops | None; programs are artefacts you optimise and ship | Compose modules | Python | Orthogonal to others (can sit inside LangGraph/ADK); needs a metric and dataset, optimiser runs cost real inference |

Also on the lists but not profiled: AWS **Strands Agents** (model-driven loop), **Agno**, **AgentScope**, **AG2**, Dify (visual).

**Cross-framework patterns (my synthesis, not a source claim):**
1. Convergence on *graph/workflow + agent loop in one runtime*: ADK 2.0, Microsoft Agent Framework, Mastra, LangGraph, and LlamaIndex Workflows all now offer both explicit control flow and autonomous loops.
2. **Durability is the unsettled seam.** LangGraph and Mastra build it in; Pydantic AI outsources it; the OpenAI SDK and smolagents leave it to the caller.
3. **Harness features are migrating into SDKs** (compaction, file memory, plan/execute, subagents, skills, approval rules): Microsoft's "Agent Harness," the Claude Agent SDK, and OpenAI's managed Codex harness are the same idea in different packages.
4. **Everyone ships MCP; most ship A2A or an equivalent**; the differentiator is state and deployment, not tool plumbing.

## B2. Managed platforms

| Platform | What it is | State / persistence | Multi-agent / framework stance | Trade-offs and flags |
|---|---|---|---|---|
| **[Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)** | Modular services usable independently: Harness (managed loop), Runtime (serverless, session-isolated, long-running), Memory (short + long term), Gateway (APIs/Lambda -> MCP tools), Identity, Code Interpreter, Browser, Observability (OTEL), Evaluations, Optimization (A/B-tested prompt/tool-description recommendations), Policy (Cedar-compatible), Registry, Payments (x402/MPP) | Memory service + isolated microVM sessions | Framework- and model-agnostic: CrewAI, LangGraph, LlamaIndex, Google ADK, OpenAI Agents SDK, Strands; MCP and A2A | Widest unbundled service menu; IAM/quota surface to learn; the doc's service list is long enough that maturity per service varies (not assessed) |
| **Google Gemini Enterprise Agent Platform** (docs now use "Agent Runtime"; **inferred** successor of Vertex AI Agent Engine; the page does not state the rename) | Managed runtime with traffic/revisions, Sessions, **Memory Bank** (long-term facts/preferences), Code Execution sandboxes (snapshots, computer use), Agent Gateway, Private Service Connect | Managed sessions and Memory Bank, IAM Conditions | ADK, A2A, LangChain, LangGraph, AG2, LlamaIndex, custom | Naming churn is itself a risk; Memory Bank processing happens in the model endpoint's region |
| **[Microsoft Foundry Agent Service](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/overview)** (page updated 2026-10-15) | Three agent types: **prompt agents** (config only), **voice prompt agents** (preview parts), **hosted agents** (container or zip; Agent Framework, LangGraph, OpenAI Agents SDK, Anthropic Agent SDK, GitHub Copilot SDK, custom). Toolboxes behind one managed MCP endpoint; Entra agent identity; publishing to Teams/Copilot | Session-level persistence for hosted agents; bring-your-own storage, Search and Cosmos DB for conversation state | Any framework via hosted agents; A2A v1.0 GA (v0.3 preview) | Strong enterprise identity/networking (per-session VM-isolated sandbox with BYO VNet); agent optimizer is preview; heavy Azure coupling |
| **Databricks Agent Bricks** | Docs describe an "agent developer platform": Agent Runtime for any framework, AgentKit durable execution, Sandbox for agent-written code, agent memory/sessions, MLflow Tracing and LLM-judge evaluation, Unity Gateway for models/MCP servers/skills, SQL AI Functions | Memory and sessions; durable runs | Framework-agnostic runtime | The fetched page did **not** mention the earlier "use-case builders with auto-optimisation" framing (Knowledge Assistant etc.) that I recall from 2025; the product has apparently been repositioned. Unverified recollection; check release notes |
| **Vendor-hosted harnesses** | OpenAI **Agents API** (managed Codex harness, compaction, hosted/self-hosted/no sandbox); Anthropic **Managed Agents** ([architecture](https://www.anthropic.com/engineering/managed-agents)) | Anthropic: append-only session log outside the context window; stateless harness resumable via `wake(sessionId)`; sandbox as a tool with credentials in a vault/proxy | Orchestration inside the hosted harness | Anthropic reports decoupling cut p50 time-to-first-token ~60% and p95 >90% (sessions not needing a sandbox no longer wait). Lock-in is to the model vendor's harness |

Convergent design visible in the platforms: separate **session log / harness / sandbox**, per-session isolation (microVM or VM), identity per agent, a tool gateway speaking MCP, OTEL tracing, and an "evaluate -> optimise" loop (AgentCore Optimization, Foundry agent optimizer) that productises the GEPA-style trace-driven prompt/tool-description improvement from A3.

## B3. Framework versus no framework

- **Anthropic, [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)** (2024-12-19): start by using LLM APIs directly because "many patterns can be implemented in a few lines of code"; frameworks "often create extra layers of abstraction that can obscure the underlying prompts," which makes debugging harder. Taxonomy: *workflows* (predefined code paths: prompt chaining, routing, parallelisation, orchestrator-workers, evaluator-optimizer) versus *agents* (LLM directs its own process).
- Counter-pressure: the same vendors now ship SDKs and managed harnesses (Claude Agent SDK, Managed Agents), i.e. the advice targets *thin orchestration frameworks*, not harnesses that bundle sandboxing, permissions, compaction and resumability, which are costly to rebuild.
- Awesome-list framing: choose by **responsibility** (who owns loop, state, recovery, deployment) rather than by brand; evaluate with a crash-resume scenario (retrieve -> proposed write -> approval -> worker crash -> resume without duplicate effect).
- Practical failure modes cited in the evidence above: opaque prompts/abstractions (Anthropic), migration churn (ADK 1.x -> 2.0 breakage; AutoGen -> Agent Framework; OpenAI Agent Builder shutdown; Vertex "Agent Engine" renames), and coordination bugs that frameworks make easy to create (MAST).
- Evidence that the *harness* (not the framework brand) moves benchmark numbers: the Terminal-Bench spread in A5 (secondary), and Anthropic's finding that token budget explains 80% of BrowseComp variance.

## B4. Protocol layer (brief; sibling file covers depth)

- **MCP** (tools/resources/context for agents): per the awesome-list updates page, a **2026-07-28 spec revision** moves toward a stateless core, "Multi Round-Trip Requests," routing/cache changes and Tasks as an extension, and an updated roadmap landed 2026-08-22. Every framework in B1 lists MCP support; AgentCore Gateway and Foundry Toolboxes both wrap tools as MCP endpoints.
- **A2A** (agent-to-agent): the spec documents 1.0 behaviour and changes from 0.3; Foundry states A2A v1.0 is GA (v0.3 preview); AgentCore Runtime and Google's runtime list A2A.
- **Agent Skills** (portable instruction bundles with progressive loading, [agentskills.io](https://agentskills.io/specification)) and **AG-UI** (agent-to-UI event stream; Pydantic AI, Mastra integrations) fill adjacent layers.

## B5. Open questions and uncertainty flags

- Leaderboard numbers (SWE-bench Pro, OSWorld, Terminal-Bench, WebArena, GAIA) come largely from aggregators that disagree on leaders and splits; none is authoritative here. Re-check against the owners' pages before citing.
- LangGraph's 1.0 release date and exact feature list, CrewAI version/CVE counts and adoption stats, and the Google rename chain are secondhand or inferred.
- Autellix, Parrot, SGLang and LLMCompiler numbers are authors' own and measured on 2023-2025 hardware/models; serving stacks (vLLM, SGLang) have since absorbed parts of the ideas (prefix caching is now standard), so headline multipliers likely overstate today's marginal gain. This is my inference, not a sourced claim.
- METR: single task family (software/ML/cyber), wide confidence intervals, and a ceiling near 16 h; the "doubling time" is window-dependent.
- Multi-agent value is task-shape dependent; no source provides a cross-vendor controlled comparison on production workloads.

---

## Sources

1. [Awesome-Agentic-System-Design README](https://github.com/gtzheng/Awesome-Agentic-System-Design) [read]
2. [Awesome list: resources/papers.md](https://raw.githubusercontent.com/gtzheng/Awesome-Agentic-System-Design/main/resources/papers.md) [read]
3. [Awesome list: resources/frameworks.md](https://raw.githubusercontent.com/gtzheng/Awesome-Agentic-System-Design/main/resources/frameworks.md) [read]
4. [Awesome list: UPDATES.md](https://raw.githubusercontent.com/gtzheng/Awesome-Agentic-System-Design/main/UPDATES.md) [read]
5. [Awesome list: docs/evaluation.md](https://raw.githubusercontent.com/gtzheng/Awesome-Agentic-System-Design/main/docs/evaluation.md) [read]
6. [SGLang, arXiv 2312.07104](https://arxiv.org/abs/2312.07104) [read]
7. [LLMCompiler, arXiv 2312.04511](https://arxiv.org/abs/2312.04511) [read]
8. [Parrot, arXiv 2405.19888](https://arxiv.org/abs/2405.19888) [read]
9. [Autellix, arXiv 2502.13965](https://arxiv.org/abs/2502.13965) [summary]
10. [Continuum, arXiv 2511.02230](https://arxiv.org/abs/2511.02230) [summary]
11. [Sutradhara, arXiv 2601.12967](https://arxiv.org/html/2601.12967v1) [summary]
12. [AsyncFC, arXiv 2605.15077](https://arxiv.org/pdf/2605.15077) [summary]
13. [TraceLab, arXiv 2606.30560](https://arxiv.org/pdf/2606.30560) [indexed]
14. [ADAS, arXiv 2408.08435](https://arxiv.org/abs/2408.08435) [read]
15. [AFlow, arXiv 2410.10762](https://arxiv.org/abs/2410.10762) [read]
16. [GEPA, arXiv 2507.19457](https://arxiv.org/abs/2507.19457) [summary]
17. [Darwin Gödel Machine, arXiv 2505.22954](https://arxiv.org/abs/2505.22954) [summary]
18. [Huxley-Gödel Machine, ICLR 2026](https://proceedings.iclr.cc/paper_files/paper/2026/hash/821d20219c2f14850af1b5220f0ed13f-Abstract-Conference.html) [summary]
19. [Self-Improvements in Modern Agentic Systems, arXiv 2607.13104](https://arxiv.org/pdf/2607.13104) [indexed]
20. [Hierarchical Self-Improvement, arXiv 2608.08466](https://arxiv.org/pdf/2608.08466) [indexed]
21. [DSPy repo](https://github.com/stanfordnlp/dspy) [read]
22. [Anthropic multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) [read]
23. [Cognition, Don't Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents) [read]
24. [Scaling Agent Systems, arXiv 2512.08296](https://arxiv.org/abs/2512.08296) [summary]
25. [MAST, arXiv 2503.13657](https://arxiv.org/abs/2503.13657) [summary]
26. [LLM-based Autonomous Agents survey, arXiv 2308.11432](https://arxiv.org/abs/2308.11432) [summary]
27. [Rise and Potential of LLM-Based Agents, arXiv 2309.07864](https://arxiv.org/abs/2309.07864) [indexed]
28. [LLM-based Multi-Agents survey, arXiv 2402.01680](https://arxiv.org/abs/2402.01680) [indexed]
29. [Deep Research survey, arXiv 2512.02038](https://arxiv.org/pdf/2512.02038) [indexed]
30. [Agent System and Harness Design survey, arXiv 2606.20683](https://arxiv.org/pdf/2606.20683) [indexed]
31. [METR time horizons page](https://metr.org/time-horizons/) [read]
32. [METR paper, arXiv 2503.14499](https://arxiv.org/abs/2503.14499) [read]
33. [METR Time Horizon 1.1 post](https://metr.org/blog/2026-1-29-time-horizon-1-1/) [read]
34. [Manifold/anatol summaries of METR 2026 figures](https://buttondown.com/anatol/archive/are-ai-time-horizons-still-doubling/) [summary]
35. [SWE-Bench Pro leaderboard (Scale)](https://labs.scale.com/leaderboard/swe_bench_pro_public) [read]
36. [SWE-Bench Pro / Verified aggregators (morphllm, benchlm, etc.)](https://www.morphllm.com/swe-bench-pro) [summary]
37. [OSWorld aggregators](https://presenc.ai/research/osworld-computer-use-leaderboard-2026) [summary]
38. [Terminal-Bench 2.0 aggregators](https://www.morphllm.com/terminal-bench-2) [summary]
39. [Terminal-Bench site](https://www.tbench.ai/leaderboard/terminal-bench/2.0) [read]
40. [TheAgentCompany, arXiv 2412.14161](https://arxiv.org/abs/2412.14161) [read]
41. [tau2-bench, arXiv 2506.07982](https://arxiv.org/abs/2506.07982) [read]
42. [WebArena, arXiv 2307.13854](https://arxiv.org/abs/2307.13854) [read]
43. [GAIA, arXiv 2311.12983](https://arxiv.org/abs/2311.12983) [read]
44. [GAIA/WebArena 2026 aggregators](https://agentmarketcap.ai/blog/2026/04/11/composite-agentic-benchmark-view-2026) [summary]
45. [Microsoft Agent Framework at Build 2026](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-at-build-2026-announce/) [read]
46. [OpenAI agents docs overview](https://developers.openai.com/api/docs/guides/agents) [read]
47. [OpenAI Agents SDK repo](https://github.com/openai/openai-agents-python) [read]
48. [Claude Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) [read]
49. [Anthropic Managed Agents architecture](https://www.anthropic.com/engineering/managed-agents) [read]
50. [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) [read]
51. [LangGraph repo](https://github.com/langchain-ai/langgraph) [read]
52. [LangGraph release/version trackers](https://www.piwheels.org/project/langgraph/) [summary]
53. [Google ADK 2.0 docs](https://adk.dev/2.0/) [read]
54. [ADK Go 2.0 announcement](https://developers.googleblog.com/en/announcing-adk-go-20/) [indexed]
55. [Pydantic AI repo](https://github.com/pydantic/pydantic-ai) [read]
56. [Pydantic AI durable execution docs](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/) [summary]
57. [Mastra repo](https://github.com/mastra-ai/mastra) [read]
58. [smolagents repo](https://github.com/huggingface/smolagents) [read]
59. [LlamaIndex workflows-py](https://github.com/run-llama/workflows-py) [read]
60. [CrewAI overview aggregators](https://atlan.com/know/ai-agent/what-is-crewai/) [summary]
61. [Amazon Bedrock AgentCore docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html) [read]
62. [Google Agent Runtime / Gemini Enterprise Agent Platform docs](https://docs.cloud.google.com/agent-builder/agent-engine/overview) [read]
63. [Microsoft Foundry Agent Service overview](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/overview) [read]
64. [Databricks Agent Bricks docs](https://docs.databricks.com/aws/en/generative-ai/agent-bricks/) [read]
65. [A2A specification](https://a2a-protocol.org/latest/specification/) [indexed]
66. [MCP 2026-07-28 revision post](https://blog.modelcontextprotocol.io/posts/2026-07-28/) [indexed]
67. [Agent Skills specification](https://agentskills.io/specification) [indexed]
