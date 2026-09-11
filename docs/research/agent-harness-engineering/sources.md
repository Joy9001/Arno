# Sources

Everything consulted for this survey, 2026-09-07. Marked by how directly it was used.

- **[read]** — fetched and read in full
- **[summary]** — content obtained via search summary, not fetched directly
- **[indexed]** — encountered as a citation in a read source; recorded, not consulted

## Starting point

- **[read]** [Addy Osmani — *Agent Harness Engineering*](https://addyosmani.com/blog/agent-harness-engineering/)
  — the survey's entry point. Synthesises the field: definition, primitives, patterns, failure
  modes, and credits the practitioners carrying the discourse.

## Primary vendor engineering

- **[read]** [Anthropic — *Effective Context Engineering for AI Agents*](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
  — context rot, system-prompt structure, just-in-time retrieval, compaction, note-taking,
  sub-agents. The densest single source on context.
- **[read]** [Anthropic — *Writing Effective Tools for Agents*](https://www.anthropic.com/engineering/writing-tools-for-agents)
  — naming, granularity, return shapes, errors, token efficiency; the Slack/Asana measured result.
- **[read]** [Anthropic — *Building Effective Agents*](https://www.anthropic.com/engineering/building-effective-agents)
  — the workflow-vs-agent taxonomy and five workflow patterns. Still the best framing for how much
  autonomy a problem needs.
- **[read]** [Anthropic — *Demystifying Evals for AI Agents*](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
  — the eval vocabulary, grader types, pass@k vs pass^k, 20-50 task sizing, the CORE-Bench story.
- **[read]** [Anthropic — *How We Contain Claude*](https://www.anthropic.com/engineering/how-we-contain-claude)
  — three isolation patterns, the 93% approval / 84% reduction figures, injection measurements,
  and candid production gaps.
- **[read]** [OpenAI — *Harness engineering: leveraging Codex in an agent-first world*](https://openai.com/index/harness-engineering/)
  — Ryan Lopopolo, 2026-02-11. The zero-human-code experiment: ~1M lines, ~1,500 PRs, 3.5 PRs per
  engineer per day, AGENTS.md-as-map, mechanical architecture enforcement, golden principles and
  garbage collection. Richest primary source in the survey. **Retrieved via browser — openai.com
  returns 403 to WebFetch.**
- **[read]** [OpenAI — *Unrolling the Codex Agent Loop*](https://openai.com/index/unrolling-the-codex-agent-loop/)
  — Michael Bolin, 2026-01-23. Turn/thread structure, prompt construction and role priority, the
  layered AGENTS.md walk with 32 KiB cap, prompt-cache prefix discipline and what breaks it,
  stateless-for-ZDR trade, the `/responses/compact` endpoint, and the MCP sandbox gap. **Retrieved
  via browser.**
- **[read]** [LangChain — *The Anatomy of an Agent Harness*](https://www.langchain.com/blog/the-anatomy-of-an-agent-harness)
  — Viv Trivedy's seven-component decomposition and the `Agent = Model + Harness` framing.
- **[read]** [ZenML LLMOps DB — *Codex CLI Architecture and Agent Loop Design*](https://www.zenml.io/llmops-database/building-production-ready-ai-agents-openai-codex-cli-architecture-and-agent-loop-design)
  — secondary analysis of the Codex loop; corroborates the primary above.
- **[read]** [arXiv 2604.18071 — *Architectural Design Decisions in AI Agent Harnesses*](https://arxiv.org/abs/2604.18071)
  — abstract only. 70 systems, five design dimensions, five archetypes. One of the few non-vendor
  systematic studies; independently corroborates file-persistent context strategies.
- **[read]** [ZenML LLMOps DB — *Context Engineering for Production SRE Agents*](https://www.zenml.io/llmops-database/context-engineering-for-production-sre-agents)
  — secondary write-up of Microsoft's Azure SRE agent. Source of the 100+→5 tools, 50+→handful of
  agents, and four-handoff findings. Used because the Microsoft original returns HTTP 403.
- **[summary]** [Microsoft — *Context Engineering: Lessons from Building Azure SRE Agent*](https://techcommunity.microsoft.com/blog/appsonazureblog/context-engineering-lessons-from-building-azure-sre-agent/4481200)
  — the primary version. **Not directly readable** (403). The "Intent Met 45%→75%" figure comes via
  search summary and the Osmani article, not from the original — treat with slightly more caution
  than the other Microsoft numbers.
- **[indexed]** [Microsoft — *Harness Engineering for Azure SRE Agent: the Agent Self-Improvement Loop*](https://techcommunity.microsoft.com/blog/appsonazureblog/the-agent-that-investigates-itself/4500073)
- **[summary]** [Anthropic — *Mitigating Prompt Injection in Browser Use*](https://www.anthropic.com/research/prompt-injection-defenses)
- **[summary]** [Anthropic — *Trustworthy Agents in Practice*](https://www.anthropic.com/research/trustworthy-agents)
  — the five-principle framework.

- **[read]** [Anthropic — *Beyond permission prompts: making Claude Code more secure and autonomous*](https://claude.com/blog/beyond-permission-prompts-making-claude-code-more-secure-and-autonomous)
  — sandbox mechanics: bubblewrap/seatbelt, subprocess coverage, domain-restricting proxy,
  `claude --sandbox`, credentials-never-enter-the-sandbox for the cloud variant. **Contains no
  measured data** — the 93%/84% figures come from *How we contain Claude*. Lives on `claude.com`,
  not `anthropic.com/engineering`, which is why the first pass 404'd.

## Academic sources read

Second-pass additions. All abstract-only unless noted.

- **[read]** [arXiv 2608.23550 — *When "Do Not" Is Not Deny*](https://arxiv.org/abs/2608.23550)
  — 481 config files; only **4-16%** of stated security rules have an enforced control (strictest
  4.4%, CI 2.6-6.7%); extraction captured 66.3% of eligible rules; two blind practitioner
  validators. Corrects a flat "~4%" this survey previously carried from an index entry.
- **[read]** [arXiv 2606.10106 — *What makes a harness a harness*](https://arxiv.org/abs/2606.10106)
  — constitutive definition, four necessary elements (loop, tool interface, context, controls),
  validated against six real systems plus edge cases.
- **[read]** [arXiv 2606.20683 — *From Question Answering to Task Completion: A Survey on Agent
  System and Harness Design*](https://arxiv.org/abs/2606.20683) — the six runtime components and
  the extended evolution ladder ending in model-harness co-evolution.
- **[read]** [arXiv 2606.25447 — *The Interplay of Harness Design and Post-Training*](https://arxiv.org/abs/2606.25447)
  — harness as trainable dimension; minimal-harness agents degrade sharply under tool-environment
  shift. Extended ALFWorld methodology.
- **[read]** [arXiv 2606.09498 — *Self-Harness: Harnesses That Improve Themselves*](https://arxiv.org/abs/2606.09498)
  — weakness mining, proposal, regression validation; **up to 132% relative gains** across three
  benchmarks and three model families. Non-vendor.
- **[read]** [arXiv 2604.18071 — *Architectural Design Decisions in AI Agent Harnesses*](https://arxiv.org/abs/2604.18071)
  — 70 systems, five dimensions, five archetypes.
- **[indexed]** arXiv 2603.28052 *Meta-Harness*; arXiv 2606.12882 *HarnessBridge*; arXiv 2603.03329
  *AutoHarness* — the automated-harness cluster, not read.

## Indexes and taxonomies

- **[read]** [ai-boost/awesome-harness-engineering](https://github.com/ai-boost/awesome-harness-engineering)
  — the field's index. **Re-fetched in full from the raw README**, closing the first pass's
  truncation. Caveat recorded in [`ecosystem.md`](ecosystem.md): early sections return specific,
  checkable items (named projects, arXiv IDs); later sections degrade into generic concept names
  that read as summariser padding. Category names are trustworthy; the generic item lists are not.
- **[indexed]** RUCAIBox/awesome-agent-harness — sibling list, not consulted
- **[indexed]** [yyyujintang/Awesome-Agent-Memory-Papers](https://github.com/yyyujintang/Awesome-Agent-Memory-Papers)
- **[indexed]** [Shichun-Liu/Agent-Memory-Paper-List](https://github.com/Shichun-Liu/Agent-Memory-Paper-List) — from *Memory in the Age of AI Agents: A Survey*

## Secondary and practitioner writing

- **[summary]** [LangChain — *Tuning the Harness, Not the Model*](https://developer.nvidia.com/blog/create-a-langchain-deep-agents-harness-profile-for-nvidia-nemotron-3-ultra-to-improve-performance/)
  — the "within one point at ~1/10 cost" result. NVIDIA co-published; both parties benefit from the
  finding, so weight accordingly.
- **[summary]** [DeepEval — *Eval Harness: what it is and why you should care*](https://deepeval.com/blog/what-is-an-eval-harness)
  — source of the judge-model-capability observation.
- **[summary]** [Atlan — *What Is Harness Engineering AI?*](https://atlan.com/know/what-is-harness-engineering/)
- **[summary]** [Augment Code — *Harness Engineering for AI Coding Agents*](https://www.augmentcode.com/guides/harness-engineering-ai-coding-agents)
- **[summary]** [Vishal Mysore — *Harness Engineering for AI Agents in 2026*](https://medium.com/@visrow/harness-engineering-for-ai-agents-in-2026-114fcb8edf9e)
- **[summary]** [Braintrust — *Agent Observability: the complete guide for 2026*](https://www.braintrust.dev/articles/agent-observability-complete-guide-2026)
- **[summary]** [Arize — *14 best AI agent observability tools in 2026*](https://arize.com/blog/best-ai-observability-tools-for-autonomous-agents-in-2026/)
- **[summary]** [yunwei37 — *Runtime Observability and Enforcement for Opaque AI Agents with eBPF*](https://medium.com/@yunwei356/runtime-observability-and-enforcement-for-opaque-ai-agents-with-ebpf-beyond-sandboxes-and-6f4eb21edc7f)
  — the opaque-harness argument.
- **[summary]** [Letta — *Memory Models: Towards Agents That Learn*](https://www.letta.com/blog/towards-agents-that-learn/)
  — "memory more valuable than the model." Letta sells memory infrastructure.
- **[summary]** [Red Hat — *From Context to Dreams: Architecting Memory for AI Agents*](https://next.redhat.com/2026/06/01/from-context-to-dreams-architecting-memory-for-ai-agents/)

## Referenced but not consulted

Named in read sources, recorded for follow-up. Not verified.

**Foundations:** OpenAI *Harness Engineering*; OpenAI *Unrolling the Codex Agent Loop*; OpenAI
*Run Long-Horizon Tasks with Codex*; Anthropic *Harness Design for Long-Running Application
Development*; Anthropic *Beyond Permission Prompts*; Anthropic *2026 Agentic Coding Trends Report*
(source of the "5+ points" claim); Anthropic *Agent Harness Design: 3 Patterns*; Martin Fowler
*Harness Engineering*; Birgitta Böckeler *Harness engineering for coding agent users*; Red Hat
*Structured Workflows*; OpenAI *A Practical Guide to Building AI Agents*.

**Loop and orchestration:** ReAct (arXiv 2210.03629); LATS (arXiv 2310.04406); Plan-and-Act (arXiv
2503.09572); LangChain *Choosing the Right Multi-Agent Architecture*; GitHub *Multi-Agent Workflows
Often Fail*; *Agents Learn Their Runtime* (arXiv 2603.01209); *Real-Time Deadlines Reveal Temporal
Awareness Failures* (arXiv 2601.13206); *Task-Decoupled Planning for Long-Horizon Agents* (arXiv
2601.07577); microsoft/TaskWeaver.

**Context:** LLMLingua (Microsoft Research); *Active Context Compression* (arXiv 2601.07190);
*A-RAG* (arXiv 2602.03442); Anthropic *Compaction*; Anthropic *Prompt Caching*.

**Tools and skills:** *AutoHarness* (arXiv 2603.03329); *SkillTester* (arXiv 2603.28815); *Scaling
Parallel Tool Calling* (arXiv 2602.07359); *TopoCurate* (arXiv 2603.01714); *Design Patterns for
Deploying AI Agents with MCP* (arXiv 2603.13417); MCP *Tool Annotations as Risk Vocabulary*;
Anthropic *Code Execution with MCP*; outlines; instructor.

**Memory:** MemSkill (arXiv 2602.02474); *Cross-Scenario Generality of Agentic Memory Systems*
(arXiv 2606.04315); SkillForge (arXiv 2604.08618); ICLR 2026 MemAgents workshop proposal.

**Security:** *Preventing Prompt Injection with Type-Directed Privilege Separation* (arXiv
2509.25926); *Agent Operating Systems* (arXiv 2606.01508); *Agent libOS* (arXiv 2606.03895); *SoK:
Agentic Skills* (arXiv 2602.20867).

**Harness meta:** *Agentic Harness Engineering: Observability-Driven Automatic Evolution* (arXiv
2604.25850); *HarnessBridge* (arXiv 2606.12882); *Harness as an Asset: CAAF* (arXiv 2604.17025);
*The Interplay of Harness Design and Post-Training* (arXiv 2606.25447); *What Makes a Harness a
Harness* (arXiv 2606.10106); *Code as Agent Harness* (arXiv 2605.18747).

## Gap-closing log

**First pass gaps, all now resolved or explained:**

| Gap | Status |
|---|---|
| OpenAI's harness writing never read directly | **Closed.** Both primaries retrieved via browser after WebFetch 403s |
| awesome-list fetch truncated | **Closed.** Full raw README fetched; reliability caveat recorded in [`ecosystem.md`](ecosystem.md) |
| Almost no arXiv read | **Closed.** Six papers read (abstracts), covering definition, survey, post-training interplay, self-improvement, architecture corpus, and the enforcement-gap study |
| Benchmark claims unverified | **Closed enough to correct two errors** — see below |
| Anthropic *Beyond Permission Prompts* unread | **Closed.** It lives on `claude.com`, not `anthropic.com/engineering` |
| Microsoft primary unreadable | **Not closed — and won't be.** The page sits behind an Azure WAF bot check. Working around bot detection was declined, so the 45%→75% figure stays second-hand and is flagged as the weakest number in this survey |

## Corrections made in the second pass

Recorded rather than silently fixed, because both were repeated confidently in the first pass:

1. **"Harness setup swings benchmarks 5+ percentage points"** → the report's actual wording is
   *"several percentage points — sometimes more than the leaderboard gap between top models."*
   The specific "5+" was not supported.
2. **"Only ~4% of natural-language rules are backed by controls"** → the study reports **4-16%**,
   strictest measure **4.4% (CI 2.6-6.7%)**, with the extraction capturing 66.3% of eligible rules.
3. **Terminal-Bench attribution.** The Top 30 → Top 5 result is attributed to **Cursor's** team in
   the write-ups found during verification; Osmani's article attributes the framing differently.
   Attribution is soft. The cleaner datapoint found during verification —
   **Opus at 93% in Cursor vs 77% in Claude Code, a 16-point harness-only gap** — is now the
   headline figure in [`foundations.md`](foundations.md).

## What remains open

1. **Microsoft's primary source** — inaccessible by policy, as above.
2. **All arXiv reading is abstract-level.** No full paper was read. Methodologies, limitations, and
   effect-size caveats are therefore unverified; the 132% and 12-23% style figures should be read as
   claimed, not confirmed.
3. **Anthropic's *Harness Design for Long-Running Application Development*** was still not located
   directly, only cited.
4. **The awesome-list long tail** (77%, 98%, 120x, 252x token-reduction claims) remains entirely
   vendor-self-reported and partly suspect given the padding issue.
5. **Vendor-source bias is structural.** Most quantitative claims come from organisations describing
   their own products. Six academic sources now partly offset this, but the qualitative convergence
   across independent teams is still more trustworthy than any single figure.
