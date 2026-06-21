# Agent Framework Decision: LangChainGo over Google ADK Go

> ⚠️ **SUPERSEDED — May 17, 2026.** The language decision shifted from Go to Python; this Go-based framework choice no longer applies. Kept for historical reasoning context.  
> **Current decisions:** [[Language Python over Go]] + [[Agent Framework PydanticAI]]

---

> **Status:** ~~Decided~~ Superseded  
> **Date:** April 2026 (decided), May 17, 2026 (superseded)  
> **Decision:** ~~Use [LangChainGo (`tmc/langchaingo`)](https://github.com/tmc/langchaingo) as the AI agent orchestration framework for V1.~~

---

## Context

The Personal AI Financial Assistant requires an agent framework to handle:

- Parsing natural language expense messages from Telegram into structured JSON
- Orchestrating a ReAct-style tool-calling loop (e.g., calling the Tax Calculator, Forex MCP, or Safe-to-Spend Oracle)
- Maintaining multi-turn financial context across a conversation session
- Running **100% locally** on the Asus TUF A15, with Ollama + Qwen3 8B as the LLM backend

The two primary Go-native candidates evaluated were **LangChainGo** and **Google ADK Go**.

---

## Decision: LangChainGo

### Reason 1: Ollama is a First-Class Provider

The entire infrastructure architecture is built around **absolute data sovereignty** — all LLM inference runs locally on the Asus TUF, with zero data leaving the home network. LangChainGo treats Ollama as a first-class LLM provider with a dedicated, battle-tested package (`llms/ollama`). It works out of the box with a single `go get`.

Google ADK Go, by contrast, connects to Ollama through a **LiteLLM bridge shim** — Ollama is an afterthought in an ecosystem built around Gemini. Google has commercial incentives to tighten the Gemini dependency in future releases, which could create friction for a fully local, privacy-first stack.

### Reason 2: Proven ReAct Agent Loop with Tool Calling

The core agent behaviour — receive message → understand intent → select and call a tool (Tax MCP, Forex API, Wishlist Scraper, Safe-to-Spend Oracle) → synthesise response — maps directly onto LangChainGo's built-in **ReAct agent executor**. This loop is stable, well-documented, and has 75+ working examples in the repository, including Ollama-specific examples.

ADK Go also supports tool calling, but its agent loop is optimised for Gemini's function calling API. With a local model like Qwen3 8B, structured tool invocation requires additional prompt hardening that is better documented in the LangChainGo community.

### Reason 3: No Google Ecosystem Lock-In

ADK Go is deeply integrated with the Google Cloud ecosystem: Vertex AI, Firebase Genkit, and Google's A2A agent protocol. For this project, these integrations are not just unnecessary — they are **actively undesirable**. The infrastructure explicitly excludes cloud-managed services to prevent any financial data leakage. LangChainGo has zero forced cloud dependencies; it is a pure Go library.

### Reason 4: Go-Specific Community & Documentation

Google ADK's large community is almost entirely Python-based. The Go port (`google/adk-go`) has a very small Go-specific community — as of April 2026, there is only **1 Go repository** tagged under the `google-adk` GitHub topic. Finding Go-specific debugging help, Ollama integration patterns, or PostgreSQL memory backend examples is significantly harder with ADK Go.

LangChainGo, while smaller in absolute terms (~8,952 stars), has an entirely Go-specific community with active GitHub Discussions, Go-native documentation, and direct relevance to this project's stack.

### Reason 5: Mature PostgreSQL Memory Backend

The financial assistant requires persistent, multi-turn memory backed by the **local PostgreSQL ledger** — the same database that stores the transaction history and goals. LangChainGo has a native PostgreSQL-compatible memory backend that integrates directly with pgvector. This means conversation context, financial goals, and transaction embeddings can all live in the same Postgres instance, managed by the existing Go + Prisma data layer.

ADK Go has session/memory abstractions, but they are newer and less documented for PostgreSQL-specific use cases.

---

## Why NOT Google ADK Go

| Concern | Detail |
|---|---|
| **Gemini-first design** | Built around Gemini's API; Ollama support is via a LiteLLM shim, not native |
| **Google cloud lock-in risk** | Ecosystem pushes toward Vertex AI, Firebase — contrary to our data sovereignty principle |
| **Go community is nascent** | Only 1 Go repo under the `google-adk` topic as of April 2026; Python community doesn't transfer |
| **v1.0.0 just shipped** | Released March 30, 2026 — too new for a production personal finance system with real money data |
| **Sparse local LLM examples** | Very few community examples of ADK Go + Ollama + local models; debugging will be harder |

---

## Accepted Trade-offs of LangChainGo

| Trade-off | Mitigation |
|---|---|
| **Single maintainer risk** (tmc/Travis Cline) | Architecture isolates the framework behind a Go interface — swappable if needed |
| **227+ open issues, slow PR merges** | Pin to a stable release tag; monitor the fork `arkvt/langchaingo` for critical patches |
| **Not a Google-backed project** | Community-maintained means no commercial pressure to add cloud dependencies |

---

## Architectural Safeguard

To protect against framework lock-in (from either LangChainGo or any future alternative), **all agent calls will be wrapped behind a thin Go interface**:

```go
type AgentRunner interface {
    Run(ctx context.Context, input string, sessionID string) (string, error)
}
```

The LangChainGo implementation satisfies this interface. If Google ADK Go matures significantly in 12–18 months, or if LangChainGo stalls, a drop-in replacement can be written without touching the Telegram layer, PostgreSQL schema, financial logic, or MCP tool servers.

---

## Related Documents

- [[Infrastructure]] — Hardware and stack architecture
- [[Capabilities/v1.0.0]] — V1 feature set (Safe-to-Spend Oracle, Tax Engine, etc.)
- [[Tools and MCPs/v1.0.0]] — MCP tool servers the agent will call
