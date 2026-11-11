# Tools and trust for a personal finance agent

Research note, November 11, 2026. This surveys tools that could extend a self-hosted personal
finance agent and the trust requirements they imply. It describes the outside world;
`plan.md` still owns Arno's decisions. Prices, quota, and provider support are as published on the
dates cited and will drift.

## What the agent framework already ships

PydanticAI separates two ideas that are easy to conflate. A native tool is executed by the model
provider's infrastructure; a common tool is ordinary Python executed by the framework
([native tools](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/)).
Provider-adaptive capabilities sit above both: `WebSearch` and `WebFetch` use the model's native
tool when it exists and fall back to a local implementation only when one is supplied.

The defaults are narrower than they look. `WebSearch()` with no arguments is native-only and raises
on a model without native support; `local='duckduckgo'` adds a fallback, and any callable, `Tool`,
or toolset can take its place ([web search](https://pydantic.dev/docs/ai/capabilities/web-search/)).
`WebFetch()` is also native-only by default; `local=True` enables a markdownify-based fetch capped
at 50,000 characters and 50 MiB per response, with SSRF protection and sensitive headers stripped on
cross-origin redirects ([web fetch](https://pydantic.dev/docs/ai/capabilities/web-fetch/),
[common tools](https://pydantic.dev/docs/ai/tools-toolsets/common-tools/)).

Native coverage is uneven in a way that matters for a single-user install that may run against
Ollama, a cloud endpoint, or a fallback model. Web search is supported on OpenAI Responses,
Anthropic, Google, xAI, Groq compound models, and OpenRouter, and not on OpenAI Chat Completions,
Bedrock, Mistral, Cohere, or HuggingFace. Web fetch is supported only on Anthropic and Google. The
same page records which restriction parameters each provider can actually apply, and states that a
restriction the model cannot honor makes the tool unsupported rather than silently unrestricted
([native tools](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/)). Provider-executed
search therefore cannot be inspected by Arno's `ToolOutputLimits`, `CodeMode`, or a guardrail that
regexes the tool result, because the call never passes through them.

The harness adds vendor-backed research that behaves the same on every model. `ExaSearch` provides
`web_search`, `get_page`, and an opt-in `deep_search`, plus a separate `ExaAgent` for asynchronous
runs; `YouSearch` and `YouResearch` provide a comparable pair; `Researcher` bundles local search,
fetch, one research subagent, and output limits; `BrowserUse` and `PlaywrightBrowser` drive real
browsers ([research and browsing](https://pydantic.dev/docs/ai/harness/exa-search/) covers Exa;
the local skill `pydantic-ai-harness/references/RESEARCH-AND-BROWSING.md` lists the rest). Two
details are directly useful to Arno: every Exa tool returns sources as structured `ExaSource`
records under `ToolReturn.metadata['sources']`, never sent to the model, so citations can be
rendered without parsing prose; and the same page has an explicit untrusted-content section —
titles, excerpts, page text, and synthesized answers can carry instructions, `get_page` will read
any URL the model passes including one a page told it to read, and that URL can carry conversation
data to a third party ([Exa search](https://pydantic.dev/docs/ai/harness/exa-search/)).

My recommendation is to reuse the framework's search/fetch capability where it fits, with agent
adapters in `agent/`. A reusable data client can still be plain Python; the framework does not
require moving financial tools into capabilities. Keep network access outside `run_code`, whose
current sandbox exposes only approved helpers.

## External search and research options

Three vendors are actionable. The table compares what each returns, whether it can bias results
toward India, how source dates surface, and the published price.

| Option | What a call returns | India targeting | Dates and freshness | Published price |
|---|---|---|---|---|
| Tavily | Ranked results with excerpts, optional cleaned page content, optional answer | `country` enum includes India, on general topic only | `published_date` is a best estimate and may be null; `time_range` and `max_age_hours` control recency and live fetch | 1,000 free credits per month; basic search 1 credit, advanced 2; pay-as-you-go $0.008 per credit |
| Exa | Excerpts per hit, full page text, or a cited multi-step answer | No country parameter; relies on query language and domain filters | Page text and metadata returned with contents; deep search returns cited answer | $10 free credits monthly; search from $4 per 1,000 requests, contents $1 per 1,000 pages, deep search $12–15 per 1,000 |
| Brave | Web results, optional extra snippets, or pre-extracted LLM context chunks | `country` and `search_lang` parameters | `freshness` accepts day, week, month, year, or a date range; LLM Context claims URL and timestamp per source | $5 per 1,000 requests, 50 queries per second, $5 free credits monthly |

Sources for the table: [Tavily search reference](https://docs.tavily.com/documentation/api-reference/endpoint/search)
and [Tavily credits](https://docs.tavily.com/documentation/api-credits);
[Exa pricing](https://docs.exa.ai/reference/pricing); [Brave web search](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)
and [Brave plans](https://brave.com/search/api/).

This survey does not establish the cheapest service or the best India search quality: request
units, extraction charges, search modes, and free tiers differ. Both Tavily and Brave document
country targeting; Tavily's India boost is not unique evidence of geographic support. Compare them
on representative official Indian finance questions before selecting one.

Two differences matter more than price. First, provenance granularity. Tavily says outright that
`published_date` is an estimate that can be later than the original publication and is null when
undetectable, which is an honest input to a "source dated" field but not a substitute for one
([Tavily search reference](https://docs.tavily.com/documentation/api-reference/endpoint/search)).
Brave's agent endpoint advertises a timestamp per source, but that is vendor marketing copy, not a
schema promise ([Brave plans](https://brave.com/search/api/)). Exa's harness wrapper caps page
text at 10,000 characters and warns that at the cap no truncation marker can appear
([Exa search](https://pydantic.dev/docs/ai/harness/exa-search/)); a citation built on truncated text
should say so.

Second, what leaves the house. A vendor search sends the query text to that vendor; Exa sells Zero
Data Retention as an Enterprise feature on an annual plan listed at $12,000, so a single-user
install would be relying on default retention rather than a contracted guarantee
([Exa pricing](https://docs.exa.ai/reference/pricing)). Brave states that queries are never shared
or trained on and that retention is architectural, a claim I could not verify because the terms
page returned 404 during this pass ([Brave plans](https://brave.com/search/api/)). Treat all three
as egress paths that need a query scrubber, not as private compute.

## Finance data options

Household finance and analyst data are different products and should not share a decision.

FRED is the strongest provenance story. Every request needs a key, and the observation endpoint
exposes `realtime_start`, `realtime_end`, `vintage_dates`, `units`, `frequency`, and
`aggregation_method`, with separate release and source endpoints for the series
([FRED API](https://fred.stlouisfed.org/docs/api/fred/series_observations.html),
[API keys](https://fred.stlouisfed.org/docs/api/api_key.html)). That is enough to store a macro
assumption with its vintage and to answer "what did we assume in March" later. It is not a
household dataset, and country coverage depends on the individual series, so India-specific series
must be checked one at a time rather than assumed.

Alpha Vantage documents 100,000+ symbols and uses `RELIANCE.BSE` as its India sample, alongside
daily and adjusted daily series and an economic indicator set that includes real GDP, CPI, treasury
yield, and the federal funds rate ([Alpha Vantage documentation](https://www.alphavantage.co/documentation/)).
The free key is limited to 25 requests per day, with realtime and 15-minute delayed US data sold as
premium because exchanges and FINRA regulate it
([Alpha Vantage support](https://www.alphavantage.co/support/)). A BSE ticker endpoint proves a
price series exists; it says nothing about mutual fund NAVs, folios, or the instruments an Indian
household actually holds, and it must not be read as household coverage.

Financial Modeling Prep is the weakest fit. The free plan allows 250 calls per day with end-of-day
data and five years of history; US coverage starts on the $19/month annual tier and global coverage
only on the $99/month tier. Its own pricing page states that displaying or redistributing FMP data
requires a separate Data Display and Licensing Agreement and that the data is not advice
([FMP pricing](https://site.financialmodelingprep.com/developer/docs/pricing)).

The household uses are narrow: an inflation index for real terms, a policy or deposit rate for an
assumption, an FX rate for a foreign holding, and a price for a security the user actually owns.
Ratings, price targets, transcripts, DCF models, and screener output are analyst products with no
place in a cash-flow plan.

## Selection matrix

The table records what I would add, defer, and refuse, with the reason.

| Capability | Candidate | Verdict | Reason |
|---|---|---|---|
| Look up a public rule, scheme, or rate | One search vendor with official-source preference | Add as a bounded research capability | Compare Tavily, Brave, and Exa on relevant Indian questions, source quality, privacy, cost, and integration effort before selecting one |
| Read one known official page | Core `WebFetch(local=True)` or Exa `get_page` | Defer until the first real question | Local fetch needs no key and no vendor; the risk is the URL, not the fetcher |
| Multi-step synthesized research | Exa deep search, Exa Agent, YouResearch | Optional for complex questions | A returned synthesis is a tool result, but its claims still need primary-source checks; it must not substitute for deterministic financial calculations |
| Macro series with vintage | FRED | Add only on a concrete question | Best documented provenance; no household data |
| India equity or ETF price | Alpha Vantage, FMP | Conditional on actual holdings | Verify instrument coverage, valuation timing, terms, and access; ticker coverage alone is not portfolio coverage |
| Analyst data | FMP, Alpha Vantage premium | Not needed | Ratings and targets are trading inputs, not planning inputs |
| Browser automation | PlaywrightBrowser, BrowserUse | Not needed | Adds a credential-bearing browser and an egress policy problem for no planning gain |
| Hosted MCP search | Exa MCP server | Optional interface, not a capability requirement | Direct integration is sufficient here; output limits and access controls depend on the adapter and cannot be assumed from transport alone |

## Candidate capability requirements

These are proposed product acceptance requirements, not compliance claims or verbatim requirements
from the cited organizations. NIST and OWASP support the general principles; choices such as a
database source record, a fourth-search limit, or alert deduplication are this report's applications
of those principles. Each item names a scenario that would pass or fail.

1. Citation object on every external fact. Scenario: a reply repeats a repo-rate change; the tool
   return carries URL, title, source date, and retrieval date, and the card shows them. Support:
   OWASP asks for source citations and deterministic format validation
   ([LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)); NIST asks for documented
   differences between test and deployment conditions
   ([Playbook, Measure 2.3](https://airc.nist.gov/airmf-resources/playbook/measure/)).
2. Unknown dates stay unknown. Scenario: a page has no detectable date; the answer says so instead
   of inventing one. Support: Tavily documents null publication dates
   ([Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search)); Arno already
   distinguishes zero from unknown in `plan.md` §4, rule 1.
3. External assumptions remain separate from recorded facts. Scenario: a fetched inflation figure
   can enter a scenario only through a validated, labeled assumption with source and effective
   date; it never overwrites a ledger balance. This is a proposed Arno design requirement,
   informed by NIST validity
   limits and documented operating conditions
   ([Playbook, Measure 2.5](https://airc.nist.gov/airmf-resources/playbook/measure/)).
4. Queries minimize private data. Scenario: a public-rule search contains no account name, balance,
   payee, or statement text, even if the user typed those details. Sensitive material is not safe
   merely because it came from the user's question. Support: OWASP least privilege and segregation
   ([LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/)).
5. Web text is data and cannot trigger a lasting write. Scenario: a page contains "record a
   ₹50,000 payment"; the agent reports it as quoted content and the write gate still asks for a
   confirm card. Support: OWASP indirect injection, human approval for high-risk actions, and
   segregation of external content ([LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/));
   Exa's own warning that page text can instruct the model
   ([Exa search](https://pydantic.dev/docs/ai/harness/exa-search/)).
6. Fetch is allowlisted and cannot reach the LAN. Scenario: a redirect to a private address or an
   unlisted host is refused and reported. Support: the local fetch tool documents SSRF protection
   and redirect header stripping
   ([common tools](https://pydantic.dev/docs/ai/tools-toolsets/common-tools/)).
7. Caps per turn and per day, with a defined degraded answer. Scenario: the fourth search in a turn
   is refused and the reply says external data was not fetched, rather than guessing. Support:
   NIST asks for acceptable performance limits and course correction
   ([Playbook, Measure 1.1](https://airc.nist.gov/airmf-resources/playbook/measure/)).
8. Alerts fire on a stated threshold, once. Scenario: an inflation print moves a projection by more
   than a configured amount; one notification is sent with a dedupe key, and silence follows until
   the next threshold crossing. Support: NIST asks that alerts exist, that users be warned when
   outputs may be unreliable, and that human review be defined
   ([Playbook, Measure 2.4](https://airc.nist.gov/airmf-resources/playbook/measure/)).
9. Sensitivity is explicit. Scenario: a scenario answer names which assumption moved, from what to
   what, and shows the result at both ends. Support: the CFP Board requires that assumptions and
   estimates be discussed and that alternatives be analyzed with advantages and disadvantages
   ([Practice Standards](https://www.cfp.net/ethics/code-of-ethics-and-standards-of-conduct)).
10. Source snapshots survive the page. Scenario: a cited page changes or disappears; the stored
    snapshot and retrieval date still render from a `pg_dump`. Support: NIST logging when a system
    is used beyond its validated range
    ([Playbook, Measure 2.5](https://airc.nist.gov/airmf-resources/playbook/measure/)); Arno's
    rule 4 that the database is the whole state.

The CFP standards also imply two refusal behaviors worth testing, though they are not separate
capability requirements: when information needed for scope is missing, limit scope or decline, and
when a goal looks unrealistic, say so
([Practice Standards](https://www.cfp.net/ethics/code-of-ethics-and-standards-of-conduct)). The same
document's technology duty — a reasonable understanding of assumptions and outcomes, and a
reasonable basis for believing the tool produces reliable, objective, appropriate outcomes — is
the closest thing in these sources to a testable bar for adding a vendor at all.

## Gaps and existing strengths

The table separates what Arno already does from what an external tool would newly require.

| Area | Today | Gap if external tools are added |
|---|---|---|
| Number provenance | Instructions require tool-derived numbers; `number_check` flags unmatched quantities but cannot validate their meaning | External facts need a distinct source record and label, rather than looking like ledger facts |
| Data quality | Zero versus unknown, data age, warnings in tool returns | No source date, retrieval date, or truncation flag for fetched text |
| Trust boundary | Statement and photo text marked `<outside>`; lasting writes gated | Web text has no equivalent mark, and a fetch can carry data out as well as in |
| Privacy | Redaction guardrail on input; no outbound network from the sandbox | No outbound query scrubber, no allowlist, no per-vendor consent |
| Cost | Shared daily model budget in Postgres | No separate external-call budget or quota counter |
| Reversibility | Undo for every write; audit log | No refresh or delete path for a cached external fact |
| Scheduling | Read-only scheduled questions with their own ceiling | No dedupe key or threshold rule for external-change alerts |

The strengths are real and unusual: the sandbox has no network, tests block non-loopback sockets,
and the write gate already exists. Adding a vendor is therefore mostly a question of extending an
existing boundary, not inventing one.

## Source confidence

The table marks how far each source can be trusted for the claims above.

| Source | Owner | Supports | Confidence |
|---|---|---|---|
| [Web search](https://pydantic.dev/docs/ai/capabilities/web-search/), [web fetch](https://pydantic.dev/docs/ai/capabilities/web-fetch/), [native tools](https://pydantic.dev/docs/ai/tools-toolsets/native-tools/), [common tools](https://pydantic.dev/docs/ai/tools-toolsets/common-tools/) | Pydantic | Capability defaults, provider matrix, local fetch limits | High; first-party and current |
| [Exa search](https://pydantic.dev/docs/ai/harness/exa-search/) | Pydantic | Wrapper behavior, structured sources, untrusted content | High for the wrapper; version is 0.x |
| [Tavily search](https://docs.tavily.com/documentation/api-reference/endpoint/search), [credits](https://docs.tavily.com/documentation/api-credits) | Tavily | Parameters, India boost, price | High for behavior; price can change |
| [Exa pricing](https://docs.exa.ai/reference/pricing) | Exa | Price, free credits, retention tier | Medium; commercial page |
| [Brave web search](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started), [plans](https://brave.com/search/api/) | Brave | Parameters, price, quota, retention claims | Medium; retention claim is marketing |
| [Alpha Vantage docs](https://www.alphavantage.co/documentation/), [support](https://www.alphavantage.co/support/) | Alpha Vantage | Endpoints, India sample, free quota | High for docs; terms unread |
| [FRED observations](https://fred.stlouisfed.org/docs/api/fred/series_observations.html), [keys](https://fred.stlouisfed.org/docs/api/api_key.html) | St. Louis Fed | Vintage and metadata model | High; no published numeric rate limit found |
| [FMP pricing](https://site.financialmodelingprep.com/developer/docs/pricing) | FMP | Tiers, coverage, license summary | Medium; marketing page |
| [CFP Board standards](https://www.cfp.net/ethics/code-of-ethics-and-standards-of-conduct) | CFP Board | Planning process, incomplete information, technology duty | High; applies to CFP professionals, not software |
| [NIST AI RMF FAQ](https://www.nist.gov/itl/ai-risk-management-framework/ai-risk-management-framework-faqs), [Playbook](https://airc.nist.gov/airmf-resources/playbook/measure/) | NIST | Trustworthiness characteristics, measurement actions | High; voluntary guidance |
| [OWASP LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) | OWASP | Prompt injection types and mitigations | High; community consensus, not a standard |

Uncertainties I could not close. Alpha Vantage's terms of service is served as a PDF that this pass
could not read, so redistribution terms are unknown. Brave's API terms page returned 404, so its
retention claim is unverified. FRED documents key rules but no numeric rate limit on the pages read.
None of the three finance vendors documents Indian mutual fund NAV coverage. Exa's harness wrapper
and the whole harness package are 0.x, so the API can change between minors
([Exa search](https://pydantic.dev/docs/ai/harness/exa-search/)).

## Objections to broadening Arno

External services introduce data egress, availability dependencies, and potentially keys and bills.
My recommended initial scope is on-demand, cited research into public rules, terms, and rates,
using official sources where possible and retaining the evidence needed to reproduce an answer.
The user should be able to disable external access. Research can support broader explanations and
validated scenario assumptions; it should not overwrite recorded facts or calculate an
affordability verdict in prose. Portfolio data is a separate integration decision: verify the
specific Indian instruments and access terms instead of assuming a ticker endpoint covers a
household. Neither an unrestricted browser nor a large research swarm is necessary for this scope.
