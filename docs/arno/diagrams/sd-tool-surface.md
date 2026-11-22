# Scripts read and compute; every write is a direct call

**What it shows:** The agent's tool surface. Read and presentation tools (14 functions) run inside
`run_code` (Code Mode): ledger reads, tested financial calculators, charts, tables, and
presentation cards. The six write tools and five delegation tools are direct tool calls. Writes
land instantly with undo, ask first in a tainted conversation, or start a background parse whose
output waits for your review.

**Diagram type:** Architecture, with two dashed zones for "behind `run_code`" and "direct calls".

**What the budget forced out:** Individual tool contracts live in
[`../specs/tools.md`](../specs/tools.md). The diagram predates later tool additions (`show_table`,
`present`, `compare_offers`, `card_payoff`, `schedule_payment`, and delegation tools), and leaves
out `run_code` limits and parser subagent functions. 9 nodes, 12 arrows; the accent marks
`run_code` and the arrow into it.

![Scripts read and compute; every write is a direct call](./sd-tool-surface.png)

**Source:** `sd-tool-surface.html` (diagram-design skill, default skin, minimal light, doc-inline
preset).

**Illustrates:** [`../architecture.md`](../architecture.md#tool-surface) and
[`../specs/tools.md`](../specs/tools.md).
