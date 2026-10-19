# Reference architecture (1 of 2): request path from client to models

![Reference architecture (1 of 2): request path from client to models](./arch-reference-architecture.png)

**What it shows.** The stateless request path in order: client, API gateway, the four orchestrator stages (input guardrails, cache lookup, router, context construction), the LLM gateway, and the three model pools, with the conversation store and the cross-cutting concerns.

**Why this diagram type.** Graphviz (top-to-bottom) gives tight control over vertical layering and grouping; a sequence diagram would hide the component topology, and Mermaid produced an unusably tall layout for this much content.

**Design decisions.** Split into two figures to stay narrow and legible. Green = deterministic code, blue = models, purple = external, grey = stores. Orchestrator stages are clustered and numbered as in the text.

**Assumptions and source.** Every element comes from the ASCII diagram and component table in section 2 and section 3 of genai-system-architecture.md. Illustrates section 2 (Reference architecture).
