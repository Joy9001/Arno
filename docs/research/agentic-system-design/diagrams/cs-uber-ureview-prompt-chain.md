# Uber uReview code review prompt chain

**What it shows:** A fixed chain of stages: generators, a grader prompt, semantic dedup and a category classifier, with reviewer feedback returning to the classifier and an optional Fixer.

**Diagram type:** Flowchart (no semantic pattern routed): shows a deterministic chain with a feedback return, not a free-roaming agent.

**What the budget forced out or merged:** The three pluggable assistants collapsed into one "Generators" node with an italic callout naming them. Accent: grader and the feedback-to-classifier arrow.

**Assumptions:** Doc-inline preset, mixed audience, balanced detail. Model names (Claude 4 Sonnet generator, o4-mini-high grader) come from the Models bullet of the same section. Illustrates the matching entry in [`../case-studies.md`](../case-studies.md).

![Uber uReview code review prompt chain](./cs-uber-ureview-prompt-chain.png)

Source: `cs-uber-ureview-prompt-chain.html` (diagram-design skill, default skin, minimal light variant).
