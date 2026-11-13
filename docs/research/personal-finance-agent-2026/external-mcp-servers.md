# External MCP servers for Arno

Primary-source review, November 13, 2026. This answers which external servers Arno should connect to
and which tools would help its users. It supplements the [capability catalog](capability-catalog.md)
and recommends integrations; `plan.md` still owns approved scope. No server was authenticated,
installed, or exercised with personal records.

## Recommended selection

I would start with Tavily for current research and the user's existing broker MCP for holdings: Kite
for Zerodha, Upstox for Upstox, or an explicitly limited Groww integration. Fi Money is a broader
personal-data candidate, but I would defer implementation until its operation, authentication, and
storage conditions are clarified. Add one general market-data server only if an actual holding or
question needs data those routes cannot supply.

Tavily is my first implementation candidate because its documented interface covers search and page
extraction, and its public tool schema exposes domain and country controls. This is a design choice,
not a measured search-quality ranking. Exa and Parallel are credible alternatives to compare on the
same Indian questions. Arno does not need all three connected by default.

The following shortlist names first-party servers and their useful read surfaces.

| Server | Official connection | Useful tools or data | Recommendation for Arno |
|---|---|---|---|
| [Tavily](https://docs.tavily.com/documentation/mcp) | `https://mcp.tavily.com/mcp/` | `tavily_search`, `tavily_extract` | First research implementation candidate: C05, C15, C17 |
| [Fi Money](https://github.com/epiFi/mcp-docs) | `https://mcp.fi.money:8080/mcp/stream` | `fetch_net_worth`, `fetch_bank_transactions`, `fetch_mf_transactions`, `fetch_stock_transactions`, `fetch_epf_details`, `fetch_credit_report` | C02/C11/C12/C19; defer pending continuity, host-side auth, and storage clarification |
| [Zerodha Kite](https://github.com/zerodha/kite-mcp-server) | `https://mcp.kite.trade/mcp` | `get_holdings`, `get_mf_holdings`, `get_quotes`, `get_historical_data` | Add when the user holds assets at Zerodha: C12, C13 |
| [Upstox](https://upstox.com/developer/api-documentation/mcp-integration/) | `https://mcp.upstox.com/mcp` | Documented holdings, mutual funds, funds, positions, and profile reads; discover actual tool names | C12/C19 for an Upstox user; daily OAuth reauthorization |
| [Groww](https://groww.in/updates/groww-mcp) | `https://mcp.groww.in/mcp` | Stock/position/market access; verify actual read schemas | Conditional C12/C19 for a Groww user; published scope excludes mutual funds; exclude trading tools |
| [Exa](https://exa.ai/docs/get-started/exa-mcp) | `https://mcp.exa.ai/mcp` | `web_search_exa`, `web_fetch_exa`; opt-in `web_search_advanced_exa` | Research alternative to Tavily, especially when testing filtered discovery |
| [Parallel](https://docs.parallel.ai/integrations/mcp/quickstart) | `https://mcp.parallel.ai/mcp` | `web_search`, `web_fetch`; authenticated `web_research` | Research alternative; deeper research only for a bounded user question |
| [Brave Search](https://github.com/brave/brave-search-mcp-server) | Vendor-maintained `@brave/brave-search-mcp-server`; local stdio or HTTP | `brave_web_search`, `brave_news_search` | Alternative when operating the connector locally is useful; pair with page retrieval |
| [Alpha Vantage](https://mcp.alphavantage.co/) | `https://mcp.alphavantage.co/mcp` | `SYMBOL_SEARCH`, `GLOBAL_QUOTE`, `TIME_SERIES_DAILY`, `FX_DAILY` | Conditional reference/market data for C12, C13, C23 |
| [Twelve Data](https://github.com/twelvedata/mcp) | `https://mcp.twelvedata.com/mcp` | `get_price`, `get_quote`, `get_time_series` in maintained source | Compare with Alpha Vantage if required instrument coverage is missing |
| [Financial Modeling Prep](https://site.financialmodelingprep.com/developer/docs/mcp-server) | `https://financialmodelingprep.com/mcp` | `quote`, `chart`, `company`, `statements`, `calendar` | Later candidate for holdings-related company information; not a core household connector |

These are documented server addresses or packages, not successful connection results. A deployed
server may expose a different tool set from its repository, depending on authentication and plan.

## Research tools to enable first

For Tavily, enable search and extraction initially. Its hosted documentation also lists crawl, map,
and research, but those are not needed for the first current-rule workflow. Use OAuth or host-held
bearer authentication; the [maintainer README](https://github.com/tavily-ai/tavily-mcp) documents
both. Its [search tool source](https://github.com/tavily-ai/tavily-mcp/blob/main/src/index.ts)
supports `include_domains`, `exclude_domains`, dates, and a country boost for general-topic search.
Check that the selected hosted schema has the same parameters. A country boost does not constrain
results to Indian law.

For example, C17 can discover an Income Tax Department page, extract the relevant passage, and
retain its applicability date. C05 can retrieve the issuer's published fee terms. Research does not
require sending transaction narrations, balances, PAN, or account identifiers to the server.

Exa documents keyless trial access for search/fetch, with authentication needed for `agent_run`. An
explicit `tools` selection replaces its defaults. Select the search/fetch subset rather than
automatically enabling an authenticated research agent. Its MCP tool names differ from the Pydantic
harness adapter's `web_search` and `get_page`; the catalog must not conflate them.

Parallel documents keyless, limited search/fetch and authenticated research. Its [programmatic
guide](https://docs.parallel.ai/integrations/mcp/programmatic-use) documents bearer keys and
authenticated configuration overrides. Start with `web_search` and `web_fetch`. Its separate
`web_decisions` output must not replace Arno's affordability engine. Query policy and budgets belong
to the chosen integration.

Brave's official repository documents an API key and a locally operated server with stdio as its
default transport. It is a practical alternative for search/news; an integration still needs the
known-page reading path required by C17. Do not substitute an older community package merely because
its name also contains Brave.

## Personal financial data to evaluate

Fi Money is an important addition to the original survey. Its vendor documentation names six finance
tools, including transaction and EPF access. Its current [banking notice](https://fi.money/) does
not establish continued MCP availability. Verify the account's linked coverage and provenance; the
published bank-history example describes a two-month window. An aggregate balance cannot establish
complete history.

The passcode workflow needs host-side handling, and the vendor's developer storage guidance needs
clarification before persisting records. The [India-server evidence note](external-mcp-india.md)
records these blockers. `epiFi/fi-mcp-dev` is a dummy-data development server, not a live-data
connector. Fi should remain on the deferred list until these checks are resolved.

For Kite, start with a read allowlist: `get_holdings`, `get_mf_holdings`, `get_positions`,
`get_quotes`, `get_ltp`, `get_ohlc`, `get_historical_data`, and `search_instruments` as needed.
Authentication support may also require `login`; credentials stay in the host workflow. The
repository also contains order and GTT mutation tools. Zerodha's support page explicitly mentions a
GTT exception to its read-only description, so a client read allowlist is required. Broker margins
must not be treated as an additional household bank balance.

Upstox is an equally relevant route for someone whose investments are there; its official MCP
documentation describes read-only account access with daily reauthorization. Groww's published scope
is stocks and F&O with order functions; its mutual-fund coverage is not established. Neither
requires opening a Zerodha account. Discover and verify their actual hosted schemas before wiring
tools into Arno.

These connectors add acquisition methods to C02/C12. Arno still needs instrument identity,
quantity/price dates, reconciliation, and its own holdings model. A current holding alone cannot
produce a historical money-weighted return. Retain statement import as a supported alternative.

## Market data only when required

Alpha Vantage's official server exposes API functions as tools and documents OAuth authorization
using an API key. Select only the quote, history, symbol, or FX functions required by the user.
Verify each Indian instrument, available dates, account entitlement, and storage/display rights. Do
not infer Indian mutual-fund coverage from an equity example.

Twelve Data documents account-based OAuth for its hosted endpoint and a self-hosted API-key route.
Its [market-tool source](https://github.com/twelvedata/mcp/blob/main/src/tools/market.py) identifies
the read functions in the table. Validate the requested exchange/instrument and plan before choosing
it; similar tool names do not establish interchangeable data coverage.

FMP groups related endpoints under category tools. For example, `statements` takes an `endpoint` and
parameters. The server supports header-based `X-API-Key`; custom OAuth clients require prior
registration. Its plan-aware discovery and returned empty data need explicit handling. Choose it
only for a concrete company-information gap, rather than connecting its entire analyst surface.

## Completion checklist for a selected server

- [ ] Record the exact endpoint/package, server version if available, identity, transport, and auth.
- [ ] Discover and save the actual authenticated tool schemas; allow only the required operations.
- [ ] Keep credentials out of the model and confirm expiry, reauthorization, and revocation
      behavior.
- [ ] Check country, instrument/account, history, dates, units, pagination, and missing-data
      behavior.
- [ ] Map each returned fact to its source and keep data acquisition separate from confirmed ledger
      writes.
- [ ] Exercise limits, outages, stale data, repeated responses, and unexpected schema changes.
- [ ] Verify current terms, retention, costs, and permitted evidence storage for the selected use.
- [ ] Pass the relevant C02/C12/C13/C17/C19/C21 checks and a real UI connection/recovery journey.

Research-server comparison should use the same official-rule questions, date checks, citation
requirements, and cost ceilings. Account-data acceptance should use user-authorized test records. No
quality ranking, end-to-end reliability result, or blanket data license is established here.
