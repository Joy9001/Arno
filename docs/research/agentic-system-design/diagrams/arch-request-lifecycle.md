# Lifecycle of one request: cache hit vs miss, guardrail failure

![Lifecycle of one request: cache hit vs miss, guardrail failure](./arch-request-lifecycle.png)

**What it shows.** One request through gateway, orchestrator, caches, LLM gateway plus models, and output guardrails, with the cache-hit branch, the cache-miss generation path, and the guardrail pass / fail branches.

**Why this diagram type.** PlantUML sequence diagram: the point is ordering and alternatives over time, which a static architecture diagram cannot show.

**Design decisions.** Six participants to stay narrow; alt blocks for hit/miss and pass/fail; red notes mark risks (false semantic match, human handoff). The agent loop is a note, not a lane.

**Assumptions and source.** Order (exact then semantic cache; provider prefix cache inside the model call; fail -> retry / fallback model / human handoff) is from section 2 and section 3.1. Illustrates section 3.1 (Caching) and section 2.
