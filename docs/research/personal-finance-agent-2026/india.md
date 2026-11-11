# Personal finance capabilities for an Indian self-hoster

This survey asks what a best personal finance agent for an Indian self-hoster must do, and which
official sources it can reach. It uses primary sources only: regulator directions, circulars,
department help pages, and issuer or depository documentation. Every link was opened on November 11,
2026 unless the ledger marks it otherwise. Parent review on November 12 checked the principal
sources and narrowed conclusions about eligibility, coverage, and data rights. This is a reference
survey, not a decision record; [`plan.md`](../../arno/plan.md) still owns product decisions.

## Direct Account Aggregator participation is a regulated-entity path

RBI defines a _Financial Information User_ (_FIU_) as an entity registered with and regulated by a
financial-sector regulator. Its Account Aggregator directions specify consent artifacts and
revocation, and restrict use or disclosure to the consented purpose
([RBI directions](https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=10598)). From this,
I infer that an ordinary unregulated self-hosted app does not qualify as a direct FIU. This is not
a claim that every partner arrangement is illegal or that every possible bank API is unavailable.

Sahamati documents regulated-entity onboarding, technical specifications, certification, and
registry participation ([FIU onboarding](https://sahamati.org.in/financial-information-user-fiu)).
A regulated partner or user-mediated export needs its own feasibility, consent, contractual, and
data-access review; this survey did not establish a generic turnkey route for Arno.

The reviewed coverage pages emphasize deposits and investments. They do not establish complete
credit-card dues or loan-payment coverage. Absence from those pages is not proof of permanent
exclusion from every provider. Verify exact data types, institutions, history, and refresh behavior
before relying on a partner. Statements remain a practical baseline for Arno, especially for
liabilities. A hosted or multiuser deployment alone would not confer regulated eligibility.

## What an Indian household needs the agent to do

Each row names the job, what the agent must hold or compute, and the limit the official source
imposes.

| Job | What the agent must do | Source-verified limit |
|---|---|---|
| Salary and TDS | Net pay and deduction breakdown from the payslip; TDS credit; reconcile the annual certificate against AIS | Employer controls timing and arrears; Form 16 and AIS are the cross-checks |
| Variable income | Separate expected income, confirmed income, and estimates of annual tax liability | Advance-tax applicability, installments, and exemptions need current-year rules |
| Credit cards | Total due, minimum due, revolving cost, all-in EMI cost | Interest-free period survives only if the whole outstanding is paid |
| Loans | Rank by APR, amortize, answer prepayment from a schedule | KFS with APR and amortization is mandatory for new retail term loans |
| Reserve and insurance | Months of essential spend; deposit exposure per bank; policy inventory | DICGC limits depend on insured bank, depositor, capacity, and right |
| Tax calendar and regime | Dated obligations, set-aside estimate, regime scenario | New regime is default; the department's estimator compares both |
| Investments | Units, daily valuation, an as-of date on every balance | CAS is monthly with a transaction, otherwise half-yearly |

**Cards: the minimum due is not the bill.** RBI defines the total amount due as the net payable at
the end of the cycle, the minimum amount due as what avoids an overdue bill, and the interest-free
period as available only when the entire outstanding is paid by the due date
([RBI, Credit and Debit Card Directions](https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx?id=12300)).
Paying the minimum buys time and forfeits the interest-free period. The same direction requires an
EMI conversion to disclose principal, interest, and any merchant discount, and forbids presenting an
interest-bearing EMI as zero-interest or no-cost
([RBI, Credit and Debit Card Directions](https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx?id=12300)).

**Loans: APR, not the headline rate.** The Key Facts Statement direction defines APR as the annual
cost including interest and all other charges, requires a computation sheet and amortization
schedule, and applies to new retail and MSME term loans sanctioned from October 1, 2024; card
receivables are exempt
([RBI, KFS for Loans and Advances](https://rbi.org.in/Scripts/NotificationUser.aspx?Id=12663)). The
exemption is why card cost needs its own model. Two offers with the same rate and different fees are
not the same loan.

**Tax: period-aware research and deterministic estimates.** Arno should determine which rules,
income classification, withholding, exemptions, and deadlines apply before calculating a reserve.
The department provides an [estimator](https://www.incometax.gov.in/iec/foportal/income-tax-estimator),
but an external estimator does not replace a reproducible calculation inside Arno when the user
needs one. No current-year slab or deadline table is certified by this report.

The department's [transition FAQ](https://www.incometax.gov.in/iec/foportal/help/all-topics/e-filing-services/objective-and-scope-new-act-faq?mobile-app=1)
distinguishes earlier years under the 1961 Act from Tax Year 2026-27 under the 2025 Act. Earlier
proceedings continue under transition rules. Effective dates and applicability belong with each
rule, not in an undated prompt. Tax estimates can depend on expected annual income; they must not
treat that expectation as cash received. A cash reserve can be funded from actual receipts while
remaining labeled as an estimate.

**Investments: units and prices have separate dates.** SEBI's consolidated account statement joins
mutual fund folios and demat holdings by PAN, arrives monthly when there is a transaction and
half-yearly otherwise, and has been emailed by default since August 2024
([SEBI, CAS circular 2014](https://www.sebi.gov.in/sebi_data/attachdocs/1415783332661.pdf);
[SEBI, CAS email default 2024](https://www.sebi.gov.in/sebi_data/attachdocs/jul-2024/1719887551143.pdf)).
A quiet portfolio can therefore carry units six months old, and a daily NAV fixes only the price.
The depository FAQ describes a demat-only mutual fund case where a CAS is not generated and a
depository participant statement is sent instead. This is not absence of holdings statements
([CDSL, CAS FAQ](https://www.cdslindia.com/cas/faq.html)). Provident fund and pension balances come
from separate logins
([EPFO, member passbook](https://passbook.epfindia.gov.in/MemberPassBook/login);
[NPS Trust, subscriber login](https://npstrust.org.in/subscribers-login)), each with its own as-of
date. This survey did not establish a general self-hosted API for every provident fund, fixed
deposit, or PPF record; statements and hand-entered balances remain fallback methods.

**Reserve and insurance: check per bank, not per account.** DICGC insures ₹5 lakh for each depositor
at an insured bank for deposits in the same capacity and right, principal and interest. Branches
of one bank aggregate; separate banks are treated separately. This is deposit insurance, not
protection for market losses
([DICGC, guide to deposit insurance](https://www.dicgc.org.in/guide-to-deposit-insurance)). Insurance
policies can be inventoried from documents and premium debits; a general authenticated API for
every user's policies was not verified here.

## Practical data sources and what each costs

The table separates public queries, user uploads, authenticated read-only access, regulated
integration, and scraping. "Reach" means who in India can use it.

| Source | Coverage | Access | Reach | Privacy issue | License or terms | Recommendation |
|---|---|---|---|---|---|---|
| Bank and card statements | Transactions, balances, total and minimum due | User download and upload | Any bank or card | File password stays out of chat | User's own data; portal terms | Now |
| CAS from CAMS or KFintech | Fund folios, cost, NAV at statement date | Email request, then PDF | PAN and email | PAN and contact details in the PDF | Issuer terms; parser is MIT | Now, with holdings |
| NSDL or CDSL demat CAS | Demat holdings, including equities, bonds, and funds | Portal and OTP, or PDF | PAN plus demat account | PAN is often the PDF password | Issuer terms | Later |
| AMFI NAV | Published scheme valuations | Public download | Public access | Holdings queries can reveal interests | Personal-use and storage restrictions; integration rights unresolved | Candidate after terms review |
| AIS JSON or PDF | TDS, TCS, SFT, tax payments, refunds | User login, then download | Eligible taxpayer | Sensitive identifiers; password only in import field | Department terms; own data | Conditional tax module |
| MF Central | All folios plus service requests | Login and OTP | PAN holder | Account login | Terms page did not load | Later |
| EPFO passbook | PF contributions, interest, balance | UAN, password, captcha | UAN holder | Member credentials | No public API documented | Later, manual |
| NPS CRA statement | Holdings by scheme and tier | PRAN, password, date of birth, OTP | PRAN holder | Member credentials | NPS Trust does not store it | Later, manual |
| Kite Connect | Zerodha holdings and funds | App key plus daily login | Zerodha clients | Token dies at 6 a.m.; secret server-side | Free personal tier; ₹500 per month Connect | Later |
| Account Aggregator | Provider and data-type coverage must be checked | Direct FIU requires regulated eligibility | Eligible entities or reviewed partner path | Purpose-bound consent | Specifications, certification, and contracts | Not a generic self-hosted connector |
| Net-banking scraping | Everything | Stored password | Any bank | Severe; defeats the AA design | No sanctioned access path | Never |

Three licensing questions are separate: the parser's code license, the statement's terms, and the
data feed's terms. `casparser` is MIT and parses original CAS PDFs from CAMS, KFintech, NSDL, and
CDSL, but rejects re-printed PDFs, does not support MF Central, and reports parse warnings when a
scheme does not reconcile ([casparser](https://github.com/codereverser/casparser)). AMFI grants
personal, non-commercial use and forbids storing a significant portion of the site or creating
derivative works ([AMFI, terms of use](https://www.amfiindia.com/terms-of-use)); a daily fetch of the
whole `NAVAll.txt` by a distributed app is not clearly authorized by those general terms. Limiting
retrieval to held schemes can reduce data volume but does not itself establish permission.

Kite's token dies at 6 a.m., and long-lived refresh tokens are limited to approved platforms
([Kite Connect, user and token](https://kite.trade/docs/connect/v3/user/);
[Zerodha, Kite Connect pricing](https://zerodha.com/products/api/)). Do not screen-scrape net
banking; the AA design avoids credential sharing. Do not scrape generic market data for Indian
funds: AMFI publishes the official file.

## Six must-have outcomes

These are outcomes, not features. Each has an acceptance scenario against seeded data.

1. **A spending verdict from sufficient, dated coverage.** With rent, an EMI, and a SIP scheduled,
   an unknown starting bank balance is identified and unsupported total-cash probabilities are
   withheld. A merely old statement is distinguished from an entirely unknown balance.
2. **The true cost of a card balance.** Given a total due, minimum due, and EMI conversion offer,
   the agent compares repayment paths using the actual terms. It includes supported interest,
   fees, taxes on fees, and discounts; missing terms block a complete cost claim. A discount that
   offsets interest alone does not establish zero total cost.
3. **The true cost of a loan, and the value of prepaying.** Two offers with the same rate and
   different fees show their APR and total repayment. A prepayment answer uses a schedule;
   interest and principal appear as separate legs.
4. **A tax calendar that never invents law.** A new freelance income stream prompts an applicability
   review, retrieves the current official rules, and explains the basis of a tax-reserve estimate.
   Uncollected income remains separate from available cash; applicable future obligations still
   account for supported annual-income estimates and exemptions.
5. **A net worth in which every number carries its age.** A March CAS, today's NAV, a June EPF
   passbook, and a hand-entered NPS balance produce four as-of dates; units and prices are separate;
   the agent warns when a CAS is older than the last known transaction.
6. **A real answer to "am I safe if income stops."** Multiple deposits at one insured bank are
   aggregated using depositor ownership, capacity, and right before estimating insurance exposure.
   Emergency runway accounts for access restrictions and essential obligations; the policy
   inventory identifies cover, renewal dates, and unverified nominee information.

## Four optional capabilities and their triggers

- **Daily holdings through Kite Connect Personal.** Free, Zerodha-only, re-authenticated every
  morning. Revisit when CAS staleness actually changes a verdict.
- **Account Aggregator integration.** Revisit after an eligible regulated integration or partner
  arrangement is established, with verified liability coverage and consent. Hosting or multiuser
  support alone does not establish eligibility.
- **An AIS discrepancy finder.** AIS exposes SFT and TDS entries a user may have forgotten
  ([Income Tax Department, AIS](https://www.incometaxindia.gov.in/annual-information-statement)).
  Revisit when the first return is prepared with Arno's help.
- **Insurance repository pull.** Policies are an AA data type, but no personal read path is
  documented. Revisit when policies are numerous or a renewal is missed.

## Current Arno exclusions, separately from these recommendations

The parked list is mostly right, but two entries rest on weaker reasoning than the evidence
supports, and two gaps are absent from it.

| Parked item | What the evidence adds | Recommendation |
|---|---|---|
| No bank APIs | Direct FIU eligibility is restricted; exact provider coverage and partner paths need review | Keep statement-first acquisition; avoid claiming every bank API is legally impossible |
| Net worth from investment statements | CAS and NAV are candidates, but holdings can be old and some cases need a DP statement | Revisit with as-of dates, reconciliation, and verified data rights; no cheapest-route claim established |
| Broker APIs | Kite Personal is free but needs a daily token and covers one broker | Stays later |
| Tax engine | Official rules and an external estimator can support research | Begin with applicability and calendar; add a versioned calculator when estimates are required |
| Budgets, SMS capture, OCR, multicurrency, multiuser, nudges | No new evidence changes the earlier reasons | Unchanged |

Two gaps have no parked entry. The plan has no per-bank deposit-exposure check, so the DICGC limit
never reaches a verdict. And the import contract already recognizes a minimum due (journey J17.04),
but no dedicated tool outcome explaining minimum-only repayment cost was established here. These
are candidate gaps, not estimated small changes: both need additional facts and engine checks.

## Evidence gaps

- No primary text says an individual cannot be an FIU. The conclusion follows from the definition
  and onboarding steps: high confidence, but inferential.
- Whether an AA app can export a full consented dataset is unverified.
- The FY 2026-27 slab table under the 2025 Act was not verified. Only AY 2026-27 material and the
  default-regime rule were read.
- MF Central's terms did not load; automated access is unknown.
- Kite Connect's terms on storing or redistributing portfolio data were not reviewed.
- AMFI's terms are clear on personal use but silent on a distributed self-hosted app whose users
  each fetch their own funds.
- RBI DBIE did not respond, so no policy-rate source was verified.
- The absence of public EPFO or NPS APIs is inferred from portal-only documentation.

## Source confidence ledger

| Source | Anchors | Confidence |
|---|---|---|
| [RBI, Account Aggregator Directions](https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=10598) | FIU, consent, revocation | High; current to September 2024 |
| [Sahamati, FIU](https://sahamati.org.in/financial-information-user-fiu) | Onboarding, certification | High for process; alliance, not regulator |
| [RBI, Credit and Debit Card Directions](https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx?id=12300) | Due types, EMI disclosure | High; updated March 2024 |
| [RBI, KFS for Loans and Advances](https://rbi.org.in/Scripts/NotificationUser.aspx?Id=12663) | APR, amortization | High |
| [SEBI, CAS circular 2014](https://www.sebi.gov.in/sebi_data/attachdocs/1415783332661.pdf) | Consolidation, cadence | High |
| [SEBI, CAS email default 2024](https://www.sebi.gov.in/sebi_data/attachdocs/jul-2024/1719887551143.pdf) | Email dispatch | High |
| [CDSL, CAS FAQ](https://www.cdslindia.com/cas/faq.html) | Demat-only exclusion | Medium; page dated 2020 |
| [AMFI, NAV and terms](https://www.amfiindia.com/net-asset-value/nav-download) | NAV file; personal use | High for existence; terms ambiguous for this use |
| [casparser](https://github.com/codereverser/casparser) | Issuers, MIT, limits | High |
| [Kite Connect, user and token](https://kite.trade/docs/connect/v3/user/) | Login, token expiry | High |
| [Zerodha, Kite Connect pricing](https://zerodha.com/products/api/) | Tiers and price | High |
| [Income Tax Department, individual taxes](https://www.incometaxindia.gov.in/individual) | Advance tax | High |
| [Income Tax Department, AIS](https://www.incometaxindia.gov.in/annual-information-statement) | Contents, download | High |
| [Income Tax e-Filing, estimator](https://www.incometax.gov.in/iec/foportal/income-tax-estimator) | Regime comparison | High |
| [Income Tax e-Filing, transition FAQ](https://www.incometax.gov.in/iec/foportal/help/all-topics/e-filing-services/objective-and-scope-new-act-faq?mobile-app=1) | Year and applicability transition | High for the described transition; no slab or deadline table verified |
| [EPFO, member passbook](https://passbook.epfindia.gov.in/MemberPassBook/login) | Portal access | Medium; API absence not verified |
| [NPS Trust, subscriber login](https://npstrust.org.in/subscribers-login) | CRA login | High |
| [DICGC, guide to deposit insurance](https://www.dicgc.org.in/guide-to-deposit-insurance) | Cover and exclusions | High |
| [MF Central](https://www.mfcentral.com/) | Portal, portfolio | Medium; terms unverified |
| [DBIE](https://dbie.rbi.org.in/) | Macro data | Low; unreachable November 11, 2026 |
