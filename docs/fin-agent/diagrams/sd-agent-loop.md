# The agent loop and its guards

**What it shows:** The one agent's loop. Your message (or a question you scheduled) goes to the
model, which calls tools and Code Mode scripts and reads their results until it gives an answer.
Guards sit on the loop: compaction keeps the history inside the context window, tool output limits
spill or trim big returns, a backup model takes over on errors, and circuit breakers stop a
runaway turn and hand back a partial answer with the reason.

**Diagram type:** Architecture, with guards attached to loop steps by dashed lines. No semantic
pattern matched.

**What the budget forced out:** Retries before the backup model takes over, the one-running-turn
rule, and the exact breaker values are in the step 3 text. 8 nodes, 5 arrows plus 4 guard lines;
the accent marks the model.

![The agent loop and its guards](./sd-agent-loop.png)

**Source:** `sd-agent-loop.html` (diagram-design skill, default skin, minimal light, doc-inline
preset).

**Illustrates:** [`../system-design.md`](../system-design.md), step 3.
