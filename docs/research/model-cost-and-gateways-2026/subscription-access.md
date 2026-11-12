# Subscription access and gateways

**Correction, November 12, 2026:** the first investigation omitted Anthropic's current support
articles about Agent SDK subscription metering and monthly API credits. Read
[anthropic-follow-up.md](anthropic-follow-up.md) for the corrected support contract. Eligible plan
credits can fund native Anthropic API calls through a linked Console organization. Direct Agent SDK
subscription access is a separate agent-runtime integration, not equivalent to adding API-key
funding. The original blanket implication that no subscription-funded Claude route exists is
superseded by that follow-up.

Can Arno use a personal Claude or ChatGPT subscription instead of an API key, and does LiteLLM help?
Sources were read on November 12, 2026. LiteLLM's release baseline is v1.109.0 (November 11, 2026);
source inspection also covers the newer `main` snapshot, version 1.110.0, at commit
[`89149b4`](https://github.com/BerriAI/litellm/tree/89149b4544e056d52b46774bf5723b2ac57c385a).
PydanticAI claims come from current docs. Parent verification also found the ChatGPT provider in
Arno's installed Pydantic AI 2.64.0; Arno's model factory does not wire it up. No authentication or
model request was run.

## The shape of the access decides the answer

Three arrangements hide under "subscription support." They differ in who holds the token and who may
call.

| Arrangement | Token owner | Who may send requests | Documented support |
|---|---|---|---|
| Client-owned passthrough | The original client | Only that client, via a proxy base URL | Codex documents proxies; Claude Code documents gateways |
| Gateway-hosted OAuth | The gateway process | Any client with a gateway key | LiteLLM implements it for ChatGPT; OpenAI does not document it |
| App sign-in (OpenAI) | The user's app, stored locally | That app only, for the user's requests | OpenAI documents it for open-source and local apps |

LiteLLM markets all three as "your subscription"; its child pages split along this line.

## What LiteLLM implements

**Claude is passthrough only.** The setup points Claude Code at the proxy with `ANTHROPIC_BASE_URL`
and puts the LiteLLM key in `ANTHROPIC_CUSTOM_HEADERS`, leaving `Authorization` for Claude Code's
OAuth token ([tutorial](https://docs.litellm.ai/docs/tutorials/claude_code_max_subscription)).
Claude Code keeps and refreshes that login and clears it locally on `/logout`. The source confirms
`forwarded_anthropic_oauth_token()` scans the header for a token starting `sk-ant-oat` and forwards
it. The BYOK variant forwards the client's `x-api-key` instead, with header forwarding enabled:
ordinary API billing, not a subscription. Neither helps a generic SDK with a LiteLLM key; Claude
Code must be the client.

**ChatGPT has two paths.** The Codex tutorial is client-owned: Codex keeps and refreshes its login,
sets `requires_openai_auth = true`, and sends `x-litellm-api-key` alongside its bearer ([Codex
tutorial](https://docs.litellm.ai/docs/tutorials/openai_codex_byok)). The provider guide is
different: the user signs in outside the proxy by device code, then mounts the resulting
`~/.config/litellm/chatgpt/auth.json`. The gateway refreshes that login, and every `chatgpt/`
request leaves under that one account ([provider
guide](https://docs.litellm.ai/docs/providers/chatgpt)).

The proxy posts to `https://chatgpt.com/backend-api/codex`, uses the fixed client ID
`app_EMoamEEZ73f0CkXaXp7hrann`, and identifies as `codex_cli_rs`
([`common_utils.py`](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/llms/chatgpt/common_utils.py)).
It prepends a "You are Codex ... coding agent in the Codex CLI" system prompt to every request and
forces `store=false`, `stream=true`, and encrypted reasoning
([`transformation.py`](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/llms/chatgpt/responses/transformation.py)).
Any OpenAI-compatible client, PydanticAI included, can call `/v1/responses`. It works for generic
requests by acting as the Codex CLI, not as an approved ChatGPT app.

**Its numbers are not bills.** LiteLLM reports Claude subscription requests at Anthropic API list
prices, as "API-equivalent dollars." ChatGPT models have no price entry, so "recorded spend is $0
and `max_budget` limits never trigger"; cap usage with RPM or TPM instead. One deployment serves one
account, clients cannot bring their own, with no revoke call. API fallback is opt-in, and logs name
the deployment that served each request.

## What OpenAI permits

OpenAI documents two sanctioned shapes. A Codex client may use ChatGPT sign-in, including through a
custom provider with `requires_openai_auth = true`, which the auth page calls "useful when you
access OpenAI models through an LLM proxy server" ([Codex
auth](https://developers.openai.com/codex/auth)). That covers Codex's requests alone.

The second is Sign in with ChatGPT (SIWC), an explicit app grant. An open-source or locally hosted
app registers with `client_id=dynamic_agent_client`, receives a per-account client ID, and calls
`https://api.openai.com/v1/responses` with the user's token
([overview](https://developers.openai.com/siwc/token-sharing-open-source),
[sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)). The route is
constrained: `store=false`, `stream=true`, developer messages, no `max_output_tokens`,
`temperature`, `top_p`, `metadata`, or HTTP `previous_response_id`; tool support is restricted, with
web search documented as an available native tool
([limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)).

The [SIWC Terms](https://openai.com/policies/sign-in-with-chatgpt-terms/) are stricter. Tokens must
be stored locally under the user's control, never in a remote or managed environment. Requests must
come from a runtime the user controls and serve the connected app only: "Do not provide
general-purpose API access for other tools or unrelated requests." Apps may not split usage, rotate
accounts, pool, resell, or share plan usage, and users must not have to pay to use their plan.

Those terms govern SIWC tokens; LiteLLM's reviewed `chatgpt/` route uses Codex-backend credentials,
so they do not by themselves decide that route's permission. The review found documentation for
Codex clients using proxies, but did not establish equivalent permission for arbitrary apps using
the gateway-held Codex login. Do not treat technical compatibility as that permission. The SIWC
terms also allow requests from a remote runtime controlled only by the user while requiring local,
user-controlled persistent token storage. Its official [self-hosted VM
guide](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms) explicitly
describes local sign-in, protected credential transfer, and VM-owned refresh. Record this
documentation/terms tension instead of concluding that every user-controlled VM is forbidden.

Subscription usage is also not API usage. The Codex pricing page says "API token prices are separate
from subscription usage; don't use them to estimate included tasks," and describes messages per
five-hour period with possible weekly limits ([Codex
pricing](https://developers.openai.com/codex/pricing)).

## What Anthropic permits

Anthropic's legal page limits subscription OAuth to its native applications and directs developers
building third-party products, including Agent SDK products, to API keys or supported cloud
providers. It disallows offering Claude.ai login in a third-party app and collecting or
intermediating those credentials ([legal](https://code.claude.com/docs/en/legal-and-compliance)).
The [Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) requires API keys instead.

The one allowance is a user signing in to the unmodified Claude Code binary with their own
subscription. Arno's loop is not that; its own login would collect the credentials the page forbids.

The consumer terms add two constraints. Credentials may not be shared or made available to anyone
else, and automated access is barred "except when you are accessing our Services via an Anthropic
API Key or where we otherwise explicitly permit it"
([terms](https://www.anthropic.com/legal/consumer-terms)). `claude setup-token` issues a one-year
OAuth token for Claude Code and CI, not a general model API
([auth](https://code.claude.com/docs/en/authentication)).

Data handling matters here. On consumer plans Anthropic trains on inputs when the account setting is
on, including Claude Code inputs, with five-year retention in that case and 30 days when it is off
([data usage](https://code.claude.com/docs/en/data-usage)). On Pro and Max, quotas are a five-hour
session limit plus a weekly limit, shared with Claude Code, with optional credits
([support](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan)).

## PydanticAI's actual support

PydanticAI has three relevant providers.

| Provider | Behavior | Permission |
|---|---|---|
| `anthropic:` | API key or auth token; Bedrock, Vertex, Foundry clients | Supported; no subscription path |
| `openai-codex:` | Reads `~/.codex/auth.json`, runs Codex OAuth, calls the Codex backend | Tells you to check OpenAI's usage policies |
| `openai-chatgpt:` | Runs SIWC sign-in against `api.openai.com/v1` and refreshes | OpenAI's documented app path |

The codex provider never writes the CLI file and refreshes in memory; persistence is needed because
refresh tokens are single-use. It streams only, forces `store=false`, and drops sampling settings
([docs](https://pydantic.dev/docs/ai/models/openai-codex/)). The ChatGPT provider implements SIWC,
including the app name the user approves, local credentials, and refusal when `plan_usage_granted`
is false ([docs](https://pydantic.dev/docs/ai/models/openai-chatgpt/)). It is the only Claude or
ChatGPT subscription route that is both generic and documented as permitted. Anthropic has none
([docs](https://pydantic.dev/docs/ai/models/anthropic/)).

## Counting cost when a subscription pays

Four quantities answer different questions and must not be added.

| Quantity | Meaning | Source |
|---|---|---|
| Cash paid | Fee actually charged | Invoice |
| Marginal charge | Extra money this turn, usually zero in quota | Invoice and credits |
| Quota consumed | Share of the five-hour, weekly, or monthly allowance | Provider dashboard |
| Shadow cost | Tokens times API list prices | Token counts and prices |

Keep cash payments, provider-reported quota use, and an optional API-equivalent estimate separate.
Quota debit should be unknown when the provider does not expose it; do not invent weighted-token
allowance figures. Cross-provider list-price comparisons are estimates, not subscription billing.

This breaks Arno's `SpendLimits` reading. With `genai-prices` and unknown at zero, a subscription
route reports list-price dollars as spend, or zero when the gateway has no price, while the budget
cannot reliably enforce the provider's quota. Phoenix 21.0 independently prices spans from its
catalog rather than accepting an arbitrary supplied cost; that is another estimate, not a
subscription bill. The $0.02-per-turn figure has no token breakdown and is not a billing fact.

## Fit for Arno

**For LiteLLM:** its base SDK and catalog are MIT-licensed, it speaks both wire formats, and lets
PydanticAI keep one client while the proxy handles routing, token logging, virtual keys, and RPM/TPM
caps, and refreshes its hosted ChatGPT login. It records tokens, shadow cost, and metered fallback.

**Against:** Arno is one process, one database, one user. LiteLLM adds a second stateful service,
its own database schema for keys and logs, which expands Arno's backup boundary rather than
inherently violating its state rule. The standard proxy extra includes a nonpermissive dependency
([licensing review](litellm.md#licensing-is-the-hard-gate)). The ChatGPT path identifies as Codex,
injects coding instructions into every finance prompt, reports $0 so budgets never fire, and exposes
the general-purpose endpoint without establishing permission for that use. The Claude path helps
Claude Code, rather than Arno's existing Pydantic AI loop, so it gives Arno no subscription access
for its existing loop. Claude and ChatGPT allowance debit cannot be predicted from a universal API
price formula; OpenCode publishes a different, priced allowance.

For a qualifying local app, the native `openai-chatgpt` provider is the most direct candidate to
test. This is not approval for every server deployment: the official self-hosted VM guide and
token-storage terms must be reconciled for the selected host, and paid or remotely hosted service
offerings have separate eligibility requirements. For Anthropic, use an API key or a supported cloud
provider with the current Arno loop. LiteLLM remains a candidate for multi-client controls.

OpenCode Go sells $10 and $40 monthly coding-agent plans with five-hour, weekly, and monthly
allowances. Arno's finance-agent traffic differs from the stated coding-agent purpose
([Go](https://opencode.ai/docs/console/go/)); treat its suitability as unresolved rather than assume
that any compatible API client has the same entitlement. Console also offers metered inference
([Console](https://opencode.ai/docs/zen/)).

## What was not tested

No OAuth flow, credential file, proxy, or model request was touched. LiteLLM behavior comes from
docs and source, not a live call; the provider is new in v1.109.0 and may change. PydanticAI
authentication behavior in Arno's pinned version was not tested. No provider was asked whether a
single-user app qualifies, and no enforcement was observed. Indian pricing and other providers were
out of scope. Phoenix was not run; the distinctions above are accounting requirements, not measured
quota or billing behavior.

## Unresolved permissions and compatibility

- The reviewed OpenAI sources do not establish permission for gateway-held Codex tokens serving
  arbitrary non-Codex apps. Proxy docs cover Codex clients; SIWC terms cover SIWC apps. The gap is
  unresolved; an adapter's chosen client identity does not establish permission.
- SIWC's dynamic registration for open-source and locally hosted apps is distinct from the
  commercial client-ID process. Its eligibility and preview requirements must be checked for the
  selected deployment; a waitlist on an identity-only or commercial page does not negate the
  documented local-app route.
- Anthropic's product rules may never be tested on a single owner, but the text has no carve-out,
  and the consumer terms bar non-human access without a key.
- Data classification is unresolved. Consumer terms permit training unless opted out; API terms do
  not train by default. Which covers SIWC or Codex-backend requests for statements is not
  established.
- The reviewed Claude and ChatGPT sources do not provide a universal token-price formula for
  predicting subscription quota debit. OpenCode does publish model allowance rates, so do not
  extend that uncertainty to every provider. LiteLLM's ChatGPT token file also needs single-owner
  refresh coordination, writable storage, and explicit handling of a rejected login; a dollar
  budget over its $0 map cannot enforce the plan's quota.

## Sources

- LiteLLM: [subscriptions](https://docs.litellm.ai/docs/subscriptions),
  [Claude](https://docs.litellm.ai/docs/tutorials/claude_code_max_subscription),
  [ChatGPT](https://docs.litellm.ai/docs/providers/chatgpt),
  [Codex](https://docs.litellm.ai/docs/tutorials/openai_codex_byok), and source at `89149b4`
  ([auth](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/proxy/litellm_pre_call_utils.py),
  [constants](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/llms/chatgpt/common_utils.py),
  [transform](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/llms/chatgpt/responses/transformation.py)).
- OpenAI: [Codex auth](https://developers.openai.com/codex/auth),
  [pricing](https://developers.openai.com/codex/pricing),
  [SIWC](https://developers.openai.com/siwc/token-sharing-open-source),
  [sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in),
  [limits](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations),
  [SIWC Terms](https://openai.com/policies/sign-in-with-chatgpt-terms/).
- Anthropic: [legal](https://code.claude.com/docs/en/legal-and-compliance),
  [Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview),
  [auth](https://code.claude.com/docs/en/authentication),
  [data](https://code.claude.com/docs/en/data-usage),
  [terms](https://www.anthropic.com/legal/consumer-terms),
  [limits](https://support.claude.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan).
- PydanticAI: [OpenAI](https://pydantic.dev/docs/ai/models/openai/),
  [Codex](https://pydantic.dev/docs/ai/models/openai-codex/),
  [ChatGPT](https://pydantic.dev/docs/ai/models/openai-chatgpt/),
  [Anthropic](https://pydantic.dev/docs/ai/models/anthropic/).
- OpenCode: [Go](https://opencode.ai/docs/console/go/), [Console](https://opencode.ai/docs/zen/).
