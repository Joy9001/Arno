# Agentic RAG: the model runs the retrieval loop

**What it shows:** The agent-controlled retrieval loop: decide whether to retrieve, plan sub-queries, select a tool and retrieve, grade the evidence, then answer or loop back to re-plan.

**Diagram type:** Flowchart (ovals for start/end, rectangles for steps, diamonds for decisions), diagram-design skill, default skin, minimal light, `doc-inline`. The content is decision logic with one back-edge.

**Budget:** The old figure had 12 nodes. The four retrieval-tool boxes (vector, SQL, web, grep) became the sublabel of one "Select tool, retrieve" node; "Where?" and the retrieve step merged. The risk note (unbounded cost and latency, error compounding, harder to evaluate, new attack surface) is the single italic callout, attached to the re-plan back-edge. Accent is on the back-edge only.

**Assumptions:** The single "Reflect: grade evidence" node abstracts the Self-RAG / CRAG / Search-R1 / DeepRAG research lines, as in the old figure.

![Agentic RAG: the model runs the retrieval loop](./rm-agentic-rag-loop.png)

**Source:** `rm-agentic-rag-loop.html` (diagram-design skill, default skin).
