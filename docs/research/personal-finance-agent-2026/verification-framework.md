# Verification framework for finance tools

Evidence note, November 12, 2026. This records what I verified about the exact Pydantic AI and
Pydantic AI Harness tools that could carry personal-finance capabilities, for a catalog built on
tested facts.

I keep four classes apart: a *framework capability* (a class with an exact import), a *protocol*
(MCP and its transports), an *external provider* (vendor API, key, terms, egress), and an
*application financial function* (Arno's tools and engine). A symbol in the wheel proves its
contract, not a live call or output quality. I made no model requests, connections, or personal-data
reads. Retrieved November 12, 2026.

## What Arno runs

The chat path builds `OpenAIChatModel` over `OpenAIProvider`, or `OllamaProvider` when the base URL
says Ollama, from one OpenAI-compatible endpoint
([models.py:95](../../../src/arno/agent/models.py#L95),
[models.py:111](../../../src/arno/agent/models.py#L111),
[models.py:123](../../../src/arno/agent/models.py#L123)). Only the batch path builds a native
Responses, Anthropic, or Google model, for categorization, not chat
([models.py:208](../../../src/arno/agent/models.py#L208)). The agent declares `output_type=[str,
DeferredToolRequests]` and Code Mode over the read toolset
([builder.py:194](../../../src/arno/agent/builder.py#L194),
[capabilities.py:388](../../../src/arno/agent/capabilities.py#L388)).

Standard Chat Completions does not provide a portable native search, fetch, or code-execution path
([native tools](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/)). `OpenAIChatModel`
raises `UserError` for a native search without a local fallback
(`.venv/Lib/site-packages/pydantic_ai/models/openai.py:1612`), and its profile removes
`WebSearchTool` unless the provider sets `openai_chat_supports_web_search` (`openai.py:1601`).
`WebFetchTool` is not supported by this transport. Without an explicitly supported provider/model
search profile or a fallback, the native-only defaults are not a portable path for Arno. The source
contains an `openai_chat_supports_web_search` exception; this survey did not establish that Arno's
configured provider supports it.

The mechanism is exact. `NativeOrLocalTool` resolves `local=None` to no fallback, `True` to the
named strategy, and a callable to a `Tool`
(`.venv/Lib/site-packages/pydantic_ai/capabilities/native_or_local.py:123`), and marks the local
tool `unless_native` (`native_or_local.py:235`). Locked and installed: `pydantic-ai` 2.64.0,
`pydantic-ai-slim` 2.64.0, `pydantic-ai-harness` 0.64.0, `pydantic-monty` 1.4.0, `fastmcp-slim`
4.0.0, `ddgs` 9.16.1 ([pyproject.toml:16](../../../pyproject.toml#L16); `uv.lock`). `markdownify`
and `beautifulsoup4` are absent from the venv, so the fetch fallback is not runnable today.

## Matrix

"Installed" means I read or imported the symbol in the locked wheel, not that a call succeeded.
"Existing" means Arno already uses it.

| Symbol and import | Primary source | Installed and locked | Purpose | Data path | Portability | New extra | Status |
|---|---|---|---|---|---|---|---|
| `WebSearch` (`pydantic_ai.capabilities`) | [Web search](https://pydantic.dev/docs/ai/capabilities/web-search/) | 2.64.0; native default; `local=True` = DuckDuckGo | Search with native preference | DuckDuckGo via `ddgs`, or provider | Native requires a supported provider/profile; client fallback is transport-independent | None (`ddgs` present) | Candidate |
| `WebFetch` (`pydantic_ai.capabilities`) | [Web fetch](https://pydantic.dev/docs/ai/capabilities/web-fetch/) | 2.64.0; native default; `local=True` needs `web-fetch` | Read one URL as markdown | Local HTTP or provider fetch | Native: Anthropic/Google; client fallback is transport-independent | `markdownify`, `beautifulsoup4` missing | Candidate, blocked |
| `duckduckgo_search_tool` (`pydantic_ai.common_tools.duckduckgo`) | [Common tools](https://pydantic.dev/docs/ai/tools-toolsets/common-tools/) | Importable; tool `duckduckgo_search`; keys `title`, `href`, `body` | Explicit search | DuckDuckGo | Same everywhere | None | Candidate |
| `web_fetch_tool` (`pydantic_ai.common_tools.web_fetch`) | [Common tools](https://pydantic.dev/docs/ai/tools-toolsets/common-tools/) | Source present; import fails without `markdownify` | Explicit fetch | Direct HTTP, SSRF-protected | Same everywhere | `web-fetch` | Candidate, blocked |
| `MCP` (`pydantic_ai.capabilities`); `MCPToolset`, `StreamableHttpTransport` (`pydantic_ai.mcp`, `fastmcp.client.transports`) | [MCP capability](https://pydantic.dev/docs/ai/capabilities/mcp/), [client](https://pydantic.dev/docs/ai/mcp/client/) | `pydantic_ai.mcp` imports; FastMCP 4.0.0; local by default | MCP server as tools | Fixed URL over Streamable HTTP, SSE, or stdio | Same locally; native only where supported | `mcp` extra (satisfied) | Candidate |
| `ExaSearch`, `ExaAgent` (`pydantic_ai_harness.exa`) | [Exa](https://pydantic.dev/docs/ai/harness/exa-search/) | Source present; `ExaSource` at `_toolset.py:52`; `exa-py` absent | Search, page, cited deep answer, async runs | Exa API, `EXA_API_KEY` | Same everywhere unless `native=True` | `[exa]` and key | Candidate, vendor |
| `YouSearch`, `YouResearch` (`pydantic_ai_harness.youdotcom`) | [You.com](https://pydantic.dev/docs/ai/harness/youdotcom/) | Source present; `finance_research` at `_research.py:205`; SDK absent | Search, page, answer, research, finance research | You.com API, `YDC_API_KEY` | Same everywhere unless `native=True` | `[youdotcom]` and key | Candidate, vendor |
| `Researcher` (`pydantic_ai_harness.researcher`) | [Researcher](https://pydantic.dev/docs/ai/harness/researcher/) | Source present; composes local search and fetch, `SubAgents`, limits | Cited research stack | DuckDuckGo and direct fetch, or native | Local fallback; child uses parent model | `[researcher]` plus workspace or store | Candidate; default spill store requires a database-compatible design |
| `CodeMode` (`pydantic_ai_harness.code_mode`) | [Code mode](https://pydantic.dev/docs/ai/harness/code-mode/) | In use; 100 nested calls, 30 s, 256 MiB (`_capability.py:116`) | One `run_code` tool | None itself; tools, `mount`, `os_access` | Same everywhere | Already installed | Existing |
| `SubAgents`, `SubAgent`, `TaskControl` (`pydantic_ai_harness.subagents`) | [Subagents](https://pydantic.dev/docs/ai/harness/subagents/) | In use; fields at `_toolset.py:196`, `_control.py:196` | Named delegation, budgets, background | Child's own tools | Same everywhere | None | Existing |
| `Capability(defer_loading=True)`, `load_capability` | [On demand](https://pydantic.dev/docs/ai/capabilities/on-demand/) | Present in 2.64.0 | Progressive disclosure | None | Every provider; cache differs | None | Candidate |
| `Skills` (`pydantic_ai_harness.skills`) | [On demand](https://pydantic.dev/docs/ai/capabilities/on-demand/) | Source present; reads the run's workspace (`_capability.py:67`) | `SKILL.md` libraries as deferred capabilities | Workspace files | Same everywhere | `[skills]` and workspace | Candidate; static instructions differ from mutable runtime state |
| Alpha Vantage MCP | [alpha_vantage_mcp](https://github.com/alphavantage/alpha_vantage_mcp) | External; repository MIT | US quotes, fundamentals, indicators | `https://mcp.alphavantage.co/mcp` | Model-independent | Vendor key plus MCP path | Candidate, provider |
| `get_financial_context`, `project`, `run_query`, writes; `number_check` | Local source | In use ([toolset.py:473](../../../src/arno/agent/toolset.py#L473)) | Ledger truth, projections, writes | Arno's Postgres only | None | None | Existing application |

## Search, fetch, and research

`WebSearch`'s docstring says native search, `UserError` without it, and `local=True` needs the
`duckduckgo` extra (`web_search.py:20`). Restriction fields (`allowed_domains`, `blocked_domains`,
`max_uses`, `external_web_access=False`) require native support and disable the fallback; a provider
that cannot apply one makes the tool unsupported rather than unrestricted ([web
search](https://pydantic.dev/docs/ai/capabilities/web-search/)). Domain policy must live in Arno's
own tool on Chat Completions.

`WebFetch` local caps returned text at 50,000 characters and downloads at 50 MiB, is SSRF-protected,
and strips sensitive headers on cross-origin redirects ([common
tools](https://pydantic.dev/docs/ai/tools-toolsets/common-tools/)). Headers go to any URL the model
chooses, so a credential without a domain filter is exfiltration.

Exa and You.com are present in 0.64.0, but neither SDK is installed or in `uv.lock`. Exa adds
`web_search`, `get_page`, opt-in `deep_search`, and `ExaAgent` with `exa_agent`. Every tool returns
`ToolReturn[str]` with `metadata['sources']` holding `ExaSource` (`url`, `title` or `None`;
`exa/_toolset.py:52`). Empty search returns a sentence; 401, 402, and 403 raise; rate limits and
network failures retry. `get_page` ignores domain filters and reads any URL the model passes,
including one a page suggested ([Exa](https://pydantic.dev/docs/ai/harness/exa-search/)). You.com
adds `web_search`, `get_page`, and `YouResearch` with `answer`, `research`, and `finance_research`,
all returning `ToolReturn[str]` with `YouSource` (`youdotcom/_toolset.py:63`). `finance_research`
passes only input and `finance_effort` (`youdotcom/_research.py:205`), so domain, freshness, and
country filters do not apply ([You.com](https://pydantic.dev/docs/ai/harness/youdotcom/)).

Synthesized research is still a tool return: a cited `deep_search` or `finance_research` result is a
`ToolReturn` with source metadata, so it can satisfy "numbers come from a tool return" in form. It
is vendor-written text about the world, not ledger truth, and it cannot produce an affordability
verdict, which stays in the engine. Both vendors cap page text at 10,000 characters; Exa warns that
at the ceiling its truncation marker cannot fire.

`Researcher` is a `CombinedCapability` of local search, local fetch, one `researcher` delegate, and
`ToolOutputLimits`; it fails at run start without a workspace or another overflow store
(`researcher/_capability.py:36`). Its oversized results spill to files, and Arno's rule is that the
database is the whole state, so its default spill store is not a drop-in. `Skills` reads libraries
from a workspace ([on-demand capabilities](https://pydantic.dev/docs/ai/capabilities/on-demand/)).
Static shipped instructions are code, not necessarily user state. Writable workspace memory and
runtime artifacts require a separate persistence check against the database-only rule.

## Code Mode and delegation

`CodeMode` is already the computation path. Monty is not CPython: no third-party imports, a small
standard-library subset, and no host clock, randomness, files, or environment without `os_access` or
`mount`; defaults are 30 seconds and 256 MiB per snippet, with 10 MiB of printed output ([Code
mode](https://pydantic.dev/docs/ai/harness/code-mode/)). It constrains generated code, not the tools
it calls. Arno's reachable-module list includes `decimal`, `hashlib`, and `bisect`
([capabilities.py:168](../../../src/arno/agent/capabilities.py#L168)), while the harness page
describes the list as Monty 1.5's and the wheel is 1.4.0. I did not execute a snippet, so that list
is unverified.

Sub-agents already run the parser. `SubAgents` exposes `delegate_task`; each child gets its own
history and the parent's deps, shares usage unless `forward_usage=False`, does not inherit the
parent's tools, and is capped at depth 3
([Subagents](https://pydantic.dev/docs/ai/harness/subagents/)). Arno sets parse limits, questions,
and resume, with `contain_errors=True` and a 15-second auto-background
([builder.py:178](../../../src/arno/agent/builder.py#L178)). Reports arrive as `<task_output>` data
recognized by `is_task_report`; Arno turns them into outside text
([capabilities.py:259](../../../src/arno/agent/capabilities.py#L259)). A research delegate should
follow that pattern: its report is evidence, not an instruction or a write approval.

## MCP, exactly

`MCP(url)` runs locally by default and opts into native MCP with `native=True` ([MCP
capability](https://pydantic.dev/docs/ai/capabilities/mcp/)). The local path is `MCPToolset`, which
accepts a URL (Streamable HTTP, or SSE when the path ends `/sse`), a `fastmcp` transport, a client,
an in-process server, or a script ([MCP client](https://pydantic.dev/docs/ai/mcp/client/)). The
transport import is `from fastmcp.client.transports import StreamableHttpTransport`, present in the
wheel (`.venv/Lib/site-packages/pydantic_ai/mcp.py:57`, example at line 810).

Authentication is a bearer string, `httpx2.Auth`, `'oauth'`, or a sync/async function of
`RunContext` evaluated at run start; a per-run function gets its own session, so concurrent users
must not share a fixed-auth toolset (`mcp.py:1029`). FastMCP 4 uses `httpx2`; its modern mode does
not support sampling, elicitation, or `log_level`. Lifecycle is `async with toolset` or `async with
agent`, otherwise opened and closed as needed. Server instructions are off by default on a bare
`MCPToolset` (`mcp.py:957`) and enabled on toolsets constructed by `MCP`. In installed 2.64.0,
`include_instructions` belongs to `MCPToolset`, not to the `MCP` constructor. Pass an explicitly
configured toolset, such as `MCP(local=MCPToolset(url, include_instructions=False))`, to control it
([MCP capability](https://pydantic.dev/docs/ai/capabilities/mcp/)). Server text enters with the
operator's prompt authority ([MCP client](https://pydantic.dev/docs/ai/mcp/client/)).

Local MCP tools stay controllable: they are ordinary function tools, so `allowed_tools`,
`tool_prefix`, `process_tool_call`, wrapper toolsets, approval, guardrails, Code Mode, and
`ToolOutputLimits` can apply. With Arno's selector `tools={"code_mode": True}`, they remain direct
calls unless tagged, still subject to hooks and approval. Native MCP differs: the provider names and
executes the tools, so local wrappers never see them ([native
tools](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/)). That is about execution side,
not a blanket bypass.

`tool_error_behavior` is `'retry'` (default), `'failed'`, or `'error'`. For finance data, `'failed'`
is usually honest: a completed but unsuccessful call should be visible, not retried into a
fabricated answer.

## Alpha Vantage

The official repository documents a remote server at `https://mcp.alphavantage.co/mcp` over
Streamable HTTP, with an OAuth page for the API key and a deprecated `?apikey=` form; a local stdio
server also exists ([alpha_vantage_mcp](https://github.com/alphavantage/alpha_vantage_mcp)). It
exposes US quotes, time series, fundamentals, indicators, news sentiment, and Congress trades. The
repository is MIT; the data service's terms, quotas, and redistribution rules are separate and
unverified. Its market endpoints do not establish Indian household coverage; the wider Alpha Vantage
documentation includes some non-US instruments. It would be "market data for investments the user
already holds", after a live OAuth test; `MCPToolset(auth='oauth')` against this server has not been
exercised here.

## Trust, provenance, confirmation

Verified library behavior: a local tool can raise `ApprovalRequired`, which becomes
`DeferredToolRequests` since the agent declares it, and Arno's write gate does that when the
conversation row is flagged ([toolset.py:507](../../../src/arno/agent/toolset.py#L507));
`InputGuardrail` can redact the prompt before the model sees it
([capabilities.py:327](../../../src/arno/agent/capabilities.py#L327)); `ToolReturn.metadata` is not
sent to the model; and `Caching()` is provider prompt caching, not an application response cache
(`.venv/Lib/site-packages/pydantic_ai/capabilities/caching.py:12`).

What Arno lacks, and a catalog must not present as guaranteed:

- Taint covers statements, photos, and raw ledger text via `conversations.outside_text_at`
  ([mark.py:51](../../../src/arno/tools/mark.py#L51)). Web pages, research answers, and MCP results
  do not set it, so a lasting write after a web read skips the confirm card.
- `number_check` reads `ToolReturnPart`, not `NativeToolReturnPart`
  ([number_check.py:410](../../../src/arno/agent/number_check.py#L410)). Native search results are
  not a traceable number source today; local tools returning `ToolReturn` are.
- No provenance row exists: no URL, retrieval time, content hash, or provider. A citation from
  metadata is display, not audit. No response cache exists either; repeating a query calls the
  vendor again and may differ.

A proposed policy, not a guarantee: treat all web, research, and MCP output as outside text; gate
lasting writes after it; store `source_url`, `title`, `retrieved_at`, provider, and the quoted
passage in Postgres; cap each tool's text; require an "as of" date.

## Secrets, SSRF, caching

Keys for Exa, You.com, and MCP belong in the environment or a per-run credential function, never in
chat or a spec. Alpha Vantage's legacy `?apikey=` URL is deprecated; a key in a URL can leak through
logs, proxies, and traces. A shared `http_client` shares cookies across runs, so do not share one
across identities.

SSRF is partly solved and partly relocated. Local `web_fetch_tool` is SSRF-protected. Exa and
You.com fetch on their own infrastructure, so the risk is egress and vendor behavior. An MCP URL is
operator-chosen; if it became user-supplied, it would be an SSRF surface.

The only durable cache is Postgres. Prompt caching is provider-side and does not preserve a research
answer. If a figure must be reproducible, store the result and its source metadata.

## Licensing

The installed `pydantic-ai` 2.64.0 and `pydantic-ai-harness` 0.64.0 wheels declare
`License-Expression: MIT`; `pydantic-monty` 1.4.0 and `ddgs` 9.16.1 declare MIT too. The Alpha
Vantage MCP repository is MIT. Exa's and You.com's SDK licenses, terms, retention, and
redistribution rules were not verified, and their SDKs are not locked.

## Unresolved facts

| Fact | What settles it |
|---|---|
| Whether `decimal`, `hashlib`, and `bisect` import in Monty 1.4.0 | One `run_code` snippet importing each and printing a result |
| Whether DuckDuckGo returns usable India-relevant results and dates | A funded live run on official Indian finance questions |
| Whether Exa or You.com justifies a vendor, key, and egress | The same question set scored on source quality and date coverage |
| Whether Alpha Vantage OAuth works with `MCPToolset(auth='oauth')` | One live connection and tool listing in a scratch environment |
| Whether an MCP server's output contains model-directed instructions | Prompt-injection probes and a defender trial |
| Exact vendor licenses, retention, and data-handling terms | The vendors' terms pages and a written answer from each |
| Cost per answered question | Metered runs through the spend ledger, not list prices |

## Strongest choices and blockers

The strongest verified choices need no new dependency, vendor, or egress: Arno's read and write
tools over Postgres, `number_check`, Code Mode, and `SubAgents` for the parser.

For external research, the smallest complete addition is core `WebSearch(local=True)` plus
`WebFetch(local=True)`, wrapped so each return is a `ToolReturn` with source metadata and tagged for
existing output and taint policies. The blockers: `markdownify` and `beautifulsoup4` are missing,
`WebFetch()` is native-only by default, and `number_check` ignores native tool returns. Exa or
You.com would add structured citations at the cost of an SDK, key, vendor terms, and egress.
`Researcher` and `Skills` use a workspace; mutable artifacts must satisfy database-only persistence,
while static bundled instructions are a separate matter. MCP fits a market-data server such as Alpha
Vantage, but only the local toolset path is controllable, and it needs a live OAuth test.

The evidence path: install the `web-fetch` extra, prove both local tools return bounded text and
source metadata, extend taint and tracing to those returns, then decide whether a vendor or MCP
server earns a place. No provider is ranked.
