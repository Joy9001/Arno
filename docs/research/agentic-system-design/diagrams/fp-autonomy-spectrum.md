# Who controls the next step: code or model?

**What it shows:** Patterns ordered from least to most autonomous, split at Anthropic's workflow/agent line.

**Why this diagram type:** Graphviz (top-to-bottom) suits a one-dimensional ordered ladder with grouped regions.

**Design decisions:** Green = code-controlled workflow, blue = model-controlled agent; side labels keep arrows clear of group titles; order follows MongoDB's least-to-most-autonomous list with Microsoft's direct call as the lowest level.

![Who controls the next step: code or model?](./fp-autonomy-spectrum.png)

**Assumptions / source:** Illustrates section 1.2 of foundations-and-patterns.md; orchestrator-workers is omitted because no source places it on the MongoDB ordering.
