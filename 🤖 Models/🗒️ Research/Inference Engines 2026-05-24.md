# Inference Engines — FinAgent on the Fortress (2026-05-24)

Companion note to [[Research Notes 2026-05-24]] and [[Research Notes 2026-05-18]]. The May-18 session bench-killed `qwen3.5:9b` and named MoE-on-Ollama as the open question. The May-24 model pass closed the model question (Granite 4.1 3B primary, Gemma 4 E2B for Hinglish, Cogito v1 3B wildcard) and concluded: **the next real lever is the inference engine, not the model.** This note answers that.

## How I framed the problem

Same hard constraints as the model search:

- **4 GB VRAM** (GTX 1650 Mobile, Turing, CUDA compute capability 7.5) — the binding ceiling
- **Windows 11** — native preferred; WSL2 is acceptable but a maintenance tax
- **PydanticAI integration** — needs OpenAI-compatible chat-completions or a first-class Python client, native tool calling, and grammar-/schema-constrained JSON
- **24/7 server** — single-user latency matters more than throughput; the engine has to live as a background service
- **Permissive license** on the engine itself

Soft preferences:

- Active 2026 development (not abandonware)
- GGUF ecosystem compatibility — preserves the Ollama model library investment
- KV-cache quantization + speculative decoding support (the two compounding levers)
- MoE expert-level offload — the unlock for 26–35B-A3B-class quality on this rig

## The 2026 landscape — taxonomy

There are *many* engines; most are wrappers around two cores. Sorting them honestly:

| Category | Engines | Underlying core |
| --- | --- | --- |
| **Core engines (write the kernels)** | llama.cpp, vLLM, SGLang, TensorRT-LLM, ExLlamaV3, MLC LLM, KTransformers, NexaML | each its own |
| **llama.cpp wrappers / DX layers** | Ollama, LM Studio, llamafile, GPT4All, Jan, text-generation-webui | llama.cpp + ggml |
| **vLLM forks / extensions** | Aphrodite Engine, LMDeploy (sibling) | vLLM PagedAttention |

> "**Ollama, LM Studio, and GPT4All are all just llama.cpp — here's why performance still differs**" ([dev.to article](https://dev.to/plasmon_imp/ollama-lm-studio-and-gpt4all-are-all-just-llamacpp-heres-why-performance-still-differs-59h5)). The wrapper layer determines defaults (KV-cache quant on/off, flash attention on/off, batching policy), and on this rig those defaults are worth 10–30%.

The big strategic divide for a single-user box is **vLLM-class (production, multi-user, batch-oriented) vs llama.cpp-class (single-user, latency-oriented, portable)**. vLLM-class engines are designed for the wrong shape of workload here and most don't run native on Windows.

## Tier 1 — actually plausible for this rig

### 1. llama.cpp directly *(MIT, native Windows binaries with CUDA 12.4/13.1)*

The unlock path the May-24 notes already flagged. This is the engine; everything else in Tier 1 is a wrapper.

- **MoE expert-level offload via `--n-cpu-moe N` + `--override-tensor` regex.** Keeps "always hot" attention/router weights on GPU, streams cold expert FFNs from RAM via PCIe. Realistic: Gemma 4 26B-A4B at ~24.5 tok/s, Qwen 3.6 35B-A3B at ~24 tok/s on similar consumer rigs ([llama.cpp MoE offload guide — HuggingFace](https://huggingface.co/blog/Doctor-Shotgun/llamacpp-moe-offload-guide), [Medium walkthrough — Qwen 3 235B on consumer hardware](https://medium.com/@david.sanftenberg/gpu-poor-how-to-configure-offloading-for-the-qwen-3-235b-a22b-moe-model-using-llama-cpp-13dc15287bed)). This is the single biggest quality-per-VRAM lever in 2026.
- **Speculative decoding via `--model-draft`.** Draft model predicts N tokens; target verifies in one parallel pass. 1.5–3× speedup, no quality loss. Recommended pairs: Qwen3 0.6B → Qwen3 8B (~1.9×), Gemma 3 1B → Gemma 3 27B (~1.8×) ([speculative.md docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md), [Premai blog — 2-3x faster](https://blog.premai.io/speculative-decoding-2-3x-faster-llm-inference-2026/)).
- **KV-cache quantization** at `q8_0` is roughly free (perplexity delta 0.002–0.05); halves the 32K-context KV footprint. `q4_0` is the aggressive setting.
- **Grammar-constrained JSON** via GBNF. Older than XGrammar and slower per-token but well-integrated; PydanticAI's structured-output requirement maps cleanly.
- **OpenAI-compatible server** via `llama-server`. PydanticAI talks to it the same way it talks to Ollama.
- **Prebuilt Windows CUDA binaries** as of llama.cpp b9196 (May 2026), CUDA 12.4 and 13.1 — no compilation needed ([release notes](https://github.com/ggml-org/llama.cpp/releases), [Knightli writeup](https://www.knightli.com/en/2026/05/18/llama-cpp-windows-cuda-vulkan-gguf/)).
- **Real-world Windows speed vs Ollama:** 3–10% on dense single requests, ~20% less VRAM on the same model, but the gap blows up to 3× under concurrent load and is much larger on MoE because of the `--n-cpu-moe` mechanic ([Markaicode benchmarks](https://markaicode.com/benchmarks/ollama-vs-llamacpp-benchmark/), [Morph comparison](https://www.morphllm.com/comparisons/llama-cpp-vs-ollama)).

**Verdict:** **the right primary engine for the production runtime.** Operational cost: managing a Windows service for `llama-server`, model paths, draft-model pairing, and an env file. One-time setup, then it stays out of the way.

### 2. Ollama *(MIT, current default)*

Still earns Tier 1 — but only as the **development convenience layer**, not the optimization target.

- Strong defaults, one-command pulls, structured outputs via JSON Schema ([Ollama docs](https://docs.ollama.com/capabilities/structured-outputs)), native tool calling, OpenAI-compatible API. PydanticAI integration is the path of least resistance.
- KV-cache quantization is *supported* but off by default — set `OLLAMA_KV_CACHE_TYPE=q8_0`. Flash attention also off by default — set `OLLAMA_FLASH_ATTENTION=1` ([smcleod writeup](https://smcleod.net/2024/12/bringing-k/v-context-quantisation-to-ollama/), [Ollama PR #15505 adds TurboQuant tq2/tq3/tq4](https://github.com/ollama/ollama/pull/15505)). **Both should already be on in your environment** — likely free 15–30% from a one-line change. The May-18 open question #4 ("is `OLLAMA_KV_CACHE_TYPE=q8_0` actually respected by Ollama 0.24.0?") is now answered yes, with caveats: embedding models auto-disable it.
- **MoE handling is still layer-level, not expert-level** ([issue #11772](https://github.com/ollama/ollama/issues/11772) open). For Gemma 4 26B-A4B / Qwen3 35B-A3B this is the difference between 3–5 tok/s and 20–30 tok/s. **Not a "tune defaults" fix — it's an architectural gap.**
- Tool-call ergonomics improved in 2026: streaming tool calls, better Llama 4/Mistral handling, but small-model tool-call bias (4B class over-calls; 12B+ correctly stays in chat) is a model-level issue, not an engine fix.

**Verdict:** keep as the dev daemon and the dense-3B-on-VRAM happy path. **Don't expect it to deliver the MoE win** — that's llama.cpp's job.

### 3. LM Studio *(closed-source app, free for personal use; uses llama.cpp + MLX)*

The "Ollama developer experience with llama.cpp's 2026 features turned on" option.

- v0.4 (Jan 2026) shipped structured JSON output, multi-GPU controls, speculative decoding for 20–50% speed boost, parallel requests, and `llmster` — a standalone **headless daemon** suitable for running as a service ([LM Studio changelog](https://lmstudio.ai/changelog), [Codersera 2026 guide](https://codersera.com/blog/lm-studio-complete-guide-2026/)).
- **Tool-call grammar for gpt-oss models** via llama.cpp engine ≥2.7.1 — significantly higher success rate. Qwen 3.5 family tool calling specifically improved.
- Grammar-based JSON for GGUF (llama.cpp) and Outlines for MLX. PydanticAI talks to it via OpenAI-compat.
- License is the catch: **closed-source app with commercial restrictions.** Free for personal use; "Work" use went through a re-licensing in 2025. For your privacy-first personal-data use this is fine; for any future commercialization, re-read terms.

**Verdict:** if the appeal of llama.cpp directly is undercut by the management burden, LM Studio's `llmster` is the realistic middle path — most of llama.cpp's wins without writing the launch scripts. If you stay fully in CLI land, skip it.

### 4. llamafile *(Apache 2.0, Mozilla)*

Single-file llama.cpp distribution. Stayed in Tier 1 because Mozilla shipped **0.10.0 in March 2026 with CUDA support restored** ([Phoronix](https://www.phoronix.com/news/Mozilla-AI-Llamafile-0.10), [release notes](https://github.com/mozilla-ai/llamafile/releases)) — for a long time it was CPU-only and that disqualified it. Now it's a self-contained executable bundling model + runtime.

- Same engine as llama.cpp ⇒ same feature ceiling on KV-cache quant, grammars, speculative decoding (depending on version).
- 2× faster than llama.cpp / 25× faster than Ollama on **CPU prompt evaluation specifically** — irrelevant once your GPU is doing the work.
- Operational appeal: one `.exe` to schedule, zero install state.

**Verdict:** valid Tier 1 for portability/simplicity but doesn't deliver anything llama.cpp doesn't, and Mozilla's update cadence trails the upstream by months. If you want simple, prefer LM Studio's `llmster`. If you want bleeding-edge llama.cpp features (the MoE knobs, the latest spec-decoding flags), use llama.cpp directly.

## Tier 2 — interesting, but wrong shape for this rig

### 5. vLLM *(Apache 2.0)*

The textbook production engine. PagedAttention + continuous batching + tensor parallelism — designed for many concurrent users on big GPUs.

- **No native Windows support.** WSL2 + CUDA only, or community fork ([SystemPanic/vllm-windows](https://github.com/SystemPanic/vllm-windows)) ([vLLM GPU install docs](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/)). Adds permanent maintenance overhead for a single-user use case.
- **At peak load:** ~35× higher RPS and ~44× higher total output tokens/sec vs llama.cpp. Single-user on RTX 4090: only 10–15% faster than llama.cpp/Ollama. **The entire performance pitch evaporates at concurrency=1.**
- **VRAM-greedy.** Reserves ~90%+ of GPU memory for the KV pool. On a 4 GB card you have ~3.5 GB left after the OS — vLLM will refuse to load most models with any usable context.
- FP8 needs Ada+ (RTX 40-series, H100) tensor cores. GTX 1650 doesn't have them.
- **XGrammar is the structured-output backend** for vLLM as of March 2026 ([XGrammar paper](https://arxiv.org/pdf/2411.15100)) — fast and good, but vLLM is still the wrong shape for the workload.

**Verdict:** **rejected.** Designed for batch serving on Ampere+ datacenter cards. Not faster than llama.cpp at concurrency=1, doesn't run native on Windows, and won't fit your VRAM budget.

### 6. SGLang *(Apache 2.0)*

vLLM-class server engine with a structured-generation focus. **Compressed FSM constrained decoding is ~3× faster than guided decoding**, hides the cost in the forward pass, and pushes JSON compliance from 90–94% → 96–98% ([SGLang vs vLLM 2026](https://particula.tech/blog/sglang-vs-vllm-inference-engine-comparison)). 29% higher throughput than vLLM on H100. Tool-call-friendly architecture.

- Same problems as vLLM: Linux-first, batch-oriented, designed for shared-prefix workloads (RadixAttention's wins disappear on unique prompts — i.e. your workload).
- Genuinely the strongest engine for "agent system with many tool calls and shared prefixes" but that's the SaaS-scale shape, not yours.

**Verdict:** **rejected** for the same reasons as vLLM. **Worth knowing it exists** if FinAgent ever moves off-rig to a cloud GPU.

### 7. TensorRT-LLM *(Apache 2.0, NVIDIA)*

NVIDIA's own engine. Highest single-GPU throughput on supported hardware after engine-compile.

- **Officially supports SM 7.5+** ([TensorRT-LLM support matrix](https://nvidia.github.io/TensorRT-LLM/reference/support-matrix.html)) — your GTX 1650 *technically* qualifies as the minimum supported arch.
- **But:** Turing has 2nd-gen Tensor Cores only, no BF16, no FP8 — most of TensorRT-LLM's win in 2026 comes from FP8 path on Hopper/Blackwell.
- Documented [bad FP16 performance on GTX 1650 cards specifically](https://github.com/LeelaChessZero/lc0/issues/1670) (different from RTX 2060+ Turing).
- Linux-first toolchain; engine-build step is a separate workflow per model per quantization; not a "swap a flag" engine.
- **Newer software releases drop older arch support over time** — minimum SM 7.5 today is no guarantee for next year.

**Verdict:** **rejected.** The single benefit you'd get from TensorRT-LLM on Turing — FP16 throughput — is exactly the precision GTX 1650 cards are known to be bad at. Tooling cost is high; payoff is near zero.

### 8. ExLlamaV3 + TabbyAPI *(MIT, MIT)*

The "fast quantization for consumer GPUs" engine. **EXL3 format is a streamlined QTIP variant with trellis-based encoding optimized for Tensor Core execution** in 16×16 tiles ([EXL3 docs](https://github.com/turboderp-org/exllamav3/blob/master/doc/exl3.md), [DeepWiki — EXL3 Quantization System](https://deepwiki.com/turboderp-org/exllamav3/4-exl3-quantization-system)). Strongest perplexity-per-bit numbers in 2026 outside research quants.

- DFlash + caching improvements: throughput moved from 59 → 178 tok/s on a coding benchmark; 56 → 141 tok/s on an agentic workload ([Kaitchup ExLlamaV3 + TabbyAPI](https://kaitchup.substack.com/p/serving-exllamav3-with-tabbyapi-accuracy)).
- **Requires PyTorch with CUDA 12.4+.** Your CUDA 13.2 is fine.
- **Optimized for "modern consumer-class GPUs"** — explicitly Ampere+ in the README. **Turing CC 7.5 is technically the floor, but Tensor Core layouts are tuned for SM 8.0+**, and the documented [bad FP16 perf on GTX 1650](https://github.com/LeelaChessZero/lc0/issues/1670) applies.
- TabbyAPI provides OpenAI-compat + HF model downloading + Jinja2 chat templates. PydanticAI works.
- **Real cost:** you'd need EXL3-format models, which is a smaller universe than GGUF. Granite 4.1 3B, Cogito 3B, Gemma 4 — you'd be re-quantizing from FP16 weights yourself in many cases.

**Verdict:** **rejected for primary**, with regret. The technology is genuinely state-of-the-art for the 4 GB VRAM problem (high-quality 2–4 bpw quants), but Turing-era cards aren't where the optimizations are tuned, and the format compatibility tax hurts the GGUF investment. **Worth checking back when/if you upgrade GPU.**

### 9. KTransformers *(Apache 2.0, KVCache.AI / Tsinghua)*

The MoE-on-laptop research project. **4.62–19.74× prefill speedup, 1.25–4.09× decode speedup over existing systems** for MoE models ([SOSP'25 paper](https://madsys.cs.tsinghua.edu.cn/publication/ktransformers-unleashing-the-full-potential-of-cpu/gpu-hybrid-inference-for-moe-models/SOSP25-chen.pdf)).

- **Heavily AMX-optimized** — Intel AMX kernels for the prefill phase. **You're on AMD Ryzen 5 4600H — no AMX.** Falls back to AVX-512 (also not present on Zen 2), then AVX2. **The marquee perf claims do not apply.**
- Published benchmarks use A100 (40 GB) and RTX 4080 (16 GB). 4 GB VRAM territory is uncharted.
- Linux-first; Windows support is community-maintained.
- Genuinely strong for the right hardware (Intel CPU with AMX + 16+ GB GPU). Not yours.

**Verdict:** **rejected for hardware mismatch.** The most impressive 2026 inference research, in the wrong room. Re-evaluate if you ever move FinAgent to an Intel + bigger-GPU box.

### 10. MLC LLM *(Apache 2.0, CMU/TVM)*

Cross-platform compile-LLMs-to-deployable-artifacts engine. Native Windows + Vulkan support; runs on iGPU/Apple Silicon/Android/WebGPU/CUDA.

- The Vulkan backend on Windows can extract performance from GPUs llama.cpp can't always reach — interesting in theory for a Turing card.
- **But:** tool-calling + grammar-constrained JSON ecosystem is much thinner than llama.cpp's. PydanticAI integration is more bespoke.
- Compile-per-model workflow trades flexibility for speed.

**Verdict:** **rejected.** Compelling for cross-platform deploys (iOS/Android/web), not for a single Windows server.

### 11. Aphrodite Engine *(AGPLv3, PygmalionAI/Ruliad)*

vLLM PagedAttention + broader quant coverage (AQLM, AWQ, BitNet, ExLlamaV3, GGUF, GPTQ, Marlin). **Takes 92% of GPU VRAM by design.** On a 4 GB card: untenable.

**Verdict:** **rejected.** Same vLLM-shape problems plus AGPL.

### 12. NexaSDK / NexaML *(open-core, Qualcomm/NexaAI)*

New kernel-level engine, Windows-supported, day-0 model support including Granite-4 and Qwen 3-VL. Supports GGUF, MLX, and a proprietary `.nexa` format.

- Main pitch is **NPU acceleration** (9× more energy efficient than CPU-only competitors). **You have no NPU.**
- On CPU/GPU it's effectively another llama.cpp-class wrapper without llama.cpp's mature MoE-offload and grammar tooling.
- Tool-calling/JSON ecosystem is shallow vs llama.cpp.

**Verdict:** **rejected** for primary. **Worth watching** — they're moving fast and the Granite-4 day-0 support is genuinely useful. Revisit in 6 months.

## Tier 3 — dropped or context-only

| Engine | Why dropped |
| --- | --- |
| LMDeploy | vLLM-class, Linux-first, multi-GPU focus. Same shape as vLLM. |
| text-generation-webui (oobabooga) | UI-first wrapper; supports llama.cpp/EXL3/Transformers under the hood. Adds nothing for a server-only role. |
| Jan | Open-source desktop app over llama.cpp. Mac-leaning. Same llama.cpp ceiling; LM Studio is the more polished version. |
| GPT4All | Earlier llama.cpp wrapper. Superseded by Ollama / LM Studio. |
| GPUStack | Cluster orchestration over multiple inference engines — for fleets, not single-rig. |
| HF Transformers / Optimum | Reference, not optimized. Single-user latency on a 4 GB card would be poor. |
| OpenLLM / BentoML | Deployment orchestration, not an inference engine. |
| Triton Inference Server | Datacenter-scale serving; pairs with TensorRT-LLM. Wrong shape. |
| MLX | Apple Silicon only. You're on Windows. |

## The speedup levers — what actually compounds

Independent of engine choice, these are the multipliers in 2026. They stack — KV-quant + flash attention + speculative decoding on the right engine is the real 2–3× win.

| Lever | Free | Quality cost | Engines that have it |
| --- | --- | --- | --- |
| **Flash Attention** | yes (correctness-preserving) | none | llama.cpp, Ollama (env), LM Studio, vLLM, SGLang, ExLlamaV3 |
| **KV-cache q8_0** | ~50% KV-VRAM saving | perplexity Δ 0.002–0.05 (negligible) | llama.cpp, Ollama (env), LM Studio |
| **KV-cache TurboQuant (tq2/tq3/tq4)** | up to ~75% KV-VRAM saving | small but non-zero | llama.cpp, Ollama (newer) |
| **Speculative decoding (draft model)** | 1.5–3× tok/s | none (verified by target) | llama.cpp `--model-draft`, LM Studio (v0.3.10+), vLLM, SGLang |
| **N-gram / prompt-lookup speculation** | 1.2–1.6× tok/s on repetitive prompts | none | llama.cpp |
| **MoE expert-level offload (`--n-cpu-moe`)** | unlocks 26–35B-A3B class on 4 GB VRAM | none | **llama.cpp only** (not Ollama yet) |
| **Grammar/schema-constrained decoding (GBNF / XGrammar / Outlines)** | JSON compliance 90% → 96–98% | none on schema-valid outputs | llama.cpp (GBNF), Ollama (JSON Schema), LM Studio, vLLM (XGrammar), SGLang (FSM) |
| **Better quantization format (EXL3 / IQ4_XS / AWQ)** | ~10–20% quality at same bpw | none | ExLlamaV3, llama.cpp (newer IQ quants), vLLM (AWQ) |

The May-18 question "is structured-outputs in Ollama actually load-bearing for PydanticAI's `ValidationError` failure mode?" generalizes: **every engine should be running grammar-constrained decoding for PydanticAI.** The free quality jump (90% → 98% JSON compliance, [JSONSchemaBench](https://arxiv.org/html/2501.10868v1)) eliminates a class of integration bug.

## The actual decision

The May-24 notes set this up correctly: **the MoE-on-Ollama question is closed in the negative.** Ollama's layer-level offload is documented as ~5–10× slower than expert-level offload for MoE; that's not a tuning fix, it's an architecture gap ([Ollama issue #11772](https://github.com/ollama/ollama/issues/11772)). So:

- If FinAgent v1 stays on a dense 3–4B model (Granite 4.1 3B, Gemma 4 E2B), **the gap between Ollama and llama.cpp is ~10–20%** plus whatever you leave on the table by not setting `OLLAMA_KV_CACHE_TYPE=q8_0` and `OLLAMA_FLASH_ATTENTION=1`. Not transformative. Stay on Ollama, turn on the env vars, ship.
- If FinAgent v1 wants 26–35B-A3B MoE quality (the Gemma 4 26B-A4B / Qwen 3.6 35B-A3B class), **llama.cpp is the only realistic engine on this rig.** Real numbers: 20–30 tok/s with expert offload vs 3–5 tok/s on Ollama. That's the difference between *usable* and *unusable* for the "checkout counter" moment.
- Adding speculative decoding (draft model: Qwen3 0.6B → Qwen3 8B at ~1.9×) on top is a second multiplier that only llama.cpp / LM Studio currently expose cleanly. **Two compounding wins.**

## One-line recommendation

**Two-engine setup:**

1. **Ollama 0.24+** as the dev daemon for fast iteration on dense 3–4B models. Set `OLLAMA_KV_CACHE_TYPE=q8_0` and `OLLAMA_FLASH_ATTENTION=1` today — free 15–30%.
2. **llama.cpp (b9196+ Windows CUDA prebuilt) as the production runtime** for any MoE experiment and as the path forward when 3–4B dense isn't enough. Use `llama-server` with `--n-cpu-moe`, `--flash-attn`, KV-cache q8_0, and optionally `--model-draft` for speculative decoding. PydanticAI talks to it on the same OpenAI-compat shape it uses for Ollama.

**Skip:** vLLM, SGLang, TensorRT-LLM, KTransformers, Aphrodite, MLC, NexaSDK — all wrong-shape or wrong-hardware for this rig.

**Keep an eye on:** ExLlamaV3 (when/if you upgrade GPU past Turing); NexaSDK (if it grows the tool-calling story); LM Studio's `llmster` if Ollama's MoE gap closes and you want a polished daemon.

## Open questions for the next session

1. **Bench llama.cpp `llama-server` on the Fortress against Ollama 0.24.0** with `granite4.1:3b` at concurrency=1, 32K context. Expected delta: 10–20%. If the gap is bigger, investigate Ollama settings before concluding.
2. **Bench Gemma 4 26B-A4B on llama.cpp with `--n-cpu-moe N`** for varying N. Find the inflection where tok/s peaks. This answers whether 26B-A4B is actually usable on this rig — the question the May-18 checklist left open.
3. **Speculative decoding pair for the chosen primary model.** If Granite 4.1 3B is primary, is there a Granite-family ≤500M draft model? If not, prompt-lookup / n-gram decoding is the fallback. Quantify the speedup before relying on it.
4. **Does `llama-server`'s OpenAI-compat tool-call format match PydanticAI's expectations exactly?** [PydanticAI issue #242](https://github.com/pydantic/pydantic-ai/issues/242) flagged divergence with Ollama. Verify before committing.
5. **TurboQuant KV-cache (tq2/tq3/tq4) quality on financial reasoning tasks specifically.** The benchmarks are generic; arithmetic-heavy tasks may be more KV-sensitive. A spot check with FinanceReasoning-style problems would resolve it.

## Sources

### Comparative analyses (2026)

- [5 Best Open-Source LLM Inference Engines in 2026 (vLLM, Ollama, llama.cpp & More) — DEV](https://dev.to/agdex_ai/5-best-open-source-llm-inference-engines-in-2026-vllm-ollama-llamacpp-more-2811)
- [vLLM vs Ollama vs llama.cpp vs TGI — aimadetools 2026](https://www.aimadetools.com/blog/vllm-vs-ollama-vs-llamacpp-vs-tgi/)
- [Ollama vs vLLM vs LM Studio — glukhov.org 2026](https://www.glukhov.org/llm-hosting/comparisons/hosting-llms-ollama-localai-jan-lmstudio-vllm-comparison/)
- [vLLM, Ollama, LM Studio, llama.cpp — BIZON 2026 guide](https://bizon-tech.com/blog/best-llm-inference-engines)
- [llama.cpp vs Ollama 2026 — Markaicode benchmarks](https://markaicode.com/benchmarks/ollama-vs-llamacpp-benchmark/)
- [llama.cpp vs Ollama 2026 — Morph comparison](https://www.morphllm.com/comparisons/llama-cpp-vs-ollama)
- [Ollama, LM Studio, GPT4All are all llama.cpp — DEV](https://dev.to/plasmon_imp/ollama-lm-studio-and-gpt4all-are-all-just-llamacpp-heres-why-performance-still-differs-59h5)
- [SGLang vs vLLM 2026 — particula.tech](https://particula.tech/blog/sglang-vs-vllm-inference-engine-comparison)
- [vLLM vs TensorRT-LLM vs SGLang H100 benchmarks — Spheron](https://www.spheron.network/blog/vllm-vs-tensorrt-llm-vs-sglang-benchmarks/)

### Engine docs & releases

- [llama.cpp releases (Windows CUDA prebuilt)](https://github.com/ggml-org/llama.cpp/releases) + [b9196 update writeup](https://www.knightli.com/en/2026/05/18/llama-cpp-windows-cuda-vulkan-gguf/)
- [llama.cpp build docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md)
- [llama.cpp speculative decoding docs](https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md)
- [llama.cpp MoE offload guide — HuggingFace](https://huggingface.co/blog/Doctor-Shotgun/llamacpp-moe-offload-guide)
- [Qwen 3 235B on consumer hardware via llama.cpp — Medium](https://medium.com/@david.sanftenberg/gpu-poor-how-to-configure-offloading-for-the-qwen-3-235b-a22b-moe-model-using-llama-cpp-13dc15287bed)
- [Ollama KV-cache quantization writeup — smcleod](https://smcleod.net/2024/12/bringing-k/v-context-quantisation-to-ollama/)
- [Ollama PR #15505 — TurboQuant KV cache](https://github.com/ollama/ollama/pull/15505)
- [Ollama MoE expert-offload issue #11772](https://github.com/ollama/ollama/issues/11772)
- [Ollama structured outputs docs](https://docs.ollama.com/capabilities/structured-outputs)
- [Ollama tool calling docs](https://docs.ollama.com/capabilities/tool-calling)
- [LM Studio changelog](https://lmstudio.ai/changelog) + [Codersera 2026 LM Studio guide](https://codersera.com/blog/lm-studio-complete-guide-2026/)
- [LM Studio v0.3.10 speculative decoding blog](https://lmstudio.ai/blog/lmstudio-v0.3.10)
- [vLLM GPU install docs](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/) + [vLLM-Windows community fork](https://github.com/SystemPanic/vllm-windows)
- [TensorRT-LLM support matrix](https://nvidia.github.io/TensorRT-LLM/reference/support-matrix.html)
- [ExLlamaV3 GitHub](https://github.com/turboderp-org/exllamav3) + [EXL3 format docs](https://github.com/turboderp-org/exllamav3/blob/master/doc/exl3.md) + [TabbyAPI GitHub](https://github.com/theroyallab/tabbyAPI)
- [ExLlamaV3 + TabbyAPI accuracy/speed — Kaitchup](https://kaitchup.substack.com/p/serving-exllamav3-with-tabbyapi-accuracy)
- [KTransformers GitHub](https://github.com/kvcache-ai/ktransformers) + [SOSP'25 paper PDF](https://madsys.cs.tsinghua.edu.cn/publication/ktransformers-unleashing-the-full-potential-of-cpu/gpu-hybrid-inference-for-moe-models/SOSP25-chen.pdf)
- [MLC LLM](https://llm.mlc.ai/) + [GitHub](https://github.com/mlc-ai/mlc-llm)
- [llamafile 0.10.0 release — Phoronix](https://www.phoronix.com/news/Mozilla-AI-Llamafile-0.10) + [releases page](https://github.com/mozilla-ai/llamafile/releases)
- [Aphrodite Engine GitHub](https://github.com/aphrodite-engine/aphrodite-engine)
- [NexaSDK GitHub](https://github.com/NexaAI/nexa-sdk)

### Speculative decoding & structured outputs

- [Speculative decoding 2-3x faster — Premai](https://blog.premai.io/speculative-decoding-2-3x-faster-llm-inference-2026/)
- [Speculative decoding explained — Vucense](https://vucense.com/dev-corner/speculative-decoding-explained-2x-faster-local-llms-ollama-llama-cpp-2026/)
- [XGrammar paper (arXiv 2411.15100)](https://arxiv.org/pdf/2411.15100)
- [JSONSchemaBench (arXiv 2501.10868)](https://arxiv.org/html/2501.10868v1)
- [PydanticAI issue #242 — Ollama structured outputs](https://github.com/pydantic/pydantic-ai/issues/242)

### Hardware compatibility (GTX 1650 / Turing CC 7.5)

- [NVIDIA CUDA GPU compute capability reference](https://developer.nvidia.com/cuda/gpus)
- [Turing Compatibility Guide for CUDA Applications](https://docs.nvidia.com/cuda/turing-compatibility-guide/index.html)
- [Bad FP16 performance on GTX 1650 cards — LeelaChessZero issue #1670](https://github.com/LeelaChessZero/lc0/issues/1670)

### Quantization

- [Quantization Methods Compared (GGUF/AWQ/GPTQ/EXL2/NVFP4) — ai.rs](https://ai.rs/ai-developer/quantization-methods-compared)
- [oobabooga blog — GPTQ/AWQ/EXL2/Q4_K_M perplexity comparison](https://oobabooga.github.io/blog/posts/gptq-awq-exl2-llamacpp/)
