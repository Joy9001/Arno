# Layered agent security

**What it shows:** Where probabilistic guardrails and deterministic boundaries sit on the path from untrusted input to tools, with the Rule of Two as the gate.

**Why this diagram type:** A top-down graph with clusters separates the two kinds of layer; GraphViz keeps long labels readable.

**Design decisions:** Dashed red cluster for the probabilistic layer, solid green cluster for deterministic controls, orange for the human check.

![Layered agent security](./ops-layered-security.png)

**Assumptions / source:** Illustrates sections 3.2 and 4.2-4.3 of production-and-operations.md. Ordering of layers is a conceptual arrangement, not a mandated architecture.
