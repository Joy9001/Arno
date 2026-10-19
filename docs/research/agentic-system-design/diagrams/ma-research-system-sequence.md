# Anthropic research system: orchestrator, parallel subagents, citation pass

**What it shows:** Message order in Anthropic's multi-agent research system: lead plans and saves to memory, subagents run in parallel and return compact answers, the CitationAgent attributes claims, and the lead delivers the final report.

**Why this diagram type:** A sequence diagram shows ordering, the parallel fan-out and the memory write, which prose bullets cannot. (Rendered with plantuml via Kroki; source: `ma-research-system-sequence.puml`.)

**Design decisions:** Fan-out drawn as one group block with a single subagent lane (3-5 in reality). Notes carry the 200k-token truncation and the synchronous-subagent bottleneck.

![Anthropic research system: orchestrator, parallel subagents, citation pass](./ma-research-system-sequence.png)

**Assumptions / notes:** The citation step is shown after integration, per "a separate CitationAgent attributes claims at the end".

**Illustrates:** multi-agent-systems.md, section 4 (Anthropic research system)
