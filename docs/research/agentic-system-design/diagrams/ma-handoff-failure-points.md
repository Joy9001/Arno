# Where multi-agent systems lose information and compound errors

**What it shows:** Two failure families from section 10: context loss when work crosses a handoff (summary, paraphrase or translation tax) and error compounding along a chain of steps or agents, with partial mitigations.

**Why this diagram type:** A graph with red annotations attached to the exact handoff where each failure arises ties the failure table to the data path. (Rendered with graphviz via Kroki; source: `ma-handoff-failure-points.dot`.)

**Design decisions:** Red for failures, green for mitigations, blue for agents. Numbers (0.95^20 ~ 36%, 17.2x vs 4.4x) are quoted from the file, not new.

![Where multi-agent systems lose information and compound errors](./ma-handoff-failure-points.png)

**Assumptions / notes:** The 36% figure assumes independent steps, as the file states.

**Illustrates:** multi-agent-systems.md, section 10 (Failure modes, consolidated)
