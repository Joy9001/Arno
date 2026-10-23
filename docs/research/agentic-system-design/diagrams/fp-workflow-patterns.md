# Anthropic's workflow patterns: chaining, routing, evaluator-optimizer

**What it shows:** Part 1 of 2. Mini flows of prompt chaining (LLM call, code gate, LLM call), routing (LLM classifier dispatching to handlers) and evaluator-optimizer (generator, evaluator, accept or feedback).

**Diagram type:** Architecture (diagram-design), three flat zones used as small-multiple panels, one pattern per panel.

**What the budget forced out:** Five patterns in one figure came to about 16 nodes, so the set was split: this figure (9 nodes, 10 arrows) and `fp-workflow-patterns-fanout`. Input and Output nodes became arrow stubs labelled INPUT / OUTPUT (Output stays a node only in evaluator-optimizer, where it is the accept target). Accent marks the code Gate and the feedback loop.

**Assumptions:** Content follows section 3.1 of foundations-and-patterns.md; handler count (two) is illustrative.

![Anthropic's workflow patterns: chaining, routing, evaluator-optimizer](./fp-workflow-patterns.png)

**Source:** `fp-workflow-patterns.html` (diagram-design skill, default skin).
