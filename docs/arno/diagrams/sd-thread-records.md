# One thread keeps two records

**What it shows:** One chat turn. The client sends the thread's ID and a new message, never its
history. The server loads the thread's model history and runs the agent. When the run ends,
`all_messages()` replaces the model history, and this turn's messages are added to `messages`
(written from `VercelAIAdapter.dump_messages`), which is never edited.

**Diagram type:** Architecture. No semantic pattern matched.

**What the budget forced out:** The reply streaming back to the client, tool calls inside the run,
and the history trimming threshold are left out; the step 6 text covers them. 5
nodes, 5 arrows; the accent marks the record that each run replaces and the arrow into it.

![One thread keeps two records](./sd-thread-records.png)

**Source:** `sd-thread-records.html` (diagram-design skill, default skin, minimal light, doc-inline
preset).

**Illustrates:** [`../architecture.md`](../architecture.md), step 6.
