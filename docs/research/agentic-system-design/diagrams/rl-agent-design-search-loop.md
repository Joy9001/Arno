# Automated Agent Design Loop

**What it shows:** The generic propose, evaluate, archive, select, mutate loop shared by ADAS, AFlow, GEPA, DGM and HGM, with each method's selection rule on the Select step and the exit that returns the best archived candidate.

**Diagram type:** Flowchart (diagram-design skill, default skin, minimal light, doc-inline). The Loop type needs 5 to 8 stations and a state hub, and this cycle has an exit, so a flowchart with a return path fits better. 6 nodes, 6 arrows.

**Budget handling:** The caveats node became the single italic callout with a dashed leader to Evaluate (the optimiser sees the metric). The accent marks Select, where the methods differ. LLM/CODE tags replace the old blue/green colour coding.

**Assumptions:** A generalisation across the A3 table, not any one paper's exact algorithm.

![Automated Agent Design Loop](./rl-agent-design-search-loop.png)

Source: `rl-agent-design-search-loop.html` (diagram-design skill, default skin). Illustrates section A3 of `../research-and-landscape.md`.
