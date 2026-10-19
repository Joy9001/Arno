# Cost and latency levers on the request path

**What it shows:** Cost and latency techniques mapped onto one agent request and its loop: semantic cache, guardrail, budget gate, router, prompt cache, parallel tools, plus batch as an offline path.

**Diagram type:** Flowchart. It shows where each lever sits and what it can cut off.

**What the budget forced out or merged:** Twelve source nodes became nine: the prompt cache was merged into the Model call node, the cached-answer node became the HIT arrow to Final answer, the Stop node is kept, and the Batch API node became an italic callout with a leader to the request. The stale-plan risk became the second callout. One accent element (the budget gate).

**Assumptions / source:** Illustrates section 5 of production-and-operations.md. The order of levers on the path is illustrative; figures are those quoted in the file. Plan was recorded here rather than confirmed with the user (not reachable): doc-inline preset (960x600), minimal light, default skin, static.

![Cost and latency levers on the request path](./ops-cost-latency-levers.png)

Source: `ops-cost-latency-levers.html` (diagram-design skill, default skin). Exported to `.svg` and `.png` from it.
