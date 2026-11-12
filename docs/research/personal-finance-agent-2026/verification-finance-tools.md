# Verification of finance tool suggestions

Research note, November 12, 2026. This file checks suggestions in [the capability
survey](README.md), [the India note](india.md), and [tools and trust](tools-and-trust.md) against
primary documentation. It is evidence for a future catalog, not the catalog; `plan.md` owns
decisions. No package, key, account, or record was used. A vendor page establishes documented
behavior on the date read, not fitness for this project.

## 1. PyXIRR: functions, license, solver limits

PyXIRR installs and imports as `pyxirr` ([PyPI](https://pypi.org/project/pyxirr/)). Documented
functions are `fv`, `nfv`, `xfv`, `xnfv`, `pmt`, `ipmt`, `cumipmt`, `ppmt`, `cumprinc`, `nper`,
`rate`, `pv`, `npv`, `xnpv`, `irr`, `mirr`, `xirr`, and `year_fraction`, plus `DayCount`,
`is_conventional_cash_flow`, and `zero_crossing_points`
([functions](https://anexen.github.io/pyxirr/functions.html)). The license is the Unlicense, a
public-domain dedication ([LICENSE](https://github.com/Anexen/pyxirr/blob/master/LICENSE)).

`xirr(dates, amounts, *, guess=0.1, silent=False, day_count=DayCount.ACT_365F)` solves a dated
net-present-value equation: it is money-weighted over every payment, not a ratio of two balances.
For nonconventional flows, the documented strategy first searches near 0.1; if that fails it makes
other attempts and selects the lowest found IRR. It does not promise to enumerate every root, and a
flow can have no solution ([multiple
IRR](https://anexen.github.io/pyxirr/functions.html#multiple-irr-problem)). No convergence returns
`None`; a result can be `inf` or `-inf`. `InvalidPaymentsError` covers mismatched array lengths and
a series without one negative and one positive amount, so an all-inflow series is invalid input, not
a 0% return. Twelve day-count conventions exist, defaulting to Actual/365 Fixed; a 30/360 lender
will not reproduce without that argument.

The CAGR contrast is arithmetic: the SEC defines a rate of return as the percentage change in value
per year ([SEC](https://www.sec.gov/investor/tools/mfcc/rate-of-return-help.htm)), which annualized
is the n-th root of ending over beginning, collapsing contributions into two endpoints. XIRR uses
each dated payment, so a CAGR where money moved is a different measure, and the answer must name
which one it used.

Rounding is a layer question: ledger amounts stay integer paise, while the solver returns a rate,
and quantizing a rate to paise would destroy it. PyXIRR accepts `Decimal` and returns a float, so
the scientific operation need not be Decimal.

## 2. CASParser, dependency licenses, AMFI rights

`casparser.read_cas_pdf(path, password, output=...)` returns `CASData` for CAMS and KFintech, or
`NSDLCASData` for NSDL and CDSL demat statements
([README](https://github.com/codereverser/casparser)). Covered: CAMS and KFintech detailed and
summary; NSDL and CDSL demat consolidated. Excluded are re-printed PDFs, MF Central, and third-party
reformatted statements, because the parser verifies the original generator's watermark and metadata.

Licenses are separate: the parser is MIT. Since v1.0 the PDF engine is `pypdfium2`, described as
Apache-2.0 / BSD-3; the maintainer reports no GPL or AGPL dependencies. Declared dependencies
include `casparser-isin`, `pypdfium2`, `pydantic`, `python-dateutil`, and CLI helpers.
`casparser-isin` is separate, its local ISIN database "may get obsolete over time", and it refreshes
with `casparser-isin --update` ([casparser-isin](https://github.com/codereverser/casparser-isin)).

JSON output serializes decimals as strings and dates as ISO, while `statement_period` keeps
`DD-MMM-YYYY`, so parsing decimals as floats loses precision. `parse_warnings` becomes non-empty
when a scheme does not reconcile to the printed running balance, and that scheme should not be
trusted.

AMFI's NAV file is public but not freely redistributable. `NAVAll.txt` begins `Scheme Code;ISIN Div
Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date`, and each
row carries its own date; the copy read showed `11-Nov-2026` and a `Last-Modified` of November 11,
2026 ([NAV download](https://www.amfiindia.com/net-asset-value/nav-download)). The terms grant a
personal, non-commercial, non-transferable right, prohibit derivative works, prohibit storing "any
significant portion" of the site, and prohibit framing or modification without written approval
([AMFI terms](https://www.amfiindia.com/terms-of-use)). Public access is not permission to cache the
whole file in a distributed product; fetching only held schemes reduces volume without establishing
permission. A NAV file prices schemes, not holdings; the ISIN helper maps a scheme to an ISIN, AMFI
code, and ticker, which is still not ownership.

## 3. Contract fields behind cost and reserve claims

**Loans.** The RBI Key Facts Statement circular defines APR as the annual cost of credit including
interest and all other charges, requires a computation sheet and amortization schedule, makes
third-party charges recovered through the lender part of APR and separately disclosed, and bars any
fee absent from the KFS without explicit consent. Card receivables are exempt; the rule covers new
retail and MSME term loans sanctioned on or after October 1, 2024 ([KFS
circular](https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12663)). Annex A's fields, read
from a lender's published KFS in the RBI format because the RBI annex PDF returned a captcha:
amount, disbursal schedule, term, installment type and count with commencement, rate and whether
fixed, floating, or hybrid, benchmark plus spread, reset periodicity, effect of a 25 bps change,
fees split between lender and third parties, APR, and contingent charges for delay, penalty,
foreclosure, and switching ([HDFC
KFS](https://www.hdfcbank.com/content/bbp/repositories/723fb80a-2dde-42a3-9793-7ae1be57c87f/?path=%2FPersonal%2FBorrow%2FLoan+Against+Asset+Landing%2FLoanAgainst+Property%2FKFS+-+APR+Form%2FKFS-APR-English.pdf)).

**Cards.** RBI defines total amount due as the net payable at cycle end, minimum amount due as what
avoids an overdue bill, and the interest-free period as available only when the entire outstanding
is paid by the due date. APR must be quoted per situation, the unpaid-amount method shown, negative
amortization barred, and a minimum-payment warning printed on every statement, including that the
interest-free period is suspended while a prior balance remains. The MITC must carry fees, drawal
limits, billing including the minimum payable, default procedure, termination, loss/theft liability,
and grievance redressal; statements repeat those items ([card
directions](https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx?id=12300)).

**Insurance.** The initial pass read a 2017 regulation. Parent review did not treat its numeric
free-look periods as current law. The official June 12, 2024 [life insurance
circular](https://irdai.gov.in/document-detail?documentId=5032913) and its English PDF describe the
Customer Information Sheet, benefits, exclusions, servicing, and claims information. The PDF's text
was retrieved, but a page screenshot returned 403. The catalog therefore uses this as evidence for
reading actual policy terms, not a certified current statutory field checklist. Policy identity,
coverage, premiums, dates, riders, exclusions, nominees, and claim instructions are proposed
extraction fields; exact applicability comes from the user's policy and current rules. Extracting
those fields does not establish that the coverage is adequate.

**Deposits.** DICGC insures up to ₹5 lakh for each depositor at an insured bank, principal and
interest, in the same right and capacity, aggregates branches of one bank, treats separate banks
separately, and pays after netting the bank's dues. Bank/deposit eligibility determines coverage;
directions restricting withdrawal affect the applicable payout process
([DICGC](https://www.dicgc.org.in/guide-to-deposit-insurance)). The figure is the limit published on
November 12, 2026. An FD record without maturity, payout mode, lock-in, holder capacity, and
premature-withdrawal terms cannot support a reserve claim: insurance is not liquidity.

## 4. Document parsing against what Arno already runs

Docling's code is MIT, and the README says individual models carry their own licenses
([README](https://github.com/docling-project/docling)). The layout and TableFormer model card lists
both CDLA-Permissive-2.0 and Apache-2.0, so each weight file needs its own review
([models](https://huggingface.co/docling-project/docling-models)). PyPI ships a meta-package over
`docling-slim` with extras for OCR, ONNX, and VLM, so a standard install brings ML runtimes Arno
does not ship ([PyPI](https://pypi.org/pypi/docling/json)).

Arno already has magic-byte sniffing, `pypdfium2` text and password opening, `python-calamine`,
`python-docx`, `msoffcrypto-tool`, and a vision role for pages with no text layer
(`src/arno/importers/readers.py`, `vision.py`). Docling adds layout, reading order, and table
structure. Whether that improves the actual statements is empirical; no benchmark was run, so
replacing the reader now trades a known dependency for an unmeasured one.

Password handling is already right: `PasswordVault` keeps a password in memory for one import and
never writes it to the database, a job payload, a log, or a model
(`src/arno/importers/passwords.py`); the import card supplies it. Inferring one from PAN or date of
birth would turn a document into an identity attack surface.

## 5. FX: reference rates are not executed rates

Frankfurter serves `https://api.frankfurter.dev/v2` with `/v2/rates` and `/v2/rate/{base}/{quote}`.
It needs no key, documents no quotas but rate-limits abuse, permits storing daily observations,
states blended rates are free for commercial use while provider endpoints follow each provider's
terms, and states it does not log personal data or request URLs
([Frankfurter](https://frankfurter.dev/)). Rates are reference rates, not live quotes.

The ECB series publishes around 16:00 CET on working days except TARGET closing days, is "for
information purposes only", and discourages transaction use
([ECB](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html)).

`plan.md` keeps one base currency per instance and logs a foreign purchase at the amount charged;
multicurrency is parked with a trigger. A reference cross rate can label a foreign amount but cannot
replace the executed amount, because the network's rate, markup, and settlement date are not the ECB
fix.

## 6. Freelancer receivables and spreadsheet export

Razorpay exposes `GET /v1/settlements/`, `GET /v1/settlements/:id`, and `GET
/v1/settlements/recon/combined?year=&month=&day=`. A reconciliation row has `type` of payment,
refund, transfer, or adjustment, with separate `amount`, `fee`, `tax`, `credit`, `debit`,
`settlement_id`, `settlement_utr`, and timestamps, in currency subunits
([recon](https://razorpay.com/docs/api/settlements/fetch-recon/)). The sample charges 100000
subunits, fees 2900, and credits 97100: net is not gross, and a refund is a debit. Recording only
the net payout loses the distinction between gross receipts, fees, refunds, and cash received.
Settlement reconciliation is not a decision about the user's tax or revenue-recognition basis.

Webhooks are at-least-once. Non-2xx deliveries retry with exponential backoff for 24 hours, then the
webhook is disabled; a response must arrive within 5 seconds; order is not guaranteed. The signature
is `X-Razorpay-Signature`, an HMAC-SHA256 of the raw body keyed by the webhook secret, and the docs
warn not to parse the body first. Duplicates are detected with `x-razorpay-event-id`, unique per
event ([best practices](https://razorpay.com/docs/webhooks/best-practices/),
[validate](https://razorpay.com/docs/webhooks/validate-test/)).

XlsxWriter is BSD-2-Clause and write-only. It does not calculate formulas: it stores zero and sets a
recalculate-on-open flag, and callers may pass a cached `value`
([formulas](https://xlsxwriter.readthedocs.io/working_with_formulas.html),
[license](https://xlsxwriter.readthedocs.io/introduction.html)). openpyxl is MIT, and `data_only`
returns "the value stored the last time Excel read the sheet"
([tutorial](https://openpyxl.readthedocs.io/en/stable/tutorial.html),
[PyPI](https://pypi.org/pypi/openpyxl/json)). A non-calculating viewer shows zeros unless cached
values are supplied. A newly assigned formula without a computed cache cannot be trusted in a
non-calculating viewer. Arno's verified calculation outputs stay the source of truth.

## 7. Account Aggregator eligibility and the broker alternative

RBI defines an FIU as "an entity registered with and regulated by any financial sector regulator",
and lists FIPs as banks, NBFCs, asset managers, depositories and participants, insurers, insurance
repositories, record-keeping agencies, GSTN, and CCIL. A regulated entity joining as FIU must also
join as FIP if it holds the information, and an Account Aggregator may not request or store customer
credentials such as passwords, PINs, or private keys ([AA
directions](https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=10598)). An unregulated
single-user self-host falls outside the definition by its terms. That is not a blanket prohibition;
the direct route needs regulated status.

Sahamati's sequence is registration with RBI, SEBI, IRDAI, or PFRDA, then FIP, then ReBIT
specifications, sandbox, UAT central registry, certification by an empaneled auditor, and registry
listing ([Sahamati](https://sahamati.org.in/financial-information-user-fiu)). Its FAQ says only
asset-based data is currently available, naming bank accounts, deposits, mutual funds, insurance,
and pension, and that other types may be added. That is ecosystem status, not an RBI rule, and RBI's
provider list includes non-household entities. Neither reading supports a universal exclusion, but
loan or card coverage stays unverified until live account types are checked.

Kite Connect's login returns a `request_token` exchanged with a checksum for an `access_token`; the
token expires at 6 a.m. next day as a regulatory requirement, and a long-lived `refresh_token` is
only for approved platforms. The API secret stays server-side
([Kite](https://kite.trade/docs/connect/v3/user/)). The free Personal tier covers portfolio
management; ₹500 per month Connect adds streaming and candles
([pricing](https://zerodha.com/products/api/)). The app holds a secret and a daily token, serves one
broker, and carries the broker's terms: a credentialed single-institution read path, not a household
feed.

## 8. Enabling tools matrix

The table gives entry point, mode, gap, and boundary.

| Entry point | Mode | Evidence | License or access gap | Safety boundary | Status |
|---|---|---|---|---|---|
| `pyxirr.xirr`, `irr`, `mirr` | Internal library | Dated rate solving, day counts | Unlicense; float or `None` | Rate is not money | Candidate |
| `casparser.read_cas_pdf` | Internal library | Four issuers, `parse_warnings` | MIT; no re-prints or MF Central | Statement text untrusted | Candidate |
| AMFI `NAVAll.txt` | Direct HTTPS | Scheme NAV with per-row date | Personal, non-commercial, no bulk storage | Access is not redistribution | Candidate after terms review |
| `pypdfium2.PdfDocument` | Internal library | PDF text, images, password open | Apache-2.0 / BSD-3 | Password stays in the vault | Existing |
| Docling `DocumentConverter.convert` | Internal library with local models | Layout, order, tables | MIT code; model licenses reviewed separately | Adds models and compute | Candidate, benchmark first |
| Frankfurter `/v2/rate/{base}/{quote}` | Direct API, MCP optional | Dated reference cross | No key; provider routes have own terms | Never replaces executed FX | Candidate, foreign amounts only |
| `/v1/settlements/recon/combined` | Direct API | Gross, fee, tax, net per row | Account and key terms | Read-only | Candidate, freelancer trigger |
| `xlsxwriter.Workbook`, `Worksheet.write_formula`, `openpyxl` | Internal library | Write formulas or read cached values | BSD-2 / MIT; neither calculates | Excel recalculation is not authority | Candidate |
| Kite `/portfolio/holdings` | Direct API | One broker's holdings | Daily token and secret; verify required tier | Credentialed; one broker | Candidate, later |
| AA FIU pull | Regulated network | Consented FIP data | Regulated status, certification | Consent-bound; no credentials | Not available |

## 9. Ten tool facts and acceptance cases

Each row pairs a fact with the result and a pass/fail case.

| Fact | Outcome | Acceptance case |
|---|---|---|
| XIRR uses each dated payment | Return reflects when money went in | A SIP and a lump sum give different rates; a two-point CAGR does not |
| Non-conventional flows can have several roots | No single rate is called the rate | A flow with two sign changes is flagged ambiguous |
| CASParser rejects re-prints | A rejection asks for the original | A browser-printed CAS is refused with a reason |
| Decimal fields arrive as strings | Units do not drift | 33.412 units round-trip unchanged |
| AMFI rows carry their own date | Every valuation shows its age | A fund priced two days ago carries that date |
| KFS APR includes fees | Equal rates compare on cost | A 2% fee raises APR and total repayment |
| Card minimum due is not the bill | Minimum-only shown as a cost path | Interest and lost interest-free period appear |
| Settlement rows split gross and net | Receipts, fees, refunds, and cash stay separate | Reconciling the payout reproduces the bank credit without counting it as a second gross receipt |
| Webhook duplicates are expected | One payment, one row | Replaying an event id changes nothing |
| Reference FX is not executed FX | A foreign charge keeps the billed amount | The record matches the statement; the cross is context |

## 10. Unresolved blockers

- The RBI KFS annex PDF returned a captcha; the field list came from a lender's KFS in that format.
- AMFI's terms do not cover a distributed self-hosted app whose users fetch their own schemes.
- `casparser-isin` refreshes from unreviewed upstream sources; its data can stale.
- No benchmark compared Docling with the current reader; its weight licenses are per model.
- Frankfurter blends providers with different schedules; a displayed FX date must be the provider's.
- Razorpay account prerequisites were not verified; a webhook route also needs reviewed ingress.
- Parent checked the official 2024 life-insurance text; a complete current legal field checklist and
  numeric free-look rules were not certified and are not requirements inferred here.
- AA loan and card coverage was not established either way.

## 11. Confidence

High for PyXIRR, CASParser, Razorpay, XlsxWriter, openpyxl, ECB, Kite, the RBI card and AA
directions and DICGC aggregation. Medium for insurance field completeness and the KFS annex list,
read from a lender's reproduction, and for Docling, whose code license is clear but weights are per
model. Low for AMFI's distributed-use position. The AA conclusion is high for the definition,
inferential for this application.

The strongest conclusions are negative: a rate solver is not a rounding layer, a NAV file is not a
portfolio, a policy parse is not an adequacy verdict, and public download access is not a
redistribution right.
