# Anthropic multi-agent Research system

**What it shows:** The orchestrator-worker topology: a lead agent plans into external memory, spawns parallel subagents, receives lightweight references back, then hands off to a citation agent.

**Why this diagram type:** A Mermaid flowchart fits a directed hand-off of work between agents and stores.

**Design decisions:** Subagent outputs route through external storage rather than straight back to the lead, to show the 'lightweight reference' mechanism; effort-scaling rules are shown as a constraint on the lead. Colours follow the shared set: blue for model calls, green for deterministic code, orange for humans, red for failure causes, grey for data stores, purple for external inputs. Top-to-bottom layout keeps it narrow.

![Anthropic multi-agent Research system](./cs-anthropic-research-system.png)

**Assumptions / source:** Every element comes from the text of the section named below; no new facts. Illustrates the "Anthropic — multi-agent Research" entry in [`../case-studies.md`](../case-studies.md).

Source script: `cs-anthropic-research-system.mmd` (Mermaid, rendered via Kroki).
