# Language Decision: Python over Go

> **Status:** Decided  
> **Date:** May 17, 2026  
> **Decision:** Use **Python 3.12+** as the primary implementation language.  
> **Supersedes:** [Agent Framework LangChainGo over Google ADK Go](agent-framework-langchaingo-over-google-adk-go.md) (the Go-based framework choice no longer applies)

---

## Context

The initial architecture (April 2026) picked Go (Golang), primarily on the premise that Go is "fast and lightweight." After locking the [Product Picture v1.0.0](../product-picture/v1.0.0.md) in May 2026, we revisited the language decision because:

1. The original "fast and lightweight" premise didn't account for the actual performance bottleneck (local LLM inference on a 4GB VRAM card)
2. The Product Picture revealed Python-leaning needs: pre-built agent loops, mature PDF parsing for Indian bank statements, mem0-style memory frameworks
3. The user's stated priority shifted explicitly to **velocity** ("I want to solve the problem faster")

---

## What the Brainstorm Revealed

### The original Go reasoning didn't survive scrutiny

| Original assumption | What we found |
|---|---|
| Go is faster | True in absolute terms — but **invisible**. The bottleneck is Ollama at 5–15 tok/sec on a quantized 8B model. Orchestration runtime is microseconds inside seconds of LLM latency. |
| Go is lightweight | True (30MB binary vs 500MB Python service) — but immaterial on a 24GB machine. |
| Go ecosystem is sufficient | LangChainGo is thin compared to the Python AI ecosystem. We'd build by hand what already exists in Python. |

### Python ecosystem wins that mattered for THIS project specifically

- **PDF parsing of bank statements** — confirmed: `pdfplumber` / `pymupdf` work smoothly on Indian bank PDFs
- **Pre-built agent loops** — the user wants to customize an existing pattern, not write a ReAct loop from scratch
- **Memory layer (mem0)** — bolt-on personalization with low lock-in; no Go equivalent worth using
- **Larger answer pool + better AI codegen** — the user uses AI codegen heavily; Python's bigger corpus yields better generated code quality

### What Go genuinely gave up — and how Python mitigates it

| Go advantage | Python mitigation |
|---|---|
| Compile-time safety on money math | `decimal.Decimal` + Pydantic v2 validators + mypy strict mode |
| Single static binary deploy | `uv` + Docker Compose (single command up/down) |
| Lower memory footprint | Irrelevant on 24GB RAM |

---

## Decision Criteria Mapped to User Needs

| Priority | Go answer | Python answer | Winner |
|---|---|---|---|
| Ship fast (3–6 mo weekend pace) | Write framework code | Use existing frameworks | **Python** |
| Type safety on money math | Native | Pydantic + Decimal | Python (close) |
| Local LLM via Ollama | Works | Works | Tie |
| PDF parsing of bank statements | Painful | Smooth | **Python** |
| Memory layer | Build from scratch | mem0 + pgvector | **Python** |
| Pre-built agent flows to customize | Write own | PydanticAI / LangGraph | **Python** |
| Single-binary deploy | ✅ | Docker + uv | Go (but trivial gap) |
| Runtime perf | Faster (but invisible) | Slower (but invisible) | Tie (no observable diff) |

---

## Accepted Trade-offs

| Trade-off | Mitigation |
|---|---|
| Larger deploy artifact (Python deps vs Go binary) | Docker Compose + official `uv` image; immaterial on 24GB RAM |
| Less type safety than Go's compiler | Pydantic v2 + `Decimal` + mypy strict mode |
| Heavier process footprint | Doesn't matter on this hardware |
| Async complexity (asyncio gotchas) | FastAPI + PydanticAI standardize the patterns |

---

## Architectural Safeguard (Preserved from Original Decision)

The principle of wrapping framework calls behind a thin interface survives the language change. All agent calls go through an `AgentRunner`-style abstraction (now a Python `Protocol` or ABC) so that the framework can be swapped without touching the channel layer (Signal/web), PostgreSQL schema, financial logic, or tool implementations.

---

## Related Documents

- [Agent Framework PydanticAI](agent-framework-pydanticai.md) — framework choice within Python
- [Agent Framework LangChainGo over Google ADK Go](agent-framework-langchaingo-over-google-adk-go.md) — superseded historical decision (kept for context)
- [Product Picture v1.0.0](../product-picture/v1.0.0.md) — the spec this stack must implement
- [Infrastructure](../infrastructure.md) — hardware constraints (4GB VRAM remains binding)
