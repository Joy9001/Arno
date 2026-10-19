# DoorDash Dasher support - RAG with two-tier guardrail

**What it shows:** The online path (condense, retrieve, generate, tier-1 similarity check, tier-2 LLM groundedness check, human fallback) beside the offline monitoring loop.

**Why this diagram type:** A flowchart makes the decision logic and the default-to-human failure path explicit.

**Design decisions:** Decision diamonds for the two guardrail tiers; offline judge and Promptfoo grouped in a separate box with dotted edges because they act outside the request path. Colours follow the shared set: blue for model calls, green for deterministic code, orange for humans, red for failure causes, grey for data stores, purple for external inputs. Top-to-bottom layout keeps it narrow.

![DoorDash Dasher support - RAG with two-tier guardrail](./cs-doordash-support-guardrail.png)

**Assumptions / source:** Every element comes from the text of the section named below; no new facts. Illustrates the "DoorDash — Dasher support RAG with guardrails" entry in [`../case-studies.md`](../case-studies.md).

Source script: `cs-doordash-support-guardrail.mmd` (Mermaid, rendered via Kroki).
