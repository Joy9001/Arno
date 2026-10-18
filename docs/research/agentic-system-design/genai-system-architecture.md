# End-to-End GenAI / Agentic System Architecture, as Taught in System-Design Guides

Field survey, written **2026-10-18**. Independent of any project: it records what guides, vendor engineering posts, and interview resources teach, not what any system should adopt. Retrieval/memory, evals, security, and reliability have sibling surveys; here they appear only as boxes in the architecture.

## 1. What this slice covers and how reliable the sources are

Two bodies of material are blended in "GenAI system design":

1. **Practitioner architecture writing**: Chip Huyen's platform post (July 2024), a16z's LLM app stack (June 2023), Anthropic's agent posts (Dec 2024, Jun 2025), NVIDIA/Anyscale/vLLM serving writeups.
2. **Interview-prep material**: System Design Handbook, ByteByteGo's book, the `alirezadir/Agentic-AI-Systems` repo, newsletter/blog "design ChatGPT" pieces.

Source-quality caveats worth carrying through the rest of the document:

- Interview guides are mostly **qualitative checklists**. The System Design Handbook agentic guide contains, by its own text, no numeric thresholds (no step limits, token budgets, latency targets). The generative-AI guide has a worked example, but its cost arithmetic does not reconcile with list prices (see §6).
- **Vendor self-interest** is pervasive: a16z writes as an investor in several named stack companies; vLLM's "24x" is the vLLM team's best case; Anthropic's agent posts naturally favor simple, model-centric designs; NVIDIA's blog points to TensorRT-LLM/NIM; Educative/System Design Handbook/ByteByteGo sell courses or books.
- The **a16z stack is 3+ years old**; the tool names (ada-002, Pinecone-as-default, GPT-4-32k, "agents don't really work yet") are historical. The *shape* of the stack has survived; the product list has not. Anthropic's own agent post flags that "much of the tooling landscape has changed" since its date.

## 2. Reference architecture ("boxes and arrows")

The convergent picture across Huyen, a16z, the System Design Handbook, and the "design ChatGPT" write-ups is a request path with a fixed set of stages, wrapped by cross-cutting concerns.

```
                         ┌───────────────────────── cross-cutting ─────────────────────────┐
                         │ observability (metrics/logs/traces) · evals · cost/quota meter   │
                         │ auth/tenancy · prompt+config registry · feature flags/A-B        │
                         └──────────────────────────────────────────────────────────────────┘

 Client (web/mobile/IDE)                                              ┌─ data flywheel ──────┐
   │  SSE / WebSocket stream  ▲                                       │ thumbs, edits, logs  │
   ▼                          │ tokens                                │ → eval sets, fine-   │
 API gateway: authN, rate limit (req+token), TLS, routing             │ tune, prompt fixes   │
   │                          │                                       └──────────▲───────────┘
   ▼                          │                                                  │
 Orchestrator / app service (stateless)  ◄───── conversation store (Postgres/Redis/KV)
   │ 1. input guardrails (PII mask, injection/topic classifiers, moderation)
   │ 2. cache lookup: exact → semantic → (provider prompt-cache is applied later)
   │ 3. router: intent classifier / complexity → pick pipeline + model tier
   │ 4. context construction: query rewrite → retrieve (BM25 + dense) → rerank
   │        → assemble prompt from registry template + history + tools schema
   ▼
 LLM gateway: unified API, keys, fallbacks, retries, load balancing, per-user budgets
   │                              │                              │
   ▼                              ▼                              ▼
 Managed API (OpenAI/        Self-hosted pool (vLLM/TGI/    Small specialist models
 Anthropic/Google…)          TensorRT-LLM on GPUs)          (classifier, reranker, embedder)
   │
   ▼
 Agent loop (optional): model → tool call → executor (validated, sandboxed, permissioned)
   │        ▲                   │ read tools: search, SQL, RAG
   │        └── observations ◄──┘ write tools: email, order, DB write → human approval
   ▼
 Output guardrails: format validator, toxicity, hallucination/grounding check, PII
   │   fail → retry / fallback model / human handoff
   ▼
 Post-process, citations, stream to client, persist, emit trace + metrics
```

Huyen's stated build order is incremental: start with query → model → response, then add context construction, guardrails, router/gateway, cache, and complex logic/write actions as needs arise, with observability and orchestration spanning every layer. a16z's earlier framing reduces the same thing to three stages of "in-context learning": (1) data preprocessing and embedding, (2) prompt construction and retrieval, (3) prompt execution and inference, with logging, caching, and validation attached at stage 3.

## 3. Component-by-component

| # | Component | Job | Design decisions guides raise | Named tooling in sources |
|---|---|---|---|---|
| 1 | **Client** | Chat/IDE/voice UI; renders streamed tokens; optimistic updates | SSE vs WebSocket; reconnect/resume; partial-render of markdown | Browser `EventSource` (SSE) |
| 2 | **API gateway** | AuthN/Z, TLS, routing, request + token rate limits, telemetry | Limit by tokens not just requests; free vs paid tiers; who degrades first under load | Generic API gateways |
| 3 | **Orchestrator / app service** | Stateless coordinator of pipeline steps; parallelizes independent steps (e.g., routing + PII scrub) | Hand-rolled vs framework; Huyen: "start building your application without one first" | LangChain, LlamaIndex, Haystack, Flowise, Langflow (Huyen); LangGraph, CrewAI (Agentic-AI-Systems repo) |
| 4 | **Prompt management / registry** | Versioned templates, few-shot examples, system prompts, tool schemas; keeps system prompt separate from user input | Version-pin per release; attribute metrics to prompt version; roll back | PromptLayer, Helicone, MLflow (a16z LLMOps row) |
| 5 | **Input guardrails** | PII masking (reversible placeholder dictionary), jailbreak/injection detection, out-of-scope topic filtering, human approval for destructive actions | Latency added per check; run in parallel with routing | Sensitive-data detectors, classifiers; Rebuff for injection (a16z) |
| 6 | **Caches** | Exact cache, semantic cache, provider prompt cache | See §3.1 | Redis, GPTCache (a16z); provider prompt caching |
| 7 | **Router** | Intent classifier / complexity estimator picks pipeline or model tier; filters out-of-scope with stock answers; next-action predictor | Small specialized classifier to keep router cheap; context-window mismatch when routing to a shorter-context model | Fine-tuned small classifiers |
| 8 | **Retrieval layer** (box only) | Chunk → embed → index; hybrid BM25 + dense; rerank; metadata filters; text-to-SQL for tabular | Chunk size vs context/latency; freshness (async re-embedding); retrieval as a *tool* in agentic RAG | Elasticsearch/BM25, FAISS, ScaNN, hnswlib; Pinecone, Weaviate, Qdrant, pgvector (a16z) |
| 9 | **Model gateway** | One interface to hosted + self-hosted models; keys held centrally; fallbacks on rate limit/outage; retries; load balancing; logging; sometimes cache/guardrails | Build vs buy; added hop of latency; centralizes cost attribution | Portkey, MLflow AI Gateway, Kong AI Gateway, Cloudflare AI Gateway, TrueFoundry (Huyen) |
| 10 | **Model serving** | Managed API or self-hosted inference | See §4 | vLLM, TGI, TensorRT-LLM, NVIDIA NIM |
| 11 | **Tool layer** | Function calling, MCP servers, search, code exec, SQL, external APIs | Tools as contracts; read vs write split; blast radius; validation before execution | MCP cited by Anthropic as one integration route |
| 12 | **Agent loop / orchestration of reasoning** | Model decides next action; loop until termination | See §5 | Claude Agent SDK, Strands, LangGraph, OpenAI Agents SDK |
| 13 | **Output guardrails** | Empty/malformed output, toxicity, hallucination, sensitive data, brand risk, quality scoring | Streaming makes partial output hard to validate; guardrails add latency | Constrained sampling (guidance, outlines, instructor); SelfCheckGPT, SAFE; AI judges (Huyen) |
| 14 | **Async/queue layer** | Offline embedding, batch jobs, long-running agents, retries, back-pressure | Queue depth as autoscaling signal; resumability for long agents | (generic) |
| 15 | **Feedback / data flywheel** | Collect thumbs, edits, escalations; feed evals, fine-tuning, prompt fixes | Privacy/opt-out (GDPR/HIPAA logging); RLHF loop mentioned at wrap-up | (see sibling evals survey) |
| 16 | **Observability** | Metrics, logs, traces | See §3.2 | LangSmith (traces, Huyen); W&B, MLflow (a16z) |

### 3.1 Caching: three different things

Guides often conflate these; Huyen separates them.

| Cache | What is stored | Hit condition | Main risk | Notes (with attribution) |
|---|---|---|---|---|
| **Provider prompt cache** | Processed KV state for a shared *prefix* (system prompt, tool defs, docs) | Byte-identical prefix | Dynamic content early in the prompt kills hits | Anthropic docs (read 2026-10): cache writes cost 1.25x (5-min TTL) or 2x (1-hour TTL) base input price; reads cost ~0.1x on most models (newer models listed at 0.05x or lower); default TTL 5 min, refreshed on use; up to 4 explicit breakpoints; minimum cacheable length 512-4,096 tokens depending on model. Huyen (2024) estimated a 1,000-token system prompt × 1M daily calls avoids ~1B repeated input tokens/day, and noted Google's context cache at a 75% input discount plus $1.00 per M tokens per hour storage |
| **Exact cache** | Full outputs (or retrieval results) keyed by identical request | Same key | Stale or user-specific answers | LRU/LFU/FIFO eviction; in-memory, Redis, Postgres, tiered; a small classifier can predict cacheability; do not cache user-specific or time-sensitive queries |
| **Semantic cache** | Outputs keyed by query embedding | Cosine similarity above threshold | Wrong answer served on false match | Huyen calls its value "more dubious": depends on embedder quality, vector search, threshold tuning; worth it only with high hit rate |

Order in the request path: exact → semantic → generation (with provider prefix caching applied inside the model call).

### 3.2 Observability as taught

Huyen's three pillars, and the LLM-specific metric set that interview guides repeat:

| Pillar | Contents |
|---|---|
| Metrics | System (throughput, GPU utilization, uptime); model (toxicity, hallucination rate, timeouts, empty/malformed outputs); pipeline (context relevance/precision, vector-DB query time); latency (TTFT, TBT/ITL, TPS, TPOT, total); cost (queries, input/output tokens, RPS vs rate limit); length (query/context/response, since sudden shifts signal bugs). Slice by user, release, prompt/chain version, time |
| Logs | Everything: configs, queries, outputs, intermediate steps, component start/end/crash. Huyen also advocates manual daily inspection of production data |
| Traces | Per-request path with actions, retrieved docs, final prompt, time and cost per step |

The System Design Handbook adds retrieval hit rate and P50/P95/P99 latency, plus a failure-mode list: token overload, queue congestion, index bloat, cold starts, rate limits, prompt crashes, irrelevant retrieval, model drift.

## 4. Model serving: managed APIs vs self-hosted

### 4.1 Mechanics every guide expects you to name

| Concept | What it is | Why it matters | Figure (source) |
|---|---|---|---|
| **Prefill vs decode** | Prefill processes all input tokens in parallel (matrix-matrix, compute-bound); decode emits one token per step (matrix-vector, memory-bandwidth-bound) | TTFT is dominated by prefill + queueing; TPOT by decode | NVIDIA technical blog |
| **KV cache** | Stored keys/values for past tokens; per token = 2 × layers × (heads × head_dim) × bytes | Grows linearly with batch × sequence length; caps concurrency; makes long-context RAG expensive | NVIDIA example: Llama 2 7B, 4,096 tokens, batch 1, FP16 ≈ 2 GB; 7B weights at 16-bit ≈ 14 GB |
| **Continuous (in-flight) batching** | Finished sequences leave the batch and new ones join at each iteration, instead of waiting for the longest sequence | Largest single throughput lever; also lowers p50 latency because new requests do not wait for a full batch | Anyscale (A100 40GB, OPT-13B, 2023): ~8x over naive static batching alone; up to 23x combined with vLLM memory optimizations; FasterTransformer static ~4x |
| **PagedAttention** | KV cache split into fixed blocks, non-contiguous, located via a block table (OS-paging analogy) | Removes over-reservation (static allocation reserves max length per request), allows bigger batches | vLLM team (vendor-run, LLaMA-7B/A10G and 13B/A100-40GB, ShareGPT lengths): up to 24x vs HF Transformers and up to 3.5x vs TGI; 2.2-2.5x vs TGI on single-output requests |
| **GQA / MQA** | Query heads share K/V heads | Shrinks KV cache and memory reads; needs training/uptraining | Llama 2 70B uses GQA (NVIDIA blog) |
| **FlashAttention** | Exact attention with tiling to cut GPU memory I/O | Free speed/memory win, no model change | NVIDIA blog (no figures given) |
| **Quantization** | Lower-precision weights (and sometimes activations/KV) | Fit larger models per GPU; more tokens per byte of bandwidth. Weight quantization simpler than activation (outliers) | NVIDIA blog |
| **Speculative decoding** | Draft model proposes k tokens; target verifies in one pass; first mismatch discards the rest | Cuts latency at low batch sizes; output distribution preserved by the acceptance scheme | Secondary blogs claim 2-3x; an arXiv production-scale EAGLE paper reports ~10-30% over vLLM at batch size 1 and 1.4-2x at large batch with its optimizations; a measurement cited there shows 1.3x at batch 2 falling to 0.7x at batch 48 in stock vLLM. A 2026 MLSys study finds target-model verification dominates cost |
| **Parallelism** | Tensor (split layers), pipeline (split layer groups; bubbles), sequence | Needed when model > one GPU | NVIDIA blog |
| **Distillation** | Small student mimics large teacher | Cheaper tier for the router | DistilBERT: 40% smaller, 97% of language understanding, 60% faster (NVIDIA blog citing it) |

Independent check on engine claims: an arXiv study (Nov 2025, LLaMA-2 7B-70B) found vLLM reaching up to 24x TGI throughput under high concurrency while TGI showed lower tail latency in interactive single-user scenarios. Read it as "throughput engines vs latency engines" depending on workload, not as a universal ranking; neither source tested the latest versions of either engine.

### 4.2 Managed API vs self-hosted

| Axis | Managed API | Self-hosted (vLLM / TGI / TensorRT-LLM) |
|---|---|---|
| Time to ship | Hours | Weeks (GPU procurement, serving stack, autoscaling) |
| Marginal cost | Per token, linear | Per GPU-hour; falls steeply with utilization |
| Utilization risk | None | Idle GPUs are paid for; batching depends on traffic |
| Latency control | Provider queues, rate limits, shared tail | You control batching, placement, quantization, speculative decoding |
| Privacy | Data leaves boundary (the System Design Handbook example routes privacy-sensitive code to an in-house model) | Stays in VPC |
| Input guardrails | Needed (data leaves) | Fewer required, but you build all of them (Huyen) |
| Model choice | Vendor frontier models | Open weights; fine-tuning freedom |
| Ops burden | Low | High: scheduling, KV cache memory, version upgrades |
| Break-even | Low/spiky volume | High, steady volume. One "design ChatGPT" write-up claims self-hosting at $52.4M/month vs $159M/month via APIs at 2.5B prompts/day, with ~$525M upfront (single newsletter, unaudited assumptions) |

Common hybrid taught in guides: route most traffic to a cheap tier, a minority to a frontier API, and sensitive traffic to an in-house open model (the System Design Handbook's example is 70% / 20% / 10%). a16z (2023) noted that gpt-3.5-turbo was ~50x cheaper than GPT-4 and that some high-volume consumer teams route to fine-tuned open models.

## 5. The agentic layer

### 5.1 Workflows vs agents

Anthropic (Dec 2024) draws the distinction most guides now reuse: **workflows** orchestrate LLMs and tools through predefined code paths; **agents** let the LLM direct its own process and tool use. Its five workflow patterns map directly onto architecture boxes:

| Pattern | Architecture mapping | Trade-off stated |
|---|---|---|
| Prompt chaining (with gates) | Orchestrator sequence of calls + programmatic checks | Latency for accuracy |
| Routing | The router box | Cheap model for easy queries, strong model for hard |
| Parallelization (sectioning, voting) | Parallel guardrail + responder; multiple samples | Cost for coverage/confidence |
| Orchestrator-workers | Planner LLM spawns workers (e.g., multi-file edits) | Subtasks not predefined; more tokens |
| Evaluator-optimizer | Generator + critic loop | Only when criteria are clear and iteration measurably helps |

Its guidance: find the simplest solution, add complexity only when it demonstrably helps; agents trade latency and cost for task performance; use stopping conditions such as a maximum iteration count; start with raw LLM APIs because framework abstractions can obscure prompts and responses; spend as much effort on the agent-computer interface (tool docs) as on prompts.

### 5.2 Agentic building blocks per the System Design Handbook

Agent, goals (measurable, persistent, with termination criteria), tools/actions, memory (short-term, long-term, shared), feedback loops, an **orchestration layer that validates intent, enforces constraints, and records outcomes so agents never execute actions directly**, plus guardrails and monitoring. Its closing line: a well-designed agent "is one that knows when it should stop."

| Decision | Options (guide's reading of the interview signal) |
|---|---|
| Action granularity | Atomic (safety awareness, more coordination) vs broad (performance, higher risk) |
| Execution | Validated (production-ready, added latency) vs direct (simple, low observability) |
| Control flow | Reactive loop (simple, weak long-horizon) vs planned (foresight, costlier) vs reflection (self-correction, runaway-loop risk) |
| Multi-agent | Single (simple) / specialized agents (separation of concerns, integration cost) / manager (structure, single point of failure) / peers (flexible, coordination drift) |
| Failure mitigations | Hallucinated tool calls → strict validation; infinite loops → step limits; drift → memory curation; overconfidence → secondary checks |

### 5.3 Cost and operations reality of agents

Anthropic's multi-agent research post (Jun 2025): agents use about **4x** the tokens of chat; multi-agent systems about **15x**. On BrowseComp, token usage alone explained 80% of performance variance (95% with tool-call count and model choice). Production lessons stated: resume from the point of failure rather than restart, checkpoint, rainbow deployments so in-flight agents are not disrupted by releases, and full production tracing (monitoring decision patterns rather than conversation contents). Architecturally this means the async/queue layer and durable state are first-class once agents run for minutes, not an afterthought. (Anthropic sells the models that consume those tokens; the 15x figure is its own measurement on its own system.)

Huyen's version of the same boundary: *complex logic* (loops and branching) and *write actions* (send email, place order) raise capability and risk; the main named threat is prompt injection; mitigations are strict permissions, human approval for consequential actions, and securing writable databases.

## 6. Capacity, latency, and cost estimation

### 6.1 Metric definitions (NVIDIA NIM benchmarking docs)

- **TTFT**: query submitted to first token; includes queueing, prefill, network.
- **ITL / TPOT**: `(e2e_latency − TTFT) / (output_tokens − 1)`.
- **E2E latency** = TTFT + generation time.
- **TPS (system)**: total output tokens / wall-clock window; **TPS per user** ≈ output tokens / e2e latency, approaching 1/ITL for long outputs.
- Relationship: as concurrency rises, system TPS rises until the GPUs saturate while per-user TPS falls. That curve is the throughput-latency trade-off every serving design negotiates.

### 6.2 Estimation recipe

1. **Users → requests**: DAU × interactions/user/day; peak factor (guides use ~3x average, or "20% of daily traffic in two hours" ≈ 2.4x-3x depending on assumption).
2. **Requests → tokens**: input tokens (system prompt + history + retrieved chunks + question) and output tokens separately; multiply by peak factor to get peak tokens/sec.
3. **Tokens → GPUs**: peak output tokens/sec ÷ achievable tokens/sec/GPU at the target TPOT (measure or use published numbers).
4. **KV cache budget**: concurrent sequences × context length × per-token KV bytes must fit in HBM after weights.
5. **Cost**: tokens × price, with caching discounts and routing mix applied.
6. **Latency budget**: TTFT = network + gateway + guardrails + retrieval + queueing + prefill; E2E = TTFT + output_tokens × TPOT; add serial agent steps (each is a full LLM call).

### 6.3 Worked numbers from the sources

| Item | Figure | Source / check |
|---|---|---|
| Load | 100K DAU × 10 interactions × (1K in + 1K out) ≈ 2B tokens/day ≈ 23K tok/s avg, ≈70K tok/s at 3x peak | System Design Handbook; arithmetic checks (2B / 86,400 = 23.1K) |
| Cost | Guide states ≈$3,000/day input + ≈$10,000/day output ≈ $13,000/day ≈ $390K/month, "GPT-4 Turbo pricing" | **Does not reconcile**: 1B input tokens/day at $3,000 implies $3 per M tokens, 1B output at $10,000 implies $10 per M. Those are not the GPT-4 Turbo launch list prices ($10 / $30 per M, from the author's recollection of OpenAI's published pricing). Treat as illustrative and parametrize the price yourself |
| Per-request throughput | GPT-4 Turbo ≈ 40 tok/s per request; local LLaMA-3 150-300 tok/s on one A100 | System Design Handbook; unsourced in the guide |
| Latency classes | Autocomplete < 500 ms; clarification 1-2 s; high-end models 2-4 s | System Design Handbook |
| ChatGPT-scale sketch | 2.5B prompts/day, ≈69.4K msgs/s peak, ≈20M concurrent conversations, ≈1.5 TB/day messages, ~17,500 H100s, 99.9% availability, first token < 2 s | systemdesign.one newsletter; internally inconsistent (intro cites 11.4M concurrency and $145M/month vs body's 20M and $159M/month); it is an interview-style fermi estimate, not OpenAI data |
| Self-hosted $/token | At a hypothetical $1/GPU-hour: ~1,900 tok/s ≈ $0.15 per M tokens vs ~81 tok/s ≈ $3.43 per M tokens | Anyscale (OPT-13B, A100); ratio equals the ~23x batching gap |
| KV memory per token (derived) | Llama-2-7B (32 layers, hidden 4,096, FP16): 2 × 32 × 4,096 × 2 B = 0.5 MB/token (matches NVIDIA's ≈2 GB at 4,096 tokens). Llama-2-70B with GQA (80 layers, 8 KV heads × 128 dim): 2 × 80 × 1,024 × 2 B ≈ 0.31 MB/token | Author's arithmetic from published model configs and NVIDIA's formula; verify before quoting |
| Decode upper bound (derived) | Each decode step reads all weights once: 14 GB weights ÷ ~2 TB/s HBM (A100-80GB class) ≈ 7 ms/token ≈ 140 tok/s for a single stream; batching amortizes the weight read across sequences | Author's arithmetic from NVIDIA's memory-bound explanation and a public GPU spec; verify |

Cost-per-request template (write it with symbols in an interview):

```
cost = in_tok × p_in × (1 − cache_hit × (1 − read_mult))   # prompt-cache effect on input
     + out_tok × p_out
     + retrieval + embedding + guardrail-classifier calls
     (× LLM calls per request for agents: ~4x chat, ~15x multi-agent per Anthropic's measurement)
self-hosted $/Mtok = GPU_$/hr ÷ (tokens/sec × 3600) × 1e6
```

## 7. Scaling concerns checklist

| Concern | Typical answer in guides |
|---|---|
| Tokens, not requests, are the scaling unit | Token-based rate limits and autoscaling on queue depth / GPU KV utilization |
| GPU fleet is the bottleneck (not the DB) | Continuous batching, quantization, tiering models, autoscale with warm pools (cold starts listed as a failure mode) |
| Stateless app tier | Conversation state in Postgres/Redis; any replica can serve a stream |
| Long-lived streams | SSE through LB/gateway with reconnection; sticky routing only if state is local; cap concurrent streams per node |
| Overload | Per-tier rate limits, priority queues, degrade free tier first, fall back to smaller model, shed load before queueing unbounded |
| Hot prefixes | Provider/self-hosted prefix caching; keep stable content first in the prompt |
| Index freshness | Async re-embedding, TTL on index entries |
| Multi-region | Replicate stateless tier and read replicas; GPU capacity concentrates in a few regions, so cross-region routing is normal |
| Cost governance | Per-user quotas, dashboards, router policies, offline batch jobs for non-interactive work |
| Quality drift | Prompt/model versioning, A/B tests, feedback-driven evals |

## 8. Typical trade-off tables

| Trade-off | Option A | Option B | Deciding factor |
|---|---|---|---|
| RAG vs fine-tuning | RAG: fresh, attributable, cheap to update | Fine-tune: style/format, latency (shorter prompts) | System Design Handbook default: RAG first, fine-tune later from logs. a16z (2023): fine-tuning needed a fact ~10 times in training data to stick |
| Prompt size vs latency/cost | More retrieved chunks (guide: 3-10) | Fewer, reranked chunks | Prefill cost scales with input; long context degrades ("lost in the middle", Liu et al. 2023, via Huyen) |
| Streaming vs validation | Stream for perceived latency | Buffer for output guardrails | Huyen: streaming makes partial responses hard to evaluate |
| Guardrail depth vs latency | More checks | Fewer / parallel | Most teams accept the latency (Huyen) |
| Semantic cache | Big savings if hit rate high | Wrong-answer risk | Huyen: dubious unless hit rate is high |
| Big model vs routed tiers | Quality, simplicity | Cost, latency, complexity of router | Query mix |
| Workflow vs agent | Predictability, cost | Flexibility on open-ended tasks | Can steps be enumerated in advance? |
| Single vs multi-agent | Fewer tokens, easier debug | Parallelism, isolation; ~15x tokens | Task breadth and value per task |
| SSE vs WebSocket | Simple, auto-reconnect, one-way | Bidirectional | Token streaming rarely needs two-way |
| Framework vs raw code | Speed | Transparency, debuggability | Anthropic and Huyen both advise starting without |
| Gateway build vs buy | Control | Time | Portkey/Kong/Cloudflare etc. exist |
| Managed vs self-hosted | See §4.2 | | Volume, privacy, ops capacity |

## 9. Interview frameworks

### 9.1 Frameworks as published

| Source | Steps |
|---|---|
| **System Design Handbook, GenAI guide** (9 steps for a 45-minute interview; the text says "eight" in places, headings number nine) | 1 Clarify use case and NFRs (latency, context size, error tolerance, scale, privacy) → 2 Estimate load/tokens/throughput → 3 High-level architecture → 4 RAG deep dive → 5 Model interaction patterns (streaming, function calling, routing, templates) → 6 Trade-offs, governance, cost control → 7 Bottlenecks, observability, failure modes → 8 Security/compliance → 9 Wrap-up and scaling |
| **System Design Handbook, agentic guide** (5 steps) | Clarify requirements (autonomy level, constraints, success) → Define components (agent, goals, tools, memory, control flow) → Discuss trade-offs → Scalability/reliability/safety → Close by tying to real constraints |
| **`alirezadir/Agentic-AI-Systems`, interview prep** (5 steps) | Frame the problem → simple high-level architecture → deep dive where the interviewer probes → trade-offs, failure modes, cost → risks and next iterations. (The repo also indexes architectures, design patterns, RAG, evals, and a "2026 Agentic AI System Design Update" covering harness engineering, long-running agents, AgentOps, context engineering, MCP/A2A, security, cost-aware design; the practice-problem file itself was not retrievable during this survey) |
| **ByteByteGo, *Generative AI System Design Interview*** (Ali Aminian, Hao Sheng; 2024) | "7-step framework" (steps not listed on the public announcement page); 11 chapters; 10 worked questions, 280+ diagrams |
| **Anthropic agent guidance** (not an interview framework, used as one) | Simplest thing that works → add workflow patterns → agent only when steps cannot be predicted → stopping conditions, sandbox tests, transparent plans |

### 9.2 What interviewers say they grade (System Design Handbook)

LLM awareness (tokens, context limits, sampling), modular/composable thinking, cost-consciousness, safety and governance, communication. For agentic questions: balance of autonomy and control; over-autonomous designs signal inexperience, over-constrained ones signal fear of complexity; mid-level candidates are expected to name components, senior/staff to argue trade-offs.

### 9.3 A merged template (synthesis, not a quoted source)

1. **Clarify**: user, task, autonomy level, success metric, latency class, scale, data sensitivity, error tolerance, write-action risk.
2. **Estimate**: DAU → tokens/sec (in and out separately) → GPUs/cost; state assumptions aloud.
3. **Sketch** the §2 pipeline; mark which boxes are in scope.
4. **Deep dive** where probed: retrieval, serving, agent loop, or safety.
5. **Trade-offs**: at least one table-style comparison per major decision (§8).
6. **Failure, ops, evals**: top failure modes, metrics (TTFT/TPOT/P95, grounding rate, task success), fallbacks.
7. **Evolve**: v1 → v2 (distillation, routing, personalization, offline jobs, A/B, feedback loop).

## 10. Classic interview problems and what each tests

| Problem | Where it appears | Core thing it tests | Distinctive deep dives |
|---|---|---|---|
| **Design ChatGPT / chat assistant** | ByteByteGo ch. 4; systemdesign.one; many prep sites | End-to-end serving at scale: streaming, conversation store, rate limiting, GPU capacity | SSE connection tracking, continuous batching and KV cache, free vs paid tier degradation, moderation, multi-region |
| **RAG-based Q&A / internal doc bot** (HR Slack bot, support bot) | ByteByteGo ch. 6; System Design Handbook; Educative | Retrieval pipeline quality, freshness, grounding, handoff | Chunking, hybrid search + rerank, ACL-aware retrieval, escalation triggers (negative sentiment, explicit request, refund/legal topics), resolution/escalation/CSAT metrics |
| **AI code assistant / IDE copilot** | System Design Handbook (running example); ByteByteGo (Smart Compose analog) | Latency classes, model tiering, privacy routing | Sub-500 ms autocomplete with small/speculative models, repo context retrieval, in-house model for sensitive code |
| **Autocomplete / Gmail Smart Compose** | ByteByteGo ch. 2 | Low-latency small-model serving, personalization | Caching, on-device vs server, trigger policy |
| **Machine translation (Google Translate)** | ByteByteGo ch. 3 | Classic ML-system framing with GenAI models | Multilingual models, evaluation |
| **Text-to-image service** | ByteByteGo ch. 8-10 | Diffusion serving, queues, GPU cost, safety | Async job queue vs streaming, batch scheduling, NSFW filtering, personalized fine-tunes (headshots) |
| **Text-to-video** | ByteByteGo ch. 11 | Very expensive generation, long async jobs | Job orchestration, chunked generation, cost caps |
| **Image captioning / multimodal** | ByteByteGo ch. 5 | Multimodal encoders plus LLM | Evaluation metrics |
| **Legal assistant / resume builder with memory** | System Design Handbook | Domain grounding, memory design, hallucination risk | Citation, human review, per-user memory and privacy |
| **AI agent platform (multi-tenant)** | System Design Handbook (agentic); Agentic-AI-Systems | Autonomy vs control, tool contracts, state, termination | Tool permissioning and blast radius, durable execution/resume, per-tenant quotas, tracing, multi-agent coordination overhead |
| **LLM inference API / gateway** | techinterview.org, prachub (as indexed) | Serving internals and multi-tenant fairness | Request queue, batching, GPU scheduling, token-based limits |

A 2026 review of the ByteByteGo book (prachub, as surfaced in search, not read in full) notes the 2024 book predates agents, evaluation, and inference-cost topics, which is why newer guides (System Design Handbook agentic guide, the Agentic-AI-Systems 2026 update) add them.

## 11. Points of convergence and disagreement across sources

**Convergence**: stateless orchestrator in front of a model gateway; routing to cheaper models as the first cost lever; caching in layers; guardrails on both sides of the model; retrieval treated as context construction; observability tracked in TTFT/TPOT/token/cost terms; tools split into read vs write with approval on writes; "start simple" for both frameworks and agents.

**Disagreement or gaps**:
- Semantic caching: presented as a standard box in interview guides, but Huyen is skeptical of its value.
- Speculative decoding: interview guides list it as a latency fix; measured production results show gains shrinking with batch size.
- Self-hosting break-even: guides assert it with unaudited numbers; no source here gives a transparent utilization model.
- Quantitative thresholds for agents (step limits, token budgets) are absent from the interview guides surveyed.
- Several interview "design ChatGPT" capacity numbers are unattributed fermi estimates, and at least one is internally inconsistent.

## Sources

Tags: [read] = fetched and read this session; [summary] = obtained only as a search-result summary; [indexed] = seen in results or referenced by another source, not read.

- [read] System Design Handbook, Agentic System Design: https://www.systemdesignhandbook.com/guides/agentic-system-design/
- [read] System Design Handbook, Generative AI System Design Interview: https://www.systemdesignhandbook.com/guides/generative-ai-system-design-interview/
- [read] alirezadir/Agentic-AI-Systems README: https://github.com/alirezadir/Agentic-AI-Systems
- [read] Agentic-AI-Systems interview prep chapter: https://github.com/alirezadir/Agentic-AI-Systems/blob/main/06_interview_prep/README.md
- [read] Chip Huyen, Building A Generative AI Platform (2024-07-25): https://huyenchip.com/2024/07/25/genai-platform.html
- [read] a16z, Emerging Architectures for LLM Applications (2023-06-20): https://a16z.com/emerging-architectures-for-llm-applications/
- [read] ByteByteGo announcement, Generative AI System Design Interview chapters: https://blog.bytebytego.com/p/our-new-book-generative-ai-system
- [read] Anthropic, Building Effective Agents (2024-12-19): https://www.anthropic.com/engineering/building-effective-agents
- [read] Anthropic, How we built our multi-agent research system (2025-06-13): https://www.anthropic.com/engineering/multi-agent-research-system
- [read] Anthropic prompt caching docs (first ~100K of 160K characters): https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- [read] NVIDIA, Mastering LLM Techniques: Inference Optimization: https://developer.nvidia.com/blog/mastering-llm-techniques-inference-optimization/
- [read] NVIDIA NIM LLM benchmarking metrics: https://docs.nvidia.com/nim/benchmarking/llm/latest/metrics.html
- [read] Anyscale, continuous batching for LLM inference: https://www.anyscale.com/blog/continuous-batching-llm-inference
- [read] systemdesign.one, ChatGPT system design (paywalled after excerpt): https://newsletter.systemdesign.one/p/chatgpt-system-design
- [summary] vLLM blog, PagedAttention (figures via search summary): https://vllm.ai/blog/2023-06-20-vllm
- [summary] arXiv 2511.17593, vLLM vs HuggingFace TGI comparison: https://arxiv.org/abs/2511.17593v1
- [summary] arXiv 2508.08192, EAGLE speculative decoding at production scale: https://arxiv.org/abs/2508.08192
- [summary] MLSys 2026, Speculative Decoding: Performance or Illusion?: https://mlsys.org/virtual/2026/oral/3782
- [summary] ByteByteGo book bibliographic details and 2026 review: https://prachub.com/resources/generative-ai-system-design-interview-book-review-2026-is-it-still-enough
- [summary] ByteByteGo course intro page: https://bytebytego.com/courses/genai-system-design-interview/introduction-and-overview
- [summary] Educative, Design LLM-powered customer support bot (15-minute sprint): https://www.educative.io/courses/system-design-interview-prep-crash-course/design-llm-powered-customer-support-bot-system-15-minute-sprint
- [summary] Expertflow, AI chatbot human handoff pattern: https://catalog.expertflow.com/solution-patterns/sol-003-ai-chat-bot-human-handoff/
- [indexed] Educative, ChatGPT System Design Explained: https://www.educative.io/blog/chatgpt-system-design
- [indexed] PracHub, Design ChatGPT: streaming over SSE: https://prachub.com/interview-questions/design-chatgpt-streaming-replies-over-sse-and-rendering-long-conversations
- [indexed] PracHub, Design a chat assistant inference service: https://prachub.com/interview-questions/design-a-chat-assistant-inference-service-streaming-free-tier-limits-gpu-scheduling
- [indexed] techinterview.org, Design an LLM Inference API: https://www.techinterview.org/post/3233460079/design-an-llm-inference-api/
- [indexed] DesignGurus, Design ChatGPT case study: https://designgurus.substack.com/p/system-design-case-study-how-to-design-c3d
- [indexed] Leviathan et al. 2023 speculative decoding paper (cited secondhand; not retrieved)
