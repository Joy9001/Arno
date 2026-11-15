# Arno capability catalog

Researched November 12, 2026 against repository commit `9181891`, Pydantic AI 2.64.0, and Pydantic
AI Harness 0.64.0. This catalog defines proposed capability outcomes and acceptance criteria. It
does not approve scope changes, select paid services, or claim unfinished features are available.
[The plan](../../arno/plan.md) owns product decisions.

The external MCP server shortlist was expanded on November 13, 2026. The application-coverage labels
below retain the November 12 source baseline.

## Reading this catalog

Each entry gives a one-line name, a short description, the user benefit, agent actions, enabling
tools, and a completion checklist. _Existing_ describes inspected source; _partial_ identifies
working foundations with a narrower scope; _proposed_ describes additional work. _Parked_ means the
current plan deliberately excludes that work. None of these labels means a new live test passed.

Tool mappings identify internal application functions, Pydantic capabilities, and concrete external
MCP servers. Vendor MCP tool names may differ from that vendor's Pydantic harness adapter; the
registry names the interface being proposed.

Core capabilities are C01–C11 and C17–C22. Whole-life capabilities C12–C16 apply when the user's
assets and circumstances require them. C23–C27 are optional extensions, not requirements for every
installation. Portfolio analysis does not authorize trading, tax estimates do not authorize filing,
and modeling a payment never executes it.

All completion boxes are intentionally unchecked: they are acceptance targets for the full
capability described, not a claim that existing subfeatures are missing. Close a box only with
recorded evidence for the selected version and installation. Automated fixtures, provider sandbox
tests, and real UI journeys must be labeled separately. Do not copy an old checklist tick.

The evidence comes from [current implementation](verification-arno-coverage.md), [framework source
and documentation](verification-framework.md), [finance libraries and
APIs](verification-finance-tools.md), and the [source ledger](sources.md). External behavior was
researched from primary sources; no paid API, model eval, account connection, real financial record,
or restore was exercised for this document.

The index maps the capabilities to current coverage.

| ID | Capability | Current coverage |
|---|---|---|
| [C01](#c01-progressive-onboarding-and-personal-context) | Progressive onboarding and personal context | Partial: setup and typed memory exist |
| [C02](#c02-a-trustworthy-financial-picture) | A trustworthy financial picture | Partial: anchors and coverage exist; ongoing review needs depth |
| [C03](#c03-transactions-splits-and-corrections) | Transactions, splits, and corrections | Existing; broader live acceptance remains |
| [C04](#c04-capture-import-and-reconciliation) | Capture, import, and reconciliation | Existing paths; real-device and import proof remains |
| [C05](#c05-financial-contract-understanding) | Financial contract understanding | Proposed beyond existing document readers |
| [C06](#c06-spending-insight-and-an-adaptive-plan) | Spending insight and an adaptive plan | Partial; category budgets are parked |
| [C07](#c07-goals-and-irregular-expense-reserves) | Goals and irregular expense reserves | Partial: goals, schedules, and baselines exist |
| [C08](#c08-cash-flow-and-account-funding-readiness) | Cash flow and account funding readiness | Aggregate forecast exists; account forecasts are parked |
| [C09](#c09-purchase-affordability-and-payment-comparisons) | Purchase affordability and payment comparisons | Existing engine; calibration and full contract costs need work |
| [C10](#c10-saved-scenarios-and-stress-tests) | Saved scenarios and stress tests | Proposed; one-off calculations exist |
| [C11](#c11-debt-and-credit-lifecycle-cost) | Debt and credit lifecycle cost | Partial: amortization and prepayment exist |
| [C12](#c12-holdings-and-dated-net-worth) | Holdings and dated net worth | Manual assets exist; statement-based wealth is parked |
| [C13](#c13-investment-performance-and-benchmarks) | Investment performance and benchmarks | Proposed |
| [C14](#c14-retirement-and-life-event-planning) | Retirement and life-event planning | Proposed beyond generic goals and compounding |
| [C15](#c15-india-tax-estimates-and-obligation-calendar) | India tax estimates and obligation calendar | Tax engine is parked |
| [C16](#c16-emergency-reserves-and-financial-protection) | Emergency reserves and financial protection | Premium schedules exist; policy and deposit review proposed |
| [C17](#c17-current-financial-research-with-evidence) | Current financial research with evidence | Proposed; ledger citations already exist |
| [C18](#c18-proactive-review-and-scheduled-questions) | Proactive review and scheduled questions | Existing jobs; live delivery and usefulness need proof |
| [C19](#c19-connection-health-and-recoverable-ingestion) | Connection health and recoverable ingestion | Existing job recovery; external connectors proposed |
| [C20](#c20-inspectable-answers-and-portable-models) | Inspectable answers and portable models | Cards and data exports exist; model spreadsheets proposed |
| [C21](#c21-privacy-authorization-and-user-control) | Privacy, authorization, and user control | Existing core controls; external boundaries need extension |
| [C22](#c22-measured-reliability-and-recovery) | Measured reliability and recovery | Tests and backup tools exist; acceptance gaps remain |
| [C23](#c23-foreign-currency-valuation) | Foreign-currency valuation | Multicurrency accounting is parked |
| [C24](#c24-freelancer-receivables-and-net-payouts) | Freelancer receivables and net payouts | Variable income exists; invoice/settlement model proposed |
| [C25](#c25-merchant-identity-enrichment) | Merchant identity enrichment | Internal rules exist; external enrichment proposed |
| [C26](#c26-additional-notification-and-approval-channels) | Additional notification and approval channels | In-app channels exist; chat integrations are parked |
| [C27](#c27-business-system-and-accountant-handoff) | Business-system and accountant handoff | Generic exports exist; business connectors proposed |

## Daily money management

### C01: Progressive onboarding and personal context

**Description:** build a financial profile incrementally from the user's facts and priorities. Ask
for the missing information that matters to the current question, while retaining dates,
uncertainties, and preferences that the user can inspect and revise.

**How it helps the user:** provides a useful first answer without a large setup questionnaire.

**What the agent can do:**

- Establish accounts, salary timing, obligations, goals, and a minimum cash floor conversationally.
- Explain gaps, resolve conflicting priorities, and revise remembered facts when circumstances
  change.
- Retain relevant horizons, dependents, income stability, and risk/liquidity constraints as reviewed
  facts.

**Enabling tools and status:**

- Internal, partial: `get_financial_context`, `update_setup`, and `preference` in the
  [toolset](../../../src/arno/agent/toolset.py); enrollment in [Setup](../../../web/src/Setup.tsx).
- Pydantic: existing structured tool inputs and persisted conversation context. A richer planning
  profile needs application data and validation; generic typed memory is not that schema.
- External/MCP: none required. Avoid public searches for private profile information.

**Completion checklist:**

- [ ] A fresh user reaches a supported first answer from a balance and income snapshot.
- [ ] A missing required card field keeps the card pending; no balance, limit, or date is invented.
- [ ] Profile conflicts and changed priorities are visible, editable, and retained across restart.
- [ ] The real enrollment-to-first-answer UI journey passes with no hidden operator intervention.

### C02: A trustworthy financial picture

**Description:** represent accounts, liabilities, obligations, and their evidence consistently.
Distinguish known zero, unknown, incomplete history, and old information; preserve the provenance of
balances and explain disagreements between user statements and imported records.

**How it helps the user:** prevents confident decisions based on a partial or misleading total.

**What the agent can do:**

- Answer what is known about cash, debt, and commitments, with dates and coverage warnings.
- Identify the next material missing statement or balance and explain how to resolve a conflict.

**Enabling tools and status:**

- Internal, partial: `get_financial_context`, `query_movements`, `run_query`, and `update_setup`;
  [read coverage](../../../src/arno/tools/read.py) and
  [accounts](../../../src/arno/ledger/accounts.py).
- Pydantic: existing Code Mode and typed tool returns; proposed structured coverage-review results.
- External: statements initially; evaluate Fi Money's user-authorized personal-finance MCP under
  [T03](#t03-external-mcp-servers), including its actual liability and transaction coverage. Direct
  aggregation access remains a separate route in [T07](#t07-account-access).

**Completion checklist:**

- [ ] Known zero renders as zero; an unanchored account never establishes a known starting balance.
- [ ] One missing liquid balance prevents a complete-cash claim; any subtotal is explicitly scoped.
- [ ] History coverage and balance evidence are separate, with useful account and period details.
- [ ] A stated/imported conflict shows both dates and resolves through an audited, undoable path.

### C03: Transactions, splits, and corrections

**Description:** record real money movements with consistent accounting and reversible changes.
Distinguish consumption, transfers, lending, repayments, refunds, and goal funding so that totals
remain meaningful across chat, imports, panels, and later corrections.

**How it helps the user:** makes everyday capture dependable and keeps shared expenses
understandable.

**What the agent can do:**

- Record an expense, income, transfer, split, loan payment, refund, or settlement from user facts.
- Correct an entry, track partial reimbursements, and undo changes without duplicating money.

**Enabling tools and status:**

- Internal, existing: `record_movement`, `correct_movement`, `query_movements`, `schedule_payment`,
  and `undo`; [movements](../../../src/arno/ledger/movements.py) compute the balanced transfers.
- Pydantic: existing discriminated tool inputs and approval/undo adapters; no external calculator.
- External/MCP: none required. Reported payments are recorded; Arno never executes them.

**Completion checklist:**

- [ ] Purchase, refund, transfer, split, and partial settlement agree across all spending surfaces.
- [ ] A retry cannot duplicate a movement or apply the same approval twice.
- [ ] Schedule matching uses recorded payment evidence; a due date alone never marks a bill paid.
- [ ] Correction and undo preserve balanced entries and explain any later-edit conflict.

### C04: Capture, import, and reconciliation

**Description:** accept statements, text, voice notes, and transaction images through clear
workflows. Extract and validate records, reuse proven statement parsers, and merge overlapping
evidence while keeping ambiguous matches and statement commitment under user control.

**How it helps the user:** reduces manual entry without silently importing incorrect or duplicate
rows.

**What the agent can do:**

- Delegate a statement to the parser and explain progress, missing input, and review requirements.
- Capture a receipt or voice note, reconcile repeated evidence, and assist with ambiguous rows.

**Enabling tools and status:**

- Internal, existing: parser tools including `saved_parser`, `doc_words_range`, `doc_cells`,
  `check_statement`, and `submit_parser`; [readers](../../../src/arno/importers/readers.py),
  [reconciliation](../../../src/arno/importers/reconcile.py), and
  [capture](../../../src/arno/capture).
- Pydantic: existing `SubAgents` and parser Code Mode; host `STT_*` and `VISION_*` calls are
  separate services. A password enters only the import card; `wait_for_password` returns status to
  the model.
- External: configured transcription/vision providers; optional OCR [T04](#t04-document-reading)
  only after comparative testing. The model cannot commit an import.

**Completion checklist:**

- [ ] Locked, unfamiliar, malformed, and textless documents have explicit success or recovery paths.
- [ ] A second supported statement replays the saved parser and overlapping captures do not
      duplicate.
- [ ] Passwords never appear in model context, database, logs, or exports; a restart may request one
      again.
- [ ] Real upload/password/review/commit/undo, restart-mid-parse, voice, photo, and phone-share
      journeys pass.

### C05: Financial contract understanding

**Description:** turn relevant contract passages into structured, dated terms with source locations.
Read loan statements, Key Facts Statements, card terms, deposit conditions, and policy documents;
retain ambiguous clauses for review instead of filling gaps from generic product knowledge.

**How it helps the user:** makes fees, restrictions, and exceptions visible before a financial
decision.

**What the agent can do:**

- Explain a prepayment clause, rate reset, card fee, deposit withdrawal term, or policy exclusion.
- Supply reviewed contract terms to C11 or C16 and identify what prevents a complete calculation.

**Enabling tools and status:**

- Internal, proposed: contract schemas, field-to-passage provenance, version history, and review
  cards. Existing [document readers](../../../src/arno/importers/readers.py) are reusable
  foundations.
- Pydantic: structured output with validation and focused extraction; no built-in contract engine.
- External: actual issuer documents; optional Docling in [T04](#t04-document-reading), official rule
  retrieval in C17. A document parser is not a legal or insurance-adequacy verifier.

**Completion checklist:**

- [ ] Every material rate, fee, date, and exception links to the original document location.
- [ ] Missing, contradictory, and low-confidence terms remain unresolved until reviewed.
- [ ] Revised terms preserve the old version and identify which calculations they affect.
- [ ] Malicious document text cannot authorize a write or silently modify saved product terms.

### C06: Spending insight and an adaptive plan

**Description:** explain consumption and its changes over an appropriately covered period. Compare
actual spending with a user-chosen plan, distinguish timing from behavior, and suggest modelable
adjustments while respecting essential commitments and the user's priorities.

**How it helps the user:** answers where money went and what spending flexibility remains.

**What the agent can do:**

- Produce category, merchant, account, and period breakdowns with the underlying rows.
- Explain deviations and compare discretionary changes without inventing a budget or judging habits.

**Enabling tools and status:**

- Internal, partial: `summarize_spending`, `query_movements`, `recap`, `present`, and category
  rules; [baselines](../../../src/arno/importers/baselines.py) estimate daily variable spending.
- Pydantic: existing `run_code` combines returned data; a persistent planned-versus-actual model
  remains proposed. Category caps are a separate parked decision, not required by this entry.
- External/MCP: none required; optional merchant enrichment is C25.

**Completion checklist:**

- [ ] Transfers, card repayments, and investment contributions do not inflate consumption.
- [ ] Refunds and corrections reconcile to the rows and period actually analyzed.
- [ ] Incomplete history prevents an unqualified monthly total or misleading trend comparison.
- [ ] A changed income or priority can revise the plan with visible before/after consequences.

### C07: Goals and irregular expense reserves

**Description:** connect future obligations and goals to explicit funding needs and available cash.
Represent annual premiums, repairs, festivals, and other uneven expenses with appropriate timing;
keep reserved savings distinct from unrestricted cash without counting the same reserve twice.

**How it helps the user:** turns future expenses into manageable plans and makes goal tradeoffs
clear.

**What the agent can do:**

- Calculate required contributions and show a goal's shortfall, deadline, and funding assumptions.
- Compare priorities when income changes and model a revised contribution schedule.

**Enabling tools and status:**

- Internal, partial: `update_setup` for goals, schedules, and baselines; `project` and [goal
  accounts](../../../src/arno/ledger/goals.py) handle fenced funds.
- Proposed: feasible-contribution and irregular-reserve planning over the same engine; no generic
  framework tool supplies this business logic. Pydantic validates inputs and presents the result.
- External/MCP: none required; contract dates and premiums may come from reviewed C05 evidence.

**Completion checklist:**

- [ ] Annual and one-off commitments appear on their real dates and receive an explicit funding
      plan.
- [ ] Reserved cash is counted once across accounts, goals, and forecast outflows.
- [ ] Unconfirmed future income does not fund a confident affordability verdict.
- [ ] Revised priorities show contributions and deadline effects without changing real records
      unseen.

### C08: Cash flow and account funding readiness

**Description:** forecast cash against dated income, commitments, card settlements, and variable
spend. Evaluate both pooled affordability and the account that must fund each payment,
distinguishing usable money from illiquid, uncertain, or merely expected resources.

**How it helps the user:** catches a likely autodebit shortfall even when total household cash is
positive.

**What the agent can do:**

- Explain the cash path, uncertainty, lowest projected balance, and relevant data gaps.
- Identify an underfunded account/date and model a user-chosen funding transfer without executing
  it.

**Enabling tools and status:**

- Internal, partial: `project`, [input loading](../../../src/arno/tools/projection.py), and the pure
  [projection engine](../../../src/arno/engine/projection.py) currently pool liquid balances.
- Proposed: account routing, transfer timing, and per-account feasibility; this reopens a parked
  decision. Pydantic's Code Mode calls the engine; it is not the forecasting model itself.
- External: none required; optional reference series must enter as validated, dated assumptions.

**Completion checklist:**

- [ ] Scheduled and variable flows reconcile; principal, interest, card dues, and reserves are not
      doubled.
- [ ] Sufficient total cash with a short EMI account produces a named account/date shortfall.
- [ ] Unknown balances, unsupported transfer timing, and inaccessible assets cannot imply payment
      readiness.
- [ ] Identical inputs and seed reproduce the forecast; sensitivity and source ages remain visible.

### C09: Purchase affordability and payment comparisons

**Description:** assess a purchase against commitments, goals, and the user's cash floor in code.
Compare supported payment methods and changes on the same baseline; communicate the engine's
verdict, uncertainty, and tradeoffs without substituting a model-written financial conclusion.

**How it helps the user:** shows the consequences of a purchase before the user commits to it.

**What the agent can do:**

- Evaluate paying from an account, using a card, or using a supported installment arrangement.
- Explain which modeled change improves feasibility and which assumptions could change the answer.

**Enabling tools and status:**

- Internal, existing: `project(hypothetical=...)` invokes
  [affordability](../../../src/arno/engine/affordability.py); there is no separate `can_i_afford`
  tool.
- Pydantic: Code Mode can compare engine returns; `present` renders the selected results.
- External: C05 contract evidence only when needed. Fee-inclusive products require the C11
  extension; an external analyst or research service cannot issue Arno's verdict.

**Completion checklist:**

- [ ] Verdicts and every suggested tradeoff are engine outputs computed on the same baseline.
- [ ] Limit breaches, unsupported product costs, and unknown funding facts are surfaced.
- [ ] A delayed date alone is not mislabeled as an immediate feasible purchase.
- [ ] Account, period, goal, and probability labels match the result, including after corrections.

### C10: Saved scenarios and stress tests

**Description:** preserve related hypothetical changes as named alternatives with explicit
assumptions. Compare income interruptions, major purchases, rent changes, emergencies, and other
life events without altering real records; retain enough baseline information to reproduce an
earlier result.

**How it helps the user:** supports decisions spanning several changes and conversations.

**What the agent can do:**

- Create, duplicate, compare, revise, and explain a user-authorized scenario.
- Reopen an old comparison and distinguish its original result from a refresh on current data.

**Enabling tools and status:**

- Internal, proposed: scenario storage, immutable input snapshots, overrides, and an engine runner.
  Existing `project`, `amortize`, and `compound` are reusable calculation primitives.
- Pydantic: existing Code Mode orchestrates calculations; persisted scenario business state belongs
  in Postgres. The current affordability `baseline` return is not a saved scenario record.
- External: C17 may supply sourced assumptions; a research subagent is optional, not required.

**Completion checklist:**

- [ ] Multi-event alternatives change no live balance, schedule, or goal while being evaluated.
- [ ] Reopening retains input dates, overrides, seed, calculation version, and original output.
- [ ] Refreshing after a ledger edit shows what changed and never overwrites the original silently.
- [ ] Stress cases show conditional outcomes without inventing probabilities for unmodeled events.

### C11: Debt and credit lifecycle cost

**Description:** model what a debt costs over its repayment life using the applicable contract.
Include supported interest, fees, taxes on fees, discounts, rate resets, and early-repayment terms;
distinguish card minimum payment, full statement payment, available credit, and cash.

**How it helps the user:** exposes the cost and liquidity consequences hidden behind a headline EMI.

**What the agent can do:**

- Compare baseline repayment with reduce-tenure or reduce-EMI prepayment and other supported paths.
- Explain minimum-only card repayment and compare fee-inclusive offers without choosing a product.

**Enabling tools and status:**

- Internal, partial: `amortize` already returns prepayment savings and alternative schedules; [money
  calculations](../../../src/arno/engine/money.py) and card-cycle projection exist.
- Proposed: revolving-card, fee-inclusive APR/cost, variable-rate, and contract-specific models.
  Pydantic validates terms; Code Mode must call the tested calculation rather than recreate
  formulas.
- External: actual KFS/card terms through C05 and official rules through C17;
  [T05](#t05-financial-calculations).

**Completion checklist:**

- [ ] Existing prepayment outputs reconcile principal, interest, payoff, and rounded paise.
- [ ] Fee, tax, discount, and cash-price differences appear separately in a supported offer
      comparison.
- [ ] Minimum-only card results use issuer terms and explain continuing debt and lost grace
      assumptions.
- [ ] Missing rates, reset rules, or contingent charges prevent an unsupported complete-cost claim.

## Whole-life coverage when applicable

### C12: Holdings and dated net worth

**Description:** combine actual asset holdings and liabilities with dated valuations and coverage.
Preserve quantities separately from prices, distinguish ownership evidence from a market quote, and
identify restricted assets and missing accounts rather than reporting an apparently complete total.

**How it helps the user:** shows what the user owns and owes without presenting investments as ready
cash.

**What the agent can do:**

- Import reviewed holdings, reconcile subsequent transactions, and calculate scoped net worth.
- Explain valuation changes and warn when holdings evidence and prices have different dates.
- Show concentration by supported issuer, asset class, or account without implying diversification
  guarantees.

**Enabling tools and status:**

- Internal, proposed: holdings, instrument identities, units, cost/cash-flow records, and valuation
  snapshots. Current asset accounts store hand-updated balances, not a portfolio model.
- External: CASParser, issuer/DP statements, and approved valuation sources in
  [T06](#t06-holdings-and-valuations).
- MCP: select the user's broker route, such as Kite or Upstox, in [T03](#t03-external-mcp-servers).
  Fi Money is deferred pending its adoption checks. Alpha Vantage or Twelve Data can supply required
  reference data after instrument/plan verification. Holdings and prices remain separate inputs.

**Completion checklist:**

- [ ] Statement quantities and transaction history reconcile before valuation is trusted.
- [ ] Units, price date, ownership date, and valuation currency are independently visible.
- [ ] Transfers, duplicate holdings, corporate actions, and unknown assets do not distort the total.
- [ ] Concentration reports disclose their grouping, look-through limits, and missing holdings.
- [ ] Parser/dependency licenses and data access/storage rights are verified for the selected
      sources.

### C13: Investment performance and benchmarks

**Description:** calculate investment outcomes using dated cash flows and the correct return
measure. Separate contributions, withdrawals, fees, taxes, and price changes; disclose numerical
ambiguity, incomplete history, valuation dates, and the assumptions behind any benchmark comparison.

**How it helps the user:** explains whether growth came from saving more or from investment
performance.

**What the agent can do:**

- Calculate a money-weighted return and appropriate endpoint growth measures from sufficient
  evidence.
- Compare with a suitable benchmark over matching dates and explain why a result is undefined or
  ambiguous.

**Enabling tools and status:**

- Internal, proposed: validated performance helpers and cash-flow reconciliation built on C12.
- Library candidate: `pyxirr.xirr`, `xnpv`, and `is_conventional_cash_flow` in
  [T05](#t05-financial-calculations); these require a host wrapper, not a Monty third-party import.
- External: a licensed benchmark series only when the user requests that comparison; MCP is
  optional.

**Completion checklist:**

- [ ] Flow signs, dates, terminal valuation, day-count convention, and fee treatment are explicit.
- [ ] Missing history, non-convergence, nonfinite values, and multiple possible roots are handled
      honestly.
- [ ] External benchmark cash flows, period, currency, and distribution treatment match the stated
      comparison.
- [ ] Returns are independently validated within documented tolerances; CAGR does not replace
      cash-flow returns.

### C14: Retirement and life-event planning

**Description:** model long-term needs against resources using transparent assumptions and ranges.
Include inflation, income changes, dependent costs, retirement timing, access restrictions, and
relevant taxes without treating a smooth compound-growth curve as a guaranteed life outcome.

**How it helps the user:** shows how today's choices affect future goals and financial resilience.

**What the agent can do:**

- Compare retirement ages, career breaks, education costs, or changed contributions as scenarios.
- Explain which assumptions drive a shortfall and which missing facts prevent meaningful modeling.

**Enabling tools and status:**

- Internal, proposed: long-horizon cash-flow and withdrawal models integrated with C10 and C12.
  Existing `compound` supplies a calculation primitive, not a complete retirement planner.
- Pydantic: validated scenario inputs, Code Mode, and result presentation; no native retirement
  tool.
- External: sourced inflation or scheme rules through C17; scheme statements provide personal facts.

**Completion checklist:**

- [ ] Assumptions about inflation, longevity, returns, income, taxes, and accessible assets are
      visible.
- [ ] Nominal and real money, contribution periods, and withdrawal timing remain consistent.
- [ ] Adverse sequences and changed assumptions show sensitivity without unsupported success
      probabilities.
- [ ] A saved comparison reopens reproducibly and identifies limits requiring professional input.

### C15: India tax estimates and obligation calendar

**Description:** identify applicable tax obligations and estimate supported liabilities from dated
rules. Separate confirmed receipts, expected annual income, withholding, and tax payments, while
retaining the relevant year, regime, residence assumptions, exemptions, and source of each rule.

**How it helps the user:** reduces surprise tax cash requirements and wrong-year answers.

**What the agent can do:**

- Explain applicable dates, estimate a reserve, and compare supported tax assumptions or regimes.
- Reconcile user-provided salary/TDS/AIS evidence and identify discrepancies for review.

**Enabling tools and status:**

- Internal, proposed/parked: versioned tax rules, deterministic applicability/calculation, and
  calendar support. Existing schedules can represent known obligations but are not a tax engine.
- Pydantic: structured extraction and validated calculator inputs; research prose cannot compute
  tax.
- External: Income Tax Department sources and user-downloaded evidence via
  [T09](#t09-regulatory-and-policy-evidence); no portal password, filing, or general tax MCP is
  assumed.

**Completion checklist:**

- [ ] Every supported calculation selects an applicable year, rule version, and user classification.
- [ ] Exemptions, withheld amounts, already-paid tax, and income estimates are distinguished.
- [ ] Missing current rules or user facts produce a scoped answer rather than invented rates or
      deadlines.
- [ ] Boundary cases and official reference examples pass; reminders update when a source changes.

### C16: Emergency reserves and financial protection

**Description:** assess access to essential-expense reserves and inventory relevant protection
evidence. Track policy coverage, dates, exclusions, nominees, and documents; distinguish deposit
protection from liquidity and investment risk, with bank ownership and account capacity considered
explicitly.

**How it helps the user:** reveals practical weaknesses when income stops or an unexpected expense
occurs.

**What the agent can do:**

- Model emergency runway and explain which assets cannot be accessed on the needed date.
- Review renewals, missing policy facts, and per-bank deposit exposure with evidence and caveats.
- Identify missing nominee and document-location information for the user's continuity review.

**Enabling tools and status:**

- Internal, proposed: reserve-access models, policy inventory, and ownership-aware deposit
  aggregation. Existing `project` and premium schedules are partial foundations, not insurance
  adequacy models.
- External: actual policies/deposit terms, DICGC and IRDAI sources in
  [T09](#t09-regulatory-and-policy-evidence), with reviewed extraction from C05.
- Pydantic/MCP: structured extraction and existing planning tools; no mandatory insurer connector.

**Completion checklist:**

- [ ] Runway uses essential obligations and available resources rather than unrestricted net worth.
- [ ] Deposit protection checks the insured bank and the depositor's right/capacity before
      aggregation.
- [ ] Policy exclusions, dates, nominee uncertainty, and evidence gaps are visible and correctable.
- [ ] Continuity review exposes document locations only to the authorized user and flags missing
      records.
- [ ] No invented insurance payout, coverage-adequacy verdict, or legal-document sufficiency claim
      is issued.

## Research, review, and trust

### C17: Current financial research with evidence

**Description:** retrieve current external financial facts and preserve auditable source context.
Prefer the source that owns a rule or term, distinguish publication from effective dates, and
separate verified facts, uncertain findings, and scenario assumptions from personal ledger records.

**How it helps the user:** answers changing questions that the ledger and model memory cannot
establish.

**What the agent can do:**

- Search and read official rules, schemes, rates, and contract explanations for a named period.
- Compare conflicting sources, report retrieval failure, and offer a bounded researched explanation.

**Enabling tools and status:**

- Internal, proposed: source records, citations, private-query minimization, external-call budgets,
  and integration with outside-text confirmation and number provenance.
- Pydantic: `WebSearch(local=True)`/`WebFetch(local=True)` as explicit client-side fallbacks, or
  harness Exa/You.com in [T02](#t02-external-research). Current Arno chat does not have configured
  search or fetch.
- External MCP: Tavily's `tavily_search` and `tavily_extract` are the first implementation
  candidate. Exa and Parallel are research alternatives in [T03](#t03-external-mcp-servers);
  validate the selection on Indian questions. Direct APIs remain another integration option.

**Completion checklist:**

- [ ] A supported answer cites the passage and relevant period, with unknown dates explicitly
      unknown.
- [ ] Source title, URL, retrieval time, effective-date basis, and permitted evidence survive a
      restore.
- [ ] Private data is omitted from queries/URLs; private-address fetches and unsafe redirects are
      blocked.
- [ ] Injection, conflicting evidence, exhausted budgets, and failed retrieval cannot cause guessed
      facts or writes.

### C18: Proactive review and scheduled questions

**Description:** surface material financial changes with evidence and a useful next step. Prioritize
obligations, risk, recurring charges, and goal changes while respecting notification preferences;
run user-scheduled questions with read-only tools and defined resource ceilings.

**How it helps the user:** catches problems early without creating a stream of repetitive alerts.

**What the agent can do:**

- Explain a reminder, risk episode, or recurring-charge proposal and help the user resolve it.
- Answer a scheduled spending question into the inbox and show its data date and limitations.
- Present supported refund, duplicate, subscription-change, or unfamiliar-charge candidates for
  review.

**Enabling tools and status:**

- Internal, existing/partial: [watch jobs](../../../src/arno/jobs), `update_setup` for scheduled
  questions, and `schedule_payment` for payment evidence. Existing reminders do not require a model.
- Pydantic: the existing read-only scheduled agent, Code Mode, and spend limits.
- External: web push is a delivery service; extra channels are C26. More anomaly/refund detection
  needs explicit logic and validation; it is not supplied by adding a notification connector.

**Completion checklist:**

- [ ] One unchanged risk episode or due occurrence produces one actionable alert, with controlled
      re-arming.
- [ ] Supported payment evidence resolves a warning; undo restores the appropriate state.
- [ ] Missed schedules, model outages, opt-outs, and permission failures have visible recovery
      behavior.
- [ ] Real phone push and a real scheduled question pass, including source dates and read-only
      enforcement.

### C19: Connection health and recoverable ingestion

**Description:** expose whether every selected data source is complete, current, and successfully
processed. Recover from restarts, failed refreshes, schema changes, repeated events, and expired
access while preserving source identity and preventing a stale connection from appearing healthy.

**How it helps the user:** prevents silent gaps and duplicate money when automation fails.

**What the agent can do:**

- Explain the last successful refresh, missing interval, and safe retry or reauthorization step.
- Distinguish a pending import, failed connector, and completed reconciliation from live balances.

**Enabling tools and status:**

- Internal, partial: [job runner](../../../src/arno/jobs/runner.py), parser task/step persistence,
  and status APIs exist. Connector cursors, source-event IDs, and access state remain proposed.
- Pydantic: existing parser delegation and error reporting; external function/MCP errors need
  mapping to application status. Transport retries alone do not establish idempotent ledger
  processing.
- External: selected pull APIs or reviewed webhooks under [T10](#t10-settlements-and-webhooks).

**Completion checklist:**

- [ ] Replaying or reordering an external event does not duplicate or misstate a movement.
- [ ] Retry, restart, expired credentials, and changed input schema produce persistent, explainable
      states.
- [ ] A failed refresh leaves the old data date visible and never marks new data as reconciled.
- [ ] Existing parser restart and every selected connector's replay/recovery journeys are exercised.

### C20: Inspectable answers and portable models

**Description:** let the user inspect the evidence, assumptions, and calculations behind an answer.
Present meaningful tables and charts and export a reproducible model with its original inputs,
dates, formulas where appropriate, and trustworthy computed results.

**How it helps the user:** makes an answer checkable and shareable with an accountant or adviser.

**What the agent can do:**

- Show supporting rows, calculation assumptions, a scenario comparison, or an explanatory chart.
- Prepare a scoped financial workbook or data export without disclosing unrelated private records.

**Enabling tools and status:**

- Internal, partial: `present`, `show_chart`, `show_table`, and [JSON/CSV/dump
  exports](../../../src/arno/ledger/export.py) exist; a model-workbook export does not.
- Library candidates: XlsxWriter or openpyxl in [T08](#t08-spreadsheets-and-model-export).
- External: Sheets API or Graph is optional and separately authorized; neither a cloud account nor
  an MCP spreadsheet server is required for a downloadable `.xlsx` artifact.

**Completion checklist:**

- [ ] Displayed figures resolve to the correct source, account, unit, and period, not merely
      matching digits.
- [ ] A model export includes input snapshot, assumptions, calculation version, and result date.
- [ ] Formula cells have verified calculated values for non-calculating viewers; text cannot inject
      formulas.
- [ ] Export scope, credential exclusions, and any cloud destination are explicit and verified in
      the UI.

### C21: Privacy, authorization, and user control

**Description:** keep credentials and private data within their intended boundaries and preserve
user authority. Treat imported and external content as untrusted data; allow inspection, correction,
memory revision, and undo while requiring the appropriate confirmation for lasting or wide changes.

**How it helps the user:** enables useful automation without surrendering control over money or
records.

**What the agent can do:**

- Explain the exact proposed change, its evidence, and whether a confirmation is required.
- Honor denial, expiry, correction, opt-out, and undo without repeatedly attempting rejected
  actions.

**Enabling tools and status:**

- Internal, existing/partial: passkeys, redaction, [outside-text
  marking](../../../src/arno/tools/mark.py), audit groups, `preference`, `undo`, and [the write
  gate](../../../src/arno/agent/toolset.py) exist.
- Proposed: user-visible deletion scope and retention controls, including derived evidence and the
  limits of deletion from backups and external providers.
- Pydantic: `ApprovalRequired`/`DeferredToolRequests`, local tool controls, and structured returns.
  Web/MCP results and external-tool credentials require explicit integration with these controls.
- External: OAuth/API keys stay in host transport/storage, never prompts or arbitrary fetch headers.
  MCP metadata and server instructions also require review; read-only does not prevent data egress.

**Completion checklist:**

- [ ] Malicious statements, pages, and MCP results cannot create lasting changes without the
      required card.
- [ ] Secret fields cannot enter prompts, traces, source URLs, ordinary exports, or unauthorized
      requests.
- [ ] Approval binds the reviewed change/version; denial, replay, and stale approval write nothing.
- [ ] Users can revise memory, inspect history, disable external access, and see undo conflicts
      honestly.
- [ ] A deletion request shows affected records, retained audit/backup data, and provider-retention
      limits.

### C22: Measured reliability and recovery

**Description:** establish whether financial answers and workflows work under realistic conditions.
Measure calculation correctness, forecast calibration, semantic mistakes, failures, latency, and
recovery using appropriate fixtures and live journeys, with limitations published beside results.

**How it helps the user:** provides evidence for trusting the agent and recovering when something
fails.

**What the agent can do:**

- Report the limits of a forecast or answer and expose job/model failure information safely.
- Help the operator locate the appropriate diagnostic or recovery workflow without reading
  credentials.

**Enabling tools and status:**

- Internal, partial: [eval cases](../../../evals/cases.py),
  [backtest](../../../src/arno/engine/backtest.py), invariants, Phoenix traces, and [restore
  tooling](../../../src/arno/backup_restore.py) exist.
- Pydantic: instrumentation, scripted/test models, and spend controls support verification; a
  successful span or quantity-matching check is not proof of financial meaning.
- External: restic storage and configured inference endpoints need separate operational validation.

**Completion checklist:**

- [ ] Unit, property, invariant, and semantic cases cover wrong account, wrong period, rounding, and
      missing data.
- [ ] Usable real-history evaluation distinguishes synthetic results and controls historical
      look-ahead.
- [ ] Selected models meet documented correctness, cost, and latency criteria on representative
      journeys.
- [ ] A fresh-machine restore and the open human/device acceptance gates have recorded results.

## Conditional extensions

### C23: Foreign-currency valuation

**Description:** represent relevant foreign-currency amounts without confusing prices and booked
transactions. Keep original and base amounts, currencies, effective dates, reference sources, and
actual charged conversion evidence distinct; expand accounting rules only when foreign holdings or
income require it.

**How it helps the user:** makes foreign income, assets, and fees understandable without rewriting
bank charges.

**What the agent can do:**

- Explain a foreign charge or value a foreign holding using a clearly labeled reference rate.
- Compare documented fees and conversions and identify missing executed-rate information.

**Enabling tools and status:**

- Internal, proposed/parked: multicurrency amounts, valuation history, and realized/unrealized
  treatment where required. Current foreign purchases use the amount actually charged in base
  currency.
- External: Frankfurter/official reference sources in [T11](#t11-reference-fx); provider terms and
  source dates matter. Reference rates are not executable market quotes.
- Pydantic/MCP: a small read-only API adapter suffices; native MCP is not necessary.

**Completion checklist:**

- [ ] Original amount, charged base amount, fees, reference rate, and dates are independently
      preserved.
- [ ] A daily reference quote never overwrites an actual card or bank conversion.
- [ ] Missing rates, non-working days, stale quotes, and currency-scale differences are handled
      explicitly.
- [ ] Any realized/unrealized accounting extension has reviewed rules and balancing invariants.

### C24: Freelancer receivables and net payouts

**Description:** connect expected earnings, invoices, fees, refunds, and settlements to actual cash.
Distinguish an amount owed from money received, reconcile gross receipts to net bank credits, and
retain the user's accounting and tax basis instead of inferring it from a gateway payout.

**How it helps the user:** explains delayed income and prevents double-counting or hiding payment
fees.

**What the agent can do:**

- Show expected receipts, overdue items, partial settlements, and cash-flow sensitivity to delays.
- Reconcile a payout to its underlying payments, refunds, fees, and adjustments.

**Enabling tools and status:**

- Internal, partial/proposed: variable-income schedules and person balances exist; invoice aging,
  settlement allocation, and gateway source IDs require an explicit model.
- External: reviewed files or read-only Razorpay settlement APIs in
  [T10](#t10-settlements-and-webhooks).
- Pydantic/MCP: plain tool adapters over the same functions; no gateway write, payout, or refund
  action.

**Completion checklist:**

- [ ] Invoices and expected receipts do not become available cash without receipt evidence.
- [ ] Gross receipts, fees, refunds, tax components, and net payout reconcile to the bank credit.
- [ ] A payout and its source payments cannot be counted as two independent earnings events.
- [ ] Partial payments, chargebacks, duplicates, delayed settlements, and failed refreshes are
      tested.

### C25: Merchant identity enrichment

**Description:** improve cryptic transaction identities using evidence and reversible suggestions.
Separate the actual merchant from a payment intermediary and preserve original narration,
confidence, source, and user corrections rather than replacing uncertain text with a confident
label.

**How it helps the user:** reduces categorization work while preserving meaningful spending
analysis.

**What the agent can do:**

- Explain a likely merchant identity and ask once where the evidence remains ambiguous.
- Learn the user's categorization rule and apply it to the authorized scope.

**Enabling tools and status:**

- Internal, partial: [seed rules](../../../src/arno/importers/seed_rules.py), [rule
  precedence](../../../src/arno/importers/rules.py), categorizer, and `correct_movement` exist.
- External: optional enrichment only after Indian-transaction quality and privacy evaluation. [Plaid
  Enrich](https://plaid.com/docs/enrich/) documents US/Canada availability, not an Indian UPI
  solution.
- Pydantic: existing categorizer tools; no generic MCP or public merchant search is mandatory.

**Completion checklist:**

- [ ] A payment app is not mistaken for the merchant merely because its name appears in narration.
- [ ] User rules override model/vendor suggestions and retain their intended past/future scope.
- [ ] Uncertain identities preserve the original text and remain correctable without rewriting
      money.
- [ ] External enrichment is optional, minimizes outgoing data, and has measured Indian coverage.

### C26: Additional notification and approval channels

**Description:** deliver selected notifications through channels the user explicitly enables. Keep
message content minimal and preserve a secure relationship between identity, the exact reviewed
change, expiry, and consent; channel delivery is separate from authority to change records.

**How it helps the user:** makes important reviews easier to reach when the PWA is not convenient.

**What the agent can do:**

- Send an opted-in notification that opens the authenticated Arno review workflow.
- Explain delivery failures and honor channel-specific settings without losing the in-app record.

**Enabling tools and status:**

- Internal, existing/proposed: inbox and web push exist; chat-app integrations are parked.
- External: [Telegram Bot API](https://core.telegram.org/bots/api) is a candidate; messaging APIs
  require credentials, identity binding, retention review, and an approved delivery/ingress design.
- Pydantic/MCP: either a narrow host adapter or reviewed MCP function; neither supplies application
  authorization. Initial notifications should link back to Arno rather than approve writes in chat.

**Completion checklist:**

- [ ] Only an explicitly bound destination receives the selected content, and opt-out takes effect.
- [ ] Delivery failure leaves the in-app review available with no duplicated financial action.
- [ ] A replayed, expired, forwarded, or wrong-user callback cannot authorize a write.
- [ ] The private-network model is preserved or a separate ingress decision is recorded before
      rollout.

### C27: Business-system and accountant handoff

**Description:** exchange a deliberate subset of financial records with an accountant or business
system. Preserve entity, period, source identity, and reconciliation; distinguish company records
from personal money and avoid interpreting a registry lookup as proof of a tax entitlement.

**How it helps the user:** reduces re-entry when a freelancer or business owner needs professional
review.

**What the agent can do:**

- Prepare an accountant pack or reconcile a supported external record with the user's records.
- Explain exceptions and draft a handoff while keeping external posting under explicit
  authorization.

**Enabling tools and status:**

- Internal, proposed beyond generic exports: source/entity mapping, reconciliation reports, and
  staged handoff state. No ERP connector currently exists.
- External: [TallyPrime XML over HTTP](https://help.tallysolutions.com/xml-integration/) is a
  documented route; Xero/QuickBooks/Zoho access needs its own verified product adapter and scopes.
- Optional verification: the [GST taxpayer
  portal](https://tutorial.gst.gov.in/userguide/taxpayersdashboard/Search_Taxpayer_manual.htm) and
  [GLEIF API](https://www.gleif.org/en/lei-data/gleif-api) answer different registry questions. No
  universal KYB/GST MCP or unrestricted public API is assumed.

**Completion checklist:**

- [ ] The selected entity, period, ledger mapping, and destination are reviewed before export or
      sync.
- [ ] Repeated imports and retries preserve source IDs without creating duplicate vouchers or
      movements.
- [ ] GSTIN/LEI presence is never treated as ITC eligibility, invoice validity, or counterparty
      safety.
- [ ] External writes require a separate scope decision, preview, authorization, and verified
      idempotency.

## Verified tool registry

The registry distinguishes a tool being documented or importable from its integration being
selected, configured, and tested. Candidate services are alternatives, not a shopping list.

### T01: Internal finance tools and Pydantic execution

Arno registers reads in [toolset.py](../../../src/arno/agent/toolset.py) and exposes them through
`run_code`: `get_financial_context`, `query_movements`, `summarize_spending`, `recap`, `project`,
`run_query`, `amortize`, `compound`, `rupees`, `show_chart`, `show_table`, and `present`. The direct
writes are `record_movement`, `correct_movement`, `update_setup`, `schedule_payment`, `preference`,
and `undo`. Parser/task tools are a separate surface.

Pydantic AI validates inputs and adapts the interaction; the application's plain Python computes
financial results. [Code Mode](https://pydantic.dev/docs/ai/harness/code-mode/) executes constrained
Monty scripts, not arbitrary CPython imports. A new scientific library needs a bounded host helper.
Network access in a host helper must be reviewed even though the script itself has no generic
network API. No separate DuckDB, Polars, E2B, Redis, or unrestricted Python environment is required
to establish these capabilities.

### T02: External research

Arno uses `OpenAIChatModel` with interchangeable endpoints. Native-provider tools are not a portable
default for that transport. Installed source confirms `WebSearch` and `WebFetch` default to
native-only; explicit local fallbacks are candidates. `ddgs` is installed; the `web-fetch`
dependencies are absent. Dependency presence does not enable the tools in Arno.

The following table records the researched alternatives.

| Route | Exact enabling surface | Status and constraint |
|---|---|---|
| Core search/fetch | `pydantic_ai.capabilities.WebSearch`, `WebFetch`; client-executed fallback | Not configured; local fetch extra required. Native-only restriction fields do not automatically constrain a custom local search |
| Exa | `pydantic_ai_harness.exa.ExaSearch`, `ExaAgent`; `web_search`, `get_page`, optional `deep_search` | Capability source exists; SDK/key absent. Structured sources; enforce fetch restrictions separately |
| You.com | `pydantic_ai_harness.youdotcom.YouSearch`, `YouResearch`; includes `finance_research` | Capability source exists; SDK/key absent. A finance-labeled synthesis is external evidence, not engine truth |
| Direct search API | Tavily, Brave, or Parallel documented endpoints | No selected adapter; compare actual Indian questions, sources, dates, terms, privacy, and total cost |
| Packaged research harness | `pydantic_ai_harness.researcher.Researcher` | Not a drop-in: default workspace/spill state needs a database-backed persistence review |

Sources: [core search](https://pydantic.dev/docs/ai/capabilities/web-search/), [core
fetch](https://pydantic.dev/docs/ai/capabilities/web-fetch/),
[Exa](https://pydantic.dev/docs/ai/harness/exa-search/),
[You.com](https://pydantic.dev/docs/ai/harness/youdotcom/),
[Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search),
[Brave](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started), and
[Parallel source policy](https://docs.parallel.ai/resources/source-policy). Publication filters do
not determine a rule's statutory effective date.

### T03: External MCP servers

The recommended starting set is Tavily for research and the user's existing broker MCP for holdings.
Fi Money is a broader personal-data candidate with unresolved adoption blockers. These are proposed
selections, not configured integrations. The [server assessment](external-mcp-servers.md) records
endpoints, authentication, alternatives, and completion checks; the [India
evidence](external-mcp-india.md) covers the personal-data routes.

The following table maps the concrete servers to their role in Arno.

| Server | Tools/data to use | Capabilities and selection |
|---|---|---|
| [Tavily](https://docs.tavily.com/documentation/mcp) | `tavily_search`, `tavily_extract` | C05/C15/C17; first research implementation candidate |
| [Fi Money](https://github.com/epiFi/mcp-docs) | `fetch_net_worth`, `fetch_bank_transactions`, `fetch_mf_transactions`, `fetch_epf_details`, and other documented reads | C02/C11/C12/C19; defer until continuity, host-side auth, and storage concerns are resolved |
| [Kite](https://github.com/zerodha/kite-mcp-server) | `get_holdings`, `get_mf_holdings`, `get_quotes`, `get_historical_data` | C12/C13; conditional on a Zerodha account; expose the required reads |
| [Upstox](https://upstox.com/developer/api-documentation/mcp-integration/) | Holdings, mutual funds, funds, positions, and profile reads; verify deployed names | C12/C19; conditional on an Upstox account; documented read-only route |
| [Groww](https://groww.in/updates/groww-mcp) | Stock holdings and market reads; verify deployed names | C12/C19; conditional on a Groww account and strict read allowlist; published scope excludes mutual funds |
| [Exa](https://exa.ai/docs/get-started/exa-mcp) | `web_search_exa`, `web_fetch_exa`, optional `web_search_advanced_exa` | C17; alternative research server |
| [Parallel](https://docs.parallel.ai/integrations/mcp/quickstart) | `web_search`, `web_fetch`, optional authenticated `web_research` | C17; alternative research server |
| [Brave Search](https://github.com/brave/brave-search-mcp-server) | `brave_web_search`, `brave_news_search` | C17; locally operated search alternative with a separate page-reading path |
| [Alpha Vantage](https://mcp.alphavantage.co/) | `SYMBOL_SEARCH`, `GLOBAL_QUOTE`, `TIME_SERIES_DAILY`, `FX_DAILY` | C12/C13/C23; selected instrument/reference-data gaps |
| [Twelve Data](https://github.com/twelvedata/mcp) | `get_price`, `get_quote`, `get_time_series` | Market-data alternative after instrument/plan checks |
| [FMP](https://site.financialmodelingprep.com/developer/docs/mcp-server) | `quote`, `chart`, `company`, `statements` | Later company-information need; outside the default household connector set |

Connect only the selected tools through client-executed `MCPToolset` or the `MCP` capability, with
host-held auth and the C17/C21 controls. The existing [framework
verification](verification-framework.md) records exact Pydantic configuration. No live server
connection or comparative quality test was run.

### T04: Document reading

The current reader uses PDFium, Calamine, Office decryption, and text/document readers; capture uses
configured vision and transcription services. Their presence does not prove every file layout or
spoken language works. Benchmark original synthetic fixtures, including corrupted and adversarial
inputs, before replacing a reader or expanding accepted document types.

[Docling](https://github.com/docling-project/docling) exposes `DocumentConverter`; its code is MIT
but models and dependencies have separate licenses and resource costs. It is a layout/OCR candidate,
not an already selected dependency. Cloud OCR adds statement egress and needs explicit
configuration. Password guessing from identity attributes is not part of this catalog.

### T05: Financial calculations

Existing `amortize` and `compound` call tested application functions. Money remains whole paise;
rate solvers may legitimately use floating-point arithmetic with stated numerical tolerances.
Decimal-only execution is not a substitute for correct assumptions and tests.

[PyXIRR](https://github.com/Anexen/pyxirr) is an Unlicense candidate. Its [function
reference](https://anexen.github.io/pyxirr/functions.html) documents dated `xirr`/`xnpv`, day-count
conventions, invalid-flow errors, and failed convergence. Nonconventional flows can have multiple
roots; the library's search strategy does not prove uniqueness. Wrap validation and
nonfinite/no-result handling before exposing a host helper. CAGR, fee-inclusive debt, retirement,
and tax models still need explicit application contracts and reference cases.

### T06: Holdings and valuations

[CASParser](https://github.com/codereverser/casparser) offers `casparser.read_cas_pdf` for
documented original issuer statements. Its MIT license does not establish data rights,
transitive-license approval, or support for MF Central/reformatted documents. Preserve decimal
quantities and review its reconciliation warnings. Depository/issuer statements are the fallback for
unsupported cases.

[AMFI](https://www.amfiindia.com/net-asset-value/nav-history?s=09) supplies valuation evidence; its
[terms](https://www.amfiindia.com/terms-of-use) restrict use and storage. Public downloads do not
establish blanket redistribution or caching permission. Review the intended endpoint and use. A
dated NAV cannot refresh stale ownership evidence. Broker APIs and market MCPs are optional,
instrument-specific alternatives, not a complete picture of household assets and liabilities.

### T07: Account access

[RBI AA directions](https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=10598) define FIU
eligibility by regulated status; [Sahamati](https://sahamati.org.in/financial-information-user-fiu/)
documents onboarding. An ordinary unregulated self-host does not qualify as a direct FIU. A partner
or user-export route requires separate consent, access, contractual, and coverage checks. Neither a
vendor name nor a hosted deployment resolves them. Loan/card coverage is unverified here, not
universally denied.

[Kite authentication](https://kite.trade/docs/connect/v3/user/) documents a broker-specific token
lifecycle. Any selected account adapter needs secure host-held credentials, refresh/expiry handling,
and verified read scopes; avoid granting trading/payout tools when holdings reads are sufficient.

### T08: Spreadsheets and model export

[XlsxWriter](https://xlsxwriter.readthedocs.io/working_with_formulas.html) can write formula cells
and supplied computed values; it does not evaluate formulas itself. openpyxl likewise does not
provide a calculation engine. The application must verify cached results and distinguish original
model outputs from later spreadsheet edits. Review versions and transitive licenses before adoption.

Cloud handoff is optional: [Sheets values
APIs](https://developers.google.com/workspace/sheets/api/guides/values) read/write ranges; [Graph
Excel](https://learn.microsoft.com/en-us/graph/api/resources/excel?view=graph-rest-1.0) documents
business storage support and excludes OneDrive Consumer. Do not promise either as a generic
connection to a local Excel session. A cloud write needs explicit destination and consent.

### T09: Regulatory and policy evidence

The [RBI KFS circular](https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12663) supports APR,
fee disclosure, and amortization evidence within its scope, with a separate card-receivable
exception. The [card
directions](https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx?id=12300) distinguish total
due, minimum due, and interest-free assumptions. Capture actual contract terms rather than inferring
that all products share a repayment formula.

[Income Tax transition
guidance](https://www.incometax.gov.in/iec/foportal/help/all-topics/e-filing-services/objective-and-scope-new-act-faq?mobile-app=1)
demonstrates why year and applicability belong with a rule. No slab or filing-deadline table is
certified here. [IRDAI life-insurance
material](https://irdai.gov.in/document-detail?documentId=5032913) supports reading policy
information and exclusions; it is not a universal insurance-adequacy model.
[DICGC](https://www.dicgc.org.in/guide-to-deposit-insurance) documents deposit aggregation by bank,
capacity, and right. Store the applicable version when using any of these sources for a calculation.

### T10: Settlements and webhooks

[Razorpay reconciliation](https://razorpay.com/docs/api/settlements/fetch-recon/) exposes payment,
refund, adjustment, fee, tax, and settlement evidence. Reconcile gross and net explicitly; a bank
payout does not determine revenue recognition or taxable income. [Webhook
guidance](https://razorpay.com/docs/webhooks/best-practices/) documents repeated delivery and
retries; verify signatures, event identity, order handling, and recovery for the selected route.

Arno is privately hosted. [Stripe](https://docs.stripe.com/webhooks) documents public HTTPS ingress
for registered webhooks; a new public route is not implicit permission to expose Arno. Prefer
manual/pull access initially, or record a separate ingress architecture decision before rollout.

### T11: Reference FX

[Frankfurter](https://frankfurter.dev/) exposes reference-rate APIs and provider attribution;
provider-specific schedules and terms matter. [ECB reference
rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html)
are informational, not transaction execution evidence. Preserve observation/source dates and the
actual bank-charged amount. No real-time pricing or unrestricted provider-data license is inferred.

## Shared completion gates

These gates apply to every capability selected for implementation, in addition to its own checklist.

- [ ] Inputs distinguish facts, user assumptions, derived results, and retrieved external evidence.
- [ ] Monetary units, signs, dates, source IDs, completeness, and uncertainty survive API/UI
      boundaries.
- [ ] Missing information and unavailable services have a truthful, actionable failure state.
- [ ] Stateful user facts, scenarios, jobs, and required evidence survive the supported
      backup/restore path.
- [ ] Access, privacy, software/model licenses, and data-use terms are verified before adding a
      dependency.
- [ ] Relevant invariant, semantic, adversarial, and UI acceptance evidence is recorded with its
      limits.

No capability is complete merely because a tool exists, a model called it, or a source URL was
cited. The [completion checklist](../../arno/completion-checklist.md) retains the current live and
human gates. Any selected parked capability requires a separate approved change to `plan.md` §2 and
§5.

## Dependencies and selection decisions

Establish C02–C04, C21, and C22 before trusting new financial decisions. C08 and C11 underpin C09;
C10 saves those calculations. C12 precedes C13; C14 combines C07, C10, and relevant whole-life data.
C05 and C17 supply reviewed terms and current rules for C11, C15, and C16. C19 is required whenever
an external connector is selected; C23–C27 need their own user trigger and scope decision.

The server shortlist recommends a first pilot, not a measured quality winner. Before selecting a
service, record the actual user questions, country/instrument coverage, auth/scopes, source/date
quality, privacy, quotas/cost, failure behavior, and required data rights. A live trial must use the
authorized test account and synthetic fixtures; it is not implied by this documentation request.

Static workflow skills may guide financial review, debt comparison, scenario planning, document
extraction, or research. [On-demand
capabilities](https://pydantic.dev/docs/ai/capabilities/on-demand/) can defer specialist
instructions/tools. Static shipped instructions are code; mutable user memory and overflow files are
application state. Skills do not grant a tool, fix a missing data model, authorize a write, or make
an unsupported answer correct.
