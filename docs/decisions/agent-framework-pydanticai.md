# Agent Framework Decision: PydanticAI

> **Status:** Decided  
> **Date:** May 17, 2026  
> **Decision:** Use [PydanticAI](https://pydantic.dev/docs/ai/overview/) (v1.x, currently 1.85.1) as the agent framework.  
> **Supersedes:** [Agent Framework LangChainGo over Google ADK Go](agent-framework-langchaingo-over-google-adk-go.md)  
> **Depends on:** [Language Python over Go](language-python-over-go.md)

---

## Context

Following the [switch to Python](language-python-over-go.md), the next decision is which agent framework to build on. The May 2026 Python agent framework landscape was researched against the [Product Picture v1.0.0](../product-picture/v1.0.0.md) requirements.

**Candidates evaluated:**
- **PydanticAI** (v1, shipped September 2025) — type-safe, Pydantic-native
- **LangGraph** — stateful graph-based agent orchestration
- *(Ruled out: LangChain, CrewAI, AutoGen — too heavy or wrong shape for single-agent use case)*

---

## Decision: PydanticAI

### Mapping against Product Picture requirements

| Requirement | PydanticAI | LangGraph | Winner |
|---|---|---|---|
| Type safety on money math | ✅ Core design — typed agents, tools, dependencies, outputs | ⚠️ Possible but not native | **PydanticAI** |
| Lower token consumption on slow local model | ✅ 2.7× lower per-task tokens | Baseline | **PydanticAI** (critical at 5–15 tok/sec) |
| Velocity (LOC, learning curve) | ✅ 160 LOC for equivalent chat app, FastAPI mental model transfers | ⚠️ 280 LOC, steep curve, fragmented docs | **PydanticAI** |
| Simple tool calling (log txn, simulate, query) | ✅ Decorator + typed return | ✅ Decorator + manual parsing | **PydanticAI** |
| Forward simulation as deterministic tool | ✅ Tool returns typed Pydantic model | ✅ Tool returns dict | **PydanticAI** |
| Local Ollama support | ✅ Native; Ollama v0.5+ JSON-schema structured outputs | ✅ Native via LangChain | Tie |
| Streaming responses (SSE) | ✅ `pydantic-ai-stream` with cancellation | ✅ `stream()` iterator | Tie |
| FastAPI integration | ✅✅ Same team, same Pydantic backbone | ✅ Works fine | **PydanticAI** |
| Memory (mem0 personalization, history) | ✅ via Composio MCP / `pydantic-ai-mem0` / 5-line add | ✅✅ Native checkpointer + mem0 patterns | **LangGraph** |
| Scheduled briefings (APScheduler) | Works | Works | Tie |
| Agent shape (individual requests, not long branching workflows) | ✅ Exactly its sweet spot | ⚠️ Designed for graphs we don't need | **PydanticAI** |
| Production track record in finance | ✅ Boosted.ai (50K+ workflows); explicitly recommended "where type correctness is load-bearing" | General | **PydanticAI** |

**Score: PydanticAI 7, LangGraph 1, Tie 4.**

### Concrete numbers (May 2026 benchmarks)

- **PydanticAI:** 160 LOC for equivalent chat app, 23 bugs caught in dev, $390 testing cost
- **LangGraph:** 280 LOC, equivalent functionality
- **CrewAI:** 420 LOC, $1,088 testing cost
- **PydanticAI performance:** 44% faster P95 latency, 5× fewer errors under load, 2.7× lower token consumption

---

## Why NOT LangGraph

| Concern | Detail |
|---|---|
| Steep learning curve | Documentation fragmented across LangGraph / LangChain / LangSmith; "stack traces run deep" |
| Verbose for single-agent use case | LangGraph's strength is multi-agent orchestration and long branching workflows — neither applies to v1 finance agent |
| Higher token consumption | Matters significantly on a slow local model |
| No native type safety | Manual JSON parsing in tools loses the Go-replacement benefit |

---

## When LangGraph WOULD Win (so future-us knows)

- If finance evolves into multi-agent orchestration (e.g., separate agents for ingestion, simulation, briefings coordinating)
- If briefings become long, branching, multi-step plans needing crash-resilient checkpointing across hours
- If LangSmith's observability stack becomes essential

The 2026 modal pattern *"PydanticAI for agent logic, LangGraph for orchestration"* is the migration path if those needs emerge. Both can coexist — PydanticAI agents become LangGraph nodes.

---

## The Known Risk: Small-Model Validation Brittleness

**PydanticAI's strict validation can be brittle with small local models.** Quantized 8B models occasionally produce malformed JSON; PydanticAI throws `ValidationError`. LangGraph is more permissive because it does manual parsing.

### Mitigation (well-documented, ~20 lines)

1. **Use Ollama v0.5+ structured outputs** — JSON schema enforcement at the model level dramatically improves reliability
2. **Wrap tool calls in PydanticAI's retry mechanism** — built in, configurable per agent
3. **Fallback prompt** — on `ValidationError`, retry with a more explicit "return ONLY JSON matching this schema" instruction

This is a known/solved problem in 2026 — not a blocker.

---

## Architectural Safeguard

All agent calls go through a thin Python `Protocol` so the framework can be swapped without touching the channel layer (Signal/web), FastAPI handlers, PostgreSQL schema, financial logic, or tool implementations:

```python
from typing import Protocol

class AgentRunner(Protocol):
    async def run(self, message: str, session_id: str) -> str: ...
    async def stream(self, message: str, session_id: str) -> AsyncIterator[str]: ...
```

A `PydanticAIRunner` implementation satisfies this. A future `LangGraphRunner` could drop in.

---

## Sources (May 2026)

- [LangGraph vs PydanticAI Key Differences 2026 (AgentsIndex)](https://agentsindex.ai/compare/langgraph-vs-pydanticai)
- [PydanticAI v1: Rewriting the Python Agent Stack (AgentMarketCap, Apr 2026)](https://agentmarketcap.ai/blog/2026/04/06/pydanticai-python-agent-framework-langgraph-crewai-comparison)
- [PydanticAI vs LangChain vs LangGraph: Which Wins in 2026 (aiagentskit)](https://aiagentskit.com/blog/pydantic-ai-vs-langchain-vs-langgraph/)
- [Choosing an agent framework (Speakeasy 2026)](https://www.speakeasy.com/blog/ai-agent-framework-comparison)
- [The missing piece in the FastAPI + Pydantic AI agentic stack (Gel Blog)](https://www.geldata.com/blog/the-missing-piece-in-the-fastapi-pydantic-ai-agentic-stack)
- [Adding a Memory layer to PydanticAI Agents (Dream AI)](https://medium.com/@dreamai/adding-a-memory-layer-to-pydanticai-agents-5e7b257590f4)
- [Pydantic AI Persistent Memory: 5 Lines of Code (Hindsight, Mar 2026)](https://hindsight.vectorize.io/blog/2026/03/09/pydantic-ai-persistent-memory)
- [Building a Pydantic Agent with Local OLLAMA Model and Tool Creation](https://medium.com/@jageenshukla/building-a-pydantic-agent-with-a-local-ollama-model-and-tool-creation-63dbf2bdc6ee)
- [Pydantic AI Ollama docs](https://pydantic.dev/docs/ai/models/ollama/)

---

## Related Documents

- [Language Python over Go](language-python-over-go.md) — language-level decision this depends on
- [Agent Framework LangChainGo over Google ADK Go](agent-framework-langchaingo-over-google-adk-go.md) — superseded historical decision
- [Product Picture v1.0.0](../product-picture/v1.0.0.md) — requirements this framework must serve
- [Infrastructure](../infrastructure.md) — hardware constraints (4GB VRAM, 8B local model)
