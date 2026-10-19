# Hybrid retrieval pipeline: indexing time vs query time

**What it shows:** Anthropic's contextual-retrieval recipe split into what runs once at indexing and what runs per query, with the failure-rate reductions at each step.

**Why this diagram type:** Graphviz flowchart with two clusters, because the main insight is the indexing/query split and the shared contextualized chunks feeding both indexes.

**Design decisions:** Blue marks LLM steps (context generation, embedding); grey cylinders are the two indexes; dashed arrows are lookups. The note lists the cumulative top-20 failure rates from section 3.

![Hybrid retrieval pipeline: indexing time vs query time](./rm-hybrid-retrieval-pipeline.png)

**Assumptions / source:** Sections 3.1-3.3 of retrieval-and-memory.md. Placing embedding as a blue 'model' step is a colour-language choice; numbers are Anthropic's, as in the file.
