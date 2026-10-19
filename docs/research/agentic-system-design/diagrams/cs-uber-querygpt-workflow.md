# Uber QueryGPT - workflow of narrow agents

**What it shows:** The intent, table and column-prune agents run in sequence before SQL generation, with a user confirmation step after table selection.

**Why this diagram type:** A Mermaid flowchart suits a linear pipeline with a few side inputs.

**Design decisions:** The red note explains why column pruning exists; the eval box is attached to the output as a dotted side input. Colours follow the shared set: blue for model calls, green for deterministic code, orange for humans, red for failure causes, grey for data stores, purple for external inputs. Top-to-bottom layout keeps it narrow.

![Uber QueryGPT - workflow of narrow agents](./cs-uber-querygpt-workflow.png)

**Assumptions / source:** Every element comes from the text of the section named below; no new facts. Illustrates the "Uber — QueryGPT (text-to-SQL)" entry in [`../case-studies.md`](../case-studies.md).

Source script: `cs-uber-querygpt-workflow.mmd` (Mermaid, rendered via Kroki).
