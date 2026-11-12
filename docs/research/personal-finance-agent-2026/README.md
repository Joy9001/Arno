# Capabilities for a leading personal finance agent

Research and applied assessment, November 11–12, 2026. This report describes what I would require
before calling Arno a leading personal finance agent. It is a recommendation, not an amendment
to [the product plan](../../arno/plan.md). No application behavior changed during this research.

For the detailed specification, read the [capability catalog](capability-catalog.md). It documents
27 capabilities with descriptions, user benefits, agent actions, exact internal/framework/external
tool mappings, current coverage, and completion checklists. This assessment explains the judgment;
the catalog makes each proposed outcome and its evidence requirements inspectable.

## Judgment

Arno has a substantial foundation: a ledger, imports with review, financial calculations,
probabilistic affordability, undo, memory, reminders, and a private deployment model. The next
step is to make that foundation complete, current, reproducible, and useful across decisions.
Adding stock-market endpoints or a general research swarm would not establish those properties.

I would judge the product by whether it helps me understand today's position, meet obligations,
withstand shocks, and progress toward my own goals. This outcome framework is informed by the
[Consumer Financial Protection Bureau's financial well-being research](https://www.consumerfinance.gov/consumer-tools/financial-well-being/about/).
It is a product judgment, not a claim that a US questionnaire measures an Indian user's outcomes.

Financial planning also requires understanding circumstances, comparing alternatives, and
revisiting a plan, rather than answering isolated questions. The
[CFP Board's planning standards](https://www.cfp.net/ethics/code-of-ethics-and-standards-of-conduct)
are useful evidence for that process. They apply to professionals; they do not certify software
or authorize Arno to provide regulated advice.

## How to read the recommendations

An essential capability is an outcome the product must support, not necessarily a new tool,
integration, dashboard, or agent. For example, accurate records are essential; an automatic bank
feed is only one way to acquire them. A useful financial plan is essential; an envelope budget
is one method, not a universal requirement.

The assessment distinguishes capabilities that exist in code, gaps in their breadth, and missing
acceptance evidence. Code existence does not establish reliable live behavior. The baseline comes
from the current [tool registration](../../../src/arno/agent/toolset.py),
[projection inputs](../../../src/arno/tools/projection.py),
[engine](../../../src/arno/engine/projection.py), and
[completion checklist](../../arno/completion-checklist.md). No owner financial records were needed.

The supporting investigations cover [consumer products](market.md),
[India data and planning](india.md), and [tools and trust](tools-and-trust.md).
The catalog adds verified notes on [current Arno coverage](verification-arno-coverage.md),
[Pydantic and MCP enablement](verification-framework.md), and
[finance-tool constraints](verification-finance-tools.md).
[The source ledger](sources.md) records confidence and
limits. Vendor documentation establishes advertised or documented functionality, not independent
product quality. Prices and provider support are point-in-time observations, not selection results.

The clearest market lessons are Monarch's
[saved, independently configured scenarios](https://help.monarch.com/hc/en-us/articles/55286775198996-Using-Forecasting-Scenarios),
PocketSmith's account-bound forecasts, and Copilot's
[reviewable fixes, refunds, and flagged charges](https://help.copilot.money/en/articles/14462982-money-assistant-overview).
Origin also documents [life-event simulation](https://support.useorigin.com/hc/en-us/articles/36002288809101-What-is-Origin-s-forecasting-tool-and-how-can-it-help-me)
and [deterministic calculation engines](https://support.useorigin.com/hc/en-us/articles/20957679919629-How-AI-Advisor-Analyzes-Your-Finances).
Those documents establish useful patterns, not that every feature is independently validated or
that Arno is the only agent whose calculations run in code.

## Essential capabilities

### 1. A complete financial picture with explicit gaps

Arno should know which accounts, debts, income streams, obligations, dependents, and goals apply to
me, which are missing, and which records are old or incomplete. Onboarding should produce a useful
first answer and continue incrementally. A new import or correction should not silently erase a
conflicting fact. Evidence, dates, coverage, and user assumptions should remain distinguishable.
The profile should also retain my priorities, income stability, time horizons, and constraints,
with an explicit way to resolve conflicting goals and revise remembered preferences.

Arno already has conversational setup, balance anchors, import reconciliation, and quality notes.
What needs deepening is a persistent, user-visible coverage and freshness review: the next missing
piece that matters to the question, not a demand to finish every possible setup field.

**Acceptance:** when one account has a verified starting balance and another has none, the answer
shows partial knowledge, identifies the missing account, and avoids a complete-cash claim. When a
statement and a manually stated balance disagree, it explains dates and seeks resolution.

**Tradeoff:** importing everything automatically lowers effort but introduces access, privacy, and
reconciliation costs. A reliable statement workflow is a valid acquisition method. The
[CFP standards](https://www.cfp.net/ethics/code-of-ethics-and-standards-of-conduct) support gathering
material information and limiting scope when it is insufficient; the exact workflow is my proposal.

### 2. Account-specific liquidity and payment readiness

An aggregate surplus does not establish that the account funding an autodebit has enough cleared
money. Arno should explain both overall affordability and the funding account's position on each
due date, including planned transfers and credit-card settlement. It should distinguish an unpaid
bill, a planned payment, and a payment supported by recorded evidence.

Arno has payment-evidence matching and card cycles. Its projection currently pools liquid cash;
account-level projections are explicitly [parked](../../arno/parked.md). I would reopen that
decision before describing the product as best in class. PocketSmith documents
[account-bound forecasts and scenarios](https://learn.pocketsmith.com/calendar--forecasting/6a6X8SseDAXwf8ZqYunJuU/scenarios-everything-you-need-to-know-about-scenarios-in-pocketsmith/6a6X8SseDAQ8gWy6LCbMW8),
which establishes this as a concrete product capability, not a speculative interface idea.

**Acceptance:** I have enough total cash, but insufficient cash in the EMI account on its due date.
Arno identifies the account and date, explains the shortfall, and can model a transfer I choose.
It neither executes a transfer nor marks an unpaid installment paid.

**Tradeoff:** this requires account routing and timing throughout the model, not just another chart.

### 3. Reproducible scenarios and stress tests

Arno should model related changes together: a purchase, higher rent, delayed salary, a medical
expense, a career break, or reduced income. A scenario needs a named baseline, explicit overrides,
an assumptions record, and a comparison that can be reopened after the ledger changes. It must
never require temporarily changing real records just to ask a hypothetical question.

The current purchase hypothetical, loan calculator, and Code Mode are useful primitives. They do
not by themselves constitute saved, versioned, multi-event scenarios. PocketSmith's
[secondary scenarios](https://learn.pocketsmith.com/calendar--forecasting/6a6X8SseDAXwf8ZqYunJuU/scenarios-everything-you-need-to-know-about-scenarios-in-pocketsmith/6a6X8SseDAQ8gWy6LCbMW8)
provide market evidence for comparing bundles of decisions. This report proposes adding uncertainty
and provenance requirements suited to Arno.

**Acceptance:** compare buying now, delaying the purchase, and buying during an income interruption.
Show cash, account shortfalls, bills, goal effects, and the assumptions that change the outcome.
Reopening the comparison identifies its original data date instead of silently recomputing it.

**Tradeoff:** more scenarios are not useful if assumptions are hidden or the history cannot support
their apparent precision. Stress cases are conditional tests, not predictions with invented odds.

### 4. Debt and credit decisions at their full cost

Arno should model total repayments, interest, applicable fees, card repayment obligations, and
prepayment alternatives. It should distinguish credit availability from income and the minimum
payment from clearing a statement. The minimum useful output is a cost and timing comparison that
preserves the user's emergency reserve and obligations, not a recommendation for a loan or card.

Arno already calculates amortization and prepayment. The next coverage review should establish
which real contract terms are represented: rate changes, fees, taxes on fees, foreclosure terms,
and promotional conditions. Unsupported terms must remain unknown rather than assumed free.

**Acceptance:** an installment offer described as interest-free has a processing fee and a cash
purchase alternative. Arno asks for missing terms, computes the total cost of each supported
option, and shows cash-flow effects without calling one product the winner.

**Tradeoff:** contract-specific models are more valuable than importing a large generic analyst API.
The [RBI card directions](https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx?id=12300)
explicitly warn about minimum-only repayment. The
[loan KFS circular](https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12663) requires APR and
amortization disclosures within its scope and treats card receivables separately. Indian rule and
access evidence is detailed in [the India investigation](india.md).

### 5. A spending and savings plan that adapts to real life

Arno should help translate goals into an affordable saving contribution and discretionary spending
range, reserve for irregular expenses, and compare actual behavior with the user's chosen plan.
Annual premiums, repairs, festivals, and travel should not become surprises merely because they
are not monthly. It should support variable-income planning and explain a change without blame.

Arno already has schedules, goals, baselines, and spending summaries. A more complete plan would
make planned-versus-actual changes and irregular reserves visible and allow the user to adjust
priorities. Category budgets can remain optional. The outcome follows the control-and-resilience
dimensions of [financial well-being](https://www.consumerfinance.gov/consumer-tools/financial-well-being/about/).

**Acceptance:** after an income change, show which contributions and discretionary ranges remain
feasible, which commitments are already reserved, and which choices require user revision. Do not
present a speculative future income payment as cash already available.

**Tradeoff:** the plan must accommodate users who want flexible guardrails as well as strict caps.

### 6. Current, source-grounded financial research

Arno should be able to answer a question about a current rule, rate, scheme, or financial term from
retrieved evidence. It should prefer the source that owns the fact, distinguish announcement date
from effective date, cite the supporting passage or record, and expose uncertainty or conflicting
sources. Research output must stay separate from personal records.

This capability is missing today. A provider-independent search and fetch path is important
because Arno supports interchangeable models. PydanticAI already documents
[adaptive web search](https://pydantic.dev/docs/ai/capabilities/web-search/) and
[web fetch](https://pydantic.dev/docs/ai/capabilities/web-fetch/); native-only defaults should not be
assumed to work with Arno's current OpenAI-compatible chat transport. The
[Exa harness integration](https://pydantic.dev/docs/ai/harness/exa-search/) demonstrates structured
source metadata and explicitly documents untrusted page content.

**Acceptance:** a question asks about a rule in a named financial year. Arno retrieves an official
source, states the applicable period, retains the evidence needed to reproduce the answer, and
declines to invent a current rule if retrieval fails. Its external query contains no unnecessary
household information.

**Tradeoff:** search results and synthesized research are leads, not verified calculation inputs.
A validated external rate may enter a scenario as a labeled assumption; it may not silently
overwrite a ledger fact. The exact source-retention policy also needs to respect content terms.

### 7. Relevant warnings with a clear next step

Arno should warn when an obligation is underfunded, an important assumption changes, a goal moves
off track, a recurring charge changes, or data is too incomplete for the requested decision.
Each warning needs the evidence, its consequence, and an available next step. Users need control
over thresholds, timing, dismissal, and repeat behavior.
Useful review candidates include an expected refund that has not arrived, a changed subscription,
a possible duplicate, or a charge the user does not recognize. A flag is a request for review,
not a declaration of fraud or permission to cancel a service.

Much of this exists: reminders, risk episodes, recurring-charge proposals, scheduled questions, and
the inbox. Deepening means better prioritization, change explanations, and resolving a warning
through the product. It does not mean adding more briefings by default.

**Acceptance:** repeated checks of the same unpaid bill produce one active warning. Recording a
supported payment resolves it; undoing that payment restores the relevant state. A missing account
balance produces a data-quality explanation rather than a fabricated risk probability.

**Tradeoff:** notification volume is not evidence of financial benefit. Monitor usefulness and
missed material events, not engagement alone.

### 8. Inspectable answers, user control, and measured reliability

The user should be able to inspect the evidence and calculation behind a material claim, correct
it, and understand what changed. External text must not authorize writes or leak private data.
Exports, deletion, recovery, and disabling external access should be usable product capabilities.
Reliability must be measured across real journeys and changes in data, not inferred from a demo.

Arno has strong primitives: integer paise, deterministic calculations, ledger sources, undo,
confirmation gates, passkeys, and backups. Its
[number checker](../../../src/arno/agent/number_check.py) flags unmatched quantities; it explicitly
does not prove that a matched number was used with the correct meaning. Semantic tests are still
essential. The [backtest](../../../src/arno/engine/backtest.py) also acknowledges that later edits
to existing records can appear in historical runs. A leading forecast needs as-known-at-the-time
evaluation or an explicit limitation, plus calibration on usable real history.

**Acceptance:** adversarial web content cannot create a lasting change or encode private data in a
fetch URL; an old tax fact is not used as current; a calculation with the wrong account or unit
fails an eval even if every digit came from a tool. Restore on a fresh machine and live capture,
import, warning, and scheduled-question journeys have recorded outcomes.

**Tradeoff:** safeguards mitigate risk; they do not guarantee factual correctness. The
[OWASP injection guidance](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) supports layered
controls. A [SIGIR 2025 study](https://arxiv.org/abs/2504.05862) also found that perceived trust and
advice quality could diverge in a small investment-advice experiment. Its scope does not establish
Arno's performance, but it argues against treating user satisfaction as a correctness metric.

## Coverage required when it applies to the user

To cover a whole financial life, Arno must recognize the following areas, gather relevant facts,
and either model them accurately or state the boundary. Not every area needs a full engine on the
first installation. The [FPSB education framework](https://www.fpsb.org/wp-content/uploads/2016/01/151027_doc_EducationFramework_FINAL.pdf)
supports this breadth as a coverage taxonomy; its 2015 publication is not a source of current law.

The table separates a useful capability from an unjustified expansion.

| Area | Capability to support | Boundary |
|---|---|---|
| Investments and net worth | Import actual holdings and liabilities; separate contributions, returns, and price changes; show valuation dates, liquidity, concentration, and missing coverage | No stock picking, return guarantees, or broker execution; holdings acquisition and valuation terms must be verified |
| Retirement and life events | Model inflation-aware goals, longevity assumptions, dependent costs, career breaks, and changes in household obligations | Transparent sensitivity ranges, not a promised retirement outcome |
| India tax and benefits | Recognize relevant income types, financial years, withholding, deductions, retirement schemes, and applicable obligations; research current official rules | A versioned deterministic calculator is needed for tax estimates; research text alone is not a tax engine or filing service |
| Protection and continuity | Inventory insurance, emergency reserves, policy renewal dates, nominees, document locations, and per-bank deposit exposure; identify missing facts and questions for a professional | Verify bank coverage and ownership before estimating deposit protection; no unsupported insurance adequacy verdict or legally sufficient estate-document claim |

Arno's manual asset accounts and generic goals provide a start. Holdings-based wealth, a tax
engine, and broader planning remain scope expansions. Investment statements and direct APIs solve
different acquisition problems; an exchange price does not reveal what I own. India-specific
sources and access constraints are in [the India note](india.md).

Deposit exposure is a concrete India-specific review worth adding: the
[DICGC guide](https://www.dicgc.org.in/guide-to-deposit-insurance) aggregates insured deposits by bank
and the depositor's capacity and right. Counting each fixed deposit as separately protected would
be wrong. Separately, an asset value is not necessarily emergency cash: access restrictions,
withdrawal terms, and valuation dates matter to a resilience scenario.

## Tool and skill selection

I would start with the following small set of additions, evaluated against real questions.

| Need | Candidate | Selection condition |
|---|---|---|
| Public-rule and terms research | One of Tavily, Brave Search, or Exa; framework search/fetch where compatible | Compare source quality on Indian questions, citation support, privacy, failure behavior, terms, and total cost; no winner established by this survey |
| Read a known official page | Framework fetch with domain and network restrictions | Prefer direct retrieval when the source is already known; verify redirects and private-address blocking |
| Mutual fund valuations | Official AMFI data after holdings acquisition | Verify instrument identifiers, valuation dates, and usage terms; NAV is not holdings or an execution price |
| Investment holdings | User-supplied consolidated statements, with a checked parser | Preserve statement evidence and reconcile duplicates; review parser and dependency licenses separately from data terms |
| Bank aggregation | Statement import initially; regulated partner only after feasibility review | Do not treat an individual self-host as an eligible direct FIU or assume a partner makes access free or unrestricted |
| General market/macro data | A selected official series or licensed API only when a scenario requires it | Alpha Vantage, FMP, and FRED are not substitutes for household records; check exact India/instrument coverage |

The research services' documented controls differ: [Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search)
supports country boosting and domain controls; [Brave](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)
documents country targeting and freshness filters; [Exa's integration](https://pydantic.dev/docs/ai/harness/exa-search/)
offers search, page retrieval, and structured sources. No live comparative benchmark or paid API
trial was run, so selecting a winner would exceed the evidence.

AMFI needs a specific access-rights check: its [site terms](https://www.amfiindia.com/terms-of-use)
limit permitted use and restrict significant electronic storage and redistribution. A public NAV
download is not evidence of unrestricted caching or redistribution permission. Verify the terms
applicable to the intended data endpoint and use before committing to an automated integration.

[CASParser](https://github.com/codereverser/casparser) is a concrete parser candidate: its current
repository documents CAMS, KFintech, NSDL, and CDSL statements and an MIT license. It does not cover
every statement variant; notably its documented exclusions include MF Central and reformatted
copies. Benchmark the selected version against synthetic fixtures and supported issuer formats,
and check dependencies before adding it. No parser was installed by this research.

Agent skills should express workflows such as financial review, debt comparison, scenario planning,
and official-source research. They should select tested tools, not contain authoritative tax tables
or formulas that drift independently from the engine. Existing PydanticAI capabilities should be
checked before introducing a custom research mechanism. A large skill marketplace is not required.

## Recommended order

This sequence is my proposed priority order, not an approved implementation plan.

1. Prove and deepen the current foundation: onboarding, coverage/freshness, real imports, payment
   evidence, usable calibration, recovery, and live notification/capture journeys.
2. Address decision-critical gaps: account-level funding, saved compound scenarios, stress tests,
   and complete debt-cost comparisons. Keep existing deterministic tools and advice boundaries.
3. Add bounded, cited research for current rules and terms, with private-query controls, retained
   provenance, rate/cost limits, and the existing confirmation boundary extended to web content.
   This can proceed alongside scenario work once the external trust boundary is defined.
4. Add the applicable whole-life modules: holdings/net worth, retirement, tax estimates, insurance
   and continuity review. Let actual user circumstances determine the order and required data.

The proposed test questions are more meaningful than a feature count: can I trust today's
position, will the correct account meet each obligation, what changes under a shock, what does
each option cost, and what material fact could make the answer wrong?

## Decisions to revisit and exclusions to retain

Account-level forecasting, investment statements, and tax modeling are deliberately parked in
`plan.md` §2. The report recommends reopening them when pursuing the broader ambition. It does
not call them accidental omissions or silently reverse the product plan. A financial coverage
review and saved compound scenarios are additional proposals.

Automatic payments or trades, a broker-style research terminal, stock-picking recommendations,
an unrestricted credential-bearing browser, and a mandatory research-agent swarm are not needed
for this product. Shared-household access, multicurrency, a native app, category envelopes, and
receipt line items remain conditional features with specific user triggers. They are not measures
of whether the core agent is financially trustworthy.

## Evidence limits

This is a primary-source survey and a read-only code assessment, not an independent hands-on test
of competing products. Beta features and marketing claims must be labeled in the supporting notes.
US product capabilities establish possible user workflows, not Indian availability. Regulatory
eligibility is not equivalent to a legal opinion on every commercial arrangement. Public download
access does not establish redistribution rights.

The model-selected subagents were configured as `opencode-go/deepseek-v4.1-flash` with
`variant: high`; the parent reviewed the findings and corrected unsupported selection claims.
No model evals, paid research requests, real financial writes, deployment, or application changes
were performed. The plan and completion checklist still own current implementation and acceptance
status. The report does not claim that a better feature list establishes the best product.
