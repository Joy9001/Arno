# Capability catalog

This catalog owns unbuilt capability proposals beyond Arno's shipped product (Tiers 2–4 and
excluded Tier 5), their priority order, the rules for external capabilities, and the external tool
lookup; [`plan.md`](../plan.md) owns approved decisions and [`parked.md`](parked.md) owns parked
scope boundaries.

---

## How to read this catalog

Each entry summarizes a proposed capability, its user benefit, enabling tools, and acceptance
criteria. Status labels distinguish _partial_ (working internal foundations with narrower scope),
_proposed_ (new work), and _parked_ (deliberately excluded in `parked.md`). Nothing here approves a
scope change or selects a paid vendor: adopting a capability requires a numbered milestone in
`plan.md` §5, and reopening a parked item requires a `plan.md` §2 decision first. Portfolio
analysis never authorizes trading, tax estimates never authorize filing, and payment modeling never
executes payments.

**Shipped capabilities (Tiers 0 and 1):** Core capabilities C01–C04, C06, C07, C09, C11, C18–C22,
and C28–C30—including safe-to-spend, multi-event what-ifs, recurring price-change proposals, loan
fee APR and card minimum-due payoff, and the C20 projection workbook—shipped in L9 (`plan.md` §5,
v0.3.0–v0.4.0) and are specified in [`specs/`](../specs/data-and-engine.md). Remaining live owner
and device ship gates live in [`completion-checklist.md`](../completion-checklist.md). Two narrow
extensions to shipped capabilities remain open and depend on unbuilt proposals below: foreclosure
charges and contract-driven rate resets for C11 (waiting on C05), and per-connector ingestion
health for C19 (waiting on the first external connector such as C32 or a C12 broker).

The research basis stays in
[`docs/research/personal-finance-agent-2026/`](../../research/personal-finance-agent-2026/README.md)
(framework checks, finance libraries, external MCP server assessment, India data routes, and source
ledger).

---

## Priority order

Capabilities are ranked by `plan.md` §2's test: _Does Arno answer questions about money
incorrectly, or fail to warn the user, without it?_ Within each tier, implementation cost and
dependencies determine order. A tier is a recommendation, not an approved milestone.

The following table summarizes the unbuilt tiers in priority order.

| Tier | Meaning | Capabilities, in order |
|---|---|---|
| 2 | First external capabilities; require a `plan.md` §2 privacy and egress decision | C17, C05 |
| 3 | Reopen a parked decision or wait on daily use; promote on trigger | C08, C10, C32, C31 |
| 4 | Apply only when the owner's circumstances call for them | C12, C13, C14, C15, C16, C33, C34 |
| 5 | Kept parked or not pursued | C23, C24, C25, C26, C27 |

The following table indexes every unbuilt or conditional proposal.

| ID | Capability | Status | Tier | Depends on |
|---|---|---|---|---|
| [C05](#c05-financial-contract-understanding) | Financial contract understanding | Proposed beyond existing document readers | 2 | C17 (public issuer pages only) |
| [C17](#c17-current-financial-research-with-evidence) | Current financial research with evidence | Proposed | 2 | `plan.md` §2 external rules |
| [C08](#c08-cash-flow-and-account-funding-readiness) | Cash flow and account funding readiness | Partial (pooled forecast shipped; per-account parked) | 3 | `parked.md` trigger |
| [C10](#c10-saved-scenarios) | Saved scenarios | Proposed | 3 | Shipped multi-event engine |
| [C31](#c31-arno-as-an-mcp-server) | Arno as an MCP server | Parked | 3 | `parked.md` trigger |
| [C32](#c32-statement-collection-from-email) | Statement collection from email | Parked | 3 | `parked.md` trigger |
| [C12](#c12-holdings-and-dated-net-worth) | Holdings and dated net worth | Partial (manual assets shipped; CAS parked) | 4 | `parked.md` trigger |
| [C13](#c13-investment-performance-and-benchmarks) | Investment performance and benchmarks | Proposed (XIRR specified, not built) | 4 | C12 |
| [C14](#c14-retirement-and-life-event-planning) | Retirement and life-event planning | Proposed beyond `compound` and goals | 4 | C10, C12 |
| [C15](#c15-india-tax-estimates-and-obligation-calendar) | India tax estimates and obligation calendar | Parked | 4 | `parked.md` trigger, C17 |
| [C16](#c16-emergency-reserves-and-financial-protection) | Emergency reserves and financial protection | Partial (runway and schedules shipped) | 4 | C05 |
| [C33](#c33-settle-up-reminders) | Settle-up reminders | Partial (`v_owed` balances shipped) | 4 | |
| [C34](#c34-credit-report-as-liability-evidence) | Credit report as liability evidence | Proposed | 4 | C05 pattern |
| [C23](#tier-5-parked-or-excluded-capabilities) | Foreign-currency valuation | Parked | 5 | `parked.md` trigger |
| [C24](#tier-5-parked-or-excluded-capabilities) | Freelancer receivables and net payouts | External not pursued; internal rides on C15 | 5 | C15 trigger |
| [C25](#tier-5-parked-or-excluded-capabilities) | Merchant identity enrichment | External parked (internal rules shipped) | 5 | Measured miss rate |
| [C26](#tier-5-parked-or-excluded-capabilities) | Additional notification and approval channels | Parked | 5 | `parked.md` trigger |
| [C27](#tier-5-parked-or-excluded-capabilities) | Business-system and accountant handoff | Not pursued | 5 | Separate scope decision |

- **Tier 2:** C17 introduces outbound web research, which changes `plan.md` §2's privacy statement
  and triggers the parked egress proxy. C05's uploaded-document path needs no network and can
  precede C17; its public-terms path needs C17. C05 also feeds real fees and reset rules into C11.
- **Tier 3:** Each item waits for its operational trigger during daily use rather than speculative
  planning. C08 ranks first in this tier because an underfunded autodebit account causes a real
  missed-warning failure.
- **Tier 4:** Investment holdings (C12), performance (C13), and retirement (C14) wait on the owner
  holding investments; tax (C15) waits on freelance income; deposit-insurance and policy review
  (C16) wait on deposits exceeding DICGC limits at one bank or a policy to review; C33 and C34 are
  small standalone additions.
- **Tier 5:** Kept parked or out of scope for a single-user personal finance ledger.

---

## Proposed capabilities

### C05: Financial contract understanding

_Tier 2. Proposed beyond existing document readers._

Turn uploaded or retrieved contract passages—credit card terms, loan _Key Facts Statements_
(_KFS_), deposit conditions, and insurance policies—into structured, dated terms linked to source
passages. Retain ambiguous clauses for user review instead of filling gaps from generic product
knowledge. Start with card terms, one loan KFS, and one insurance policy.

- **User benefit:** Surfaces fees, prepayment clauses, rate resets, and policy exclusions before a
  decision, and supplies real terms to debt and protection calculations.
- **Enabling shape:** Contract schemas, passage-level provenance, version history, and review cards
  over existing readers in `src/arno/importers/readers.py`. Uploaded files need no network; public
  issuer pages need C17. Docling stays optional ([T04](#external-tool-registry)).
- **Acceptance criteria:** Every material rate, fee, date, and exception links to its document
  location; missing or conflicting clauses stay unresolved until reviewed; revised terms preserve
  prior versions and flag affected calculations; untrusted document text cannot authorize writes.

### C17: Current financial research with evidence

_Tier 2. Proposed._

Retrieve current external financial facts (RBI rules, deposit rates, issuer fees) while preserving
auditable source metadata, separating publication dates from statutory effective dates, and keeping
external findings distinct from personal ledger records.

- **User benefit:** Answers changing factual questions that neither the ledger nor model weights
  can verify.
- **Enabling shape:** Source records, citations, private-query minimization, outside-text marking,
  and number provenance over native spend budgets. Candidate backends—Pydantic AI
  `WebSearch(local=True)` / `WebFetch(local=True)` versus MCP research servers such as Tavily or
  Exa ([T02–T03](#external-tool-registry))—must be scored on the same 20 Indian official-source
  questions for source quality, dates, cost, and failure handling. Search that executes inside Arno
  returns traceable results; provider-executed search is invisible to Arno's own tools. The agent
  already searches through the model's native tool or Brave ([`specs/tools.md`](../specs/tools.md#web_search)).
- **Acceptance criteria:** Answers cite passages and effective periods (marking unknown dates
  explicitly); source records survive restore; private data never enters queries or URLs and
  private-IP fetches or unsafe redirects are blocked; retrieval failures or injection never cause
  guessed facts or writes; evals enforce the fact-versus-advice boundary.

### C08: Cash flow and account funding readiness

_Tier 3. Partial (pooled forecast shipped; per-account forecast parked in
[`parked.md`](parked.md#projection-for-each-account))._

Extend the projection engine beyond pooled liquid cash to check whether the specific account
funding each autodebit, EMI, or card bill holds enough cash on the debit date.

- **User benefit:** Warns of an autodebit shortfall even when total liquid cash across all accounts
  is positive.
- **Enabling shape:** Account routing, transfer timing, and per-account feasibility layered over
  `src/arno/engine/projection.py` and `src/arno/tools/projection.py`. Modeling a funding transfer
  never executes it.
- **Acceptance criteria:** Scheduled and variable flows reconcile without double-counting; a short
  EMI account with positive pooled cash reports a named account and date shortfall; unknown
  balances or inaccessible assets never imply payment readiness; identical inputs and seed remain
  deterministic.

### C10: Saved scenarios

_Tier 3. Proposed. Depends on the shipped multi-event engine._

Persist a named set of multi-event projection changes with immutable input snapshots so users can
reopen comparisons across conversations and compare original results against a refresh on current
ledger data.

- **User benefit:** Supports major multi-conversation decisions (job switch, move, loan) without
  rebuilding assumptions each time.
- **Enabling shape:** PostgreSQL scenario storage, immutable baseline snapshots, and a runner over
  `src/arno/engine/scenario.py`. Trigger: the same multi-event comparison is rebuilt in a second
  conversation.
- **Acceptance criteria:** Reopening preserves input dates, overrides, seed, calculation version,
  and original output; refreshing after ledger edits shows deltas without silently overwriting the
  original; stress cases never invent probabilities for unmodeled events; scenarios survive backup
  and restore.

### C31: Arno as an MCP server

_Tier 3. Parked ([`parked.md`](parked.md#mcp-server))._

Expose Arno's typed read tools over the _Model Context Protocol_ (_MCP_) to user-run desktop
assistants or other agents so they can query balances, spending, and engine-computed affordability
verdicts without duplicating ledger data. Read-only.

- **User benefit:** Answers financial questions inside another assistant the user already has open.
- **Enabling shape:** Thin MCP adapter over `src/arno/tools/` using `mcp` or `fastmcp-slim`
  ([T12](#external-tool-registry)), tailnet-only, authenticated with a scoped, expiring, revocable
  token created in **Settings** behind a fresh passkey. Start with typed reads; defer `run_query`.
- **Acceptance criteria:** Write tools are unreachable and rejected server-side; tokens are scoped
  read-only, revocable, and excluded from logs and exports; returns preserve outside-text markers,
  as-of dates, source IDs, and zero-versus-unknown semantics; access stays tailnet-only.

### C32: Statement collection from email

_Tier 3. Parked ([`parked.md`](parked.md#sms-and-email-autocapture))._

Collect bank statement and CAS attachments from a designated mailbox folder or sender allowlist on
a schedule and feed each file into the existing import pipeline, which still requires user review
before commit.

- **User benefit:** Eliminates monthly statement downloads and manual uploads while keeping review
  control intact.
- **Enabling shape:** Scheduled job using Python's `imaplib` or provider API
  ([T13](#external-tool-registry)) with host-held credentials, keeping only matching attachments.
  Email metadata and bodies are untrusted data; outbound network access invokes external
  capability rules.
- **Acceptance criteria:** Reads only the configured folder or senders and never sends mail;
  deduplicates attachments by content hash; keeps credentials out of model context, logs, and
  exports; surfaces fetch failures with last-success age (C19); routes password-protected files to
  the import card.

### C12: Holdings and dated net worth

_Tier 4. Partial (manual asset accounts shipped; investment statements parked in
[`parked.md`](parked.md#net-worth-from-investment-statements))._

Track investment holdings and liabilities with separate unit quantities, dated valuations, and
coverage reporting, distinguishing ownership evidence from market quotes and illiquid assets from
spendable cash.

- **User benefit:** Shows scoped net worth and issuer or asset-class concentration without treating
  investments as ready cash.
- **Enabling shape:** Holdings, instrument identities, unit quantities, cost records, and valuation
  snapshots. Start with monthly CAS PDFs via CASParser plus AMFI daily NAV
  ([T06](#external-tool-registry)); optional broker MCPs such as Kite, Upstox, or Groww
  ([T03](#external-tool-registry)) come only if holdings must be fresher than monthly CAS.
- **Acceptance criteria:** Statement quantities reconcile with transactions before valuation; units,
  price dates, ownership dates, and currency stay visible; transfers, corporate actions, and
  unknown assets do not distort totals; concentration reports disclose grouping and missing
  holdings; parser and data-use rights are verified.

### C13: Investment performance and benchmarks

_Tier 4. Proposed. Depends on C12._

Compute money-weighted returns (_XIRR_) and endpoint growth from dated investment cash flows,
separating contributions, withdrawals, fees, taxes, and price changes, with optional matched-period
benchmark comparisons.

- **User benefit:** Separates savings growth from investment performance without misleading single-
  endpoint figures.
- **Enabling shape:** Deterministic bracketed-bisection XIRR helper (returning a value only when cash
  flows have one sign change) or a host wrapper around `pyxirr` ([T05](#external-tool-registry)).
- **Acceptance criteria:** Flow signs, dates, terminal valuation, day-count convention, and fee
  treatment are explicit; missing history, non-convergence, and multiple roots are reported
  honestly; benchmarks match cash flows, periods, currency, and distributions; CAGR never replaces
  cash-flow returns.

### C14: Retirement and life-event planning

_Tier 4. Proposed beyond `compound` and goals. Depends on C10 and C12._

Model long-horizon cash flows and withdrawals across retirement ages, career breaks, or education
costs using transparent ranges for inflation, income changes, access restrictions, and taxes.

- **User benefit:** Shows how long-term tradeoffs affect resilience without treating smooth
  compound-growth curves as guaranteed outcomes.
- **Enabling shape:** Long-horizon cash-flow and withdrawal engine integrated with saved scenarios
  (C10) and holdings (C12); sourced inflation or scheme rules come through C17.
- **Acceptance criteria:** Inflation, longevity, return, tax, and liquidity assumptions stay
  visible; nominal and real currency stay consistent; sensitivity ranges avoid unsupported success
  probabilities; saved comparisons reopen reproducibly and state professional-advice limits.

### C15: India tax estimates and obligation calendar

_Tier 4. Parked ([`parked.md`](parked.md#tax-engine)). Depends on C17._

Estimate India tax liabilities and surface advance-tax or filing dates from dated statutory rules,
separating confirmed receipts, expected income, TDS withholding, and tax already paid.

- **User benefit:** Prevents surprise tax cash outflows and wrong-assessment-year calculations.
- **Enabling shape:** Versioned deterministic tax rules and calendar over user-supplied salary,
  TDS, or AIS records, with statutory sources retrieved via C17 ([T09](#external-tool-registry)).
  No portal passwords, filing automation, or general tax MCP.
- **Acceptance criteria:** Every calculation binds an assessment year, rule version, and user
  classification; withheld, paid, and estimated figures remain distinct; missing rules produce
  scoped explanations rather than invented slabs; official boundary examples pass.

### C16: Emergency reserves and financial protection

_Tier 4. Partial (emergency runway and premium schedules shipped). Depends on C05._

Inventory insurance policies (coverage, renewals, exclusions, nominees, and document locations) and
assess bank deposit-insurance exposure under _Deposit Insurance and Credit Guarantee Corporation_
(_DICGC_) ownership and capacity rules.

- **User benefit:** Exposes practical protection gaps, missing nominees, or uninsured deposits
  above ₹5 lakh per depositor per bank in the same right and capacity.
- **Enabling shape:** Policy inventory and ownership-aware deposit aggregation using reviewed C05
  extractions plus DICGC and IRDAI reference material ([T09](#external-tool-registry)).
- **Acceptance criteria:** Runway uses essential obligations and accessible cash; deposit checks
  group by insured bank and depositor right/capacity; policy exclusions, dates, and nominee gaps
  stay visible and editable; no invented payout or coverage-adequacy verdict is issued.

### C33: Settle-up reminders

_Tier 4. Partial (`v_owed` balances shipped)._

List open lending and split balances with their ages and draft a short reminder message—optionally
including a `upi://pay` link built from the owner's configured UPI ID—for the user to share
manually.

- **User benefit:** Simplifies settling shared expenses without manual tallying.
- **Enabling shape:** Add oldest unsettled leg age to `v_owed` and a draft/UPI-link generator. Arno
  never sends messages or initiates payments; settlements are recorded only when reported.
- **Acceptance criteria:** Amounts and ages match the ledger and settled people are omitted; link
  amounts match the full or chosen partial balance; nothing is sent and no settlement writes
  automatically.

### C34: Credit report as liability evidence

_Tier 4. Proposed. Follows the C05 pattern._

Parse a user-downloaded bureau credit report and reconcile its accounts, limits, and balances
against the ledger, flagging unknown accounts or stale liability balances.

- **User benefit:** Catches forgotten cards, unrecorded loans, or unauthorized accounts.
- **Enabling shape:** One additional document reader and extraction schema in the C05 pattern. No
  external bureau API or Fi Money passcode flow.
- **Acceptance criteria:** Report balances appear as dated evidence and never overwrite stated or
  statement balances; unknown accounts are proposed via confirm cards; identity fields (PAN, date
  of birth, address) are stripped at import (`plan.md` §3).

### Tier 5: Parked or excluded capabilities

- **C23 (Foreign-currency valuation):** Keep original currency, base INR, effective date, reference
  rate ([T11](#external-tool-registry)), and bank-charged conversion separate; parked in
  [`parked.md`](parked.md#multicurrency).
- **C24 (Freelancer receivables and net payouts):** Invoice aging and gross-to-net reconciliation
  ride on C15's freelance trigger; payment-gateway webhooks ([T10](#external-tool-registry)) are
  not pursued because Arno stays off the public internet.
- **C25 (Merchant identity enrichment):** Internal seed rules and categorizer rules handle Indian
  UPI payees; external enrichment (such as US/Canada-only Plaid Enrich) is parked unless a measured
  miss rate justifies it.
- **C26 (Additional notification and approval channels):** Inbox and web push exist; chat-app
  bridges are parked in [`parked.md`](parked.md#chat-app-integrations-signal-telegram-whatsapp) and
  may be superseded by C31.
- **C27 (Business-system and accountant handoff):** Business accounting sync (Tally, Xero,
  QuickBooks, Zoho) and GST/LEI checks are outside personal finance scope; JSON, CSV, and workbook
  exports already cover accountant handoff.

---

## Rules for external capabilities

Any capability that makes outbound network calls or exposes Arno to external clients must satisfy
these rules, with any scope change recorded in [`plan.md`](../plan.md) first:

- **Privacy statement:** `plan.md` §2 limits egress to configured model endpoints, encrypted
  backups, and contentless push pings. C17, C32, C12 broker routes, and C31 each require updating
  that boundary.
- **Egress proxy:** The first network-reaching tool or bridge triggers the parked
  [egress proxy](parked.md#egress-proxy) review.
- **No bank APIs:** Direct bank or broker aggregation (including broker MCPs) remains excluded
  until a specific route is approved in `plan.md` §2.
- **Outside text:** Web pages, MCP tool returns, and emails are untrusted data. `plan.md` §4 rule 2
  still requires confirm cards for wide writes.
- **Number provenance:** External figures must arrive through Arno-executed tool returns so the agent
  can cite them; provider-side search is not traced.
- **Credentials:** No password, OTP, or API token ever enters chat (`AGENTS.md` architecture rule
  6). Host secrets stay in environment variables or host-only storage.
- **Advice boundary and shared gates:** Every changed boundary adds eval cases. New dependencies
  must pass a manual permissive-license check and update `THIRD_PARTY_NOTICES.md`. Every selected
  capability must preserve monetary units, source IDs, truthful failure states, and backup/restore
  round-trips.

---

## External tool registry

Being documented or importable does not mean a tool is selected or tested. Candidate services are
alternatives to benchmark rather than a shopping list. Internal ledger and engine tools are
specified in [`specs/tools.md`](../specs/tools.md).

The following table maps external tool areas to researched candidates and constraints.

| ID | Area | Candidates and interfaces | Role and key constraint |
|---|---|---|---|
| T02 | External research | Pydantic AI `WebSearch(local=True)` / `WebFetch(local=True)`, `ddgs`, Exa/You.com harness capabilities, Brave/Parallel APIs | C17. Benchmark local built-ins against MCP servers on 20 Indian official-source questions; execute inside Arno so returns are traced. |
| T03 | External MCP servers | Research: Tavily, Exa, Parallel, Brave Search. Brokers (after CAS): Kite (`get_holdings`, `get_mf_holdings`, `get_quotes`), Upstox, Groww | C05, C12, C13, C15, C17, C19. Connect read-only tools via `MCPToolset` with host-held auth and client-side write blocks; see [MCP assessment](../../research/personal-finance-agent-2026/external-mcp-servers.md) and [India evidence](../../research/personal-finance-agent-2026/external-mcp-india.md). |
| T04 | Document reading | Existing PDFium/Calamine/vision readers; optional Docling (`DocumentConverter`, MIT code with separate model licenses) | C05, C34. Benchmark synthetic corrupted and adversarial layouts before adding OCR or layout dependencies; no identity-based password guessing. |
| T05 | Financial calculations | Bracketed-bisection XIRR (no dependency) or `pyxirr` (Unlicense, host wrapper) | C13. Handle non-convergence and multiple roots explicitly; never import third-party packages into the sandbox without adding them to its image. |
| T06 | Holdings and valuations | `casparser` (`read_cas_pdf`, MIT) and AMFI daily NAV history | C12. Verify transitive licenses and AMFI terms of use; preserve decimal unit quantities; NAV dates never refresh stale ownership evidence. |
| T07 | Account access | RBI Account Aggregator (FIU-regulated only; Sahamati) and broker OAuth (for example, Kite Connect v3) | C12, C19. Unregulated self-hosters do not qualify as direct AA FIUs; broker tokens stay host-held and read-scoped. |
| T08 | Spreadsheets and cloud handoff | Shipped XlsxWriter workbook; optional Google Sheets API or Microsoft Graph Excel (business storage only) | Optional cloud export beyond shipped `.xlsx`. Require explicit destination and user consent; always write verified cached formula values. |
| T09 | Regulatory and policy evidence | RBI KFS circular, RBI credit-card directions, Income Tax Department guidance, IRDAI material, DICGC guide | C05, C15, C16. Store source version and statutory effective date with every derived rule. |
| T10 | Settlements and webhooks | Razorpay reconciliation, Stripe webhooks | Not pursued (C24). Webhooks require public HTTPS ingress; use reviewed settlement files instead if freelance income appears. |
| T11 | Reference FX | Frankfurter API, ECB reference rates | Parked (C23). Reference rates are informational only and never overwrite bank-charged INR amounts. |
| T12 | Serving Arno over MCP | `mcp` (MIT) and `fastmcp-slim` (Apache-2.0), already present as transitive dependencies | C31. Expose read-only wrappers over `src/arno/tools/` over Tailscale with token auth; promoting to direct dependency requires a license check and `THIRD_PARTY_NOTICES.md`. |
| T13 | Mailbox access | Python stdlib `imaplib` or mail provider API | C32. Host-held app password or OAuth credential; read one folder or sender allowlist and retain matching attachments only. |

**Explicitly excluded MCP servers:** Fi Money (login types a passcode into chat, violating the
credential rule, and banking continuity is unconfirmed), Alpha Vantage and Twelve Data (AMFI and
CAS/broker routes already cover Indian funds and equities), and Financial Modeling Prep (equity
research is outside a household ledger).

---

## Dependencies

Shipped foundation layers (C02–C04, C21, C22) remain prerequisites for trusting any new financial
decision. Among unbuilt capabilities:

- **Research and contracts:** C17 supplies current official rules and public issuer terms to C05,
  C11, C15, and C16; C05 supplies reviewed contract terms to C11 (foreclosure charges and rate
  resets) and C16 (policy terms).
- **Scenarios and wealth:** C10 persists inputs for the shipped multi-event engine; C12 precedes
  C13; C14 combines C10, C12, and the multi-event engine.
- **Connectors:** Every external ingestion connector (starting with C32 or a C12 broker) must
  satisfy C19's idempotency, credential-expiry, and visible-gap checks.

Before selecting any external service, record target questions, Indian coverage, scopes, date
quality, privacy terms, cost, and failure behavior, and test against authorized sandbox accounts
with synthetic fixtures. As additional tools arrive, use Pydantic AI
[on-demand capabilities](https://pydantic.dev/docs/ai/capabilities/on-demand/) to defer specialist
instructions and keep the core prompt map compact.
