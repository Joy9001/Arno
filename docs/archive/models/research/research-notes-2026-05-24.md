# Independent Model Research — FinAgent on the Fortress

## How I framed the problem

Three hard constraints — anything failing one is out:

- **4 GB VRAM** ceiling (binding constraint)
- **Native tool calling + structured JSON** (PydanticAI is non-negotiable)
- **Permissive license** (privacy stance on financial data)

Plus three soft preferences for *finance specifically*:

- Strong **arithmetic / numerical reasoning** (GSM8K-ish capability)
- **Multi-step planning** (afford-X-on-Y-EMI questions)
- **Hinglish/INR** handling

## What the 2026 finance leaderboards actually say

Honest answer: **they say almost nothing useful for sub-10B open models.** I checked all the main ones:

- [FinanceBench](https://awesomeagents.ai/leaderboards/finance-llm-leaderboard/) — 2026 leaderboard has only two open entries (Llama 4 Maverick, FinGPT) and explicitly notes "most labs do not publish scores on financial benchmarks"
- [Open Financial LLM Leaderboard (FINOS)](https://huggingface.co/spaces/finosfoundation/Open-Financial-LLM-Leaderboard) — exists but sparse coverage for current small models
- [FinanceReasoning (arXiv 2506.05828)](https://arxiv.org/abs/2506.05828) — 2,238 problems, evaluated *one* open sub-10B model (QwQ-32B-Preview); everything else tested is ≥32B or proprietary
- [FinBen](https://arxiv.org/pdf/2402.12659) — 36 datasets, 24 tasks, but the public scoreboard predates the 2026 model wave

This means **you cannot pick a finance model from finance benchmarks alone.** The choice has to be made by proxy: math reasoning + tool-call reliability + structured output + the right language coverage.

## Three real proxy signals (verified, 2026)

| Signal | Best small open model in 2026 |
| --- | --- |
| BFCL V3 tool calling (3B class) | **Granite 4.1 3B** at 60.8 |
| GSM8K math (3B class) | **Granite 4.1 3B** at 86.88 |
| Self-reported FC accuracy (3B class) | **Cogito v1 3B** at 92.8% simple / 91% multi |
| MATH-500 / Math reasoning (sub-7B) | **Phi-4-mini-reasoning** beats DeepSeek-R1-Distill-Qwen-7B by 3.2 pts |
| Hinglish/Indic coverage (small) | **Gemma 4 E2B** (140+ languages, top-40 verified incl. Hindi) |

## The independent shortlist

### Tier 1 — primary candidates

**1. Granite 4.1 3B** *(Apache 2.0, 2.1 GB disk, fully fits 4 GB VRAM)*

- Released [April 29, 2026 by IBM](https://research.ibm.com/blog/granite-4-1-ai-foundation-models). Trained on 15T tokens with explicit late-stage math/reasoning curriculum.
- The IBM Granite line is *purpose-built* for "reliably calling tools and returning consistent outputs" — exactly the PydanticAI failure mode you're trying to avoid.
- GSM8K 86.88 is the strongest math score in this size class besides Phi-4-mini.
- **The most boring, most reliable pick.** It will probably not be the warmest in chat, but it is the least likely to break PydanticAI at integration time.
- Real weakness: IBM training is English-enterprise-heavy. Hinglish is the gap.

**2. Cogito v1 3B** *(Llama 3.2 license, 2.2 GB disk, fits VRAM)*

- Available on Ollama as `cogito:3b`. Self-reported 92.8% on simple tool calls, 91%+ on multi-tool — these numbers beat Granite if they hold up on independent eval (worth verifying yourself).
- Trained in 30+ languages, hybrid reasoning mode (toggleable via "Enable deep thinking subroutine" in system prompt) — useful for hard EMI/afford questions.
- **Caveat the prior notes missed:** Cogito v1 was the April 2025 release. Deep Cogito's v2 (July 2025) only shipped 70B+ sizes. So the small Cogito is now ~13 months old by 2026 standards — frozen, while Granite is brand new.
- License is Llama 3.2 community license (not Apache 2.0). Commercially permissive up to 700M MAU. For personal use, fine — but worth knowing it's not as clean as Apache.

**3. Gemma 4 E2B** *(Apache 2.0 — confirmed; 7.2 GB disk Q4_K_M, CPU spill needed)*

- Released [April 2, 2026 by Google](https://deepmind.google/models/gemma/gemma-4/). **License flipped from Gemma Terms to Apache 2.0** — your prior notes' concern about Gemma licensing is *resolved*. Clean for financial data.
- Trained on 140+ languages with human-verified eval on top 40 — explicitly includes **Hindi, Tamil, Telugu**. This is the only major model that ships verified Indic out of the box.
- Native function calling, 128K context, multimodal (text/image/audio — overkill for FinAgent but you can't strip it).
- Real cost: at 7.2 GB on disk it won't fit GPU-only on 4 GB VRAM. Expect CPU spill, roughly 8–15 tok/s — between Granite and qwen3.5:9b.
- **If Hinglish quality matters more than speed: this is the pick.**

### Tier 2 — specialist roles

**4. Phi-4-mini-reasoning 3.8B** *(MIT)*

- Beats DeepSeek-R1-Distill-Qwen-7B on Math-500. Comparable to o1-mini on math benchmarks. This is the strongest small math reasoner in 2026.
- But: documented Ollama integration issues — [`tool_calls` returning empty in LangChain](https://github.com/ollama/ollama/issues/9437), parallel function calling fails, needs custom Modelfile.
- **Don't use as primary** — use as a *math co-processor* invoked by the primary agent when arithmetic precision matters (EMI schedules, compounding, tax shadows).

**5. Qwen 3.5 4B** *(Apache 2.0)*

- The JDHodges 2026 #1 at 97.5%. Independent confirmation from BFCL V4 of *larger* Qwen 3.5 variants (27B at 68.5%) suggests the small one is genuinely strong.
- Vision-encoder weights eat VRAM you can't recover. Already benched at 7.8 tok/s on your rig.
- Strong Hinglish from Qwen lineage. Solid backup if Cogito's tool-call self-reports don't hold up.

### Tier 3 — architectural decision, not a model decision

**The MoE question (Gemma 4 26B-A4B / Qwen3 35B-A3B) is really an inference-engine question.**

Concrete state as of May 2026:

- **[llama.cpp has `--n-cpu-moe` and `--override-tensor`](https://github.com/ggml-org/llama.cpp/discussions/18049)** — true expert-level offload. People are running Qwen-3 235B-A22B on 8 GB GPUs at usable speeds.
- **Ollama still uses layer-level offload** for MoE. [Issue #11772](https://github.com/ollama/ollama/issues/11772) is open and unresolved. Best case from the discussion: 5–10× slower than expert-level offload would deliver. Ollama PR [#14703](https://github.com/ollama/ollama/pull/14703) is adding `sarvam_moe` architecture support but the general MoE-offload strategy isn't there yet.

**So:** Gemma 4 26B-A4B on Ollama probably hits 3–5 tok/s (collapse to dense). On llama.cpp with expert offload, 15–30 tok/s is realistic. **The real choice is: do you switch from Ollama to llama.cpp to unlock MoE?** That's a much bigger commitment than picking a model.

### Dropped — with 2026 reasons

| Model | Why I rejected it |
| --- | --- |
| Finance-Llama 8B ([martain7r/finance-llama-8b](https://ollama.com/martain7r/finance-llama-8b)) | 8B dense → already benched unusable at 3.4 tok/s on this rig. Also no documented tool calling — disqualifying for PydanticAI regardless of speed. |
| Llama 4 Scout | 109B MoE / 17B active — too big even with perfect MoE offload. |
| Hermes 4 (NousResearch) | Strong FC reputation, but [only 36B/70B/405B variants released](https://huggingface.co/NousResearch/Hermes-4-70B). Hermes 3 Llama 3.2 3B exists but is stale by 2026 standards. |
| Mistral Small 4 | Released March 2026 but proprietary closed for the small variant. |
| Sarvam-30B / Sarvam-M (24B) | Best 2026 Indic models, [open-sourced March 2026](https://www.sarvam.ai/blogs/sarvam-m). But too big for primary, community Ollama tags only, tool use not documented. Better as a *preprocessor* in v2. |
| Qwen 3.6 small | **Doesn't exist** as of May 2026. [Qwen 3.6 shipped April 2026](https://github.com/QwenLM/Qwen3.6) only in 27B dense + 35B-A3B MoE. No small variant yet. |
| Phi-4-mini (instruct, not reasoning) | Solid base but Ollama FC integration is brittle. Use the reasoning variant for math co-processing instead. |
| Granite 4.1 8B | GSM8K 92.49 is tempting but 8B dense = 3.4 tok/s on this rig. Will be reachable only via the same llama.cpp switch as MoE. |

## What the prior notes got right vs. what I'd change

**Right and still right:**

- Killing qwen3.5:9b ✓
- 4B-class as the sweet spot ✓
- Dropping the abliterated model ✓
- Treating MoE as the big unknown ✓

**What I'd change after this independent pass:**

1. **Promote Granite 4.1 3B from "speed king" to *primary*.** The 2026 evidence — BFCL V3 60.8, GSM8K 86.88, IBM's explicit "consistent tool-call output" design goal — makes it the best-aligned model for a *PydanticAI finance agent specifically*. The notes treat it as one of three Tier-1 picks; I'd treat it as the default and only switch if Hinglish bombs.

2. **Add Cogito v1 3B to Tier 1.** The original notes don't list it. It's on Ollama, fits VRAM, has self-reported tool-call scores that beat Granite, has 30+ language coverage including likely better Hinglish, and has the hybrid-reasoning toggle that's useful for hard financial questions. Stale (April 2025) but still relevant.

3. **Demote Gemma 4 26B and Qwen3 30B from "highest payoff" experiments.** As of May 2026, the MoE-on-Ollama question is *answered, not open*: Ollama's expert offload is not there yet. Pulling either is a coin-flip on whether you've spent 18+ GB and 30 minutes for nothing. **Better experiment: try Gemma 4 E2B at 7.2 GB** — it gets you the Apache 2.0 + verified Hindi proposition without the MoE gamble.

4. **Reframe the MoE play as "switch inference engines, not models."** If you want 30B-class quality on this rig in 2026, the realistic path is llama.cpp + `--n-cpu-moe`, not Ollama. That's worth a one-time setup investment.

5. **Use Phi-4-mini-reasoning as a tool, not the primary.** Drop it from the "warm chat" comparison; add it as a specialist your agent invokes for math. Pairs better with how PydanticAI thinks about tools anyway.

6. **There's a 2025 paper ([arXiv:2509.14180](https://arxiv.org/abs/2509.14180)) that fine-tuned Qwen-3-8B on 19K behaviorally-grounded personal-finance reasoning chains** and released both model and dataset openly. The model is too big for your rig directly, but **the dataset is the interesting artifact** — if you ever want to fine-tune Granite 4.1 3B for FinAgent specifically, that's the corpus to start from.

## My one-line recommendation

**Bench Granite 4.1 3B first.** If its Hinglish is good enough → done, daily driver, ship it. If Hinglish fails → fall back to Gemma 4 E2B (CPU spill, slower, but Hindi is verified). Cogito 3B is the wildcard worth a quick bench. Everything bigger is a llama.cpp project, not a model project.

Sources:

- [IBM Granite 4.1 release — IBM Research](https://research.ibm.com/blog/granite-4-1-ai-foundation-models)
- [Granite 4.1 BFCL V3 + GSM8K scores — Aimadetools 2026 guide](https://www.aimadetools.com/blog/granite-4-1-complete-guide/)
- [Berkeley Function Calling Leaderboard V4](https://gorilla.cs.berkeley.edu/leaderboard.html)
- [BFCL V4 results — llm-stats.com](https://llm-stats.com/benchmarks/bfcl-v4)
- [Gemma 4 — DeepMind announcement](https://deepmind.google/models/gemma/gemma-4/)
- [Gemma 4 Apache 2.0 license analysis — MindStudio](https://www.mindstudio.ai/blog/gemma-4-apache-2-license-commercial-use)
- [Gemma 4 multilingual coverage — Milvus reference](https://milvus.io/ai-quick-reference/what-multilingual-capabilities-does-gemma-4-have)
- [Cogito v1 — Deep Cogito](https://www.deepcogito.com/research/cogito-v1-preview)
- [Cogito v2 — Deep Cogito](https://www.deepcogito.com/research/cogito-v2-preview)
- [Cogito on Ollama](https://ollama.com/library/cogito)
- [Phi-4-mini-reasoning paper](https://arxiv.org/abs/2504.21233)
- [Phi-4-mini Ollama tool-call issue #9437](https://github.com/ollama/ollama/issues/9437)
- [Open Financial LLM Leaderboard (FINOS)](https://huggingface.co/spaces/finosfoundation/Open-Financial-LLM-Leaderboard)
- [Awesome Agents 2026 Finance LLM Leaderboard](https://awesomeagents.ai/leaderboards/finance-llm-leaderboard/)
- [FinanceReasoning benchmark paper](https://arxiv.org/abs/2506.05828)
- [Personal Finance LLM reasoning paper (Qwen-3-8B fine-tune)](https://arxiv.org/abs/2509.14180)
- [Ollama MoE offload issue #11772](https://github.com/ollama/ollama/issues/11772)
- [llama.cpp MoE offload guide — HuggingFace](https://huggingface.co/blog/Doctor-Shotgun/llamacpp-moe-offload-guide)
- [Sarvam-M release — Sarvam AI](https://www.sarvam.ai/blogs/sarvam-m)
- [Qwen 3.6 — GitHub](https://github.com/QwenLM/Qwen3.6)
