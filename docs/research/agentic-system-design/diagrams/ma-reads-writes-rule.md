# Convergent rule: fan out reads, keep writes single-threaded

**What it shows:** The shared 2026 rule: a lead fans read-only work out to workers with fresh context windows, their compact results inform ONE writer, a clean-context reviewer checks the draft, and parallel writers are the fragile path.

**Diagram type:** Architecture (components and connectors, with two labelled zones for the robust and fragile paths). No semantic pattern matched.

**What the budget forced out or split:** The "Task" start node was removed (the lead is the entry point), and the two writers keep one shared failure-outcome node. The three justifications for extra agents are a single italic callout.

**Assumptions / notes:** Writer examples ("final report, edits, commits") generalise from Anthropic (one agent writes the report) and Cognition (writes stay single-threaded). Accent is on the single writer and the failure outcome only.

![Convergent rule: fan out reads, keep writes single-threaded](./ma-reads-writes-rule.png)

**Source:** `ma-reads-writes-rule.html` (diagram-design skill, default skin, minimal light, doc-inline preset).

**Illustrates:** multi-agent-systems.md, section 1 (headline) with support from sections 4, 6 and 11
