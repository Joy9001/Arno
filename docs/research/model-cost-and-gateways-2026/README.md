# Model cost and gateways: Research and recommendation

Research for Arno, November 12, 2026. This is an applied recommendation, not an approved change to
[`plan.md`](../../arno/plan.md). No runtime configuration or dependencies changed, and no paid model
requests were made.

**Scope chosen after this survey:** use native Pydantic AI adapters and genai-prices for supported
major providers, add a narrow OpenCode accounting adapter, and keep a dedicated gateway parked
until a demonstrated operational need. Phoenix is the only user-facing cost dashboard. The
[Anthropic follow-up](anthropic-follow-up.md) and
[OpenCode accounting contract](opencode-accounting-contract.md) close omissions in the first pass;
the [implementation plan](../../arno/cost-tracking.md) resolves the proposed Phoenix supplied-cost
and budget-display contract. Its acceptance gates remain unexecuted.

## Recommendation

Arno needs an explicit cost-accounting boundary and dependable admission control. A gateway is
optional. Retain Pydantic AI and maintained catalog pricing, and preserve provider-reported charges
and subscription quotas separately. The complexity audit keeps native Spend Limits and the shared
Postgres counters, with direct-call accounting and batch pre-accounting through public APIs.
Keep Phoenix as the inspection surface. Adopt a gateway when translation,
shared credentials, or control across multiple applications justifies operating it.

LiteLLM is a serious gateway candidate and a useful catalog source. It does not automatically make
Arno's meter accurate: its response-cost fields do not flow into the current harness's dollar
counter, and some provider values represent list price rather than a cash charge. Its standard proxy
installation also includes a nonpermissive enterprise package, which needs resolution before it fits
Arno's production and distribution rules. See the [LiteLLM investigation](litellm.md).

The research separates four responsibilities:

1. Collect the usage and billing information the endpoint actually returns.
2. Estimate unsupported charges using a versioned price source and explicit billing identity.
3. Decide whether another request may start, including concurrent work and uncertain prior charges.
4. Reconcile estimates and reported amounts against the provider's account reports.

No catalog, tracing product, or gateway alone establishes all four.

## Read the focused investigations

The supporting notes cover distinct parts of the decision.

| Note | Question |
|---|---|
| [LiteLLM](litellm.md) | Catalog, embedded SDK, gateway, pricing semantics, deployment, budgets, and licensing |
| [Subscription access](subscription-access.md) | What subscription reuse actually supports and how it differs from API billing |
| [Native pricing and alternatives](native-and-alternatives.md) | Pydantic AI, Phoenix, and alternative gateways or managed providers |
| [Anthropic follow-up](anthropic-follow-up.md) | Eligible plan-funded API credits, SDK subscription runtime, and sign-in limits |
| [OpenCode contract](opencode-accounting-contract.md) | Wire fields, billing modes, streaming, and independent allowance/list value |
| [Implementation seams](implementation-seams.md) | Inference dispatch inventory and framework extension points |
| [Phoenix cost contract](phoenix-cost-contract.md) | Supplied costs, provenance, unknown totals, export, and required service changes |
| [Verification](verification.md) | Reproduced local behavior and the experiments still required |
| [Plan review](plan-review.md) | Independent review findings, corrections, and unresolved implementation evidence |
| [Complexity audit](complexity-audit.md) | Removed platform features, retained safeguards, and the smaller implementation contract |
| [Sources](sources.md) | Primary references, confidence, and version boundaries |

## What is broken in Arno today

The installed boundary is Pydantic AI 2.64.0, pydantic-ai-harness 0.64.0, and genai-prices 0.2.1.
Arno constructs `SpendLimits` without a custom price function or unknown-price policy. The default
prices each response through the registry; a lookup failure adds zero dollars and marks the request
unpriced. Arno's ordinary production dollar budgets have no accompanying token budget. Its host-side
store also deliberately continues when database reads or writes fail. These are coverage and
enforcement gaps, not evidence that inference is free. See
[`capabilities.py`](../../../src/arno/agent/capabilities.py),
[`spend.py`](../../../src/arno/agent/spend.py), and the [offline reproduction](verification.md).

An ordinary response with a manually supplied `usage.cost` still reprices through the registry in
the current harness. Its existing `price=` callback accepts that amount. Therefore, an adapter that
merely copies a gateway's cost into `usage.cost` is insufficient for the shared budget; the meter
must explicitly consume the chosen value.

The existing $0.02-per-turn and $18-per-month figures are a prose assumption without a recorded
token breakdown, not a deployment measurement ([design
estimate](../../arno/system-design.md#latency-and-cost)). This research does not replace them with
another unsupported monthly total.

## Define cost before choosing a calculator

The following values answer different questions and must remain distinguishable.

| Value | Meaning | Appropriate use |
|---|---|---|
| Provider-reported charge | Endpoint says it charged this amount to an account or balance | Provisional actual spend, with source and reporting scope |
| Catalog estimate | Recorded usage multiplied by a selected rate revision | Immediate estimates, comparisons, and conservative admission |
| Subscription allowance debit | Units deducted from a plan's quota, sometimes expressed as dollar equivalents | Quota management, not cash-spend totals |
| Fixed subscription fee | Invoice amount for the billing period | Monthly operating cost; allocation to requests is an accounting choice |
| Unknown or partial | Missing usage, rate, category, or final streaming result | Visible incomplete accounting and a defined admission policy |
| Explicit zero | Endpoint or configuration positively establishes a zero charge in the selected scope | A real zero, with provenance |

A fixed plan can charge no additional cash for one request while consuming allowance and having a
nonzero API-equivalent value. Do not sum those as three expenses. A provider-reported amount can
also exclude tax, credit-purchase fees, discounts, or fixed commitments; reconcile its documented
scope rather than label every reported number an invoice total. OpenRouter documents an account
charge in `usage.cost`; Anthropic's analytics API separately reports discounted and list amounts and
excludes some commitment fees ([OpenRouter
usage](https://openrouter.ai/docs/cookbook/administration/usage-accounting), [Anthropic
analytics](https://platform.claude.com/docs/en/manage-claude/usage-cost-analytics-api)).

## How to calculate estimates correctly

After normalizing usage into nonoverlapping billable categories, the general calculation is:

```text
estimated charge = sum(billable quantity for category × effective unit price for category)
```

Token rates may be quoted for one token or one million tokens. Other categories use seconds, images,
searches, or requests. Preserve decimal precision below a cent; round for display only. Do not use
ledger paise to store tiny USD inference charges.

Selecting a price needs more than a model name: the billed provider or reseller, endpoint and
billing profile, upstream model and alias mapping, request time, region, service tier, batch mode,
context size, and cache-write lifetime can all affect it. Record the facts that applied to the
request, along with the price revision and the source of the amount.

Normalize before calculating. Some APIs include cache reads in total input while others expose
separate input categories. Reasoning tokens may already be included in output. Charging a parent
total and its children twice is wrong. A missing cache or tool-price field is not proof that the
category is free. The LiteLLM catalog has broad coverage, but its own entries include missing rates,
explicit zeros, aliases, and provider-specific variants ([catalog and calculation
review](litellm.md)).

Use provider-reported billing where its semantics are established. Otherwise use a maintained,
validated catalog or an explicit custom-endpoint price configuration. Never infer a reseller's price
from the original model vendor merely because the model names match. Persist raw numeric usage and
nonsecret billing metadata so a corrected estimator can be compared with the original estimate
without rewriting history. Financial prompts and statement text are unnecessary here.

## What LiteLLM and Phoenix solve

LiteLLM offers three separable products: catalog data, an in-process SDK, and a gateway. The JSON
catalog can be used without either the SDK or another server. The SDK provides provider translation
and cost calculation. The gateway adds centralized routing, authentication, budgets, and logs. Those
operational features are useful when requests come from multiple clients; they also introduce
credentials, availability dependencies, and recoverable state ([LiteLLM review](litellm.md)).

The standard proxy extra in the inspected release depends on `litellm-enterprise`. Its presence does
not make the catalog or base SDK proprietary, and it is not a claim that every community gateway
feature requires payment. It does mean the selected image must pass Arno's permissive license policy
before production use or distribution. Its enterprise-route imports are guarded; an enterprise-free
supported assembly remains unproven ([dependency
declaration](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/pyproject.toml),
[enterprise
license](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/enterprise/LICENSE.md)).

Phoenix computes costs from ingested usage and pricing configuration. Its current documentation says
its built-in table is generated from LiteLLM, supports custom prices, and refreshes from GitHub;
recorded span costs are not automatically recalculated when prices change. Thus Phoenix and LiteLLM
are not independent corroboration of a price. Trace ingestion happens after the request and cannot
itself authorize or prevent that request. Keep tracing outside the spending authority ([Phoenix cost
tracking](https://arize.com/docs/phoenix/tracing/how-to-tracing/cost-tracking)).

For reproducibility, validate a catalog revision, retain the last good snapshot, report its age, and
apply refreshes atomically. A raw moving `main` URL is a discovery feed, not an immutable audit
record. Runtime rate selections and overrides must survive Arno's supported database backup.

## Spending control requires a separate guarantee

The current harness stops the next request after a budget has been exhausted. It does not reserve
the maximum price before each concurrent call. Setting `on_unpriced='raise'` acts after a response
has already incurred usage; it is not a preflight check. Unknown-rate validation must also happen
before starting paid work if the policy promises a dollar ceiling ([harness
documentation](https://pydantic.dev/docs/ai/harness/spend/), [local verification](verification.md)).

There are two defensible policies:

- Keep a circuit breaker and state its limit: stop further requests after recorded exhaustion,
  with bounded concurrency, request limits, and output caps where supported.
- For stricter admission, atomically reserve a conservative charge, count active reservations
  against the remaining budget, and reconcile afterward. Keep uncertain attempts reserved until
  resolved. A guarantee requires bounds on every charged category, not just output tokens.

Do not claim a strict cash ceiling when an endpoint omits final usage, ignores output caps, or can
bill hidden retries. Provider-side limits can add protection where their documentation establishes
the guarantee. The application must still expose reporting delay and unaccounted attempts. LiteLLM's
reviewed reservation path improves concurrency control, but its default reservation for uncapped
output is finite and does not establish a strict maximum charge ([gateway budget
analysis](litellm.md#budgets-spend-control-and-durability)).

Arno should retain one durable authority for admission and budget totals. If a gateway owns it, Arno
can still enforce workflow request limits and show its own attribution; it should not silently
maintain a conflicting second estimate as the same authoritative cash total. Database failure must
have an explicit policy: a strict paid-call budget cannot silently become uncapped.

## Subscription access is a separate decision

LiteLLM's subscription feature must be evaluated by the actual route and client. Forwarding an
authenticated coding client's subscription request is different from hosting credentials for an
arbitrary finance agent. A price catalog cannot establish permission or available quota.

Current Pydantic AI documentation describes a native ChatGPT-plan provider, and official OpenAI
documentation describes plan access for open-source and locally hosted apps. This corrects the
earlier assumption that no native route exists. Eligibility, preview API restrictions, version
availability, and Arno's self-hosted deployment still need their own checks ([Pydantic AI
provider](https://pydantic.dev/docs/ai/models/openai-chatgpt/), [OpenAI plan
access](https://developers.openai.com/siwc/token-sharing-open-source)).

The [subscription investigation](subscription-access.md) distinguishes that route from Codex backend
access and Claude subscription passthrough. Provider-approved account access, not the presence of an
adapter, establishes the appropriate integration. API fallback would require an explicit billing
choice rather than silently moving a quota-limited subscription to paid API use.

## Proposed path and decision gates

First, repair cost semantics and complete coverage using existing framework hooks. This is needed
whether Arno later adopts a gateway or not: unknown is not zero, list price is not an invoice, and a
subscription allowance is not an additional cash payment. Include chat, parse, categorizer,
compaction, scheduled jobs, batch work, vision, and direct speech-to-text calls in the inventory.

Next, validate maintained price feeds and native provider-reported fields against synthetic fixtures
and a small, explicitly authorized provider sample. Prefer improving the native adapter or upstream
catalog over a parallel homegrown universal pricing engine. Retain a generic custom billing
configuration for endpoints not represented by maintained adapters. The exact choice between
genai-prices data extensions and consuming LiteLLM data should follow a tested capability matrix,
not catalog row count.

Only then choose a gateway if its routing, credential, and admission features remove more work than
its deployment adds. LiteLLM remains conditional on licensing and end-to-end cost parity; the
[alternative assessment](native-and-alternatives.md) covers other credible options. A separate
database can share Arno's existing Postgres server, but gateway configuration, tokens, and
authoritative counters must be included in a tested recovery path.

Before adoption, require the experiments in [verification.md](verification.md): price and quota
parity, partial usage and canceled streams, retry attribution, concurrent budget admission, database
failure, restart and restore, private-data logging, and semantic compatibility. Any paid model
checks remain unperformed until explicitly requested.

This research does not select a production gateway, add a billing schema, enable subscription
credentials, or claim a reliable monthly running-cost total. Those are intentional next decisions
after the evidence gates pass.
