# External MCP servers for Indian personal finance

Point-in-time survey, November 13, 2026. This file records what first-party Indian finance MCP
servers document about transport, tools, authentication, and coverage, and what that means for Arno.
It is evidence for `plan.md` §2, not a decision. No credentials, MCP session, install, or paid call
was used. Claims come from vendor pages or the vendor's repository; silence is recorded as unknown.

A user-mediated MCP does not change Arno's bank position: consent and aggregation happen inside Fi's
product, and the client receives only the assembled response. Nothing here makes a self-hosted
instance an eligible direct Account Aggregator participant. Fi documents assembled bank data; that
differs from Arno obtaining direct bank credentials or network-participant status.

## Fi MCP

Fi runs a hosted MCP over its Net Worth feature, documented on the [product
page](https://fi.money/features/using-fi-mcp-for-money-management) and in the [setup
guide](https://fi.money/features/getting-started-with-fi-mcp).

**Transport and prerequisite.** The endpoint is `https://mcp.fi.money:8080/mcp/stream`, streamable
HTTP, added through `mcp-remote` or as a direct URL. The prerequisite is the Fi Money app with Net
Worth populated: mutual funds, bank balances, loans, PFs, all linked in the app. With nothing linked
the tools return empty results, so empty is indistinguishable from a real zero without a connection
check.

**Authentication.** A tool call returns a login link. The user enters a Fi-registered phone number
on a Fi page, reads a passcode from the app under Net Worth > Talk to AI > Get Passcode, types it
into the client, then answers Done. Fi documents a strict 30-minute expiry and says credentials
never reach the assistant. Refresh behavior after 30 minutes is undocumented.

**Tools.** Fi's own documentation repository lists six
([epiFi/mcp-docs](https://github.com/epiFi/mcp-docs)):

| Tool | Returns |
|---|---|
| `fetch_net_worth` | Total and per-attribute assets and liabilities, scheme analytics (XIRR, invested and current value, NAV, ISIN) |
| `fetch_credit_report` | Experian report: score, active and closed accounts, limits, utilization, inquiries, date of birth |
| `fetch_epf_details` | UAN accounts, establishment details, employee and employer shares, PF and pension balance |
| `fetch_mf_transactions` | Per-scheme buy and sell history with date, amount, units, price, stamp duty |
| `fetch_bank_transactions` | Bank, amount, narration, date, type code, mode, running balance |
| `fetch_stock_transactions` | Per-ISIN buy, sell, bonus, and split records with date, quantity, NAV where present |

Fi's sample code prefixes names with `networth:`.

**Coverage and edges.** Across the two Fi pages the named asset classes are mutual funds, Indian and
US stocks, fixed deposits, real estate, ESOPs, bank balances, EPF, and NPS, with loans and credit
cards as liabilities. Six tools are documented, with no dedicated NPS or real-estate tool; aggregate
net worth may still contain those classes. The pages do not establish whether those figures are
self-declared or linked. The published bank example describes only the last two months of
transactions, with older data "coming soon," and no documented pagination. Credit data is a bureau
pull, not a live liability balance, so a card balance there can differ from the net worth figure.

**The development repository is a mock.** The vendor's
[epiFi/fi-mcp-dev](https://github.com/epiFi/fi-mcp-dev) serves static dummy JSON and simulates
login. Its Google ADK examples do not make it a Google-operated production finance connector. It
does not establish live account access. A production-server software license was not established
here.

**Status objection.** Fi's current [official notice](https://fi.money/) says new savings accounts
cannot be opened through its app and existing users should use FedMobile. The notice also dates
reward-point changes to March 2026. It does not explicitly state that MCP or the Wealth Tracker has
closed. Continued MCP operation, eligibility, and support therefore need confirmation; a live
documentation page is insufficient evidence. Do not infer total shutdown from the banking notice.

## Zerodha Kite MCP

Kite MCP is official and hosted, with no additional MCP charge documented by its owner
([Z-Connect](https://zerodha.com/z-connect/featured/connect-your-zerodha-account-to-ai-assistants-with-kite-mcp),
[support](https://support.zerodha.com/category/trading-and-markets/general-kite/others-kite/articles/connect-zerodha-ai-assistant)).

**Transport and deployment.** `https://mcp.kite.trade/mcp` for streamable HTTP, with `/sse` also
served. The server is Go, MIT licensed, and self-hostable with one's own Kite Connect credentials
([repository](https://github.com/zerodha/kite-mcp-server)). `EXCLUDED_TOOLS` builds a read-only
instance, the supported way to run it without trading tools.

**Authentication.** `login` returns a `kite.zerodha.com/connect/login` URL under Zerodha's own
`kitemcp` key. The user enters the Kite password and TOTP or app code in the browser, never in chat.
The client keeps the resulting access token for the session; where it stores that token is not
documented. The underlying Kite Connect token expires at 6 AM the next day unless invalidated ([Kite
Connect user docs](https://kite.trade/docs/connect/v3/user/)). Verify how the hosted MCP session
handles that boundary rather than assuming unattended refresh.

**Tools.** The README lists 22: `login`; `get_profile`, `get_margins`, `get_holdings`,
`get_positions`, `get_mf_holdings`; `get_orders`, `get_trades`, `get_order_history`,
`get_order_trades`; `get_gtts`; `get_quotes`, `get_ltp`, `get_ohlc`, `get_historical_data`,
`search_instruments`; and the writes `place_order`, `modify_order`, `cancel_order`,
`place_gtt_order`, `modify_gtt_order`, `delete_gtt_order`. Zerodha says the hosted instance is
read-only on its [product page](https://zerodha.com/products/mcp/), while the [support
page](https://support.zerodha.com/category/trading-and-markets/general-kite/others-kite/articles/connect-zerodha-ai-assistant)
mentions a GTT-order exception. The exact hosted set was not discovered here, and an older Z-Connect
note said order placement, trade history, and portfolio data were all unavailable, so the pages
disagree. Only `tools/list` settles it; that was not run here.

**Coverage.** The documented surface includes demat holdings, Coin mutual fund holdings, positions,
margins, profile, same-day order/trade reads, and market data. No household bank, loan, EPF, NPS,
consolidated net-worth, or historical portfolio-valuation endpoint was established. Historical price
candles alone cannot recover portfolio history: a past value reconstructed from today's holdings is
wrong after any trade.

## Groww and Upstox

Groww and Upstox are both first-party and broker-scoped.

Groww serves `https://mcp.groww.in/mcp` over streamable HTTP with OAuth and a one-day user token
([update](https://groww.in/updates/groww-mcp), [engineering
post](https://tech.groww.in/building-groww-mcp-a-technical-deep-dive-into-ai-powered-portfolio-intelligence-beea42be67f1)).
Its scope is stocks and F&O; order placement is advertised, and mutual funds are not yet supported.

Upstox serves `https://mcp.upstox.com/mcp`, documents read-only access to holdings, orders,
positions, mutual funds, funds, and profile, requires daily OAuth, and blocks trading ([Upstox
docs](https://upstox.com/developer/api-documentation/mcp-integration/)). It is free with an active
account.

All three broker routes require an actual account relationship. Their overlap is a reason to select
the user's broker, not to reject Upstox or Groww merely because Kite exists.

## How the options compare

The table compares documented capability, not product quality.

| Server | First party | Account data | Bank, loan, EPF | Session | Writes exposed | Call |
|---|---|---|---|---|---|---|
| Fi MCP | Yes, beta | Net worth, MF, stocks, EPF, credit | Yes, two months of bank transactions | Passcode, 30 minutes | None documented | Conditional |
| Kite MCP | Yes, hosted | Demat, Coin MF, positions, margins | Not documented | Underlying token expires 6 AM; verify hosted session | Repository has order/GTT writes; hosted policy has a GTT exception | Evaluate for a Zerodha user |
| Upstox MCP | Yes | Holdings, positions, MF, funds | No | OAuth, daily | None documented | Evaluate for an Upstox user |
| Groww MCP | Yes, early | Stocks and F&O | No | OAuth, one day | Order placement advertised | Conditional on a Groww account and enforced reads |

## Recommendation

**Kite MCP: evaluate for a Zerodha user.** Enable only the reads needed for the selected capability.
Block the order and GTT mutation tools at the client, regardless of hosted-server defaults. No
production-quality ranking was measured, and read-only claims must account for the GTT exception.

**Fi MCP: conditional, and do not build yet.** It is the only server that would supply bank
balances, loans, EPF, and consolidated net worth, exactly where Arno is thin. Three objections hold
it back. The official banking transition notice leaves MCP continuity unconfirmed. Fi's developer
guidelines discourage sensitive-data storage/caching, so compatibility with durable local records
needs clarification; this is guidance, not a concluded legal restriction. The documented passcode
workflow also types a credential into the conversation, so a short-lived financial credential passes
through model context, which Arno's rule 6 treats as a line not to cross. Revisit when Fi confirms
continuity, publishes a retention position compatible with a local ledger, and offers a session
story better than a 30-minute secret pasted into chat.

**Upstox and Groww: select by the user's broker.** Upstox's documented read-only account route is
useful for an Upstox user. Groww needs an explicit read allowlist and current coverage checks. There
is no reason to open or assume a Zerodha account just to use its MCP.

None closes the gap. Broker MCPs answer "what do I hold at one broker, at today's price," not "what
is my whole position," and they do not replace statements for history. Fi documents broader personal
data, but its suitability and current operation remain unresolved.

## Unresolved checks

- Enumerate `tools/list` against `mcp.kite.trade` and record the hosted set, including whether
  `get_orders` and `get_trades` are present, since the Zerodha pages disagree.
- Read `kc/session.go` to confirm how the Kite server stores the access token and what happens at
  the 6 AM boundary.
- Ask Fi in writing whether MCP continues after the banking transition, and whether a user without a
  Fi savings account can use Net Worth.
- Obtain Fi's position on local storage of MCP responses, given its "never store or cache" guidance.
- Establish Fi MCP pricing, rate limits, and support commitments after beta; none are published.
- Confirm the provenance of Fi's real estate, ESOP, and NPS figures, and whether NPS has a tool.
- Verify the two-month bank transaction window, whether pagination exists, and how a partial window
  is signaled.
- Check whether a remote streamable HTTP server with per-user browser consent can attach to Arno's
  agent runtime at all, with no credential written to a file.
