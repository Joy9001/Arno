# Durable execution: journal, crash, resume

**What it shows:** A model call and a payment tool call journaled step by step, a crash after the external action, and resume from the journal with a retried step.

**Diagram type:** Sequence. The ordering of persist, act and crash is the point; four lifelines, one phase divider per run.

**What the budget forced out or merged:** The external payment API's internal self-message ('performs the charge') and the agent's self-message ('step 1 skipped') were merged into the neighbouring arrow labels; the third note sentence (separate runs need a business ID or DB uniqueness constraint) stays in the prose of section 1.4. The 'Idempotency-Key = step ID' detail moved into the closing callout. Two callouts, two accent elements (crash marker, same-key retry).

**Assumptions / source:** The payment API stands in for any mutating tool; no numbers beyond the file. Illustrates section 1.4 (and 1.1/1.3) of production-and-operations.md. Plan was recorded here rather than confirmed with the user (not reachable): doc-inline preset (960x600), minimal light, default skin, static.

![Durable execution: journal, crash, resume](./ops-durable-execution-journal.png)

Source: `ops-durable-execution-journal.html` (diagram-design skill, default skin). Exported to `.svg` and `.png` from it.
