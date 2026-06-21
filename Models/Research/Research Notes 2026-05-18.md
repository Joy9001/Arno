# Model Research — Session Notes (2026-05-18)

> Companion to [Ollama Try-Outs.md](Ollama%20Try-Outs.md) (the checklist). This document captures the *reasoning* and *evidence* that produced the checklist, plus dead ends, open questions, and mistakes worth knowing.

## Session goal

Find the right local LLM for the FinAgent (per [Product Picture v1.0.0](Product%20Picture/v1.0.0.md), [Agent Framework PydanticAI](Tech%20Decisions/Agent%20Framework%20PydanticAI.md), [Infrastructure.md](Infrastructure.md)) given the actual hardware. Infrastructure.md currently names "Qwen3 8B" as the default; this session's job was to validate or replace that choice.

## What was used

| Tool | Purpose |
|---|---|
| `llmfit` (`scoop install llmfit`) | Hardware-aware model recommender. Detects rig, scores models for fit/speed/quality, runs real inference benchmarks |
| `nvidia-smi`, PowerShell `Get-CimInstance` | Hardware ground truth |
| Web search (May 2026 sources) | Verify model existence, BFCL leaderboard, JDHodges eval, vendor announcements |
| Ollama 0.24.0 | The actual inference engine that will run in production |
| `llmfit bench --provider ollama` | **Critical:** measures real tok/s on this rig, vs llmfit's estimates |

## Hardware ground truth

| Component | Value |
|---|---|
| Machine | Asus TUF A15 |
| CPU | AMD Ryzen 5 4600H — 6 cores / 12 threads @ 3.0 GHz |
| RAM | 23.42 GB total, ~10–13 GB free at idle |
| GPU | NVIDIA GTX 1650 Mobile — **4 GB VRAM**, Turing (compute 7.5), CUDA 13.2 |
| OS | Windows 11 Home Single Language |
| Ollama | 0.24.0 |

**The binding constraint is VRAM, not RAM.** This single fact changes most of the recommendation math.

---

## Headline findings

### 1. llmfit's tok/s estimates are off by 2–5× on this rig

Real-world `llmfit bench` against the actual Ollama install:

| Model | Real TPS | TTFT | llmfit estimate | Gap |
|---|---|---|---|---|
| **granite4.1:3b** | **33.9** | 387 ms | 27.6 | +23% (under-promised) |
| nemotron-3-nano:4b | 13.2 | 714 ms | 52.5 | **−75%** |
| qwen3.5:4b | 7.8 | 761 ms | 40.3 | **−81%** |
| qwen3.5:9b | 3.4 | 1488 ms | 4.9 | −31% |

llmfit's estimates assume aggressive quantization (Q3_K_M / Q2_K) and pure memory-bandwidth math. Ollama defaults to Q4_K_M, loads multimodal weights even when unused (Qwen3.5 is multimodal — vision encoder eats VRAM), and spills KV cache the moment context grows. **Granite is the only model that beat llmfit's estimate** — because it's the only one that fully fits in VRAM with headroom.

**Implication:** always bench before recommending. llmfit's `--cli`/`recommend` outputs are useful for *shortlist generation*, not for *speed prediction*.

### 2. The "8B at 5–15 tok/s" line in Infrastructure.md is technically true but practically wrong

Infrastructure.md says:
> The 24 GB of system RAM is sufficient for running an 8B parameter Small Language Model (SLM)... Q4 quantization + CPU offload required. Expect ~5–15 tok/sec.

Measured reality: **qwen3.5:9b runs at 3.4 tok/s**, not 5–15. A 300-token response took 90 seconds. The Product Picture's "checkout counter" moment (answering "can I afford this ₹60k laptop?" in-store) is incompatible with 90-second responses.

**Implication:** the 8B class is not a quality compromise on this hardware; **it's not viable for the primary interactive path**. The 3B–4B class isn't a fallback — it's the only class that can actually deliver the Mission. Worth a new tech decision doc: `Local Model — 3B/4B over 8B`.

### 3. KV cache at 32K context is the hidden VRAM killer

I'd been doing fit math at default 8K context, which is too rosy. The realistic working context for FinAgent (ledger snapshot + chat history + tool-call protocol) is 32K. KV cache size grows linearly with context and is *separate* from model weights.

Measured via `llmfit plan <model> --context 32768`:

| Model | Weights (Q4_K_M) | KV @ 32K fp16 | **Total VRAM** | Fits in 4 GB? |
|---|---|---|---|---|
| Granite 4.1 3B | 2.0 GB | 2.5 GB | 5.0 GB | ❌ (✅ with KV-q8_0 → 3.7 GB) |
| Qwen 3.5 4B | 2.7 GB | 4.0 GB | 7.2 GB | ❌ even with KV-q8_0 |
| **Phi-3.5 mini 3.8B** | 2.2 GB | **12.0 GB** | 14.7 GB | ❌❌ catastrophic |
| Qwen 3.5 9B | 5.6 GB | 4.0 GB | 10.1 GB | ❌ |
| Gemma 4 26B-A4B (MoE) | 1.7 GB active | — | ~12 GB total (VRAM+RAM) | ✅ via MoE offload |

**Phi-3.5 mini is a dead end at any realistic context — its attention architecture blows out the KV cache.** Don't confuse with `phi4-mini` which has different architecture (still needs verification).

**KV-cache quantization** is the single biggest lever for this rig: `OLLAMA_KV_CACHE_TYPE=q8_0` halves KV cache memory at negligible quality cost. The difference between "fits at 32K" and "spills."

### 4. The 2026 benchmark evidence is anti-intuitive

[JDHodges's May 2026 evaluation](https://www.jdhodges.com/blog/local-llms-on-tool-calling-2026-pt1-local-lm/) tested 13 local LLMs across 40 tool-calling tasks. Results:

| Rank | Model | Pass rate | Notes |
|---|---|---|---|
| 🥇 | **Qwen3.5 4B** | 97.5% | Beat models 5× its size; best multi-tool score in field |
| 🥈 | Nemotron Nano 4B | 95% | Best value pick |
| 🥉 | Mistral Nemo 12B | 92.5% | Best for sequential / multi-turn |

The [Berkeley Function Calling Leaderboard V4](https://gorilla.cs.berkeley.edu/leaderboard.html) corroborates: Qwen3 0.6B (88%) outscores Qwen3 1.7B (67%) on tool tests. **Architecture + tool-use training matter more than parameter count.** Llama 3.1 8B, often recommended, fails to parse 12/60 test cases.

### 5. The MoE-on-Ollama question is unanswered

llmfit's strongest recommendations at 32K context are all MoE models (Gemma 4 26B-A4B, Qwen3-Coder-30B-A3B, Qwen3.5-35B-A3B, DeepSeek-R1-0528 distill). Math:

| Model | Active VRAM | RAM offload | Disk | llmfit est. tok/s | Quality score |
|---|---|---|---|---|---|
| `google/gemma-4-26B-A4B` | 1.7 GB | 12.5 GB | 18 GB | 29.7 | 87 |
| `Qwen/Qwen3-Coder-30B-A3B-Instruct` | 1.6 GB | 12.2 GB | 17.7 GB | 14.0 | 98 |
| `Qwen/Qwen3.5-35B-A3B` | 1.1 GB | 11.4 GB | 20.9 GB | 15.6 | 94 |
| `unsloth/DeepSeek-R1-0528-GGUF` (29.6B) | 1.2 GB | 14.7 GB | 17.2 GB | 19.8 | 98 |

**The unresolved question:** llmfit's MoE math assumes [llama.cpp's explicit expert-level offload](https://github.com/ggerganov/llama.cpp/pull/6840) (only the 8 active experts in VRAM per token, the rest in RAM). **Ollama typically does layer-level offload**, which on MoE can collapse to dense-model speeds. If Ollama gets it right, you get 14–30 tok/s with 30B-class quality. If not, you get the dense-model penalty (3–5 tok/s). **Only a real bench resolves this.**

This is the most consequential unknown left in the research. The checklist's recommendation to pull `gemma4:26b` exists specifically to answer it.

---

## llmfit deep dive — what it gets right and wrong

### What llmfit gets right
- **Shortlist generation.** Pulls correct candidates from a 200+ model HuggingFace-scraped database that's far more current than memory.
- **VRAM/RAM math.** Memory requirement calculations match `nvidia-smi` observations.
- **KV cache awareness via `plan`.** The `--context 32768` planning surfaced KV cache as a binding constraint I hadn't sized correctly.
- **`bench` command.** Real on-rig benchmarks via Ollama auto-detect. Killer feature.
- **MoE expert math.** Correctly computes active-experts-in-VRAM vs offloaded experts for MoE architectures.

### What llmfit gets wrong (use with caution)
- **Speed estimates.** Off by up to 5× on this rig, in both directions. Treat as ordinal hints, not predictions.
- **`--capability tool_use` filter is sparse.** Most tool-capable models (Qwen 3.5, Granite 4.1, Phi-4 mini, Gemma 4) lack the metadata flag in llmfit's DB. Filtering on this will hide good candidates.
- **`--license` filter is mostly empty.** Almost every entry has `license: null`. Filter returns 0 results.
- **Database includes lots of obscure community quants.** `JANGQ-AI/MiniMax-M2.7-JANGTQ` (15.3B, claims to be MiniMax M2.7 but the real M2.7 is 230B/10B-active — provenance unclear), `rdtand/Qwen3.6-35B-A3B-PrismaQuant-4.75bit-vllm` (experimental community quant), etc. **Cross-check unfamiliar names against the original HF repo or Ollama before trusting.**
- **No "Tool Use" tag on Gemma 4** despite Google explicitly advertising native function calling — another reason to ignore the capability filter.

### Useful llmfit commands

```powershell
llmfit                                          # interactive TUI
llmfit system                                   # hardware detected
llmfit --max-context 32768 recommend --use-case general --min-fit good --limit 10
llmfit --max-context 32768 info "<HF model ID>"
llmfit plan "<HF model ID>" --context 32768     # shows KV cache + alternatives
llmfit diff "Qwen/Qwen3.5-4B" "ibm-granite/granite-4.1-3b"
llmfit bench <ollama-tag> --runs 3 --provider ollama   # real tok/s
llmfit bench <ollama-tag> --provider ollama --json     # for parsing
```

---

## 2026 model landscape research

### The Indian context (Sarvam, Krutrim)

- **Sarvam-1 (2B)** — best small model for 10 Indic languages (Bengali, Gujarati, Hindi, Kannada, Malayalam, Marathi, Oriya, Punjabi, Tamil, Telugu). Matches Llama 3.1 8B on Hindi at 1/4 the size. **No first-class tool use.** Available as community GGUF; not yet in Ollama's official library ([issue #14688](https://github.com/ollama/ollama/issues/14688)).
- **Sarvam-M (24B)** — launched Feb 2026 alongside the 30B/105B Indic Sovereign series. Tool use unclear; size doesn't fit this rig anyway.
- **Role for FinAgent v1:** *Sarvam-1 as a Hinglish pre-processor only.* Voice transcript → cleaned Hindi/English text → feed to the main agent. Pair with whisper.cpp. Not the primary chat model.

### The function-calling specialist track

- **Salesforce xLAM family** — small models fine-tuned for function calling. xLAM-1b-fc-r hits 78.9% on BFCL (vs Qwen3.5 4B's 97.5%), xLAM-7b-fc-r hits 88.2%.
- **argilla/Llama-3.2-1B-Instruct-APIGen-FC-v0.1** — Llama 3.2 1B fine-tuned on the APIGen function-call dataset.
- **Role for FinAgent:** *Specialist router model.* Fast classifier ("is this a tool-call intent? which tool?") at 60+ tok/s. Not the primary chat model — too small for warm conversation, but excellent for the first hop of an agent pipeline.

### Ollama's `structured outputs` capability — the brittleness mitigation

[Ollama 0.5+ supports JSON-schema-enforced output via `format: <json-schema>`](https://docs.ollama.com/capabilities/structured-outputs). This is **constrained decoding at the token level** — the model literally cannot emit tokens that produce invalid JSON. Fundamentally more reliable than prompt-based "please return JSON" approaches.

**Critical for PydanticAI.** The [tech decision doc](Tech%20Decisions/Agent%20Framework%20PydanticAI.md) flags small-model `ValidationError` as a known risk. Constrained decoding is the documented fix. There is a known [PydanticAI issue #242](https://github.com/pydantic/pydantic-ai/issues/242) noting that PydanticAI was using tool-use invocations rather than the structured outputs feature directly — verify this is resolved before serious work.

---

## Per-model evaluations

### Tier 1 — primary candidates (worth real bench time)

| Model | Why it earned this tier |
|---|---|
| `granite4.1:3b` (IBM, Apache 2.0) | **Benched: 33.9 tok/s real on this rig.** Function-calling + structured JSON as first-class design goals. Fully GPU-resident. The clean winner on speed. Hinglish/INR quality is the open question. |
| `qwen3.5:4b` (Alibaba, Apache 2.0) | **JDHodges 2026 #1 at 97.5%** tool-call correctness. Strong Hinglish/Indic foundation (Qwen lineage). On this rig: 7.8 tok/s real — slower than expected due to vision-encoder overhead in the multimodal weights. |
| `nemotron-3-nano:4b` (NVIDIA Open Model License) | **JDHodges 2026 #2 at 95%.** Benched: 13.2 tok/s real. No multimodal overhead. The cleanest middle-ground pick. |
| `gemma4:26b` (Google, Gemma license) | **The big unknown.** Google's April 2026 release: 25.2B/3.8B-active MoE, native function calling, 256K context, 9.1M Ollama pulls. llmfit estimates 29.7 tok/s on this rig via MoE offload. **Validates or kills the MoE-on-Ollama hypothesis.** Cost to verify: 18 GB pull + 14 GB RAM during inference. |

### Tier 2 — worth a one-shot try

| Model | Why |
|---|---|
| `phi4-mini:3.8b` (Microsoft, MIT) | Best math/reasoning per parameter at this size. Relevant for forward-simulation, EMI, tax-shadow math. **Verify KV cache behavior** — Phi-3.5 was catastrophic; phi-4 might inherit. |
| `mistral-nemo:12b` (Mistral, Apache 2.0) | JDHodges #3, called out as best for sequential / multi-turn workflows (relevant for forward-simulation tool chains). llmfit estimates ~4 tok/s on this rig — probably too slow but worth confirming. |
| `qwen3:30b` (Alibaba, Apache 2.0) | Backup MoE pick if Gemma 4's chat tone feels wrong. General-purpose Qwen3 A3B variant. Same 19 GB cost as Gemma 4. |
| `qwen3-coder:30b` (Alibaba) | Strongest tool/agentic training in the Qwen family — but coder-tuned, which is the wrong shape for warm finance conversation per Product Picture. Worth knowing exists; probably not for primary chat. |

### Tier 3 — specialist support roles

| Model | Role |
|---|---|
| `nomic-embed-text` (installed) | Embeddings for mem0 personalization + ledger semantic search. Keep. |
| Sarvam-1 (2B, community GGUF) | Hinglish pre-processor before the main agent. Not in Ollama yet. |
| Salesforce xLAM / argilla APIGen-FC | Fast tool-call router if a 2-stage pipeline becomes useful (small classifier → big generator). Premature optimization for v1. |

### Dropped — with reasons

| Model | Why dropped |
|---|---|
| `qwen3.5:9b` | **Benched: 3.4 tok/s real.** 90s per response. Incompatible with the Product Picture's "checkout counter" moment. Async-only at best. |
| `deepseek-r1` (original) | No tool use in the original; wrong shape for PydanticAI. (R1-0528 added FC but Ollama's `deepseek-r1:8b` distill page doesn't expose it as a capability — verify before pulling.) |
| `ministral-3:8b` | Decent tool use, but qwen3.5:4b beats 8B-class models on BFCL; no reason to spend 8B-class VRAM for inferior reliability. |
| `rnj-1:8b` | Essential AI code/STEM-tuned; wrong shape for finance chat. |
| `huihui_ai/qwen3.5-abliterated:4b-Claude` | Alignment-stripped community fine-tune. The "no moralizing" Product Picture rule is a prompt + design decision, not a model decision. Abliteration regresses on instruction-following and JSON discipline. |
| `Snowflake/snowflake-arctic-instruct` | Old (April 2024), 4K context only. Superseded. |
| `Qwen/Qwen1.5-MoE-A2.7B-Chat` | Old (Feb 2024). Superseded by Qwen 3.x. |
| `JANGQ-AI/MiniMax-M2.7-JANGTQ` | Real MiniMax M2.7 is 230B/10B-active. This 15.3B HF entry is a community quant of unclear provenance — does not match official spec. |
| All `Qwen3.5-35B-A3B-Claude-4.6-Opus-Reasoning-Distilled` community variants | Trained to imitate Claude *style*, not for function calling. Quality unverified. |
| `nvidia/Gemma-4-26B-A4B-NVFP4`, `prithivMLmods/gemma-4-26B-A4B-it-MXFP4A16` | NVFP4/MXFP4 are vLLM/experimental quants — use the Ollama `gemma4:26b` (Q4_K_M) instead. |
| `microsoft/Phi-3.5-mini-instruct` | KV cache at 32K is 12 GB. Dead at our context. (Phi-4 mini may differ — verify.) |

---

## Mistakes & corrections this session

This is the meta-research log. Future-me coming back to this should know what I got wrong on first pass so the mistakes don't repeat.

### Mistake 1: trusting llmfit's speed estimates
First pass, I wrote up the checklist with "qwen3.5:4b at ~40 tok/s" based on llmfit's `info` output. Real bench: 7.8 tok/s. **5× error.** Corrected after running `llmfit bench`.

**Lesson:** llmfit's *fit* math is good; its *speed* math is unreliable on consumer Windows + NVIDIA rigs. Always run the real bench before committing.

### Mistake 2: anchoring on already-installed models
First two passes, I built recommendations around what was already pulled (qwen3.5, nemotron, rnj-1, ministral-3, deepseek-r1) and didn't engage seriously with llmfit's actual top picks. The user pushed back twice ("I am feeling you are stuck on the models I already have on my system") — correctly.

**Lesson:** when the user asks you to use a recommender system, the *recommendations* are the artifact to evaluate, not the names you already know. Engage with unfamiliar candidates explicitly, even if they look weird at first glance.

### Mistake 3: pattern-matching obscure HF names to "skip"
First pass at llmfit's recommend output, I saw `codgician/...`, `lordx64/...`, `JANGQ-AI/...` and pattern-matched the whole list as "HuggingFace scraper noise." Then when forced to actually check, three of those leads (Gemma 4 26B-A4B, DeepSeek-R1-0528, Ling-mini-2.0) were genuine 2026 releases that materially improved the recommendation.

**Lesson:** verify before dismissing. The cost of one WebSearch call is low; the cost of missing a strong candidate is high. The provenance/quality check is the right filter — not the visual familiarity of the name.

### Mistake 4: not asking about disk budget before pulling
Pulled `granite4.1:3b` (2 GB) without checking. Then stalled at `gemma4:26b` (18 GB) because that felt large enough to ask. **Inconsistent** — should have asked upfront what the disk budget is, then operated within it.

**Lesson:** establish operating constraints early. "Are 5-minute, 2 GB pulls OK without asking each time? Where's the threshold to check first?" is a one-line question worth asking at the start of any research session that involves resource consumption.

---

## Open questions for the next session

1. **Does Ollama's MoE offload behavior match llama.cpp's expert-level offload?** Tests `gemma4:26b` or `qwen3:30b`. If 20+ tok/s on this rig → MoE is the right primary path. If 3–5 tok/s → MoE is dead, Granite/Qwen 4B dense wins.
2. **Does `phi4-mini:3.8b` inherit Phi-3.5's catastrophic KV cache, or is the architecture fixed?** A one-shot `llmfit plan phi4-mini --context 32768` answers it.
3. **How does Granite 4.1 3B actually handle Hinglish + INR/lakh number formats?** IBM's training is English-enterprise-heavy. The Product Picture demands "1.5 lakh" parsing fluency. Only a real prompt run answers it.
4. **Is the `OLLAMA_KV_CACHE_TYPE=q8_0` env var actually respected by Ollama 0.24.0?** Documentation exists; integration is another thing. Worth verifying with a `nvidia-smi` watch during a long-context run.
5. **Is the PydanticAI ↔ Ollama structured-outputs path** ([issue #242](https://github.com/pydantic/pydantic-ai/issues/242)) **resolved in PydanticAI 1.85.1?** Or do we still hit the "tool-use invocation instead of structured outputs" path that makes small-model JSON brittle?
6. **License clarity for Gemma 4** — Gemma license is "open" but has use restrictions. Project privacy stance is uncompromising; verify Gemma's terms are compatible before adopting as default.

---

## Sources

### Benchmarks & leaderboards
- [JDHodges — I Tested 13 Local LLMs on Tool Calling (May 2026)](https://www.jdhodges.com/blog/local-llms-on-tool-calling-2026-pt1-local-lm/)
- [Berkeley Function Calling Leaderboard V4](https://gorilla.cs.berkeley.edu/leaderboard.html)
- [InsiderLLM — Best Local LLMs for Function Calling 2026](https://insiderllm.com/guides/function-calling-local-llms/)
- [Awesome Agents — Function Calling Benchmarks Leaderboard 2026](https://awesomeagents.ai/leaderboards/function-calling-benchmarks-leaderboard/)
- [Docker — Local LLM Tool Calling: A Practical Evaluation](https://www.docker.com/blog/local-llm-tool-calling-a-practical-evaluation/)

### Tooling
- [llmfit README (AlexsJones)](https://github.com/AlexsJones/llmfit/blob/main/README.md)
- [Ollama Structured Outputs docs](https://docs.ollama.com/capabilities/structured-outputs)
- [Ollama Structured Outputs blog](https://ollama.com/blog/structured-outputs)
- [PydanticAI issue #242 — use Ollama's structured outputs feature](https://github.com/pydantic/pydantic-ai/issues/242)

### Models — first-party announcements
- [Gemma 4 — Google DeepMind](https://deepmind.google/models/gemma/gemma-4/) + [Google blog post](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/)
- [Gemma 4 26B-A4B on HuggingFace](https://huggingface.co/google/gemma-4-26B-A4B)
- [MiniMax M2.7](https://www.minimax.io/models/text/m27) (230B/10B-active, March 2026)
- [Sarvam-1 (Indic 2B)](https://huggingface.co/sarvamai/sarvam-1) + [Sarvam-M (24B) blog](https://www.sarvam.ai/blogs/sarvam-m)
- [Ling-mini-2.0 (Ant Group)](https://huggingface.co/inclusionAI/Ling-mini-2.0) + [release blog](https://huggingface.co/blog/RichardBian/ling-mini-2-moe-release)
- [DeepSeek-R1-0528 GGUF (Unsloth)](https://huggingface.co/unsloth/DeepSeek-R1-0528-GGUF) + [tool calling discussion #20](https://huggingface.co/unsloth/DeepSeek-R1-0528-GGUF/discussions/20)
- [argilla/Llama-3.2-1B-Instruct-APIGen-FC](https://huggingface.co/argilla/Llama-3.2-1B-Instruct-APIGen-FC-v0.1)
- [Salesforce xLAM family](https://github.com/SalesforceAIResearch/xLAM)

### Ollama library pages (model availability + capability check)
- [granite4.1](https://ollama.com/library/granite4.1), [qwen3.5](https://ollama.com/library/qwen3.5), [qwen3](https://ollama.com/library/qwen3), [qwen3-coder](https://ollama.com/library/qwen3-coder), [gemma4](https://ollama.com/library/gemma4), [phi4-mini](https://ollama.com/library/phi4-mini), [mistral-nemo](https://ollama.com/library/mistral-nemo), [deepseek-r1](https://ollama.com/library/deepseek-r1), [nemotron-3-nano](https://ollama.com/library/nemotron-3-nano), [ministral-3](https://ollama.com/library/ministral-3), [rnj-1](https://ollama.com/library/rnj-1)

### Real on-rig benchmarks (this session, 2026-05-18)
- `llmfit bench` against Ollama 0.24.0 on the Asus TUF A15:
  - `granite4.1:3b` → 33.9 tok/s avg (n=3, range 32.9–34.8), 387 ms TTFT
  - `nemotron-3-nano:4b` → 13.2 tok/s avg (n=2), 714 ms TTFT
  - `qwen3.5:4b` → 7.8 tok/s avg (n=2), 761 ms TTFT
  - `qwen3.5:9b` → 3.4 tok/s (n=1), 1488 ms TTFT, 90 s for 300 tokens
