# Sources

Everything consulted for this survey, 2026-10-18. Each topic file ends with its own full,
tagged bibliography; this page records how the starting sources were used and how to read the
tags. Across the seven files there are roughly **340 unique URLs**.

- **[read]**: fetched and read in full
- **[summary]**: content obtained via a search summary only, not fetched directly. Numbers resting on these are secondhand.
- **[indexed]**: encountered as a citation or listing in a read source, recorded but not consulted

## Starting sources (all 13 read)

| Source | Used in | Notes |
|---|---|---|
| [System Design Handbook — *Agentic System Design*](https://www.systemdesignhandbook.com/guides/agentic-system-design/) | [`genai-system-architecture.md`](genai-system-architecture.md) | Interview-oriented component walkthrough |
| [System Design Handbook — *Generative AI System Design Interview*](https://www.systemdesignhandbook.com/guides/generative-ai-system-design-interview/) | [`genai-system-architecture.md`](genai-system-architecture.md) | Its cost example does not reconcile with list prices; flagged in the file |
| [genaisystemdesign.com](https://www.genaisystemdesign.com/) | [`case-studies.md`](case-studies.md) | 22-post index; the "case studies" are **design walkthroughs** of hypothetical companies, not deployments |
| [DesignGurus — *How do you design a multi-agent system?*](https://www.designgurus.io/answers/detail/how-do-you-design-a-multi-agent-system) | [`multi-agent-systems.md`](multi-agent-systems.md) | Introductory; useful mainly for debugging-cost framing |
| [MongoDB — *Agentic systems*](https://www.mongodb.com/resources/basics/artificial-intelligence/agentic-systems) | [`foundations-and-patterns.md`](foundations-and-patterns.md) | "Degree of agenticity" spectrum; vendor framing |
| [`alirezadir/Agentic-AI-Systems`](https://github.com/alirezadir/Agentic-AI-Systems) | [`genai-system-architecture.md`](genai-system-architecture.md), [`multi-agent-systems.md`](multi-agent-systems.md) | Curated learning index; little multi-agent depth; the practice-problems file was not retrieved |
| [Gerred Dillon — *Building an Agentic System*](https://gerred.github.io/building-an-agentic-system/) | [`case-studies.md`](case-studies.md) | Dissection of a Claude-Code-style terminal agent; success metrics unsourced |
| [Anthropic — *Building Effective Agents*](https://www.anthropic.com/engineering/building-effective-agents) | [`foundations-and-patterns.md`](foundations-and-patterns.md), [`research-and-landscape.md`](research-and-landscape.md) | The workflow/agent taxonomy most other sources map onto (2024-12) |
| [Google Cloud — *Choose a design pattern for your agentic AI system*](https://docs.cloud.google.com/architecture/choose-design-pattern-agentic-ai-system) | [`foundations-and-patterns.md`](foundations-and-patterns.md) | Most complete vendor catalog, including swarm and loop patterns |
| [`gtzheng/Awesome-Agentic-System-Design`](https://github.com/gtzheng/Awesome-Agentic-System-Design) | [`research-and-landscape.md`](research-and-landscape.md) | README is an index; substance is in `resources/papers.md`, `resources/frameworks.md`, `UPDATES.md` (all read). Some 2026 arXiv IDs unverified |
| [Exabeam — *Agentic AI architecture*](https://www.exabeam.com/explainers/agentic-ai/agentic-ai-architecture-types-components-best-practices/) | [`foundations-and-patterns.md`](foundations-and-patterns.md) | Architecture-type explainer; vendor |
| [Databricks — *Agentic systems guide*](https://www.databricks.com/blog/agentic-systems-guide) | [`foundations-and-patterns.md`](foundations-and-patterns.md) | Declines to define autonomy levels; vendor |
| [IBM — *Agentic architecture*](https://www.ibm.com/think/topics/agentic-architecture) | [`foundations-and-patterns.md`](foundations-and-patterns.md) | Vertical / horizontal / hybrid framing; "not all agents are agentic" |

## Full bibliographies, by file

| File | Read | Summary / indexed |
|---|---|---|
| [`foundations-and-patterns.md#sources`](foundations-and-patterns.md#sources) | 23 | 17 |
| [`genai-system-architecture.md#sources`](genai-system-architecture.md#sources) | 15 | 15 |
| [`multi-agent-systems.md#sources`](multi-agent-systems.md#sources) | 21 | 12 |
| [`retrieval-and-memory.md#sources`](retrieval-and-memory.md#sources) | 33 | 22 |
| [`production-and-operations.md#sources`](production-and-operations.md#sources) | 38 | 39 |
| [`case-studies.md#sources`](case-studies.md#sources) | 23 | 22 |
| [`research-and-landscape.md#sources`](research-and-landscape.md#sources) | 38 | 29 |

Counts are tagged bibliography lines. Some lines group several URLs, so the unique-URL total is
higher than the sum.

## Most load-bearing sources outside the starting set

Cited across several files, and doing the most work in the synthesis:

- [Anthropic — *How we built our multi-agent research system*](https://www.anthropic.com/engineering/multi-agent-research-system): +90.2%, ~15× tokens, 80% variance from token spend
- [Cognition — *Don't Build Multi-Agents*](https://cognition.com/blog/dont-build-multi-agents): the single-thread counter-position
- [Cemri et al. — *Why Do Multi-Agent LLM Systems Fail?* (MAST)](https://arxiv.org/abs/2503.13657): 14 failure modes, 1,642 traces
- [Chip Huyen — *Building A Generative AI Platform*](https://huyenchip.com/2024/07/25/genai-platform.html): the layered reference architecture
- [OpenAI — *A practical guide to building agents*](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf): single-agent-first, manager vs decentralised
- [HumanLayer — *12-Factor Agents*](https://github.com/humanlayer/12-factor-agents): "own your control flow"
- [Anthropic — *Contextual Retrieval*](https://www.anthropic.com/news/contextual-retrieval): the best-quantified retrieval recipe
- [ZenML — *LLMOps Database*](https://www.zenml.io/llmops-database): ~2,177 production case-study summaries; best breadth index for further mining
- [Simon Willison — *The lethal trifecta*](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/): the architectural framing for injection risk

For the exact `[read]`/`[summary]` status of each of these, see the bibliography of the file that
cites it.
