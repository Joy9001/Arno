# Arno capability catalog

This file holds the detail behind `plan.md` §2 and §5: capabilities proposed beyond the built
product, their acceptance targets, the plan rules an external capability must meet, and a proposed
priority order. The `plan.md` file owns decisions, and this file owns their shape. If the two
disagree, the plan wins and this file is stale.

Researched on November 12, 2026, against commit `9181891`, and refreshed on November 15, 2026,
against commit `1567975`, Pydantic AI 2.64.0, and Pydantic AI Harness 0.64.0. The catalog moved
here from `docs/research/personal-finance-agent-2026/` on November 15; the research it rests on
stays there. Nothing here approves a scope change, selects a paid service, or claims that an
unfinished feature works. A capability is adopted only when `plan.md` §5 gives it a milestone, and
a parked one only after the `plan.md` §2 change that [`parked.md`](parked.md) requires.

## Reading this catalog

Each entry gives a short description, the user benefit, agent actions, enabling tools with their
status, and a completion checklist. _Existing_ describes inspected source; _partial_ identifies
working foundations with a narrower scope; _proposed_ describes additional work; _parked_ means the
plan deliberately excludes the work, with a trigger in `parked.md`. None of these labels means a new
live test passed.

Tool mappings name internal functions, Pydantic AI capabilities, and concrete external servers. The
_Model Context Protocol_ (_MCP_) is the standard way an agent connects to an outside tool server.
Vendor MCP tool names can differ from the same vendor's harness adapter; the registry names the
interface being proposed.

Every entry carries a tier from the [priority order](#priority-order). Tier 0 entries describe
built features. Their live journeys are tracked in the [completion
checklist](completion-checklist.md), so those entries keep only the acceptance targets the
checklist doesn't track and point to it for the rest. Portfolio analysis never authorizes trading,
a tax estimate never authorizes filing, and modeling a payment never executes it.

All completion boxes are unchecked: they are acceptance targets for the full capability, not a
claim that existing subfeatures are missing. Close a box only with recorded evidence for the
selected version and installation. Label automated fixtures, provider sandbox tests, and real UI
journeys separately, and don't copy an old tick.

The evidence comes from the November 12 [implementation
audit](../research/personal-finance-agent-2026/verification-arno-coverage.md), the [framework
check](../research/personal-finance-agent-2026/verification-framework.md), [finance libraries and
APIs](../research/personal-finance-agent-2026/verification-finance-tools.md), the November 13
[external MCP assessment](../research/personal-finance-agent-2026/external-mcp-servers.md) with its
[India evidence](../research/personal-finance-agent-2026/external-mcp-india.md), and the [source
ledger](../research/personal-finance-agent-2026/sources.md). The implementation audit predates the
November 13–15 engine, pattern, and money work; where it disagrees with this file, this file is
current. No paid API, model eval, account connection, real financial record, or restore was
exercised for this document.

## What changed since November 12

The following table records what landed between the two baselines and which entries it changes.

| Change | Entries | Commits |
|---|---|---|
| Affordability returns up to four minimal plans that combine waiting, a card or EMI, delaying a goal, and cutting reducible categories, each verified by a rerun on the same scenarios | C09 | `483bdae` |
| A card EMI carries its interest, processing fee, and 18% GST on both; the rate comes from the what-if, then the card, then a labeled 16% default | C09, C11 | `b611374`, `483bdae` |
| `amortize` infers a loan's rate from its EMI and matches a bank's rounding conventions | C11 | `b611374`, `2868e4b` |
| Recurring charges are detected by calendar rules scored by likelihood; a detected series at confidence 0.999 or more enters the projection as an outflow; a payment can be marked one-off | C07, C08, C18 | `93d2133`, `29f5843`, `1413449` |
| The projection runs 16,384 scenarios on an integer kernel; calibration was built, measured, and not shipped | C08, C09, C22 | `f645b69`, `cb90de8` |
| The backtest scores each forecast as a distribution against three baselines; HackerRank scores the shipped engine through an adapter at 86.8% | C22 | `00598fd`, `679297a` |
| Inference cost tracking with native budgets | C17, C21 | `50880d5` |
| The `app` database role runs without superuser | C21 | `ad490ef` |
| Local speech-to-text runs whisper.cpp's native server with Silero voice activity detection | C04 | `757251b` |
| The ledger schema is rebuilt around movement legs, and `run_query` reads named views | C02, C03 | `8a7b28c`, `838418d` |

XIRR is specified in the [engine blueprint](engine-blueprint.md) but was not built, because nothing
uses it yet. Seven capabilities were added on November 15 (C28–C34), and C10 now covers only saved
scenarios; its unsaved multi-event part is C29.

## Index

The index maps each capability to its coverage on November 15, 2026, and its tier.

| ID | Capability | Coverage | Tier |
|---|---|---|---|
| [C01](#c01-progressive-onboarding-and-personal-context) | Progressive onboarding and personal context | Partial: setup and typed memory exist | 0 |
| [C02](#c02-a-trustworthy-financial-picture) | A trustworthy financial picture | Partial: anchors and coverage exist; ongoing review needs depth | 0 |
| [C03](#c03-transactions-splits-and-corrections) | Transactions, splits, and corrections | Existing; broader live acceptance remains | 0 |
| [C04](#c04-capture-import-and-reconciliation) | Capture, import, and reconciliation | Existing paths; real-device and import proof remains | 0 |
| [C05](#c05-financial-contract-understanding) | Financial contract understanding | Proposed beyond existing document readers | 2 |
| [C06](#c06-spending-insight-and-an-adaptive-plan) | Spending insight and an adaptive plan | Partial; category budgets are parked | 0 |
| [C07](#c07-goals-and-irregular-expense-reserves) | Goals and irregular expense reserves | Partial: goals, yearly schedules, and projected series exist; contribution planning is C29 | 0 |
| [C08](#c08-cash-flow-and-account-funding-readiness) | Cash flow and account funding readiness | Aggregate forecast exists; account forecasts are parked | 3 |
| [C09](#c09-purchase-affordability-and-payment-comparisons) | Purchase affordability and payment comparisons | Existing: minimal verified plans and card EMI cost | 0 |
| [C10](#c10-saved-scenarios) | Saved scenarios | Proposed; depends on C29 | 3 |
| [C11](#c11-debt-and-credit-lifecycle-cost) | Debt and credit lifecycle cost | Partial: amortization, prepayment, rate inference, card EMI cost | 1 |
| [C12](#c12-holdings-and-dated-net-worth) | Holdings and dated net worth | Manual assets exist; statement-based wealth is parked | 4 |
| [C13](#c13-investment-performance-and-benchmarks) | Investment performance and benchmarks | Proposed; XIRR specified, not built | 4 |
| [C14](#c14-retirement-and-life-event-planning) | Retirement and life-event planning | Proposed beyond generic goals and compounding | 4 |
| [C15](#c15-india-tax-estimates-and-obligation-calendar) | India tax estimates and obligation calendar | Tax engine is parked | 4 |
| [C16](#c16-emergency-reserves-and-financial-protection) | Emergency reserves and financial protection | Premium schedules exist; runway is C29; policy and deposit review proposed | 4 |
| [C17](#c17-current-financial-research-with-evidence) | Current financial research with evidence | Proposed; ledger citations already exist | 2 |
| [C18](#c18-proactive-review-and-scheduled-questions) | Proactive review and scheduled questions | Existing jobs; live delivery and usefulness need proof | 0 |
| [C19](#c19-connection-health-and-recoverable-ingestion) | Connection health and recoverable ingestion | Existing job recovery; external connectors proposed | 0 |
| [C20](#c20-inspectable-answers-and-portable-models) | Inspectable answers and portable models | Cards and data exports exist; workbook export proposed | 0 |
| [C21](#c21-privacy-authorization-and-user-control) | Privacy, authorization, and user control | Existing core controls; external boundaries need extension | 0 |
| [C22](#c22-measured-reliability-and-recovery) | Measured reliability and recovery | Evals, distributional backtest, HackerRank, and restore tools exist; real-data gaps remain | 0 |
| [C23](#c23-foreign-currency-valuation) | Foreign-currency valuation | Parked | 5 |
| [C24](#c24-freelancer-receivables-and-net-payouts) | Freelancer receivables and net payouts | Variable income exists; external settlement is not pursued | 5 |
| [C25](#c25-merchant-identity-enrichment) | Merchant identity enrichment | Internal rules exist; external enrichment is parked | 5 |
| [C26](#c26-additional-notification-and-approval-channels) | Additional notification and approval channels | In-app channels exist; chat integrations are parked | 5 |
| [C27](#c27-business-system-and-accountant-handoff) | Business-system and accountant handoff | Not pursued | 5 |
| [C28](#c28-safe-to-spend) | Safe-to-spend | Engine function exists, unexposed | 1 |
| [C29](#c29-multi-event-what-ifs) | Multi-event what-ifs | Proposed; the hypothetical takes one purchase | 1 |
| [C30](#c30-price-changes-in-recurring-charges) | Price changes in recurring charges | Partial: stepped amounts are detected, not reported | 1 |
| [C31](#c31-arno-as-an-mcp-server) | Arno as an MCP server | Proposed; reopens the parked chat-app bridge | 3 |
| [C32](#c32-statement-collection-from-email) | Statement collection from email | Proposed; reopens parked email capture | 3 |
| [C33](#c33-settle-up-reminders) | Settle-up reminders | Partial: balances owed exist | 4 |
| [C34](#c34-credit-report-as-liability-evidence) | Credit report as liability evidence | Proposed | 4 |

## Priority order

Proposed on November 15, 2026. Capabilities are ranked first by the plan's own test (`plan.md`
§2): _does the agent answer questions about my money incorrectly, or fail to warn me, without it?_
Cost and dependency order them within a tier. A tier is a recommendation, not a milestone.

The following table summarizes the tiers, in the order to build them.

| Tier | Meaning | Capabilities, in order |
|---|---|---|
| 0 | Built: finish acceptance | The ship gates in the completion checklist, then C01–C04, C06, C07, C09, C18–C22 |
| 1 | Build now: fixes a wrong or missing answer, reopens no parked decision, adds no dependency or network | C28, C29, C30, C11 |
| 2 | First external capability: needs a `plan.md` decision | C17, C05 |
| 3 | Reopens a parked decision or waits on real use: promote on its trigger | C08, C10, C32, C31 |
| 4 | Applies only when the user's circumstances call for it | C12, C13, C14, C15, C16, C33, C34, C20's workbook |
| 5 | Not pursued, or kept parked | C23, C24, C25, C26, C27 |

**Tier 0: built.** The ship gates in groups B and C of the completion checklist outrank every new
capability: confirming the off-site backup, milestone 24's backfill through the review screen,
milestone 29's real backtest, a phone with web push, and milestone 41's four weeks. The four weeks
start once two conditions hold: the engine redesign has landed, which it did on November 15
(`plan.md` §6, L5 redesign, release gate), and group B's durability and import work is done, which
is still open.

**Tier 1: build now.** Each one fixes an answer Arno gets wrong or can't give today:

1. **C28, safe-to-spend:** the smallest change. The engine already computes the figure, and only
   the HackerRank adapter calls it.
2. **C29, multi-event what-ifs:** the largest correctness gap. A job loss, a rent change, or a new
   salary has no engine path, so the model would have to compute it, which rule 1 in `plan.md` §4
   forbids for a verdict. Emergency runway comes with it.
3. **C30, price changes:** small, and it sits on the stepped-amount detection that already ships.
4. **C11's remainder:** the card minimum-due path and loan fees. The engine assumes that every card
   bill is paid in full; paying only the minimum keeps a balance that accrues the issuer's finance
   charge, which today goes unmodeled.

Tier 1 fits beside milestone 41's clock: it changes no plan decision, and each change ships under
the normal definition of done.

**Tier 2: the first external capability.** C17 first, then C05. Research reaches the network, so
it changes the privacy requirement and fires the parked egress-proxy trigger; see [plan rules for
external capabilities](#plan-rules-for-external-capabilities). C05's uploaded-document part needs
no network and can start before C17; its public-terms part needs C17. C05 also supplies C11 with
real rates and fees.

**Tier 3: promote on a trigger.** The plan promotes parked items "from a working, used system—not
from a document" (`plan.md`, "What 'shipped' means"). Each of these waits for its trigger during
the four weeks, or for an explicit plan change:

- **C08, account funding:** first in this tier, because the plan's test already says yes: without
  it, Arno can't warn that the account an EMI debits is short. Trigger: an autodebit fails because
  money was in the wrong account (`parked.md`).
- **C10, saved scenarios:** the same multi-event comparison is rebuilt in a second conversation.
- **C32, statement collection:** a monthly statement upload is skipped or late enough to leave a
  coverage gap.
- **C31, the MCP server:** the owner wants Arno's answers inside another assistant they already
  use. This is the MCP shape of the parked chat-app bridge (`parked.md`).

**Tier 4: when circumstances apply.** Holdings come first, then their performance, then retirement
(C12, C13, C14), on the parked trigger of about three months of held investments. That trigger was
written in October 2026, so the owner confirms whether it still holds. Tax (C15) waits for freelance
income. Deposit-insurance and policy review (C16's remainder) wait for deposits over the insured
limit at one bank or a policy to review. C33, C34, and C20's workbook export are small and
independent.

**Tier 5: not pursued or kept parked.** C23 and C26 stay parked with their triggers. C24's external
settlement route needs a public endpoint, and its internal part rides on C15's trigger. C25's
external enrichment has no Indian provider. C27 is business accounting, outside a personal finance
product. In the registry, Fi Money, Alpha Vantage, Twelve Data, and Financial Modeling Prep are not
selected.

**How this order relates to earlier ones.** The research report's [recommended
order](../research/personal-finance-agent-2026/README.md#recommended-order) puts account funding
and saved scenarios before research. This order moves unsaved multi-event what-ifs (C29) ahead of
both, because they are cheap and unparked, and leaves the parked items to their triggers. The
[external MCP assessment](../research/personal-finance-agent-2026/external-mcp-servers.md) picked
Tavily and a broker MCP first. This order replaces Tavily-first with a measurement (C17, T02) and
puts the _Consolidated Account Statement_ (_CAS_) ahead of broker MCPs (C12, T03), as `parked.md`
already reasons. Where they differ, this order is current.

## Daily money management

### C01: Progressive onboarding and personal context

_Tier 0._

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
  [toolset](../../src/arno/agent/toolset.py); enrollment in [Setup](../../web/src/Setup.tsx).
- Pydantic: existing structured tool inputs and persisted conversation context. A richer planning
  profile needs application data and validation; generic typed memory is not that schema.
- External/MCP: none required. Avoid public searches for private profile information.

**Completion checklist:**

- [ ] A fresh user reaches a supported first answer from a balance and income snapshot.
- [ ] A missing required card field keeps the card pending; no balance, limit, or date is invented.
- [ ] Profile conflicts and changed priorities are visible, editable, and retained across restart.
- [ ] The real enrollment-to-first-answer UI journey passes with no hidden operator intervention.
      Milestone 40's install covers the steps before enrollment, not this journey.

### C02: A trustworthy financial picture

_Tier 0._

**Description:** represent accounts, liabilities, obligations, and their evidence consistently.
Distinguish known zero, unknown, incomplete history, and old information; preserve the provenance of
balances and explain disagreements between user statements and imported records.

**How it helps the user:** prevents confident decisions based on a partial or misleading total.

**What the agent can do:**

- Answer what is known about cash, debt, and commitments, with dates and coverage warnings.
- Identify the next material missing statement or balance and explain how to resolve a conflict.

**Enabling tools and status:**

- Internal, partial: `get_financial_context`, `query_movements`, `run_query`, and `update_setup`;
  [read coverage](../../src/arno/tools/read.py) and [accounts](../../src/arno/ledger/accounts.py).
  Since the November 13 schema redesign every transfer pairs with a movement leg, and `run_query`
  reads named views.
- Pydantic: existing Code Mode and typed tool returns; proposed structured coverage-review results.
- External: statements first. A credit report adds liability evidence (C34). Fi Money is not
  selected ([T03](#t03-external-mcp-servers)); direct aggregation is a separate route in
  [T07](#t07-account-access).

**Completion checklist:**

- [ ] Known zero renders as zero; an unanchored account never establishes a known starting balance.
- [ ] One missing liquid balance prevents a complete-cash claim; any subtotal is explicitly scoped.
- [ ] History coverage and balance evidence are separate, with useful account and period details.
- [ ] A stated/imported conflict shows both dates and resolves through an audited, undoable path.

### C03: Transactions, splits, and corrections

_Tier 0._

**Description:** record real money movements with consistent accounting and reversible changes.
Distinguish consumption, transfers, lending, repayments, refunds, and goal funding so that totals
remain meaningful across chat, imports, panels, and later corrections.

**How it helps the user:** makes everyday capture dependable and keeps shared expenses
understandable.

**What the agent can do:**

- Record an expense, income, transfer, split, loan payment, refund, or settlement from user facts.
- Correct an entry, mark a payment one-off, track partial reimbursements, and undo changes without
  duplicating money.

**Enabling tools and status:**

- Internal, existing: `record_movement`, `correct_movement` (which also sets the one-off mark),
  `query_movements`, `schedule_payment`, and `undo`; [movements](../../src/arno/ledger/movements.py)
  compute the balanced transfers.
- Pydantic: existing discriminated tool inputs and approval/undo adapters; no external calculator.
- External/MCP: none required. Reported payments are recorded; Arno never executes them. Reminding
  the people who owe money is C33.

**Completion checklist:**

- [ ] Purchase, refund, transfer, split, and partial settlement agree across all spending surfaces.
- [ ] A retry cannot duplicate a movement or apply the same approval twice.
- [ ] Schedule matching uses recorded payment evidence; a due date alone never marks a bill paid.
- [ ] Correction and undo preserve balanced entries and explain any later-edit conflict.

### C04: Capture, import, and reconciliation

_Tier 0._

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
  `check_statement`, and `submit_parser`; [readers](../../src/arno/importers/readers.py),
  [reconciliation](../../src/arno/importers/reconcile.py), and [capture](../../src/arno/capture).
- Pydantic: existing `SubAgents` and parser Code Mode; the `STT_*` and `VISION_*` calls are separate
  services. Local speech-to-text runs whisper.cpp's native server with Silero voice activity
  detection. A password enters only the import card; `wait_for_password` returns status to the
  model.
- External: configured transcription/vision providers; optional OCR [T04](#t04-document-reading)
  only after comparative testing. Collecting statements from email is C32. The model cannot commit
  an import.

**Completion checklist:**

- [ ] Locked, unfamiliar, malformed, and textless documents have explicit success or recovery paths.
- [ ] A second supported statement replays the saved parser and overlapping captures do not
      duplicate.
- [ ] Passwords never appear in model context, database, logs, or exports; a restart may request one
      again.

The real upload, password, review, commit, undo, restart-mid-parse, voice, photo, and phone-share
journeys are tracked in groups A2 and B of the completion checklist.

### C05: Financial contract understanding

_Tier 2._

**Description:** turn relevant contract passages into structured, dated terms with source locations.
Read card terms, loan _Key Facts Statements_ (_KFS_), deposit conditions, and policy documents;
retain ambiguous clauses for review instead of filling gaps from generic product knowledge. Start
with card terms, one loan KFS, and one insurance policy.

**How it helps the user:** makes fees, restrictions, and exceptions visible before a financial
decision.

**What the agent can do:**

- Explain a prepayment clause, rate reset, card fee, deposit withdrawal term, or policy exclusion.
- Supply reviewed contract terms to C11 or C16 and identify what prevents a complete calculation.

**Enabling tools and status:**

- Internal, proposed: contract schemas, field-to-passage provenance, version history, and review
  cards. Existing [document readers](../../src/arno/importers/readers.py) are reusable foundations.
- Pydantic: structured output with validation and focused extraction; no built-in contract engine.
- External: an uploaded document needs no network. A public issuer page needs C17. Docling stays
  optional in [T04](#t04-document-reading). A document parser is not a legal or insurance-adequacy
  verifier.

**Completion checklist:**

- [ ] Every material rate, fee, date, and exception links to the original document location.
- [ ] Missing, contradictory, and low-confidence terms remain unresolved until reviewed.
- [ ] Revised terms preserve the old version and identify which calculations they affect.
- [ ] Malicious document text cannot authorize a write or silently modify saved product terms.

### C06: Spending insight and an adaptive plan

_Tier 0._

**Description:** explain consumption and its changes over an appropriately covered period. Compare
actual spending with a user-chosen plan, distinguish timing from behavior, and suggest modelable
adjustments while respecting essential commitments and the user's priorities.

**How it helps the user:** answers where money went and what spending flexibility remains.

**What the agent can do:**

- Produce category, merchant, account, and period breakdowns with the underlying rows.
- Explain deviations and compare discretionary changes without inventing a budget or judging habits.

**Enabling tools and status:**

- Internal, partial: `summarize_spending`, `query_movements`, `recap`, `present`, and category
  rules; [baselines](../../src/arno/importers/baselines.py) estimate daily variable spending.
- Pydantic: existing `run_code` combines returned data; a persistent planned-versus-actual model
  remains proposed. Category caps are a separate parked decision, not required by this entry. How
  much is left to spend is C28.
- External/MCP: none required; optional merchant enrichment is C25.

**Completion checklist:**

- [ ] Transfers, card repayments, and investment contributions do not inflate consumption.
- [ ] Refunds and corrections reconcile to the rows and period actually analyzed.
- [ ] Incomplete history prevents an unqualified monthly total or misleading trend comparison.
- [ ] A changed income or priority can revise the plan with visible before/after consequences.

### C07: Goals and irregular expense reserves

_Tier 0. New work is in C29._

**Description:** connect future obligations and goals to explicit funding needs and available cash.
Represent annual premiums, repairs, festivals, and other uneven expenses with appropriate timing;
keep reserved savings distinct from unrestricted cash without counting the same reserve twice.

**How it helps the user:** turns future expenses into manageable plans and makes goal tradeoffs
clear.

**What the agent can do:**

- Show a goal's shortfall, deadline, and funding assumptions.
- Compare priorities when income changes, through C29.

**Enabling tools and status:**

- Internal, partial: `update_setup` for goals, yearly schedules, and baselines; `project` and [goal
  accounts](../../src/arno/ledger/goals.py) handle fenced funds. A yearly schedule plus a goal with
  a contribution schedule already acts as a reserve for an annual bill. Detected recurring series
  enter the projection since November 15.
- Proposed: "can I put ₹X a month toward this?" is a what-if that adds a contribution, which C29
  answers with the engine. No separate reserve object is needed.
- External/MCP: none required; contract dates and premiums may come from reviewed C05 evidence.

**Completion checklist:**

- [ ] Annual and one-off commitments appear on their real dates and receive an explicit funding
      plan.
- [ ] Reserved cash is counted once across accounts, goals, and forecast outflows.
- [ ] Unconfirmed future income does not fund a confident affordability verdict.

### C08: Cash flow and account funding readiness

_Tier 3. Reopens the parked projection for each account._

**Description:** forecast cash against dated income, commitments, card settlements, and variable
spend. Evaluate both pooled affordability and the account that must fund each payment,
distinguishing usable money from illiquid, uncertain, or merely expected resources.

**How it helps the user:** catches a likely autodebit shortfall even when total cash is positive.

**What the agent can do:**

- Explain the cash path, uncertainty, lowest projected balance, and relevant data gaps.
- Identify an underfunded account/date and model a user-chosen funding transfer without executing
  it.

**Enabling tools and status:**

- Internal, partial: `project`, [input loading](../../src/arno/tools/projection.py), and the pure
  [projection engine](../../src/arno/engine/projection.py) pool liquid balances across 16,384
  scenarios. Detected recurring series and payment attribution are already in the inputs.
- Proposed: account routing, transfer timing, and account-level feasibility. This reopens a parked
  decision; the trigger is an autodebit that fails because money was in the wrong account.
  Pydantic's Code Mode calls the engine; it is not the forecasting model itself.
- External: none required; optional reference series must enter as validated, dated assumptions.

**Completion checklist:**

- [ ] Scheduled and variable flows reconcile; principal, interest, card dues, and reserves are not
      doubled.
- [ ] Sufficient total cash with a short EMI account produces a named account/date shortfall.
- [ ] Unknown balances, unsupported transfer timing, and inaccessible assets cannot imply payment
      readiness.
- [ ] Identical inputs and seed reproduce the forecast; sensitivity and source ages remain visible.

### C09: Purchase affordability and payment comparisons

_Tier 0._

**Description:** assess a purchase against commitments, goals, and the user's cash floor in code.
Compare supported payment methods and changes on the same baseline; communicate the engine's
verdict, uncertainty, and tradeoffs without substituting a model-written financial conclusion.

**How it helps the user:** shows the consequences of a purchase before the user commits to it.

**What the agent can do:**

- Evaluate paying from an account, using a card, or using a card EMI at its real cost.
- Explain which minimal plan makes the purchase work and which assumptions could change the answer.

**Enabling tools and status:**

- Internal, existing: `project(hypothetical=...)` invokes
  [affordability](../../src/arno/engine/affordability.py); there is no separate `can_i_afford`
  tool. A `yes` needs the floor probability at 90% or more and every obligation payable in 99% of
  scenarios. The engine returns up to four minimal plans that combine waiting, a card or EMI,
  delaying a goal, and cutting reducible categories, each verified by a rerun on the same
  scenarios. A card EMI carries its interest, processing fee, and GST.
- Calibration was built, measured, and not shipped (`plan.md` §6, L5 redesign): the engine reports
  its own quantiles, and the held-out P10–P90 band holds 66% to 74% of outcomes against an 80%
  target. The P10–P90 band is the range between the 10th and 90th percentile outcomes.
- Pydantic: Code Mode can compare engine returns; `present` renders the selected results.
- External: C05 contract evidence only when needed; an external analyst or research service cannot
  issue Arno's verdict. A purchase alongside other changes is C29.

**Completion checklist:**

- [ ] Verdicts and every suggested tradeoff are engine outputs computed on the same baseline.
- [ ] Limit breaches, unsupported product costs, and unknown funding facts are surfaced.
- [ ] Account, period, goal, and probability labels match the result, including after corrections.

### C28: Safe-to-spend

_Tier 1._

**Description:** report the largest one-off amount the user can spend today while the projection
still clears the floor at the engine's 90% threshold, with its horizon, the date the floor binds,
and the data-quality notes. The figure comes from the engine, never from a balance minus the floor.

**How it helps the user:** answers "how much can I spend before payday?" with one number that
already accounts for bills, card dues, goals, and simulated everyday spending.

**What the agent can do:**

- State the safe amount, its as-of date, and the date the floor is closest.
- Explain what lowers it, such as a bill due before salary or a goal contribution, and hand a named
  purchase to C09.

**Enabling tools and status:**

- Internal, existing but unexposed: `safe_amount_today` in the [projection
  engine](../../src/arno/engine/projection.py) returns the largest payment today that keeps the
  floor probability at the threshold. Only the HackerRank adapter calls it.
- Proposed: return the figure from `project` and show it on the home page with its horizon and
  binding date. The summary's `median_low` is the median path's low point, not the threshold
  path's, so subtracting the floor from it overstates the safe amount.
- Pydantic and external: none needed.

**Completion checklist:**

- [ ] The safe amount and `project`'s verdict agree at the boundary on the same inputs and seed; any
      difference is named, such as an obligation the floor test doesn't cover.
- [ ] With no known liquid balance there is no figure, and the answer names the missing account.
- [ ] No headroom renders as ₹0 with the binding date, never as a negative amount.
- [ ] Chat and the home page show the same figure, as-of date, and data-quality notes.

### C29: Multi-event what-ifs

_Tier 1._

**Description:** run the projection and affordability engine on a set of dated changes, not only
one purchase. A change can stop or change income, add or change an obligation such as rent or a
new EMI, pause a contribution such as a _systematic investment plan_ (_SIP_), or add a one-time
receipt. Nothing is saved and no real record changes.

**How it helps the user:** answers the big decisions, such as a job switch, a move, or a loan, with
the same engine and probability as a purchase verdict. Emergency runway, the time the user stays
above the floor with income stopped, comes with it.

**What the agent can do:**

- Combine a purchase with income and obligation changes, and report the floor probability, first
  floor crossing, goal effects, and verdict.
- Compare a few alternatives side by side and explain which event drives a shortfall.
- Answer "how much can I put toward this goal each month?" by trying the contribution as an
  obligation.

**Enabling tools and status:**

- Internal, proposed: `ProjectHypothetical` in the [toolset](../../src/arno/agent/toolset.py)
  accepts one purchase: an amount, a date, a method, and EMI terms. Extend it with dated changes to
  income and obligation schedules, applied as path transforms by the affordability engine before
  the kernel runs, one engine run for each alternative.
- Without it, a shock question has no engine path, and the model would have to compute the effect
  in `run_code`, which rule 1 in `plan.md` §4 forbids for a verdict.
- Pydantic: a discriminated union of event inputs; Code Mode compares alternatives.
- External: none required. Saving and reopening a set of changes is C10.

**Completion checklist:**

- [ ] Each event kind changes only the intended flows; no live balance, schedule, or goal changes.
- [ ] Income stopped from a date reports the runway and first floor crossing from the engine.
- [ ] The same events give the same result in any order, on the same inputs and seed.
- [ ] Unconfirmed or changed income is labeled and never funds a `yes`.
- [ ] An eval case answers a job-loss question from engine output, not `run_code` arithmetic.

### C10: Saved scenarios

_Tier 3. Depends on C29._

**Description:** preserve a set of C29 changes as a named alternative with explicit assumptions.
Retain enough baseline information to reproduce an earlier result and compare it with a refresh on
current data.

**How it helps the user:** supports decisions that span several conversations.

**What the agent can do:**

- Create, duplicate, compare, revise, and explain a user-authorized scenario.
- Reopen an old comparison and distinguish its original result from a refresh on current data.

**Enabling tools and status:**

- Internal, proposed: scenario storage, immutable input snapshots, and an engine runner over C29's
  event inputs. The affordability `baseline` return is not a saved scenario record.
- Pydantic: Code Mode orchestrates calculations; persisted scenario state belongs in Postgres.
- External: C17 may supply sourced assumptions. Trigger: the same comparison is rebuilt in a
  second conversation.

**Completion checklist:**

- [ ] Reopening retains input dates, overrides, seed, calculation version, and original output.
- [ ] Refreshing after a ledger edit shows what changed and never overwrites the original silently.
- [ ] Stress cases show conditional outcomes without inventing probabilities for unmodeled events.
- [ ] Saved scenarios survive the supported backup and restore path.

### C11: Debt and credit lifecycle cost

_Tier 1 for the minimum-due path and loan fees._

**Description:** model what a debt costs over its repayment life using the applicable contract.
Include supported interest, fees, taxes on fees, discounts, rate resets, and early-repayment terms;
distinguish card minimum payment, full statement payment, available credit, and cash.

**How it helps the user:** exposes the cost and liquidity consequences hidden behind a headline EMI.

**What the agent can do:**

- Compare baseline repayment with reduce-tenure or reduce-EMI prepayment and other supported paths.
- Explain what paying only a card's minimum costs, and compare fee-inclusive offers without choosing
  a product.

**Enabling tools and status:**

- Internal, partial: `amortize` returns the EMI, interest, payoff, schedule, and prepayment under
  reduced tenure or reduced EMI; it infers a loan's rate from its EMI and matches a bank's rounding
  conventions. A card EMI purchase in affordability carries interest, the processing fee, and 18%
  GST on both, with a labeled default rate when the offer states none. Code: [money
  calculations](../../src/arno/engine/money.py).
- Proposed, Tier 1: a minimum-due path for a card, and a processing fee with the resulting _annual
  percentage rate_ (_APR_, the yearly cost including fees) for a loan in `amortize`. The engine pays
  every card bill in full on its due date. Paying only the minimum keeps a balance that accrues the
  issuer's finance charge, and new purchases lose their interest-free period, so the path needs the
  card's finance rate and minimum-due rule from C05 or from the user.
- Proposed, later: foreclosure charges and variable-rate resets. Pydantic validates terms; Code Mode
  must call the tested calculation rather than recreate formulas.
- External: actual KFS and card terms through C05, and official rules through C17;
  [T05](#t05-financial-calculations).

**Completion checklist:**

- [ ] Existing prepayment outputs reconcile principal, interest, payoff, and rounded paise.
- [ ] Fee, tax, discount, and cash-price differences appear separately in a supported offer
      comparison.
- [ ] Minimum-only card results use issuer or stated terms and show the continuing balance and the
      lost interest-free period.
- [ ] Missing rates, reset rules, or contingent charges prevent an unsupported complete-cost claim.

### C33: Settle-up reminders

_Tier 4._

**Description:** list who owes the user and how long each balance has been open, and draft a
reminder the user sends themselves, optionally with a UPI payment link for the amount.

**How it helps the user:** split and lending balances get settled without anyone doing the sums.

**What the agent can do:**

- Show each open balance and its age, and draft a short reminder.
- Record a settlement only after the user reports that the payment happened.

**Enabling tools and status:**

- Internal, partial: the `v_owed` view gives each person's balance, direction, and next expected
  date. An open balance's age needs the oldest unsettled leg.
- Proposed: a drafted message and a `upi://pay` link built from the user's own UPI ID in settings.
- Boundary: Arno never sends a message or moves money; the user shares the reminder.

**Completion checklist:**

- [ ] Amounts and ages come from the ledger, and a settled person shows nothing.
- [ ] The link's amount equals the open balance or a partial amount the user chose.
- [ ] Nothing is sent, and no settlement is recorded until the user reports the payment.

## Whole-life coverage when applicable

### C12: Holdings and dated net worth

_Tier 4. Reopens the parked net worth from investment statements._

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
- External, first: the CAS read by CASParser, plus the _Association of Mutual Funds in India_
  (_AMFI_) daily _net asset value_ (_NAV_) for fund prices, in [T06](#t06-holdings-and-valuations).
  The CAS lists every mutual fund and demat holding in one PDF, across brokers, which is why
  `parked.md` puts it ahead of broker APIs.
- MCP, later: the user's own broker route, such as Kite or Upstox, in
  [T03](#t03-external-mcp-servers), only when holdings must be fresher than the monthly CAS.
  Holdings and prices remain separate inputs.
- Trigger: about three months of held investments (`parked.md`, written in October 2026).

**Completion checklist:**

- [ ] Statement quantities and transaction history reconcile before valuation is trusted.
- [ ] Units, price date, ownership date, and valuation currency are independently visible.
- [ ] Transfers, duplicate holdings, corporate actions, and unknown assets do not distort the total.
- [ ] Concentration reports disclose their grouping, look-through limits, and missing holdings.
- [ ] Parser/dependency licenses and data access/storage rights are verified for the selected
      sources.

### C13: Investment performance and benchmarks

_Tier 4. Depends on C12._

**Description:** calculate investment outcomes using dated cash flows and the correct return
measure. Separate contributions, withdrawals, fees, taxes, and price changes; disclose numerical
ambiguity, incomplete history, valuation dates, and the assumptions behind any benchmark comparison.

**How it helps the user:** explains whether growth came from saving more or from investment
performance.

**What the agent can do:**

- Calculate a money-weighted return (_XIRR_, the return on money that went in at different times)
  and appropriate endpoint growth measures from sufficient evidence.
- Compare with a suitable benchmark over matching dates and explain why a result is undefined or
  ambiguous.

**Enabling tools and status:**

- Internal, proposed: validated performance helpers and cash-flow reconciliation built on C12. The
  [engine blueprint](engine-blueprint.md) specifies XIRR as a bracketed bisection returned only when
  the cash flows have one sign change; it was not built because nothing uses it yet.
- Library alternative: `pyxirr` in [T05](#t05-financial-calculations), which needs a host wrapper,
  not a Monty third-party import.
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

_Tier 4. Depends on C10, C12, and C29._

**Description:** model long-term needs against resources using transparent assumptions and ranges.
Include inflation, income changes, dependent costs, retirement timing, access restrictions, and
relevant taxes without treating a smooth compound-growth curve as a guaranteed life outcome.

**How it helps the user:** shows how today's choices affect future goals and financial resilience.

**What the agent can do:**

- Compare retirement ages, career breaks, education costs, or changed contributions as scenarios.
- Explain which assumptions drive a shortfall and which missing facts prevent meaningful modeling.

**Enabling tools and status:**

- Internal, proposed: long-horizon cash-flow and withdrawal models integrated with C10, C12, and
  C29. Existing `compound` supplies a calculation primitive, not a complete retirement planner. The
  blueprint keeps inflation indexing and investment returns out of the engine today.
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

_Tier 4. Reopens the parked tax engine._

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
  Trigger: freelance income appears, or self-hosters ask (`parked.md`).
- Pydantic: structured extraction and validated calculator inputs; research prose cannot compute
  tax.
- External: Income Tax Department sources and user-downloaded evidence via
  [T09](#t09-regulatory-and-policy-evidence), retrieved through C17; no portal password, filing, or
  general tax MCP is assumed.

**Completion checklist:**

- [ ] Every supported calculation selects an applicable year, rule version, and user classification.
- [ ] Exemptions, withheld amounts, already-paid tax, and income estimates are distinguished.
- [ ] Missing current rules or user facts produce a scoped answer rather than invented rates or
      deadlines.
- [ ] Boundary cases and official reference examples pass; reminders update when a source changes.

### C16: Emergency reserves and financial protection

_Tier 4. Runway itself comes with C29 in tier 1._

**Description:** assess access to essential-expense reserves and inventory relevant protection
evidence. Track policy coverage, dates, exclusions, nominees, and documents; distinguish deposit
protection from liquidity and investment risk, with bank ownership and account capacity considered
explicitly.

**How it helps the user:** reveals practical weaknesses when income stops or an unexpected expense
occurs.

**What the agent can do:**

- Model emergency runway through C29 and explain which assets cannot be accessed on the needed date.
- Review renewals, missing policy facts, and deposit exposure at each bank with evidence and
  caveats.
- Identify missing nominee and document-location information for the user's continuity review.

**Enabling tools and status:**

- Internal, proposed: reserve-access models, policy inventory, and ownership-aware deposit
  aggregation. Existing `project` and premium schedules are partial foundations, not insurance
  adequacy models.
- External: actual policies and deposit terms, and _Deposit Insurance and Credit Guarantee
  Corporation_ (_DICGC_) and IRDAI sources in [T09](#t09-regulatory-and-policy-evidence), with
  reviewed extraction from C05. DICGC insures up to ₹5 lakh for each depositor at each insured
  bank, in the same capacity and right, so the deposit review matters only above that at one bank.
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

### C34: Credit report as liability evidence

_Tier 4._

**Description:** read a credit report the user downloads from a credit bureau and compare its
accounts, limits, and outstanding amounts with the ledger's cards and loans. Flag accounts the
ledger doesn't know and balances that are stale or conflicting.

**How it helps the user:** catches a forgotten card, a loan never set up, or an account the user
didn't open.

**What the agent can do:**

- Explain each difference with the report's date.
- Propose a missing account through a confirm card, because report text is outside text.

**Enabling tools and status:**

- Internal, proposed: one more document type through the existing readers and an extraction schema,
  in the C05 pattern. Report rows are dated evidence, never a balance overwrite.
- External: none; the user downloads the report. Fi Money's `fetch_credit_report` is not selected.

**Completion checklist:**

- [ ] A report balance never replaces a stated or statement balance; it shows as dated evidence.
- [ ] An unknown account is proposed, not created.
- [ ] Identity fields such as PAN, date of birth, and address are dropped at import, as
      `plan.md` §3 requires for identifiers the product doesn't use.

## Research, review, and trust

### C17: Current financial research with evidence

_Tier 2._

**Description:** retrieve current external financial facts and preserve auditable source context.
Prefer the source that owns a rule or term, distinguish publication from effective dates, and
separate verified facts, uncertain findings, and scenario assumptions from personal ledger records.

**How it helps the user:** answers changing questions that the ledger and model memory cannot
establish, such as a current RBI rule, a deposit rate, or an issuer's fee.

**What the agent can do:**

- Search and read official rules, schemes, rates, and contract explanations for a named period.
- Compare conflicting sources, report retrieval failure, and offer a bounded researched explanation.

**Enabling tools and status:**

- Internal, proposed: source records, citations, private-query minimization, and integration with
  outside-text marking and number provenance. Call budgets can use the native spend budgets built
  on November 12.
- Pydantic: `WebSearch(local=True)` and `WebFetch(local=True)`, wrapped so each return carries
  source metadata, or a harness or MCP research server ([T02](#t02-external-research)). Current Arno
  chat has no configured search or fetch.
- Route: measure before choosing. The [framework
  check](../research/personal-finance-agent-2026/verification-framework.md) found the built-in
  tools the smallest complete path; the [MCP
  assessment](../research/personal-finance-agent-2026/external-mcp-servers.md) proposed Tavily.
  Score both on the same 20 Indian official-source questions for source quality, dates, cost, and
  failure behavior.
- Search must run inside Arno whatever the backend: `number_check` ignores returns from search the
  model provider runs itself, so every figure from it would read as untraced.
- Plan changes it needs: [plan rules for external
  capabilities](#plan-rules-for-external-capabilities).

**Completion checklist:**

- [ ] A supported answer cites the passage and relevant period, with unknown dates explicitly
      unknown.
- [ ] Source title, URL, retrieval time, effective-date basis, and permitted evidence survive a
      restore.
- [ ] Private data is omitted from queries/URLs; private-address fetches and unsafe redirects are
      blocked.
- [ ] Injection, conflicting evidence, exhausted budgets, and failed retrieval cannot cause guessed
      facts or writes.
- [ ] Eval cases cover the advice boundary: a list of rates is a fact, and picking one is a
      recommendation.

### C18: Proactive review and scheduled questions

_Tier 0._

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

- Internal, existing/partial: [watch jobs](../../src/arno/jobs), `update_setup` for scheduled
  questions, and `schedule_payment` for payment evidence. Existing reminders don't need a model.
  Since November 15 recurring charges are detected by calendar rules scored by likelihood:
  proposed at confidence 0.9, projected at 0.999, and reported missed 3 or more days late. Price
  changes are C30.
- Pydantic: the existing read-only scheduled agent, Code Mode, and spend limits.
- External: web push is a delivery service; extra channels are C26. More anomaly/refund detection
  needs explicit logic and validation; anomaly detection stays parked.

**Completion checklist:**

- [ ] One unchanged risk episode or due occurrence produces one actionable alert, with controlled
      re-arming.
- [ ] Supported payment evidence resolves a warning; undo restores the appropriate state.
- [ ] Missed schedules, model outages, opt-outs, and permission failures have visible recovery
      behavior.

Real phone push and a real scheduled question are tracked in group B of the completion checklist.

### C30: Price changes in recurring charges

_Tier 1._

**Description:** notify when a recurring charge's amount steps to a new level, such as a
subscription price rise, a changed EMI, or a raised premium. Show the old and new amounts, the first
new date, and the payments behind it, as a proposal.

**How it helps the user:** catches silent subscription increases.

**What the agent can do:**

- Explain the change and cite the payments.
- Offer to update the schedule's amount as an ordinary undoable write.

**Enabling tools and status:**

- Internal, partial: `engine/series.py` classifies a series' amounts as fixed, stepped, or variable
  and returns the new level of a stepped one, but not the old level or the step date. The [pattern
  job](../../src/arno/jobs/patterns.py) emits only new subscriptions, new income, and missed
  charges.
- Proposed: carry the previous level and step date out of the amount model, add a proposal kind,
  and compare a confirmed schedule's matched payments with its stated amount.
- Pydantic and external: none needed.

**Completion checklist:**

- [ ] One alert for each step, keyed by series and step date; it re-arms only on another step.
- [ ] A variable bill, such as electricity, never raises a step alert from ordinary variation.
- [ ] The old amount, new amount, step date, and payment rows are shown and cited.
- [ ] Dismissing the proposal writes nothing.

### C19: Connection health and recoverable ingestion

_Tier 0 for jobs and imports; each external connector brings its own part._

**Description:** expose whether every selected data source is complete, current, and successfully
processed. Recover from restarts, failed refreshes, schema changes, repeated events, and expired
access while preserving source identity and preventing a stale connection from appearing healthy.

**How it helps the user:** prevents silent gaps and duplicate money when automation fails.

**What the agent can do:**

- Explain the last successful refresh, missing interval, and safe retry or reauthorization step.
- Distinguish a pending import, failed connector, and completed reconciliation from live balances.

**Enabling tools and status:**

- Internal, partial: [job runner](../../src/arno/jobs/runner.py), parser task/step persistence,
  and status APIs exist. Connector cursors, source-event IDs, and access state are proposed with
  the first connector: C32, or a C12 broker route.
- Pydantic: existing parser delegation and error reporting; external function/MCP errors need
  mapping to application status. Transport retries alone don't establish idempotent ledger
  processing.

**Completion checklist:**

- [ ] Replaying or reordering an external event does not duplicate or misstate a movement.
- [ ] Retry, restart, expired credentials, and changed input schema produce persistent, explainable
      states.
- [ ] A failed refresh leaves the old data date visible and never marks new data as reconciled.

The parser's restart-mid-parse journey is tracked in groups A2 and E of the completion checklist.

### C32: Statement collection from email

_Tier 3. Reopens the parked email autocapture._

**Description:** collect statement and CAS attachments from the user's mailbox on a schedule and
hand each one to the existing import pipeline, which still ends in review.

**How it helps the user:** removes the monthly download and upload that reconciliation depends on.
Banks email statements monthly, and the CAS has been emailed by default since August 2024.

**What the agent can do:**

- Report the statements found, and point to the import card for a password.
- Explain an attachment it skipped and why.

**Enabling tools and status:**

- Internal, proposed: a scheduled job that reads one chosen folder or sender list through the
  standard library's `imaplib` or the provider's API, with the credential held on the host, and
  keeps only matching attachments.
- Plan: `parked.md` parks SMS and email autocapture of transaction alerts. Statements are a
  different job, but the change still needs a `plan.md` §2 decision. Email filenames and bodies are
  outside text, and the job reaches the network.
- Trigger: a monthly upload is skipped or late enough to leave a coverage gap.

**Completion checklist:**

- [ ] Only the chosen folder or senders are read; nothing else is read, and nothing is sent.
- [ ] The same attachment is never imported twice, by content hash.
- [ ] The credential never reaches model context, logs, or exports, and revoking it is visible.
- [ ] A failed fetch shows the last success and the gap, as C19 requires.
- [ ] A password-protected file reaches the import card the same way an upload does.

### C20: Inspectable answers and portable models

_Tier 0. The workbook export is tier 4._

**Description:** let the user inspect the evidence, assumptions, and calculations behind an answer.
Present meaningful tables and charts and export a reproducible model with its original inputs,
dates, formulas where appropriate, and trustworthy computed results.

**How it helps the user:** makes an answer checkable and shareable with an accountant or adviser.

**What the agent can do:**

- Show supporting rows, calculation assumptions, a scenario comparison, or an explanatory chart.
- Prepare a scoped financial workbook or data export without disclosing unrelated private records.

**Enabling tools and status:**

- Internal, partial: `present`, `show_chart`, `show_table`, and [JSON/CSV/dump
  exports](../../src/arno/ledger/export.py) exist; a model-workbook export doesn't.
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

### C31: Arno as an MCP server

_Tier 3. Reopens the parked chat-app bridge._

**Description:** expose Arno's read tools over MCP to clients the user runs, such as a desktop
assistant or another agent, so they can ask the ledger and the engine without a copy of the data.
Read-only.

**How it helps the user:** Arno answers inside the assistant already open, and another agent can
check affordability before suggesting a purchase.

**What the agent can do:**

- Through the client's agent: read the financial context, movements, spending, recaps, and
  projections, including affordability verdicts from the engine.

**Enabling tools and status:**

- Internal, proposed: a thin server adapter over the functions in `tools/`, which rule 3 in
  `plan.md` §4 anticipates. `parked.md` names this shape for chat-app bridges.
- Dependencies: `mcp` 2.3.0 (MIT) and `fastmcp-slim` 4.0.0 (Apache-2.0) are already locked as
  transitive dependencies of the agent framework, with server support. Importing either directly
  makes it a direct dependency, which needs `THIRD_PARTY_NOTICES.md` and the license audit.
- Auth: login is passkey-only, and an MCP client can't use a passkey, so the server needs a scoped,
  expiring, revocable token issued from Settings behind a fresh passkey. It is reached over the
  tailnet only.
- Limits: the client's model runs none of Arno's number check, cards, or confirm cards, so the
  server stays read-only. Start with the typed reads; `run_query` waits. Ledger data goes to
  whichever model the client uses, which token creation states.

**Completion checklist:**

- [ ] No write tool is reachable, and a write attempt fails at the server.
- [ ] A token is created behind a fresh passkey, scoped read-only, expiring, and revocable, and
      never appears in logs or exports.
- [ ] Returns keep outside-text markers, as-of dates, source IDs, and zero versus unknown.
- [ ] The server is reachable only through the same tailnet path as the API.

### C21: Privacy, authorization, and user control

_Tier 0._

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
  marking](../../src/arno/tools/mark.py), audit groups, `preference`, `undo`, and [the write
  gate](../../src/arno/agent/toolset.py) exist. Since November 14 the `app` role owns the
  databases without superuser, and native spend budgets cap every agent run.
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

_Tier 0._

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

- Internal, partial: [eval cases](../../evals/cases.py),
  [backtest](../../src/arno/engine/backtest.py), invariants, Phoenix traces, and [restore
  tooling](../../src/arno/backup_restore.py) exist. The
  backtest scores each forecast as a distribution against three baselines on ten synthetic
  households, and the engine beats every baseline in every cell. HackerRank scores the shipped
  engine at 86.8% through an adapter. A real ledger becomes a measurement once 90 days follow a
  confirmed, linked salary (`plan.md` §5, L5 status).
- Pydantic: instrumentation, scripted/test models, and spend controls support verification; a
  successful span or quantity-matching check is not proof of financial meaning.
- External: restic storage and configured inference endpoints need separate operational validation.

**Completion checklist:**

- [ ] Unit, property, invariant, and semantic cases cover wrong account, wrong period, rounding, and
      missing data.
- [ ] Usable real-history evaluation distinguishes synthetic results and controls historical
      look-ahead.

The local-model scorecard, median cost, latency, fresh-machine restore, and human gates are tracked
in groups B and C of the completion checklist.

## Conditional extensions and exclusions

These entries stay short: each is parked or not pursued, with the reason and what would bring it
back.

### C23: Foreign-currency valuation

_Tier 5. Parked: multicurrency._

Represent foreign-currency amounts with original and base amounts, currencies, effective dates,
reference sources, and the actual charged conversion kept distinct. Today a foreign purchase is
logged at the amount charged in rupees. A reference rate is not an executable quote and never
overwrites a bank's conversion. Trigger: a foreign-currency account or foreign income
(`parked.md`). Reference sources are in [T11](#t11-reference-fx).

### C24: Freelancer receivables and net payouts

_Tier 5 for external settlement; the internal part rides on C15's trigger._

Connect invoices, fees, refunds, and settlements to actual cash. Variable and one-off income with a
confirmed flag already exist, and unconfirmed income never funds a verdict. Invoice aging and
gross-to-net reconciliation need an explicit model, worth building when freelance income appears,
the same trigger as C15. Gateway settlement APIs and webhooks are not pursued: a webhook needs a
public endpoint, and Arno is private by design ([T10](#t10-settlements-and-webhooks)).

### C25: Merchant identity enrichment

_Tier 5 for external enrichment._

Internal [seed rules](../../src/arno/importers/seed_rules.py), [rule
precedence](../../src/arno/importers/rules.py), the categorizer, and `correct_movement` already
separate a payment app from the merchant and learn the user's rules. External enrichment stays
parked: [Plaid Enrich](https://plaid.com/docs/enrich/) documents US and Canada coverage only, no
Indian UPI provider was identified, and narration text would leave the box. Trigger: a measured
categorization miss rate that internal rules can't fix.

### C26: Additional notification and approval channels

_Tier 5. Parked: chat-app integrations._

The inbox and web push exist. A chat channel such as [Telegram](https://core.telegram.org/bots/api)
needs credentials, identity binding, retention review, and a delivery design, and Telegram bot
chats aren't end-to-end encrypted. A channel would link back to Arno rather than approve writes in
chat. Trigger: `parked.md`'s chat-app trigger; C31 may serve the same need first.

### C27: Business-system and accountant handoff

_Tier 5. Not pursued._

Tally, Xero, QuickBooks, and Zoho sync, GST and LEI registry checks, and staged accountant handoffs
serve business accounting, outside a personal finance product. The JSON and CSV exports already
hand an accountant the user's records, and C20's workbook would add a model. Revisit only with a
separate scope decision in `plan.md` §2.

## Plan rules for external capabilities

Any capability that reaches the network, or exposes Arno to another client, meets these rules, and
each one that changes a plan decision needs that change recorded in `plan.md` first:

- **Privacy requirement:** `plan.md` §2 says nothing leaves the box except encrypted backups and
  contentless push pings. Research (C17), email collection (C32), broker routes (C12), and an MCP
  server (C31) each change that sentence.
- **Egress proxy:** `parked.md` parks an egress proxy with the trigger "any tool or bridge that
  reaches the network." The first such capability reopens that decision, and the system design's
  statement that the agent has no network path of its own changes with it.
- **No bank APIs:** `plan.md` §2's constraint and the parked broker APIs stand until a route is
  selected; a broker MCP is a broker API.
- **Outside text:** web pages, MCP results, and email are other people's words. They're marked as
  outside text, so a conversation that reads them asks for a tap before a memory, setup, or wide
  write (rule 2 in `plan.md` §4). Research conversations therefore show more confirm cards.
- **Number provenance:** a figure from an external source must come from a tool return Arno can
  trace. Search executed by the model provider isn't traced by `number_check`.
- **Credentials:** no credential, passcode, or token passes through chat. The agent never handles
  passwords (architecture rule 6 in `AGENTS.md`), and a statement password goes only on the import
  card. This is why Fi Money's passcode flow is not selected.
- **Advice boundary:** each changed boundary gets an eval case, as `AGENTS.md` requires for every
  fixed rule.
- **Promotion:** parked work is promoted from a working, used system (`plan.md`, "What 'shipped'
  means"), through a milestone in `plan.md` §5.

## Verified tool registry

The registry distinguishes a tool being documented or importable from its integration being
selected, configured, and tested. Candidate services are alternatives, not a shopping list.

### T01: Internal finance tools and Pydantic execution

Arno registers reads in [toolset.py](../../src/arno/agent/toolset.py) and exposes them through
`run_code`: `get_financial_context`, `query_movements`, `summarize_spending`, `recap`, `project`,
`run_query`, `amortize`, `compound`, `rupees`, `show_chart`, `show_table`, and `present`. The direct
writes are `record_movement`, `correct_movement`, `update_setup`, `schedule_payment`, `preference`,
and `undo`. Parser/task tools are a separate surface. `project`'s hypothetical takes one purchase
(C29 extends it), and the engine's `safe_amount_today` has no tool yet (C28).

Pydantic AI validates inputs and adapts the interaction; the application's plain Python computes
financial results. [Code Mode](https://pydantic.dev/docs/ai/harness/code-mode/) executes constrained
Monty scripts, not arbitrary CPython imports. A new scientific library needs a bounded host helper.
Network access in a host helper must be reviewed even though the script itself has no generic
network API. No separate DuckDB, Polars, E2B, Redis, or unrestricted Python environment is required
to establish these capabilities.

### T02: External research

Arno uses `OpenAIChatModel` with interchangeable endpoints. Native-provider tools are not a portable
default for that transport. Installed source confirms `WebSearch` and `WebFetch` default to
native-only; explicit local fallbacks are candidates. `ddgs` 9.16.1 (MIT) is installed; the
`web-fetch` dependencies are absent. Dependency presence does not enable the tools in Arno.

The following table records the researched alternatives.

| Route | Exact enabling surface | Status and constraint |
|---|---|---|
| Core search/fetch | `pydantic_ai.capabilities.WebSearch`, `WebFetch`; client-executed fallback | Not configured; local fetch extra required. Native-only restriction fields do not automatically constrain a custom local search. The smallest complete path in the framework check |
| Tavily MCP | `tavily_search`, `tavily_extract` through `MCPToolset` | Domain and country controls in its schema; vendor key, terms, and egress. The MCP assessment's first pick |
| Exa | `pydantic_ai_harness.exa.ExaSearch`, `ExaAgent`; `web_search`, `get_page`, optional `deep_search` | Capability source exists; SDK/key absent. Structured sources; enforce fetch restrictions separately |
| You.com | `pydantic_ai_harness.youdotcom.YouSearch`, `YouResearch`; includes `finance_research` | Capability source exists; SDK/key absent. A finance-labeled synthesis is external evidence, not engine truth |
| Direct search API | Brave or Parallel documented endpoints | No selected adapter; compare on the same questions |
| Packaged research harness | `pydantic_ai_harness.researcher.Researcher` | Not a drop-in: default workspace/spill state needs a database-backed persistence review |

Select by measurement: the same 20 Indian official-source questions, scored for the owning source,
effective dates, cost per answered question, and failure behavior. Every route must execute inside
Arno so its returns are traced and marked. Sources: [core
search](https://pydantic.dev/docs/ai/capabilities/web-search/), [core
fetch](https://pydantic.dev/docs/ai/capabilities/web-fetch/),
[Exa](https://pydantic.dev/docs/ai/harness/exa-search/),
[You.com](https://pydantic.dev/docs/ai/harness/youdotcom/),
[Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search),
[Brave](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started), and
[Parallel source policy](https://docs.parallel.ai/resources/source-policy). Publication filters do
not determine a rule's statutory effective date.

### T03: External MCP servers

The [server assessment](../research/personal-finance-agent-2026/external-mcp-servers.md) records
endpoints, authentication, alternatives, and completion checks; the [India
evidence](../research/personal-finance-agent-2026/external-mcp-india.md) covers the personal-data
routes. On November 15 the selection changed: research waits on the T02 measurement, holdings start
with the CAS, and four servers are not selected.

The following table maps the concrete servers to their role in Arno.

| Server | Tools/data to use | Selection |
|---|---|---|
| [Tavily](https://docs.tavily.com/documentation/mcp) | `tavily_search`, `tavily_extract` | C05, C15, C17; selected only if it beats the built-in tools on the T02 measurement |
| [Exa](https://exa.ai/docs/get-started/exa-mcp) | `web_search_exa`, `web_fetch_exa`, optional `web_search_advanced_exa` | C17; alternative in the same measurement |
| [Parallel](https://docs.parallel.ai/integrations/mcp/quickstart) | `web_search`, `web_fetch`, optional authenticated `web_research` | C17; alternative in the same measurement |
| [Brave Search](https://github.com/brave/brave-search-mcp-server) | `brave_web_search`, `brave_news_search` | C17; locally operated alternative with a separate page-reading path |
| [Kite](https://github.com/zerodha/kite-mcp-server) | `get_holdings`, `get_mf_holdings`, `get_quotes`, `get_historical_data` | C12, C13; after the CAS, for a Zerodha user who needs fresher holdings; block order and GTT tools at the client |
| [Upstox](https://upstox.com/developer/api-documentation/mcp-integration/) | Holdings, mutual funds, funds, positions, and profile reads; verify deployed names | C12, C19; after the CAS, for an Upstox user; daily reauthorization |
| [Groww](https://groww.in/updates/groww-mcp) | Stock holdings and market reads; verify deployed names | C12, C19; after the CAS, for a Groww user, with a strict read allowlist; published scope excludes mutual funds |
| [Fi Money](https://github.com/epiFi/mcp-docs) | `fetch_net_worth`, `fetch_bank_transactions`, `fetch_credit_report`, and other documented reads | Not selected: its login types a passcode into the conversation, which crosses the credential rule, and its continuity after Fi's banking transition is unconfirmed |
| [Alpha Vantage](https://mcp.alphavantage.co/) | `SYMBOL_SEARCH`, `GLOBAL_QUOTE`, `TIME_SERIES_DAILY`, `FX_DAILY` | Not selected: fund prices come from AMFI and equities from the CAS or broker; revisit for a concrete instrument gap |
| [Twelve Data](https://github.com/twelvedata/mcp) | `get_price`, `get_quote`, `get_time_series` | Not selected, for the same reason |
| [FMP](https://site.financialmodelingprep.com/developer/docs/mcp-server) | `quote`, `chart`, `company`, `statements` | Not selected: company research is outside a household ledger |

Connect only the selected tools through client-executed `MCPToolset` or the `MCP` capability, with
host-held auth and the C17/C21 controls. The existing [framework
verification](../research/personal-finance-agent-2026/verification-framework.md) records exact
Pydantic configuration. No live server connection or comparative quality test was run.

### T04: Document reading

The current reader uses PDFium, Calamine, Office decryption, and text/document readers; capture uses
configured vision and transcription services. Their presence does not prove every file layout or
spoken language works. Benchmark original synthetic fixtures, including corrupted and adversarial
inputs, before replacing a reader or expanding accepted document types, including C34's credit
report.

[Docling](https://github.com/docling-project/docling) exposes `DocumentConverter`; its code is MIT
but models and dependencies have separate licenses and resource costs. It is a layout/OCR candidate,
not an already selected dependency. Cloud OCR adds statement egress and needs explicit
configuration. Password guessing from identity attributes is not part of this catalog.

### T05: Financial calculations

Existing `amortize` and `compound` call tested application functions. Money remains whole paise;
rate solvers may legitimately use floating-point arithmetic with stated numerical tolerances.
Decimal-only execution is not a substitute for correct assumptions and tests. `amortize` has no
processing-fee input yet (C11).

The engine blueprint specifies XIRR as a bracketed bisection, returned only when the cash flows have
one sign change, which needs no dependency. [PyXIRR](https://github.com/Anexen/pyxirr) is an
Unlicense alternative; its [function reference](https://anexen.github.io/pyxirr/functions.html)
documents dated `xirr`/`xnpv`, day-count conventions, invalid-flow errors, and failed convergence.
Nonconventional flows can have multiple roots; neither search strategy proves uniqueness. CAGR,
fee-inclusive debt, retirement, and tax models still need explicit application contracts and
reference cases.

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

Not pursued with C24's external route. [Razorpay
reconciliation](https://razorpay.com/docs/api/settlements/fetch-recon/) exposes payment, refund,
adjustment, fee, tax, and settlement evidence, and its [webhook
guidance](https://razorpay.com/docs/webhooks/best-practices/) documents repeated delivery and
retries. [Stripe](https://docs.stripe.com/webhooks) documents public HTTPS ingress for registered
webhooks. A new public route is not implicit permission to expose Arno; a reviewed settlement file
through the import pipeline is the route if freelance income appears.

### T11: Reference FX

Parked with C23. [Frankfurter](https://frankfurter.dev/) exposes reference-rate APIs and provider
attribution; [ECB reference
rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html)
are informational, not transaction execution evidence. Preserve observation/source dates and the
actual bank-charged amount.

### T12: Serving Arno over MCP

For C31. The `mcp` 2.3.0 (MIT) and `fastmcp-slim` 4.0.0 (Apache-2.0) packages are locked as
transitive dependencies of the agent framework, and both include server support. The adapter wraps
the same functions in `tools/` that the agent and the REST API call (rule 3 in `plan.md` §4) and is
reached through the same tailnet path as the API. It needs its own token auth, because passkeys
don't work for an MCP client, and it exposes reads only. Making either package a direct dependency
needs `uv lock --check`, the license audit, and `THIRD_PARTY_NOTICES.md`.

### T13: Mailbox access

For C32. The standard library's `imaplib` reads a mailbox over IMAP with no new dependency;
providers differ on whether IMAP takes an app password or OAuth, so verify the owner's provider
before choosing. The credential is held on the host like the other deployment secrets, never in the
database rows the agent reads, the model context, or exports. Only matching attachments are kept.

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
cited. The [completion checklist](completion-checklist.md) retains the current live and human gates.
Any selected parked capability requires a separate approved change to `plan.md` §2 and §5.

## Dependencies

Establish C02–C04, C21, and C22 before trusting new financial decisions. C28 and C29 build on the
engine behind C08 and C09; C10 saves C29's inputs. C12 precedes C13; C14 combines C10, C12, and
C29. C17 supplies current rules and public terms to C05, C11, C15, and C16, while C05 supplies
reviewed contract terms to C11 and C16. C19 is required whenever an external connector is selected,
starting with C32 or a C12 broker route. C23–C27 need their own user trigger and scope decision.

Before selecting a service, record the actual user questions, country/instrument coverage,
auth/scopes, source/date quality, privacy, quotas/cost, failure behavior, and required data rights.
A live trial must use the authorized test account and synthetic fixtures; it is not implied by this
document.

Static workflow skills may guide financial review, debt comparison, scenario planning, document
extraction, or research. [On-demand
capabilities](https://pydantic.dev/docs/ai/capabilities/on-demand/) can defer specialist
instructions/tools, which keeps the prompt a short map as tools are added. Static shipped
instructions are code; mutable user memory and overflow files are application state. Skills do not
grant a tool, fix a missing data model, authorize a write, or make an unsupported answer correct.
