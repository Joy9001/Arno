# Reference architecture (2 of 2): agent loop, output guardrails, flywheel

![Reference architecture (2 of 2): agent loop, output guardrails, flywheel](./arch-reference-architecture-agent-output.png)

**What it shows.** What happens after the model call: the optional agent loop (read tools direct, write tools behind human approval), output guardrails with the fail paths (retry / fallback model, human handoff), post-processing and streaming, and the data flywheel feeding evals, fine-tuning and prompt fixes.

**Why this diagram type.** Graphviz flow with a cluster for the loop; the observation back-edges are easiest to show in a node-edge graph.

**Design decisions.** Red for the fail branches, orange for human steps, dashed edges for feedback paths. Continues from figure 1 via an input node.

**Assumptions and source.** Grounded in section 2 (ASCII diagram) and section 3 components 11-13 and 15. Illustrates section 2.
