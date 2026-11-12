# Sources and confidence

Accessed November 12, 2026. Official documentation describes a supported or advertised feature;
source inspection establishes what the inspected version implements. Neither is evidence that a
deployment has passed the acceptance experiments in [verification.md](verification.md).

## Versions and reproducibility

The research uses these boundaries:

| Component | Boundary | What was verified |
|---|---|---|
| Arno Python dependencies | Pydantic AI 2.64.0, harness 0.64.0, genai-prices 0.2.1 | Installed package versions and synthetic pricing probe |
| Phoenix | Compose tag 21.0; source `6359d6388815d56f7795a3a8cd11e89a71793e01` | Source and manifest; no running-image verification |
| LiteLLM release | v1.109.0, `55c9c6af17a9acc4b46e707e8761c4592d0e0954` | Catalog inspected by delegated research |
| LiteLLM newer source | `89149b4544e056d52b46774bf5723b2ac57c385a`, version 1.110.0 | Catalog, dependency declaration, selected implementation paths |
| Provider documentation | Pages as accessed on the research date | Published semantics; no account entitlement or live charge verification |

Main-branch source and documentation can describe unreleased behavior. In particular, the LiteLLM
review distinguishes the released version from newer streaming-cost features. Catalog counts,
hashes, and coverage are recorded in [litellm.md](litellm.md#artifacts-inspected); broad coverage is
not a guarantee for every model, endpoint, or billing plan.

## Catalog, gateway, and budget evidence

The following primary sources support the catalog and control-plane findings.

| Source | Supports | Confidence and limit |
|---|---|---|
| [LiteLLM catalog](https://raw.githubusercontent.com/BerriAI/litellm/89149b4544e056d52b46774bf5723b2ac57c385a/model_prices_and_context_window.json) | Provider-specific entries, units, capabilities, missing rates | High for this snapshot; not an invoice |
| [Custom cost map](https://docs.litellm.ai/docs/proxy/custom_model_cost_map) | Overrides, startup fetch, offline mode, partial-override behavior | Official docs; verify chosen release |
| [Cost map](https://docs.litellm.ai/docs/proxy/model_cost_map) | Price fields and catalog behavior | Official docs; fields are not always complete |
| [Response headers](https://docs.litellm.ai/docs/proxy/response_headers) | Total, component, model, retry, and call identifiers | Official wire contract; not tested through Arno |
| [Budget reservation source](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/litellm/proxy/spend_tracking/budget_reservation.py) | Reservation and reconciliation mechanics | High for inspected code; no concurrency experiment |
| [LiteLLM dependencies](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/pyproject.toml) | Proxy extra includes enterprise package | High for declared dependencies; image not audited |
| [Enterprise license](https://github.com/BerriAI/litellm/blob/89149b4544e056d52b46774bf5723b2ac57c385a/enterprise/LICENSE.md) | Nonpermissive enterprise terms | Primary license text; not a claim about all MIT components |
| [genai-prices data maintenance](https://github.com/pydantic/genai-prices/blob/main/prices/README.md) | Versioned feeds and external discrepancy sources | Repository docs; no automatic LiteLLM runtime import established |
| [Pydantic AI usage and prices](https://pydantic.dev/docs/ai/api/pydantic-ai/usage/) | Cost fields and background price updates | Official docs plus local installed source |
| [Harness spend](https://pydantic.dev/docs/ai/harness/spend/) | Custom price hook, unknown policy, budget guarantees | Official docs; local probe governs installed precedence |

## Phoenix and alternatives

The following sources distinguish observation from enforcement and cover alternatives.

| Source | Supports | Confidence and limit |
|---|---|---|
| [Phoenix cost tracking](https://arize.com/docs/phoenix/tracing/how-to-tracing/cost-tracking) | LiteLLM-derived table, updates, custom prices, ingestion-time costing | Official docs; corroborated with pinned source in the focused note |
| [Phoenix calculator](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/server/cost_tracking/span_cost_calculator.py) | How ingested spans are priced | High for code; no running ingestion test |
| [Phoenix manifest](https://github.com/Arize-ai/phoenix/blob/6359d6388815d56f7795a3a8cd11e89a71793e01/src/phoenix/server/cost_tracking/manifests/v1.json) | Catalog origin and dates | High for pinned data |
| [Pydantic AI Gateway](https://pydantic.dev/docs/logfire/manage/ai-gateway/) | Centralized model access and controls | Official docs; hosted and commercial self-host distinctions matter |
| [Bifrost budgets](https://docs.getbifrost.ai/features/governance/budget-and-limits) | OSS budget state and multi-node limits | Official docs; restart behavior not tested |
| [Bifrost license](https://github.com/maximhq/bifrost/blob/main/LICENSE) | Apache-2.0 root license | Full selected-image dependencies still need audit |
| [Helicone gateway](https://docs.helicone.ai/gateway/overview) | Translation, logging, managed endpoint | Vendor documentation; deployment modes differ |
| [Helicone self-hosting](https://docs.helicone.ai/getting-started/self-host/docker) | Self-host stack and persistence | Official docs; do not assume cloud gateway features exist in all images |

## Provider billing and subscription evidence

These sources own the meaning of charges and access; gateway support cannot supersede them.

| Source | Supports | Confidence and limit |
|---|---|---|
| [OpenRouter accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting) | Account cost, upstream cost, and terminal streaming usage | API documentation; no real account sample |
| [OpenAI Costs API](https://developers.openai.com/api/reference/resources/admin/subresources/organization/subresources/usage/methods/costs) | Provider account-cost reconciliation | Official API; access permissions and reporting granularity apply |
| [Anthropic analytics](https://platform.claude.com/docs/en/manage-claude/usage-cost-analytics-api) | Usage, discounted versus list cost, delays, revisions | Official beta API; not a real-time admission source |
| [OpenCode Go](https://opencode.ai/docs/console/go/) | Plan, allowance, time-dependent rates, client purpose | Official docs; allowance/list-cost equality unmeasured |
| [LiteLLM OpenCode adapter](https://docs.litellm.ai/docs/providers/opencode_go) | Native provider and cost fields | Official docs plus source in focused note |
| [LiteLLM subscriptions](https://docs.litellm.ai/docs/subscriptions) | Supported client arrangements | Feature overview; child pages and source establish route differences |
| [LiteLLM ChatGPT provider](https://docs.litellm.ai/docs/providers/chatgpt) | Gateway-owned Codex-backend authentication | Technical implementation; does not establish provider permission |
| [OpenAI app plan access](https://developers.openai.com/siwc/token-sharing-open-source) | App-specific consent and local/open-source integration | Official supported path; eligibility and preview limits apply |
| [OpenAI preview limits](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations) | Responses API fields and tools | Official requirements; relevant to output-cap guarantees |
| [OpenAI self-hosted VMs](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms) | Local sign-in, credential transfer, VM refresh | Official guide; read alongside token-storage terms |
| [OpenAI sign-in terms](https://openai.com/policies/sign-in-with-chatgpt-terms/) | App identity, credential control, plan-use scope | Primary terms, effective November 4, 2026; do not transfer them blindly to another credential type |
| [Pydantic AI ChatGPT provider](https://pydantic.dev/docs/ai/models/openai-chatgpt/) | Native app sign-in and token refresh | Official docs; provider source also exists in installed 2.64.0 |
| [Claude credential rules](https://code.claude.com/docs/en/legal-and-compliance) | Native client OAuth versus third-party API-key integrations | Official policy; not evidence of an exception for Arno |

## Follow-up primary sources

| Source | Supports | Confidence and limit |
|---|---|---|
| [Claude SDK with a plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan) | Separate linked-Console API credits and official SDK subscription routes | Official support; not a general OAuth transport permission |
| [Monthly API credits](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-ultra-and-team-plans) | Eligibility, linking, funding, expiry, and overage behavior | Official support; no remaining-credit API documented |
| [Claude data use](https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training) | Commercial API data-use defaults | Official policy; credit funding does not imply zero retention |
| [Claude retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention) | API retention and exceptions | Official policy; account agreements can differ |
| [OpenCode inference API](https://opencode.ai/docs/console/api/inference/) | Cost, billing source, list value, allowance, token semantics, and terminal streaming events | Official wire contract; no owner response captured |
| [Phoenix supplied-cost issue](https://github.com/Arize-ai/phoenix/issues/15240) | Missing ingestion precedence and linked development branch | Open issue, no committed release timing |
| [Phoenix nested-cost issue](https://github.com/Arize-ai/phoenix/issues/17856) | Nested LLM span cost double counting | Reproduction in upstream issue; avoid overlapping instrumentation |

## Remaining verification

The final review also checked the [OpenAI transcription response](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)
for token versus duration usage, Python 3.14's
[`wave` contract](https://docs.python.org/3.14/library/wave.html) for PCM-only parsing, and the
[browser audio decoder](https://developer.mozilla.org/en-US/docs/Web/API/BaseAudioContext/decodeAudioData)
for complete-file decoding. Browser-format compatibility, bounded conversion, and real transcription
billing remain implementation acceptance; these references do not prove an audio pipeline exists.

No live provider or gateway request, subscription sign-in, invoice comparison, gateway image license
audit, concurrent budget test, or restore rehearsal was performed. The local pricing probe is
reproducible and free of model calls, but it does not verify actual provider debit. Conclusions
about fitness are recommendations from the cited evidence, not production certification.
