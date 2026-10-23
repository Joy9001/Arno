# Start simple, escalate on measured evidence

**What it shows:** Four questions from a deterministic solution or single LLM call, through a workflow and a single agent, to multi-agent, with the cautions from the sources.

**Diagram type:** Flowchart (diagram-design). Questions are rectangles tagged with a small diamond glyph rather than full diamonds, because the quoted criteria are too long for diamond shapes.

**What the budget forced out:** The "Start with the simplest thing that works" node was dropped (the title carries it); the two caution notes became the two italic callouts. 8 nodes, 7 arrows plus 2 dashed. Question wording is condensed with the detail kept as sublabels; only criteria quoted in section 4 are used. Accent marks the two dashed "falls short" escalation arrows. The old per-arrow wording "measured evidence it falls short" / "workflow falls short too often" is shortened to FALLS SHORT, and "escalate only on evidence" to ON EVIDENCE (14-character label limit).

**Assumptions:** Code-controlled vs model-controlled outcome shading matches the autonomy-spectrum figure.

![Start simple, escalate on measured evidence](./fp-escalation-decision-flow.png)

**Source:** `fp-escalation-decision-flow.html` (diagram-design skill, default skin). Illustrates section 4 (and section 6 agreements 1-2) of foundations-and-patterns.md.
