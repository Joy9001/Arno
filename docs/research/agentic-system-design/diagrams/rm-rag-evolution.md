# RAG evolution: control moves from fixed code into the model

**What it shows:** The four RAG paradigms as stacked pipelines, coloured by who holds control (deterministic code, model call, retrieval tool).

**Why this diagram type:** A stacked sequence with labelled transitions (drawn in Graphviz with HTML-table nodes) shows the progression and the stage contents at a glance without the wide staircase a left-to-right layout produced.

**Design decisions:** Green steps are fixed code; blue are model calls; purple are retrieval tools. Only the agentic stage puts the planning and grading steps inside a model-directed loop.

![RAG evolution: control moves from fixed code into the model](./rm-rag-evolution.png)

**Assumptions / source:** Section 2 (RAG evolution) of retrieval-and-memory.md; stage contents come from the section 2 table.
