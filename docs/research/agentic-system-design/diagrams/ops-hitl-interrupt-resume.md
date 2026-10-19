# LangGraph interrupt and resume

**What it shows:** Control flow of a LangGraph node that calls interrupt(): pause, persist, resume, and the re-execution of the node from its first line.

**Diagram type:** State machine, Lifecycle phase map pattern. One subject (the node) moves through phases with a wait state and a terminal outcome; the repeated phase is the lesson.

**What the budget forced out or merged:** Eight source states merged to seven: 'Node starts' + 'Code before interrupt()' became 'Run node'; 'Node re-executes from its first line' + 'Code before interrupt() runs AGAIN' became 'Re-execute'; start and end pseudo-states dropped. The two notes became the two callouts. One accent element (Re-execute).

**Assumptions / source:** Illustrates section 1.4 (human-interrupt re-execution) and 2.1 of production-and-operations.md. Plan was recorded here rather than confirmed with the user (not reachable): doc-inline preset (960x600), minimal light, default skin, static.

![LangGraph interrupt and resume](./ops-hitl-interrupt-resume.png)

Source: `ops-hitl-interrupt-resume.html` (diagram-design skill, default skin). Exported to `.svg` and `.png` from it.
