# Source ledger

Sources accessed on November 11–12, 2026. This ledger emphasizes the sources that drive the
applied assessment. The supporting notes carry the wider product and API citations.

## Confidence rules

First-party documentation is strong evidence of documented behavior, parameters, and limitations.
It does not establish independent product quality, real-world accuracy, Indian availability, or
fitness for Arno. Vendor marketing is weaker evidence and must be labeled. Research papers support
only the population, tasks, and conditions they studied. Professional standards provide a planning
taxonomy and process analogy, not a software certification or local legal authority.

The parent opened the sources listed as checked below, reviewed the delegated notes, and checked
current Arno source for the main gap claims. Other detailed vendor claims remain the delegated
investigation's findings unless explicitly identified as parent-verified. No vendor API was
benchmarked and no account was connected.

The table records the principal evidence and its limits.

| Source | Owner and evidence type | What it supports | Important limit | Review |
|---|---|---|---|---|
| [Financial well-being](https://www.consumerfinance.gov/consumer-tools/financial-well-being/about/) | CFPB, public consumer research summary | Daily control, shock resilience, goals, and freedom as product outcomes | US research; no claim of India score validity | Parent checked |
| [Planning standards](https://www.cfp.net/ethics/code-of-ethics-and-standards-of-conduct) | CFP Board, professional standards | Circumstances, assumptions, alternatives, monitoring, incomplete information | Duties apply to professionals, not certification of Arno | Parent checked |
| [Planning education framework](https://www.fpsb.org/wp-content/uploads/2016/01/151027_doc_EducationFramework_FINAL.pdf) | FPSB, 2015 primary framework | Breadth across management, investing, insurance, tax, retirement, estate | Stable taxonomy; not a source of current law | Parent checked |
| [PocketSmith scenarios](https://learn.pocketsmith.com/calendar--forecasting/6a6X8SseDAXwf8ZqYunJuU/scenarios-everything-you-need-to-know-about-scenarios-in-pocketsmith/6a6X8SseDAQ8gWy6LCbMW8) | PocketSmith, product help | Account-specific forecasts and multiple secondary scenarios | Documentation is not an independent accuracy test | Parent checked |
| [Monarch scenarios](https://help.monarch.com/hc/en-us/articles/55286775198996-Using-Forecasting-Scenarios) | Monarch, product help | Saved scenarios with independent assumptions and side-by-side comparison | Plus tier and web; no independent accuracy result | Parent checked |
| [Copilot assistant](https://help.copilot.money/en/articles/14462982-money-assistant-overview) | Copilot, product help | In-chat edits, standing instructions, reviewable fixes and charges | Current assistant scope is iOS; privacy statements are vendor claims | Parent checked |
| [Copilot manual accounts](https://help.copilot.money/en/articles/10682991-understanding-manual-accounts), [onboarding](https://help.copilot.money/en/articles/5542003-historical-transaction-data) | Copilot, product help | Manual records coexist with an onboarding bank-link requirement | Does not establish Indian eligibility or arbitrary statement import | Parent checked |
| [Origin forecasting](https://support.useorigin.com/hc/en-us/articles/36002288809101-What-is-Origin-s-forecasting-tool-and-how-can-it-help-me), [analysis](https://support.useorigin.com/hc/en-us/articles/20957679919629-How-AI-Advisor-Analyzes-Your-Finances) | Origin, product help | Life-event scenarios, simulations, and a claimed deterministic-engine architecture | Architecture and quality not independently audited; forecasting page labels its plan-gating discussion as inferred | Parent checked |
| [Monarch AI](https://help.monarch.com/hc/en-us/articles/16116906962452-About-Monarch-s-AI-Features) | Monarch, product help, updated Nov 2, 2026 | Assistant, insights, recap; proactive experience beta status | The new experience is paid US early access; do not generalize availability | Parent checked |
| [Sahamati FAQ](https://sahamati.org.in/faq/) | AA ecosystem association, first-party participation guidance | Regulated entity eligibility for FIUs and ecosystem participation | Not an exhaustive legal opinion on commercial partner arrangements | Parent checked |
| [RBI AA directions](https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=10598), [FIU onboarding](https://sahamati.org.in/financial-information-user-fiu/) | RBI directions and Sahamati process documentation | Direct FIU eligibility, purpose-bound consent, onboarding | Ordinary self-host ineligibility is an inference from eligibility; no blanket opinion on partners or all bank APIs | Parent checked |
| [RBI card directions](https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx?id=12300) | RBI, directions and FAQs | Minimum-only repayment warnings, interest-free period, repayment-cost distinctions | Actual calculation still needs issuer contract terms; source displays its revision date | Parent checked |
| [RBI loan KFS](https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12663) | RBI, April 2024 circular | APR and amortization disclosures, stated scope and card-receivable exception | Not a model for every product or a complete statement of later law | Parent checked |
| [DICGC guide](https://www.dicgc.org.in/guide-to-deposit-insurance) | DICGC, official consumer guide | Per-bank aggregation and ownership/capacity/right distinctions | Bank and depositor eligibility must be established; not market-loss protection | Parent checked |
| [Tax transition FAQ](https://www.incometax.gov.in/iec/foportal/help/all-topics/e-filing-services/objective-and-scope-new-act-faq?mobile-app=1) | Income Tax Department, official FAQ | Year and applicability transition between Acts | Not verification of a current slab, exemption, or filing deadline table | Parent checked |
| [CASParser](https://github.com/codereverser/casparser) | Maintainer source repository | Supported issuers, exclusions, MIT license, documented PDF backend | Version and transitive dependencies still require adoption-time audit; no parser accuracy claim | Parent checked |
| [CDSL CAS FAQ](https://www.cdslindia.com/CAS/FAQ.html) | CDSL, depository documentation | Consolidated demat and mutual fund statement coverage | Not a complete household balance sheet or a general third-party API license | Parent search evidence; India note provides detail |
| [AMFI NAV history](https://www.amfiindia.com/net-asset-value/nav-history?s=09) | AMFI, official data page | Existence of historical mutual fund NAV lookup | Public access does not prove redistribution rights or holdings ownership | Parent search evidence; India note provides detail |
| [AMFI terms](https://www.amfiindia.com/terms-of-use) | AMFI, site terms | Personal/noncommercial permitted use and storage/redistribution restrictions | Terms applicable to a particular endpoint and proposed integration still need review | Parent checked |
| [Kite authentication](https://www.kite.trade/docs/connect/v3/user/) | Zerodha, API reference | Authentication flow and next-day token expiry | Broker-specific access; no assumption of unattended permanent authorization | Parent search evidence; India note provides detail |
| [Adaptive web search](https://pydantic.dev/docs/ai/capabilities/web-search/) | Pydantic, framework documentation | Native-only default, optional local/custom fallback, restriction behavior | Current docs do not prove compatibility with Arno's locked versions | Parent checked |
| [Adaptive web fetch](https://pydantic.dev/docs/ai/capabilities/web-fetch/) | Pydantic, framework documentation | Native-only default, local fetch and domain restriction options | Fetching is not factual verification or complete injection protection | Parent checked |
| [Exa harness](https://pydantic.dev/docs/ai/harness/exa-search/) | Pydantic, integration documentation | Search/page retrieval, structured sources, untrusted-content and fetch-URL risks | Search filters do not automatically constrain page retrieval; 0.x package | Parent checked |
| [Tavily search](https://docs.tavily.com/documentation/api-reference/endpoint/search) | Tavily, API reference | Domain restrictions, country boost, source-date fields | Estimated/null publication dates are not effective dates or verified truth | Parent checked |
| [Brave search](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started) | Brave, API documentation | Country/language targeting and freshness controls | No quality, cheapest-provider, or retention conclusion established | Parent checked |
| [OWASP LLM01](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) | OWASP, security guidance | Indirect injection, least privilege, separation, human control | Mitigation guidance, not guaranteed prevention | Parent checked |
| [NIST Measure](https://airc.nist.gov/airmf-resources/playbook/measure/) | NIST, voluntary AI risk guidance | Fit-for-purpose testing, limits, ongoing measurement, external-input monitoring | Does not prescribe Arno's exact budgets, schema, or alert deduplication | Parent checked |
| [Personalized financial adviser study](https://arxiv.org/abs/2504.05862) | Authors, SIGIR 2025 paper | Preference elicitation and divergence between trust and quality in a lab study | 64 participants and investment-advice tasks; no generalized Arno result | Parent checked |

## Unresolved checks before adoption

- Search provider comparison requires an Indian-question benchmark and current privacy/retention
  terms. Pricing units, extraction charges, and modes differ; headline prices do not rank value.
- Vendor docs and available data do not establish an Indian household's instrument coverage.
  Verify exact holdings, identifiers, and available dates before choosing a valuation API.
- AMFI, exchange, broker, and other data access terms must be reviewed independently from a
  parser library's software license. A permissive parser does not license the underlying data.
- Read current Indian legal/tax sources for the applicable financial year and effective date
  before implementing a calculator. No rate or due-date table in this survey is an authoritative
  rule source for production.
- Verify the selected framework capability against Arno's locked dependencies and actual model
  transport before implementation. This research did not change dependencies or run external APIs.

## Repository evidence

The applied assessment uses [the plan](../../arno/plan.md),
[parked decisions](../../arno/parked.md),
[completion status](../../arno/completion-checklist.md),
[agent registration](../../../src/arno/agent/builder.py),
[tools](../../../src/arno/agent/toolset.py),
[quality and projection loading](../../../src/arno/tools/projection.py),
[projection](../../../src/arno/engine/projection.py),
[backtest limits](../../../src/arno/engine/backtest.py), and
[number-check limits](../../../src/arno/agent/number_check.py).

These are current-source observations. The survey did not re-run live journeys, measure a live
ledger, or convert an open acceptance item into a pass.
