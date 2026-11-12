# Anthropic follow-up: plan credits, the Agent SDK, and two concrete paths

Research follow-up, November 12, 2026. This note corrects the Anthropic section of
[`subscription-access.md`](subscription-access.md), which concluded that Arno had no subscription
route to Claude. Two sources omitted from that investigation change the picture. No authentication
flow, credential, or model request was run.

Labels: _official docs_ (read on November 12, 2026), _inspected source_ (a local file), _inference_,
_unknown_. Every _unknown_ has a resolution path below.

## What changed

The prior note read the legal page and the SDK overview as the whole answer. Both still say a
product developer must use API keys. Neither mentions the October 7, 2026 credit program, which
makes the ordinary Claude API a supported destination for a subscription.

| Date | Source | What it establishes |
|---|---|---|
| Oct 7, 2026 | [Agent SDK with your plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan) | Max, Ultra, and Team include monthly API credits covering the Claude API, Managed Agents, and the Agent SDK or `claude -p` with a key from the linked Console organization |
| Oct 7, 2026 | [Monthly API credits](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-ultra-and-team-plans) | Amounts, eligibility, claim flow, coverage, and what happens at zero |
| Jun 15, 2026 | Agent SDK article above | The announced SDK billing change is paused; SDK, `claude -p`, and third-party app usage still draw on subscription limits |
| current | [Legal](https://code.claude.com/docs/en/legal-and-compliance) | OAuth is for subscribers and native apps; product developers should use API keys; an end user may sign in to the unmodified binary |

The support article establishes current metering behavior; it should not be read as permission to
extract tokens or impersonate a client. The legal and product pages describe different credential
and client arrangements. Treat a reconciliation between their scopes as an inference, not a legal
determination or evidence that every third-party integration has been approved.

## Reconciling the legal page with the credits

The page distinguishes subscription OAuth for native Anthropic clients from API-key authentication
for third-party products. It also describes end users authenticating the unmodified Claude Code
binary in products that host it, subject to its stated conditions. Customers may provision their
own API keys for authorized users, with usage billed to the key owner. Those are separate supported
shapes, not a general right to reuse a native client's credentials in a different protocol adapter.

Path A sits inside the API-key allowance: the key belongs to the owner, usage bills to the owner's
organization, and no claude.ai credential enters Arno. A credit balance funded by a plan changes the
source of funds, not the authentication method, which is why the October 7 article can call the API
covered without contradicting "use API keys": the key is the credential, the credit is a payment
instrument.

Path B sits on that exception's edge: it is written for an end user signing in to the unmodified
binary, an owner running it in the owner's own container is the closest fit, and the prohibition's
object, a developer serving its users, is absent. No sentence grants it, so it is an owner-accepted
reading.

## Path A: native API key on plan credits

Max receives $100 a month, Ultra $200, Team $20 per Standard and $100 per Premium seat, pooled and
capped at $500; Free, Pro, and Enterprise are ineligible. Seven days on the plan is required, and
Max and Ultra require being the subscriber. One Console organization is linked, takes credits from
one plan only, and cannot be changed by the user (official docs).

Credits refresh each cycle, do not roll over, are spent before purchased credits, are shared by
everyone holding a key, and are separate from plan limits. Coverage is the Messages and Batches
APIs, Playground, Managed Agents, and the Agent SDK; it excludes interactive Claude Code, extra
usage, and Bedrock, Vertex, or Foundry, and runs launched from GitHub Actions, an IDE, or desktop
count as Claude Code even with `-p` (official docs).

Two consequences follow. The route is the plain Claude API, so PydanticAI's native `AnthropicModel`
serves it with no gateway or new loop (inspected source: 2.64.0 ships `providers.anthropic`,
documented with streaming, native structured output, caching, batch, and `genai-prices` cost). And
the balance is prepaid. At zero, purchased credits or auto-reload continue the account; otherwise
"API requests stop until your next monthly credits arrive" and "usage is never charged to your
Claude plan" (official docs), a provider-enforced stop stronger than any client-side ceiling.

## Billing attribution: three ledgers

| Ledger | Question | Source and precision | Label |
|---|---|---|---|
| Gross API usage | Cost at contract rates | Cost endpoint `amount`, "after discounts and before any credits are applied", cents to six decimals | official docs |
| List-price value | Cost at list | Same endpoint, `list_amount` | official docs |
| Credit consumed | Balance gone | Console Billing page, amount and expiry | official docs |
| Cash invoice | What was charged | Console or sales invoice | official docs |

The Usage and Cost Analytics API (`/v1/organizations/analytics/{usage,cost}`, beta header
`analytics-usage-cost-2026-08-24`) groups by model, workspace, key, actor, tier, context window,
geography, and speed (official docs). Freshness rules it out as admission control: "typically
refreshes within 4 hours", can take 24, revised until about seven days after month end. It is a late
reconciliation feed. Credit balance is the gap: the Billing page is the documented surface, and
neither analytics endpoint documents a remaining-credit field (official docs; absence of evidence).
Balance therefore stays owner-observed, never an API-derived "credits remaining".

## Controls and support contract

The public data-handling baseline is also established: Anthropic describes API traffic as a
commercial product and says commercial inputs and outputs are not used for training by default.
Using plan-funded credits does not change the request's API route. Account-specific agreements and
feature/model retention rules still apply; this is not a zero-retention promise
([commercial training policy](https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training),
[API retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)).

Provider-side controls exist, but none bounds one turn: an organization monthly limit below the tier
cap, returning HTTP 400 starting "You have reached your specified API usage limits"; workspace spend
limits and rate limits, settable through the Rate Limits API, though not on the default workspace;
and tier caps, returning HTTP 429 with `enforced_spend_limit_reached` and no `retry-after` (official
docs), which needs its own mapping because retrying cannot help.

Arno supports Path A when all of these hold, each testable without a paid request.

1. Configuration names one model ID and reads only `ANTHROPIC_API_KEY`; no OAuth token,
   `ANTHROPIC_AUTH_TOKEN`, or subscription credential is read anywhere. _Test:_ assert the client is
   built with `api_key` alone.
2. The model is `AnthropicModel` with `AnthropicProvider`, not the OpenAI-compatible client every
   role uses today, so structured output and caching are native. _Test:_ assert the constructed type
   and base URL.
3. `max_tokens` is set, because Anthropic requires it and an unset value becomes the model maximum.
   _Test:_ assert a non-null request parameter.
4. Cost comes from `ModelResponse.cost()` with the `genai-prices` revision stored beside it, and
   unpriced results are unknown, never zero. Still not true today.
5. A provider-side stop exists independently: credits exhausted, or an organization or workspace
   limit. Arno's limiter stays a circuit breaker, not a ceiling.
6. Reconciliation pulls `amount` and `list_amount`, states the freshness window, and takes balance
   from the Console.
7. The model never states a balance, quota, or invoice total. Rule 1 in `plan.md` §4 covers money,
   and credits are money.

The monthly-credit benefit is not available to every plan. That is not a reason for Arno to reject
ordinary paid Anthropic API keys from Pro, Free, or Enterprise users. Arno validates its model and
provider configuration; the owner links and verifies the funding organization in Anthropic's own
flow. Arno must not claim to detect plan eligibility or remaining credits from a normal API key.

## Path B: the official Agent SDK under the owner's subscription

The SDK drives a bundled native binary: `query()` spawns `claude`, speaks JSON over stdio, one
subprocess per session, transcripts under `~/.claude/projects/` (official docs). The package is MIT
(inspected source: `claude-agent-sdk-python/LICENSE`), the binary inside is Anthropic's, and the
docs put use under the Commercial Terms. Headless auth means `/login` credentials at
`~/.claude/.credentials.json`, mode 0600, relocatable with `CLAUDE_CONFIG_DIR`, or a one-year
`CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` (official docs). Both are Anthropic's own flows;
neither shows Arno a credential.

It still cannot serve PydanticAI's `Model` contract.

| Arno requirement | SDK capability | Verdict |
|---|---|---|
| `request()`/`request_stream()` returning `ModelResponse` | None; its own loop over five message types | incompatible |
| History in Postgres | Session JSONL on disk, best-effort `SessionStore` mirror | not rule 4 without a volume |
| Typed Python tools | In-process MCP servers via `create_sdk_mcp_server` | workable, parallel layer |
| Structured output | `output_format`, JSON Schema draft-07, re-prompted | workable |
| Cancellation | `interrupt()`, streaming only, buffer drained first | workable, different |
| Confirm card before a lasting write | `permission_mode="default"` plus `can_use_tool`, or hooks | workable |
| `RunUsage`, `SpendLimits`, `ModelFallback` | `usage`, `model_usage`, `total_cost_usd` | incompatible |
| Cost truth | `total_cost_usd` is a "client-side estimate, not authoritative billing data" from a table bundled at build time, `costBasis` of `list`, `managed`, or `unknown` | estimate only |

A bridge is conceivable: one `query()` per turn, tools as MCP, history relayed as a prompt,
`output_format` for the answer, `interrupt()` for cancel. That is a second agent loop with its own
accounting and session store, beside PydanticAI rather than under it. Its budget is checked after
responses arrive, so spend "can pass the cap by up to the cost of that one response", and `usage`
excludes subagent work that `total_cost_usd` includes (official docs).

Operations add weight: about 1 GiB RAM per session, a process tree beside the API process, and
hardening through `setting_sources=[]`, `strict_mcp_config=True`, `disallowed_tools` for every
built-in touching files or shells, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`, and `max_turns` (official
docs). Supported, but for a different agent.

The prior legal objection was too broad; what remains is architecture. Entry conditions if
revisited: the owner accepts the reading risk in writing; the SDK comes unmodified from PyPI; auth
is a setup token or `/login` volume, never a token Arno mints; the turn runs as one `query()`;
built-ins are denied; lasting writes return through `can_use_tool`; `number_check` runs on returned
text; the UI says "estimate only, quota unknown".

Do not extract an OAuth token and call `api.anthropic.com` with it, or point `ANTHROPIC_BASE_URL` at
a proxy Arno runs to relabel subscription traffic. That turns a documented client into an
undocumented one, which is the conduct the page names, and makes the credential Arno's to store.
Path A needs neither.

## OpenAI ChatGPT plan usage: confirmation and limits

The prior claim holds in the installed version: Pydantic AI 2.64.0 ships
`pydantic_ai.providers.openai_chatgpt`, with OAuth against `api.openai.com/v1`, `store=false`,
`stream=true`, system messages sent as developer messages, dropped sampling settings, only
`WebSearchTool` among native tools, `count_tokens()` raising, and refusal when `plan_usage_granted`
is false (inspected source; official docs).

Two corrections. Plan usage "in a paid or remotely hosted app needs OpenAI's approval first," so a
single-owner self-host is the local case. And quota is unobservable: the signals are
`subscription_sharing_usage_limit_exceeded` and `subscription_sharing_usage_unavailable`, which can
arrive as `response.failed` after streaming starts, plus UI guidance to send users to
`chatgpt.com/settings/usage` while the app shows its own 30-day totals (official docs). Available:
per-response tokens, Arno's aggregates, error codes. Not available: remaining allowance, cost,
balance. Requests never fall through to API billing, so this is `quota consumed: unknown`.

## Recommendation

Support Path A: native `AnthropicModel` on a key from the Console organization linked to the owner's
plan, credits as funding, a local estimate for admission, scheduled reconciliation against `amount`
and `list_amount`. It uses the loop and hooks Arno already needs, the credential the legal page
names, and is the only Anthropic arrangement both documented and architecturally free.

Defer Path B with the entry conditions recorded, so the decision reads "two loops are not worth a
quota-limited route" rather than "subscription access is impossible". The second sentence was wrong;
this note retires it. Phoenix remains the only cost dashboard, adding no surface, gateway, or
billing schema, and it keeps labeling span costs as catalog estimates because the authoritative
figures arrive too late to be a running total.

## Evidence gaps and how each closes

| Gap | Resolution |
|---|---|
| No documented API field for credit balance | Leave remaining credits unknown and link to Console Billing; do not scrape a private endpoint or invent a balance |
| Whether the organization limit counts credit-funded usage | Set a $1 limit on a throwaway organization, spend one credit-funded request, read the error |
| Whether a one-person Console organization can create an Admin API key, needed by the Analytics API | Owner checks the organization and admin-key pages; if absent, reconcile from the Console cost CSV |
| Account-specific terms for credit-funded traffic | Funding follows Supplemental Credit Terms; the owner verifies any account-specific Console agreement before a real import |
| India availability and billing currency for Max and Ultra | Check the pricing page country list and the Billing currency during the claim flow |
| `total_cost_usd` accuracy, if Path B returns | Compare one session's `model_usage` against Analytics for the same day, with owner approval |
| SIWC category for this deployment | Confirm in writing through the interest form before enabling the provider |

## Sources

Official documentation read November 12, 2026: Anthropic support [Agent SDK with your
plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan),
[monthly API
credits](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-ultra-and-team-plans);
Claude Code [legal](https://code.claude.com/docs/en/legal-and-compliance), [SDK
overview](https://code.claude.com/docs/en/agent-sdk/overview), [Python
reference](https://code.claude.com/docs/en/agent-sdk/python), [agent
loop](https://code.claude.com/docs/en/agent-sdk/agent-loop), [cost
tracking](https://code.claude.com/docs/en/agent-sdk/cost-tracking),
[authentication](https://code.claude.com/docs/en/authentication); [Analytics
API](https://platform.claude.com/docs/en/manage-claude/usage-cost-analytics-api), [rate
limits](https://platform.claude.com/docs/en/api/rate-limits); [Credit
Terms](https://www.anthropic.com/legal/credit-terms); Pydantic AI
[Anthropic](https://pydantic.dev/docs/ai/models/anthropic/),
[ChatGPT](https://pydantic.dev/docs/ai/models/openai-chatgpt/); OpenAI
[overview](https://developers.openai.com/siwc/token-sharing-open-source),
[inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference),
[limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations),
[UI/UX](https://developers.openai.com/siwc/ui-ux-guidelines).

Inspected: `claude-agent-sdk-python/LICENSE` (MIT); `pydantic-ai` 2.64.0 in this environment exposes
`providers.anthropic` and `providers.openai_chatgpt`, while `src/arno/agent/models.py` builds every
role through `OpenAIChatModel`, with one native Anthropic branch in `batch_model()`.

Findings: plan credits make the Claude API a subscription-funded route through an ordinary API key,
which Arno should support; the Agent SDK cannot serve PydanticAI's `Model` contract without a second
loop, so it is deferred on architecture, not legality; neither vendor exposes remaining allowance in
a form Arno can meter.

Evidence gaps: credit balance, the limit's interaction with credits, Admin API eligibility for a
one-person organization, data terms, and India availability needed the owner's account or a paid
request; each has a resolution above.
