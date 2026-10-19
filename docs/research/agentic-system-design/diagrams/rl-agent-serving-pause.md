# Why Agent Workloads Stress LLM Serving

**What it shows:** One agent step across two LLM calls with a tool call between them, the idle tool-latency window, and which systems intervene where.

**Diagram type:** Sequence (diagram-design skill, default skin, minimal light, doc-inline). Time ordering between four actors is the point. 4 lifelines, 8 messages, no combined fragments.

**Budget handling:** The tool-latency window is the single accent element (a zone spanning the LLM server and KV cache lifelines). The five interventions are one side card rather than five annotations. The earlier "re-prefill if evicted" wording on the second KV message is carried by the window text ("evicted, swapped or recomputed"), so the arrow label is just REUSE PREFIX.

**Assumptions:** The GPU-idle and eviction framing follows the A2 premise that request-level serving discards program structure, and Continuum/InferCept's per-pause options; no numbers are shown.

![Why Agent Workloads Stress LLM Serving](./rl-agent-serving-pause.png)

Source: `rl-agent-serving-pause.html` (diagram-design skill, default skin). Illustrates section A2 of `../research-and-landscape.md`.
