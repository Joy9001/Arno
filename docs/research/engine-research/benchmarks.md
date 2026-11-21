# Production benchmarks for the `engine/` package

_Applied research survey for Arno's numerical core (`src/arno/engine/`), compiled November 21, 2026.
This document belongs to `docs/research/` and records external standards, real-world corpora, and
recommended verification harnesses; it is not an approved decision record (`plan.md` owns
decisions)._

---

## 1. Architectural anatomy of the `engine/` package

The engine (~465 KB across 13 modules) is a pure, clock-free, database-free numerical core.
`numpy` (`>=2.3` in [`pyproject.toml`](../../../pyproject.toml#L37)) is the sole third-party
runtime numeric dependency, used in the Monte Carlo kernel, projection, affordability, scoring, and
backtest modules. `money.py`, `series.py`, `patterns.py`, and `inputs.py` use only the Python
standard library plus `arno.india.*` calendar and recurrence helpers (`python-dateutil` and
`holidays`). Every monetary quantity below display formatting is integer paise (`1/100 INR`), dates
are `datetime.date`, and probabilities inside the kernel are quantized to `2^32` integer thresholds
([`projection.py:712-717`](../../../src/arno/engine/projection.py#L712-L717)).

### Data flow and four functional subsystems

The production pipeline is strictly one-directional and pure at every seam below
[`tools/projection.py`](../../../src/arno/tools/projection.py#L1-L6), which is the single database
marshaling boundary.

```text
Postgres ledger
  └─ tools/projection.py: load_snapshot()            (sole DB seam; 10+ SQL reads)
       └─ inputs.py: build_inputs()                  (pure snapshot -> ProjectionInput)
            ├─ series.py + patterns.py               [Subsystem B] Bayesian recurring & price steps
            ├─ scenario.py: apply_changes()          (pure immutable what-if mutation)
            ├─ projection.py: prepare() -> kernel.run() [Subsystem A] 16,384-path SplitMix64 MC walk
            ├─ affordability.py: afford()            [Subsystem D] Chance-constrained lever solver
            └─ money.py                              [Subsystem C] Decimal amortization, APR, GST EMI
```

The table below maps each subsystem to its source files, core algorithms, and code-verified risks.

| Subsystem | Key modules and citations | Core algorithms and production workload | Verified code-level risks and defects |
|---|---|---|---|
| A. Probabilistic cashflow and balance-path kernel | [`kernel.py:36-68`](../../../src/arno/engine/kernel.py#L36-L68), [`kernel.py:438-612`](../../../src/arno/engine/kernel.py#L438-L612), [`projection.py:636-645`](../../../src/arno/engine/projection.py#L636-L645), [`projection.py:953-1032`](../../../src/arno/engine/projection.py#L953-L1032) | Vectorized 16,384-scenario `int64` Monte Carlo in 127-day chunks (`CHUNK_DAYS=127`) over 90d typical and 3,650d max horizons. Counter-based 4-round SplitMix64 hashing `(seed, stream, scenario, day)` guarantees scenario independence and common random numbers across counterfactuals. Shipped `MODEL`: whole-day empirical pooling (`block_length=1`), MAD-outlier rare-day replacement (`median + 3*MAD` over 365d, Poisson rate per scenario), credit-card billing pieces with `limit_spend=False`, raw order-statistic P10/P50/P90. | 1. Inline event-loop blocking: [`api/home.py:27`](../../../src/arno/api/home.py#L27), [`api/ledger.py:636`](../../../src/arno/api/ledger.py#L636), and [`agent/toolset.py:469`](../../../src/arno/agent/toolset.py#L469) run NumPy walks synchronously on FastAPI's asyncio loop (~1,896 ms home load at [`plan.md:70-73`](../../arno/plan.md#L70-L73)). 2. Peak chunk memory is 100–250 MiB across 6–10 live `16384x127` columns (16.6 MiB per single column), and full 3,650-day path retention takes ~478 MiB. 3. Cards always pay in full on due date ([`kernel.py:773-776`](../../../src/arno/engine/kernel.py#L773-L776)); unknown unbilled split front-loads 100% outstanding to the first due date ([`projection.py:676-680`](../../../src/arno/engine/projection.py#L676-L680)). 4. Optimistic median shifts `curve[:,1] + cumsum(unconfirmed)` (sum-of-quantiles instead of quantile-of-sums, [`projection.py:1381-1399`](../../../src/arno/engine/projection.py#L1381-L1399)). |
| B. Bayesian recurring and price-step detector | [`series.py:545-645`](../../../src/arno/engine/series.py#L545-L645), [`series.py:810-980`](../../../src/arno/engine/series.py#L810-L980), [`series.py:1221-1236`](../../../src/arno/engine/series.py#L1221-L1236), [`patterns.py:48-124`](../../../src/arno/engine/patterns.py#L48-L124) | Homogeneous Poisson background ratio (`lambda = M/(e*T)`), 11 calendar-rule families, triangular jitter kernel `f(d)`, censoring-adjusted skip probabilities, canonical two-table sum-product DP and Viterbi attribution, float64 with `2^-512` power-of-two rescaling, arithmetic mean over a 12-point `(jitter, skip, end)` grid, 50/50 rule vs no-pattern prior, and `price_step`/`stepped_level` split scanner. | 1. Heuristic pruning before likelihood: `_pruning_score` keeps only the top 8 candidate rules (`_MAX_CANDIDATES=8`, [`series.py:1232-1236`](../../../src/arno/engine/series.py#L1232-L1236)) before scoring the 12-point DP grid; a true rule ranked 9th is invisible. 2. Variable series store at most 12 empirical samples drawn uniformly ([`series.py:931-939`](../../../src/arno/engine/series.py#L931-L939), [`kernel.py:413-414`](../../../src/arno/engine/kernel.py#L413-L414)), truncating amount tails. 3. Payee-only grouping prevents splitting two plans at one merchant. 4. `patterns.same_merchant` uses raw token intersection without `_GENERIC_TOKENS` ([`patterns.py:75-83`](../../../src/arno/engine/patterns.py#L75-L83)). |
| C. Fixed-income and retail credit math | [`money.py:255-474`](../../../src/arno/engine/money.py#L255-L474), [`money.py:477-887`](../../../src/arno/engine/money.py#L477-L887), [`money.py:890-1241`](../../../src/arno/engine/money.py#L890-L1241) | `Decimal` `ROUND_HALF_UP` amortization (monthly rest, `30/360`, `actual/365`, rate resets, tenure/EMI prepayments), integer-lattice bisection for APR and `infer_rate` (`bps * 100`), Indian credit-card EMI with 18% GST on interest and fee (CSB Bank One Card convention), revolving payoff (5% min / INR 200 floor), `compare_offers`, `compound`, `scale_cumulative`, largest-remainder `allocate`. | 1. Confirmed day-count defect: `_days_30_360` ([`money.py:255-258`](../../../src/arno/engine/money.py#L255-L258)) clamps both endpoints to 30, which is 30E/360 (Eurobond basis), not US 30/360 Bond Basis; `"actual/365"` is ambiguous under ISDA (means Actual/365 Fixed). 2. Confirmed RBI APR sensitivity: on RBI KFS Annex B, `_compute_apr` matches 17.07% on unrounded EMI INR 969.73 (`17.0702%`) but yields `17.0988%` (+0.03 pp) on disclosed INR 970; nominal `12 * monthly IRR` matches India and differs from EU effective `(1+r)^12 - 1` (`18.5046%`). 3. `inputs.py:145,212,222` uses Python banker's `round()` while `money.py` uses `ROUND_HALF_UP`; `money.py` relies on unpinned global `Decimal` context. 4. `infer_rate` returns `exact=True` even across a multi-unit rounding plateau `low < high` ([`money.py:809-814`](../../../src/arno/engine/money.py#L809-L814)). |
| D. Stochastic affordability and lever optimizer | [`affordability.py:260-496`](../../../src/arno/engine/affordability.py#L260-L496), [`affordability.py:584-895`](../../../src/arno/engine/affordability.py#L584-L895), [`scenario.py:151-262`](../../../src/arno/engine/scenario.py#L151-L262) | Emits three verdicts (`yes`, `yes-but`, `no`) governed by dual chance constraints: `P(min >= floor) >= 0.90` and `P(obligation_met) >= 0.99` ([`affordability.py:260-261`](../../../src/arno/engine/affordability.py#L260-L261)). Solves three levers (method switch `account`/`card`/`emi`, goal deferral within 31 days, spend cut up to 50% until payday) via linearized checkpoint critical values, Pareto dominance pruning, full re-projection or bisection verification (`_verify`), and wait-date search (up to 12 attempts). | 1. SAA sampling noise at the 0.99 gate: at `S = 16,384`, binomial SE at `p = 0.99` is 0.078 pp (95% CI +/-0.152 pp), so `P_hat = 0.990` has only 50% confidence that `P_true >= 0.99`. 2. Worst-case `no` fan-out runs up to 24 wait walks + 3 method matrices + 4 plans x up to 17 verify/bisect walks (tens to >100 full 16,384-path walks per question). 3. Pareto dominance prunes candidate plans on the linearized card-uncapped model before `_verify` runs ([`affordability.py:392-422`](../../../src/arno/engine/affordability.py#L392-L422)). |

### Complexity, rounding, and determinism profile

The table below summarizes asymptotic cost, memory footprint, and reproducibility caveats across the
core engine modules.

| Module | Time complexity | Memory footprint | Determinism and rounding caveats |
|---|---|---|---|
| `inputs.py` | `O(R + K*90 + S_sched + G)` | `O(K*90)` zero-filled tuples | Uses Python banker's `round()` at lines 145, 212, and 222; card-share tie-break follows dict insertion order ([`inputs.py:216-223`](../../../src/arno/engine/inputs.py#L216-L223)). |
| `series.py` | `O(C*(n+m) + 8*12*n*m)` per payee (`O(n^3 log n)` in `price_step`) | `O(n*m)` Viterbi backpointers | Float64 DP with `2^-512` power-of-two rescaling; deterministic on one platform, minor cross-platform libm risk; builds `IndiaCalendar` per default call ([`series.py:1002-1009`](../../../src/arno/engine/series.py#L1002-L1009)). |
| `kernel.py` | `O(S*H*(Q*P + K))` plus `O(K*H*S)` day-loop when `limit_spend=False` | `O(S*127*#cols)` (~100–250 MiB peak); paths `O(S*keep_days)` | Bit-for-bit deterministic via SplitMix64 (`draw` oracle verified in tests); rare Poisson CDF uses `math.exp` + float `cumsum` ([`kernel.py:318-322`](../../../src/arno/engine/kernel.py#L318-L322)); `below()` contract `bound < 2^32` unchecked at runtime. |
| `money.py` | `O(months * log2(10^6))` for APR and `infer_rate`; `O(K^2)` in `compare_offers` | `O(months)` schedule rows | `ROUND_HALF_UP` (and `ROUND_CEILING` option on EMI/min-due); depends on unpinned process-global `Decimal` context precision ([`money.py:1-12`](../../../src/arno/engine/money.py#L1-L12)). |
| `affordability.py` | Up to ~100x `kernel.run` on worst-case `no` verdict | Reuses single-run chunk budget sequentially | Exact order-canonical `scenario.py` mutations ([`scenario.py:151-155`](../../../src/arno/engine/scenario.py#L151-L155)); unverified dominance pruning before `_verify`. |

### Document-to-code deltas worth tracking

Benchmark design must target the shipped code rather than stale prose in
[`engine-blueprint.md`](../../arno/engine-blueprint.md). The table below lists the five material
divergences verified during the audit.

| Claim in `engine-blueprint.md` | Shipped code reality |
|---|---|
| Exact Python integer weights over powers of two in `series.py` (`blueprint:462-466`) | Shipped DP uses float64 with `1e150` / `2^-512` rescaling ([`series.py:41-44`](../../../src/arno/engine/series.py#L41-L44), [`series.py:545-645`](../../../src/arno/engine/series.py#L545-L645)). |
| Variable bill predicts from residual quantiles with 90% interval (`blueprint:484-487`) | `_summarize_amounts` returns up to 12 empirical samples and heuristic `(n-1)/(n+1)` coverage ([`series.py:890-939`](../../../src/arno/engine/series.py#L890-L939)); kernel draws uniformly from those samples ([`kernel.py:413-414`](../../../src/arno/engine/kernel.py#L413-L414)). |
| Two plans at one merchant scored separately (`blueprint:488-490`) | Not implemented; grouping is strictly by payee ([`tools/patterns.py:336-341`](../../../src/arno/tools/patterns.py#L336-L341)). |
| Memory stays near 16 MiB (`blueprint:271-274`) | 16.6 MiB is one `16384x127` `int64` matrix; real chunk peak holds 6–10 columns (~100–250 MiB, [`kernel.py:676-683`](../../../src/arno/engine/kernel.py#L676-L683)). |
| Checkpoint lemma skips non-checkpoint days for floor test (`blueprint:296-315`) | `kernel.run` computes running minima across every day ([`kernel.py:581-584`](../../../src/arno/engine/kernel.py#L581-L584)); checkpoints are used for affordability and reducible-spend measurement. |

---

## 2. Audit of the existing evaluation harness

Auditing [`scoring.py`](../../../src/arno/engine/scoring.py),
[`backtest.py`](../../../src/arno/engine/backtest.py),
[`personas.py`](../../../src/arno/engine/personas.py),
[`hackerrank.py`](../../../src/arno/engine/hackerrank.py),
[`README.md:61-148`](../../../README.md#L61-L148), and
[`tests/invariants.py`](../../../tests/invariants.py) shows a strong internal foundation (leakage
guards, Newey-West HAC standard errors, Murphy Brier decomposition, bit-for-bit PRNG oracle tests)
paired with six structural blind spots that prevent it from serving as a production-grade
evaluation suite.

### Current coverage in `scoring.py` and `backtest.py`

The table below lists every metric currently implemented in
[`scoring.py`](../../../src/arno/engine/scoring.py) and its gaps.

| Metric in `scoring.py` | Formula and scope | Limitation in `backtest.py` |
|---|---|---|
| `crps` (`:21-32`) | Pairwise `E|X-y| - (1/2)E|X-X'|` on sorted samples (paise); degenerates to MAE for single-path baselines | Marginal only on `end` and `min`; blind to multi-day autocorrelation; single simulation seed (`DEFAULT_SEED=20261103`) omits Monte Carlo variance. |
| `pinball` (`:42-45`) and `quantile` (`:35-39`) | Non-interpolated order statistics at `TAUS=(0.1, 0.5, 0.9)` | Plain means without confidence intervals ([`backtest.py:923-948`](../../../src/arno/engine/backtest.py#L923-L948)); misses extreme tails (`0.01`, `0.05`, `0.95`, `0.99`) where the 0.99 obligation gate operates. |
| `pit` (`:48-56`) and `histogram` (`:163-168`) | Half-credit ties in `[0, 1]`, 10 equal-width bins | Computed ([`backtest.py:934`](../../../src/arno/engine/backtest.py#L934)) but never printed or tested for uniformity; continuous PIT is biased on discrete rare-day counts. |
| `hac_estimate` (`:69-89`) and `diebold_mariano` (`:92-111`) | Newey-West Bartlett weights (`lag = ceil(h/7)-1`), paired two-sided normal p-value | Uncorrected per-cell DM p-values across 10 cells (2 targets x 5 horizons) x 4 forecasters create ~40% familywise false-positive risk. |
| `brier` (`:141-160`) and `wilson` (`:114-122`) | Brier score + Murphy `reliability - resolution + uncertainty`; 95% Wilson CI | Only overall Brier is printed; no reliability curve at 0.90, no breach clustering test, and `hackerrank.py:1558-1566` duplicates `wilson`. |

### Six structural blind spots

1. **Generator-shaped synthetic backtest:** all 978 scored origins (`(9*52 + 21)*2` across 10
   synthetic households on holdout seeds `201, 202`, [`personas.py:18-41`](../../../src/arno/engine/personas.py#L18-L41))
   share `arno.india.recurrence.expand_rule` with the engine, generate independent daily spend across
   categories (matching the kernel's independence assumption), round every amount to whole rupees
   ([`personas.py:320`](../../../src/arno/engine/personas.py#L320), never exercising paise rounding),
   calibrate floors on the exact 52x90-day scored windows to hit 8–22% breach base rates
   ([`personas.py:1963-1974`](../../../src/arno/engine/personas.py#L1963-L1974)), and group recurring
   detection by ground-truth `payee_key` rather than normalized UPI text
   ([`backtest.py:365-371`](../../../src/arno/engine/backtest.py#L365-L371)). Two leakage asymmetries
   also remain: persona schedule-series exclusion omits `schedule.created <= as_of`
   ([`backtest.py:486-493`](../../../src/arno/engine/backtest.py#L486-L493)), and the DB path's
   `known_then=True` filters on `created_at` but reads later edits to existing schedules. The real DB
   backtest path ([`backtest.py:808-854`](../../../src/arno/engine/backtest.py#L808-L854)) is
   implemented but blocked on linked income ([`plan.md:535-547`](../../arno/plan.md#L535-L547)).
2. **Accepted miscalibration and autocorrelation blindness:** published holdout P10–P90 coverage is
   66–77% against the nominal 80% target ([`README.md:105-125`](../../../README.md#L105-L125)), and
   no automated gate fails CI when coverage or skill regresses ([`cli.py:549-570`](../../../src/arno/cli.py#L549-L570);
   the 1% CRPS bar in [`plan.md:700-705`](../../arno/plan.md#L700-L705) is prose only). Reducing
   trajectories to marginal `min` CRPS leaves multi-day persistence invisible.
3. **Missing breach severity and regime diagnostics:** the harness checks _whether_ a floor breaks,
   never _how deep_ the overdraft runs (Expected Shortfall) or whether skill degrades in specific
   regimes such as salary week, month-end, or pre-EMI days.
4. **Zero verdict or lever evaluation:** `Score` ([`backtest.py:220-233`](../../../src/arno/engine/backtest.py#L220-L233))
   contains no decision field. `afford()`, `safe_amount_today`, `first_full_payment_date`, and lever
   minimality have no backtest calibration or independent optimization oracle.
5. **`hackerrank.py` is not an industry benchmark:** it replays `n = 25` deterministic, card-free,
   zero-spend-pool CSV requests ([`hackerrank.py:1166-1178`](../../../src/arno/engine/hackerrank.py#L1166-L1178))
   through ~15 dataset-specific adapter heuristics (185-day window, 4-day tail, Indonesian message
   regexes, category-first grouping) tuned directly on those 25 rows
   ([`plan.md:525-529`](../../arno/plan.md#L525-L529)). Its `runs` parameter is inert, 3 of 6 output
   fields go unscored, `_amount_ok` checks tolerance against `requested` rather than `expected`
   ([`hackerrank.py:1539-1541`](../../../src/arno/engine/hackerrank.py#L1539-L1541)), and its 86.80%
   combined score carries a 26.6-point Wilson CI (68.6%–95.2%) improperly applied to a weighted
   composite of correlated rates.
6. **Zero systems, memory, or PRNG regression gates:** [`tests/invariants.py`](../../../tests/invariants.py#L104-L122)
   checks ledger accounting only; the sole timing check in the repository is persona generation under
   1.0 s ([`tests/test_engine_personas.py:158-162`](../../../tests/test_engine_personas.py#L158-L162)).
   A 10x kernel slowdown or a 2x chunk RSS spike passes CI silently.

---

## 3. Ranked industry-standard, production-grade benchmark suites

> **Verified negative finding:** no peer-reviewed, public, consumer-level benchmark for daily bank
> balance-path forecasting or floor-breach probability exists. The closest public cashflow dataset is
> the macro US Treasury General Account (wrong granularity); Plaid, Tink, Yodlee, and FinRegLab
> loan-level consumer datasets are proprietary. Production-grade evaluation of `src/arno/engine/`
> therefore combines regulatory financial-math test beds, institutional tail-risk and multivariate
> scoring protocols, real historical banking and retail-pricing corpora, and open-model systems and
> PRNG batteries.

The five suites below are ranked by production defect-detection value relative to engineering effort.

---

### Rank 1: Regulatory financial math and day-count conformance (Subsystem C)

These suites test [`money.py`](../../../src/arno/engine/money.py) against statutory disclosures and
institutional contract engines, targeting silent sub-rupee and basis-point bugs that survive unit
tests.

| Benchmark or standard | Canonical reference | Real-world workload and scope | Accuracy metrics and pass criteria |
|---|---|---|---|
| 1A. RBI KFS and Digital Lending Directions (2024/2025) | [RBI/2024-25/18 (KFS Circular)](https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12663), Annex B and C; [RBI/2025-26/36 (Digital Lending Directions 2025)](https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12848) | India's binding statutory APR and loan-schedule disclosure vectors for retail and MSME term loans (credit-card receivables are exempt under para 10). | Exact match to 2 decimal places (`17.07%` on Annex B); explicit verification of unrounded vs disclosed EMI input policy and nominal `12 * monthly IRR` convention. |
| 1B. OpenGamma Strata and QuantLib day-count cross-oracle | [Strata DayCounts](https://strata.opengamma.io/day_counts); [QuantLib Dates and DayCounters](https://quantlib-python-docs.readthedocs.io/en/latest/dates.html) | 400+ stratified real calendar pairs across leap years, Feb 28/29, 30/31 month-ends, and multi-year spans. | Zero-day discrepancy on `Actual/365 Fixed`, `Actual/Actual (ISDA)`, `30E/360 (Eurobond)`, and `30/360 (US Bond Basis)` year-fractions. Immediately catches `_days_30_360`. |
| 1C. ACTUS conformance test beds (`ANN` and `PAM`) | [ACTUS Tech Specs](https://www.actusfrf.org/techspecs); [actusfrf/actus-tests](https://github.com/actusfrf/actus-tests) (JSON test beds, ~56 `ANN` + `PAM` contracts) | Institutional banking contract schedules: annuities (`ANN`), principal-at-maturity (`PAM`), rate resets (`RRY`/`RRN`), business-day rolling, scaling and prepayments (`PRF`). A Lean 4 reference passes 236/236 contracts and 3,155/3,155 cash flows ([actus-spec-lean](https://github.com/monoid-gmbh/actus-spec-lean)). | 100% schedule-date equality and at most 1 paisa error across the full cashflow vector (not just scalar EMI). Note: ACTUS has no revolving card or GST-on-EMI type. |
| 1D. CFPB Reg Z Appendix J and EU CCD 2023/2225 Annex III | [12 CFR 1026.22](https://www.ecfr.gov/current/title-12/chapter-X/part-1026/subpart-C/section-1026.22), [Appendix J](https://www.consumerfinance.gov/rules-policy/regulations/1026/j/), [APR Tables Vol I/II](https://www.consumerfinance.gov/compliance/compliance-resources/other-applicable-requirements/annual-percentage-rate-tables/); [Directive (EU) 2023/2225](https://eur-lex.europa.eu/eli/dir/2023/2225/2023-10-30/eng) | Irregular schedules (fractional first periods, odd final/balloon payments, multiple advances) plus effective annual APR `(1+r)^12 - 1` on 30.41666-day (`365/12`) months. | Reg Z statutory tolerance (`+/-1/8 pp` regular, `+/-1/4 pp` irregular); EU Annex III exact match at >=1 decimal `ROUND_HALF_UP`. Stress-tests `_compute_apr` beyond uniform grids. |

#### Empirical findings already surfaced against Rank 1 oracles

Running a read-only check of `money._compute_apr` against RBI KFS Annex B (INR 20,000 sanctioned,
15% fixed, 24 monthly EMIs, INR 400 fees, INR 19,600 net disbursed, statutory APR 17.07%) and
inspecting `money._days_30_360` produced two immediate findings:

- **RBI Annex B unrounded-EMI and nominal-vs-effective conventions:** `_compute_apr(1_960_000, [96_973]*24)`
  returns `17.0702%` (matching RBI's 17.07%), whereas `_compute_apr(1_960_000, [97_000]*24)` returns
  `17.0988%` (+0.03 pp). RBI's Annex B computes APR on the unrounded EMI (INR 969.73) and discloses
  the rounded EMI (INR 970) in Annex C, using nominal `12 * monthly IRR` rather than effective
  `(1+r)^12 - 1` (`18.5046%`). Conformance tests must pin both `apr_input` (`"unrounded"` vs
  `"disclosed"`) and `apr_basis` (`"nominal_monthly"` vs `"effective_annual"`).
- **Silent day-count mislabeling:** [`money.py:255-258`](../../../src/arno/engine/money.py#L255-L258)
  implements `(y2-y1)*360 + (m2-m1)*30 + (min(d2,30) - min(d1,30))`, which is **30E/360 (Eurobond
  basis)**, not US 30/360 Bond Basis; they diverge on Feb 28/29 endpoints and `(d1=30, d2=31)`.
  Likewise, ISDA treats bare `"actual/365"` as an alias for Actual/Actual (ISDA), so the fixed
  365-day denominator should be labeled `"actual/365f"`.

---

### Rank 2: Multivariate path-coherence, tail-risk, and proper-scoring protocol (Subsystem A)

This protocol upgrades [`scoring.py`](../../../src/arno/engine/scoring.py) and
[`backtest.py`](../../../src/arno/engine/backtest.py) to institutional financial risk-engine
standards in ~225 lines of pure NumPy, with no new runtime dependencies.

| Standard or protocol | Canonical reference | Real-world failure mode measured | Exact formulas and metrics |
|---|---|---|---|
| 2A. Scheuerer-Hamill Variogram Score and 2-D Energy Score | Scheuerer & Hamill (_MWR_, 2015, [PDF](https://journals.ametsoc.org/view/journals/mwre/143/4/mwr-d-14-00269.1.pdf)) | Multi-day autocorrelation and path coherence. Marginal CRPS cannot distinguish independent daily shocks from persistent spending bursts that govern `min(balance[0..H])`; Energy Score alone also struggles to separate correlation structures. | `VS_p(F, y) = sum_{i,j} w_ij (|y_i - y_j|^p - E_F|X_i - X_j|^p)^2` with `p in {0.5, 1}` and `w_ij = 1/|i-j|` over checkpoint balance vectors (`d ~ 5-20`), plus exact 2-D Energy Score on `(min_{1..H}, end_H)`. Diagnostic complement to CRPS. |
| 2B. Basel / Kupiec POF and Christoffersen Conditional Coverage | Kupiec (1995); Christoffersen (_IER_, 1998); [BIS Trading Book Risk Survey](https://www.bis.org/publications/messages-academic-literature-risk-measurement-trading-book.pdf) | Marginal breach calibration and temporal breach clustering across sequential origins (e.g., clustered misses inside a single bad salary month caused by 365-day pooling and MAD rare-day replacement). | `LR_uc` (`chi2(1)` unconditional coverage), `LR_ind` (`chi2(1)` Markov transition `pi_01 = pi_11`), and `LR_cc = LR_uc + LR_ind` (`chi2(2)`), cluster-bootstrapped by household to account for overlapping 90-day windows. |
| 2C. Acerbi-Szekely `Z2` and Fissler-Ziegel `FZ0` (VaR + ES) | Acerbi & Szekely ([MSCI 2014 PDF](https://www.msci.com/documents/10199/238916/Research_Insight_Backtesting_Expecting_Shortfall_December_2014.pdf)); Fissler & Ziegel (_Ann. Stat._ 2016); Patton, Ziegel & Chen (_JoE_ 2019, [PDF](https://public.econ.duke.edu/~ap172/Patton_Ziegel_Chen_JoE_2019.pdf)) | Breach severity ("how deep is the average overdraft when the floor breaks?"). Currently unmeasured despite `safe_amount_today` relying internally on P10 VaR. | `VaR_t = Q_0.10(running_min) - floor`, `ES_t = E[running_min - floor | running_min < floor]`; `Z2 = sum(X_t * I_t / (T * alpha * ES_t)) + 1` (`H_0: E[Z2]=0`, requires no independence assumption) and strictly consistent joint `FZ0` loss. |
| 2D. Weighted Interval Score (WIS), M5 extreme tails, and Czado PIT | Bracher et al. (_PLoS Comput Biol_, 2021, [article](https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1008618)); M5 Uncertainty (_IJF_, 2022, [doi](https://www.sciencedirect.com/science/article/pii/S0169207021001722)); Czado, Gneiting & Held (_Biometrics_, 2009, [doi](https://academic.oup.com/biometrics/article/65/4/1254/7333505)) | Separates sharpness from over- and underprediction, scores extreme tails where the 0.99 obligation gate lives, weights errors by rupee obligation exposure, and properly checks discrete rare-day counts. | `IS_alpha = (u-l) + (2/alpha)(l-y)1{y<l} + (2/alpha)(y-u)1{y>u}`, `WIS` decomposed into dispersion + overprediction + underprediction; Pinball/SPL at `TAUS = {0.01, 0.05, 0.10, 0.50, 0.90, 0.95, 0.99}` weighted by rent/EMI exposure at 7–30d; non-randomised conditional-CDF PIT for discrete counts. |
| 2E. Model Confidence Set (MCS), Giacomini-White, and `fev-bench` bootstrap | Hansen, Lunde & Nason (_Econometrica_, 2011, [PDF](https://reinhardhansen.github.io/papers/2011HansenLundeNason-MCS-Econometrica.pdf)); Giacomini & White (_Econometrica_, 2006); `fev-bench` ([arXiv:2509.26468](https://arxiv.org/html/2509.26468)); `TIME` ([arXiv:2602.12147](https://arxiv.org/abs/2602.12147)) | Controls familywise false-positive rate across multi-cell model comparisons (~40% down to 10%) and detects regime-dependent failures hidden in pooled averages. | 90% MCS via block bootstrap (`lag = ceil(h/7)-1`), paired block-bootstrap 95% CIs (`B=1000`) on clipped geometric-mean skill (`fev-bench` convention), and GW conditional predictive ability test using `h_t = (days_to_salary, month_end, emi_window)` (`TIME` regime stratification). |

---

### Rank 3: Real-world banking, recurring-detection, and price-step corpora (Subsystems B and A)

These corpora supply real transaction noise, labeled standing orders, real running balances, and
permanent-vs-promotional price shifts that synthetic personas cannot replicate.

| Benchmark or corpus | Canonical reference | Real-world workload and access | Accuracy and detection metrics |
|---|---|---|---|
| 3A. PKDD'99 Berka Czech Banking Corpus (highest-value public real banking dataset) | Berka & Sochorova ([dataset guide](https://web.archive.org/web/20161019192412/http://lisp.vse.cz/pkdd99/berka.htm); [mirror](https://github.com/jlacko/berka-dataset)) | 4,500 real bank accounts, 1,056,320 transactions (`TRANS`), 6,471 labeled standing orders (`ORDER` with `k_symbol` in insurance, household SIPO, leasing, loan, interest), 682 loans, 892 cards (1993–1998, free for research). Uniquely provides both labeled recurring outflows and real post-transaction balances (`TRANS.balance`). ~1,700 accounts have sufficient density for 90-day windows. | For `series.detect` and `patterns.is_covered`: weighted Precision, Recall, and F1 (per Sluijmers 2022, macro-F1 distorts under class imbalance), onset latency (days from first debit to confident detection), and termination latency (ghost debits after last true occurrence). For `projection`: real balance-path CRPS, `VS_1`, WIS, and `Z2` after rebasing CZK to integer paise. Caveats: transaction-day granularity only, negative overdrafts allowed, no card due-date cycles, `ORDER` gives membership/level rather than explicit cadence dates. |
| 3B. TCPD / TCPDBench protocol and top-8 prune ceiling | van den Burg & Williams ([arXiv:2003.06222](https://arxiv.org/abs/2003.06222); [TCPD](https://github.com/alan-turing-institute/TCPD), [TCPDBench](https://github.com/alan-turing-institute/TCPDBench), [Zenodo v3.0](https://zenodo.org/records/6299767)) | 37 real multi-annotator changepoint series plus the default-vs-oracle evaluation methodology (TCPDBench showed BOCPD drops from 0.890 oracle F1 to 0.696 default F1 due to search/tuning ceilings). | Margin-windowed F1 (`+/-margin` days), Covering (Jaccard segmentation agreement), Annotation Error, and Internal Prune Recall@8 comparing `_pruning_score` top-8 rules against the full unpruned `C * 12` DP grid. |
| 3C. Tatbul Range-Based P/R and BBVA ICAIF '24 downstream protocol | Tatbul et al. (_NeurIPS_ 2018, [arXiv:1803.03639](https://arxiv.org/abs/1803.03639)); Ibrain, Hernandez & Peinado (BBVA AI Factory, _ICAIF '24_, [ACM](https://dl.acm.org/doi/fullHtml/10.1145/3677052.3698596)); Sluijmers ([TU/e 2022 PDF](https://pure.tue.nl/ws/portalfiles/portal/288881421/Sluijmers_M.pdf)); Yousfi ([SISSA 2021 PDF](https://iris.sissa.it/retrieve/dd8a4bf9-032f-20a0-e053-d805fe0a8cb0/Yousfi_Tesi.pdf)) | Asymmetric range-boundary risk (early vs late debit errors have opposite liquidity impact) plus multi-plan single-counterparty disentanglement (e.g., rent + utilities paid to one landlord) where ground-truth labels are absent. | Tatbul range P/R with front-end positional bias `delta` for onset and back-end `delta` for termination; BBVA downstream next-date MAE (days) and next-amount WAPE/CRPS on disentangled sub-series. |
| 3D. Dominick's Finer Foods Price Corpus | Kilts Center, Chicago Booth ([dataset page](https://www.chicagobooth.edu/research/kilts/research-data/dominicks), [codebook](https://www.chicagobooth.edu/-/media/enterprise/centers/kilts/datasets/dominicks-dataset/dominicks-manual-and-codebook_kiltscenter.pdf)); [eurostat/dff](https://github.com/eurostat/dff) (EUPL-1.2) | ~100M weekly real retail observations across ~3,500 UPCs and 29 categories with explicit `price` and `sale` flags (academic use). Directly isolates temporary promotional discounts from permanent price hikes ("INR 499 for 3 months, then INR 899"). | Margin-2-week Step F1 plus Sale-Window False-Step Rate (fraction of temporary promotions misclassified as permanent steps by `series.price_step`), benchmarked against a `ruptures` (PELT) baseline. |

For India-specific production data ingestion (schemas rather than public corpora), validate the
importer-to-`ProjectionInput` seam against **ReBIT Account Aggregator** Deposit schema v2.0.0
([api.rebit.org.in](https://api.rebit.org.in/schema); RBI Master Direction
[DNBR](https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=10598)) and **ISO 20022
`camt.053`**, both of which carry date, direction, amount, counterparty, narration, and running
balance.

---

### Rank 4: Affordability decisioning and chance-constrained SAA verification (Subsystem D)

This suite tests [`affordability.py`](../../../src/arno/engine/affordability.py) and
[`scenario.py`](../../../src/arno/engine/scenario.py) on normative policy invariants, exact
optimization quality, statistical sampling error, and decision stability.

| Standard or benchmark | Canonical reference | Real-world workload and policy scope | Decision accuracy and robustness metrics |
|---|---|---|---|
| 4A. UK FCA CONC 5.2A normative compliance and RBI audit trail | [FCA Handbook CONC 5.2A](https://handbook.fca.org.uk/handbook/conc5/conc5s6) ([PS18/19](https://www.fca.org.uk/publication/policy/ps18-19.pdf)); RBI Digital Lending Directions 2025 para 7 | Maps 1:1 onto Arno's verdicts: no borrowing to repay (`5.2A.12R(3)`), no missed contractual obligations (`(4)` -> `obligations_met`), no severe financial adverse impact (`(5)` -> `min >= floor` and `yes-but`), no third-party guarantee reliance (`5.2A.14R`), statistical non-discretionary spend estimation (`5.2A.15R-19G`), proportionate assessment depth (`5.2A.20R`), durable audit record (RBI para 7). | Zero policy post-condition violations across all verified `yes`/`yes-but` plans (no negative cash on obligation days, no card drawdown funding obligations); durable audit row completeness (`inputs_hash`, `runs`, `seed`, `thresholds`, `verdict`, `binding_constraint`). |
| 4B. Exact-enumeration Common-Random-Numbers (CRN) optimality oracle and SAA LCB | Kleywegt, Shapiro & Homem-de-Mello (_SIAM J. Optim._ 2002, [doi](https://doi.org/10.1137/S1052623499363220)); Pagnoncelli, Ahmed & Shapiro (_JOTA_ 2009, [doi](https://doi.org/10.1007/s10957-009-9523-6)) | Because Arno's lever space (`method x goal_subset x cut_bps in [0, 5000] x wait_date`) is finite and counter-based SplitMix64 supplies free CRN, exhaustive evaluation on the exact same scenario matrix serves as an exact oracle (no MILP dependency required). | Optimality gap `(cost(arno) - cost(exact_opt)) / cost(exact_opt)`, feasibility gap (false `yes`/`yes-but` or missed feasible plan pruned by `_dominates`), and one-sided 95% Wilson/Clopper-Pearson Lower Confidence Bound (LCB) at `0.90` and `0.99`. |
| 4C. FinRegLab cashflow decision calibration, asymmetric regret, and Lipschitz stability | [FinRegLab Empirical Findings (2019)](https://finreglab.org/research/the-use-of-cash-flow-data-in-underwriting-credit-empirical-research-findings); [2025 Consumer ML Paper](https://finreglab.org/research/advancing-the-credit-ecosystem-machine-learning-cash-flow-data-in-consumer-underwriting) | Replays historical purchase probes (5–50% of liquid balance) across real-ledger and Berka origins against realized post-purchase paths (reject-inference limitations explicitly disclosed). | AUC and KS of `floor_probability` rank-ordering realized breaches, reliability curve at 0.90, asymmetric cost-weighted regret (`c_FN >> c_FP`, where false `yes` causes missed obligations), `safe_amount_today` regret vs realized safe amount, and Lipschitz perturbation flip rate over `K=50` perturbations (income `+/-1%`, one spend day `+/-INR 500`, bill date `+/-2d`). |

#### Monte Carlo SAA precision at `S = 16,384`

The table below quantifies binomial sampling error for Arno's two decision gates at the shipped run
count (`DEFAULT_RUNS = 16_384`, [`projection.py:32`](../../../src/arno/engine/projection.py#L32)).

| Decision gate | Threshold `p` | Binomial SE at `S = 16,384` | 95% CI half-width | Required `P_hat` for 95% one-sided confidence (`LCB_95 >= p`) |
|---|---|---|---|---|
| `P(min >= floor)` | 0.90 | 0.234 pp (`2.34e-3`) | +/-0.459 pp | `>= 0.9039` |
| `P(obligation_met)` | 0.99 | 0.078 pp (`7.77e-4`) | +/-0.152 pp | `>= 0.9913` (or raise `S >= 38,000` for `+/-0.10 pp` bound) |

Do not apply full-path Sobol quasi-Monte Carlo: at 3,650 days x multiple streams, effective
dimension collapses QMC back to `O(S^-1/2)`. Use a one-sided Wilson/Clopper-Pearson LCB gate first;
if variance reduction is needed, stratify only the scenario index (`u_scenario` via scrambled Sobol
or Latin Hypercube while keeping the day axis hashed) and report effective sample size `n_eff`.

---

### Rank 5: Production systems, concurrency, memory, and PRNG quality (Runtime and kernel)

These benchmarks target Arno's real operational bottlenecks: synchronous CPU-bound NumPy on
FastAPI's event loop, per-chunk RSS growth, worst-case affordability fan-out, and SplitMix64
counter-based stream independence.

| Benchmark or suite | Canonical reference | Real-world production workload | Performance and quality metrics |
|---|---|---|---|
| 5A. k6 open-model load test and server-side event-loop lag heartbeat | [k6 Thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds); [`constant-arrival-rate`](https://grafana.com/docs/k6/latest/using-k6/scenarios/executors/constant-arrival-rate) | Mixed API traffic (`GET /api/home`, `GET /api/projection`, worst-case `no`-verdict `afford`, concurrent SSE chat) at 1, 2, 5, and 10 RPS using an open arrival model (closed VU models mask event-loop stalls via coordinated omission). | API p50, p95, p99, and p99.9 latency, `dropped_iterations == 0`, and server-side asyncio heartbeat drift (10 ms timer vs `time.perf_counter()`: max lag, p99 lag, count of stalls `>100 ms`). Directly detects and validates fixing inline-NumPy blocking (`asyncio.to_thread` behind a bounded `Semaphore`). |
| 5B. Airspeed Velocity (ASV) and `pytest-memray` macro-workloads | [ASV Writing Benchmarks](https://asv.readthedocs.io/en/latest/writing_benchmarks.html); [`pytest-memray`](https://pytest-memray.readthedocs.io/en/latest/usage.html) | Four production macro-workloads: (1) `kernel.run` at 90d and 3,650d, (2) chunk peak memory at 3,650d (with `Spec` built in `setup_cache`), (3) worst-case `no`-verdict `afford()` fan-out (capped card + 3,650d), (4) 500-payee `series.detect` sweep plus 52-origin DB snapshot + walk. | Throughput in `scenario-days/sec`, high-water allocation per 127-day chunk (`@pytest.mark.limit_memory`), PR wall-time regression gate (`<=5%` via `pytest-benchmark` on pinned runner), and `n_eff` / SE per wall-second. |
| 5C. PractRand, TestU01 BigCrush, and 2-D inter-stream serial battery | [PractRand](https://pracrand.sourceforge.net/PractRand.txt); [PractRand Tests Overview](https://github.com/MartyMacGyver/PractRand/blob/master/doc/Tests_overview.txt); L'Ecuyer & Simard (_ACM TOMS_ 2007, [PDF](https://dl.acm.org/doi/pdf/10.1145/1268776.1268777)) | Streams `kernel.draw` (64-bit) and `kernel.below(draw, B)` for production bucket counts `B in {2, 6, 10, 100, 1000, 4096}`, 4-way high-32/low-32/bit-reversed projections, and adjacent `(stream, stream+1)` pairs at fixed day. | Zero PractRand anomalies (`stdin32` and `stdin64`, auto-extending to >=1 TB nightly), 160/160 TestU01 BigCrush passes per release, and 64x64 2-D `chi2` inter-stream independence. |

#### Quantified `below()` bucket bias check

[`kernel.below`](../../../src/arno/engine/kernel.py#L66-L68) computes `((x >> 32) * B) >> 32`.
Per-bucket relative bias is bounded above by `B / 2^32`. At `B = 1000`, relative bias is `2.3e-7`
(~0.004 scenarios over 16,384 paths, compared with ~78 scenarios of Monte Carlo standard error: a
bias-to-noise ratio of `5e-5`). A rejection loop is unnecessary; assert `B < 2^32` explicitly in
code so future recency-weighted repeat tables cannot wrap silently.

---

### Explicitly excluded benchmarks

The table below lists popular benchmarks intentionally excluded from Arno's evaluation roadmap.

| Excluded item | Reason for exclusion |
|---|---|
| HackerRank "Buy or Wait" (`hackerrank.py`) | `n = 25`, deterministic, card-free, zero spend pool, tuned-on-test adapter heuristics. Retain only as a legacy smoke check, never a quality benchmark. |
| PaySim, BankSim, AMLSim, Kaggle PCA fraud sets | Synthetic toy generators or anonymized fraud classification tables lacking real recurring cadences and daily bank balances. |
| GIFT-Eval, Monash, BOOM raw leaderboards | Wrong unit of analysis (macro cross-domain series, not single-household paise liquidity); adopt only their aggregation and leakage reporting discipline (`fev-bench`, `TIME`). |
| NIST SP 800-22 and `dieharder -a` | Cryptographic or superseded batteries; PractRand's own comparative review recommends PractRand + TestU01 BigCrush over `dieharder -a` and NIST STS for simulation PRNGs. |
| ECE and isotonic calibration curves | Redundant with Murphy's Brier reliability decomposition already implemented in [`scoring.py:141-160`](../../../src/arno/engine/scoring.py#L141-L160). |
| Fair-CRPS finite-sample correction | Bias is `O(1/S)`; at `S = 16,384` the relative difference is `~3e-5`, below noise. |

---

## 4. Harnessing architecture for the `engine/` package

Arno's pure-function boundaries allow all five benchmark suites to attach cleanly without adding
heavy dependencies (`pandas`, `scipy`, `torch`, or MILP solvers) to production runtime.

### Exact code seams and adapters

1. **Regulatory financial math (`tests/test_finmath_conformance.py`):**
   - **RBI KFS Annex B:** call `money.amortize()` and `money._compute_apr()` on `1_960_000` paise;
     expose explicit `apr_basis` (`"nominal_monthly"` vs `"effective_annual"`) and pin unrounded vs
     disclosed EMI behavior.
   - **Strata day-count golden suite:** commit a 400-pair golden JSON vector generated from OpenGamma
     Strata's `StandardDayCounts` (`ACT/365F`, `ACT/ACT ISDA`, `30E/360`, `30/360 US`) and assert
     exact parity against `money._days_30_360` (renamed/fixed to `30e/360`) and
     `money._period_interest`.
   - **ACTUS `ANN`/`PAM`:** map pinned `actus-tests-ann.json` and `actus-tests-pam.json` contract
     terms (`notionalPrincipal`, `nominalInterestRate`, `cycleOfPrincipalRedemption`,
     `dayCountConvention`) into `money.amortize()`, asserting exact schedule dates and `<= 1` paisa
     cashflow error.
2. **Path-coherence, tail-risk, and statistical gates (`src/arno/engine/scoring.py` and `backtest.py`):**
   - Add pure-NumPy functions (~225 LOC) to [`scoring.py`](../../../src/arno/engine/scoring.py):
     `variogram_score(paths_at_checkpoints, actual_at_checkpoints, p=1.0)`,
     `energy_score_2d(min_samples, end_samples, actual_min, actual_end)`,
     `wis(samples, outcome, taus)` (reusing `pinball`), `kupiec_christoffersen(hits)`,
     `acerbi_szekely_z2(min_samples, actual_min, floor, alpha=0.10)`,
     `fz0_loss(var, es, outcome, alpha)`, `model_confidence_set(loss_matrix, lag, B=1000)`, and
     `giacomini_white(loss_diff, instruments, lag)`.
   - In [`backtest.py:236-278`](../../../src/arno/engine/backtest.py#L236-L278), slice the already
     computed `(16384, H+1)` matrix at `checkpoints_for()` ([`projection.py:1492-1502`](../../../src/arno/engine/projection.py#L1492-L1502))
     for `VS_1`, compute `VaR`/`ES` from `running_min`, and expand `TAUS` to
     `(0.01, 0.05, 0.10, 0.50, 0.90, 0.95, 0.99)`.
   - Fix two harness defects first: group persona detection by normalized `payee` text rather than
     ground-truth `payee_key` ([`backtest.py:365-371`](../../../src/arno/engine/backtest.py#L365-L371)),
     and run 3–5 simulation seeds per origin with enforceable CI exit gates on coverage, skill, and
     Christoffersen independence.
   - Unblock the real-DB backtest path ([`backtest.py:808-854`](../../../src/arno/engine/backtest.py#L808-L854))
     and add a ReBIT Account Aggregator / ISO 20022 `camt.053` fixture loader feeding
     `inputs.build_inputs()`.
3. **Real banking and price-step evaluation (`scripts/eval_berka.py`, `scripts/eval_dominicks.py`):**
   - **Berka adapter:** stream `TRANS` per `account_id` into `series.Payment(movement_id=trans_id,
     date=d, amount_paise=round(czk * 100))` and `ProjectionInput` (`SpendRow` + `LiquidAccount` from
     `TRANS.balance`); map `ORDER` rows to `patterns.KnownSchedule`. Score `series.detect` and
     `patterns.is_covered` with weighted P/R/F1, Tatbul front/back range P/R, and onset/termination
     latency; score `projection.simulate_paths` across ~1,700 dense accounts with CRPS, `VS_1`, WIS,
     and `Z2`.
   - **Prune-ceiling unit test:** add an optional `prune: bool = True` debug parameter at
     [`series.py:1232`](../../../src/arno/engine/series.py#L1232) and assert `recall@8 == 1.0`
     against the full `C * 12` unpruned grid on persona fixtures and a Berka slice.
   - **Dominick's adapter:** feed weekly `(store, upc)` prices into `series.price_step()` and score
     margin-2-week Step F1 and Sale-Window False-Step Rate against a `ruptures` PELT baseline.
4. **Affordability exact oracle and decision backtest (`tests/test_affordability_oracle.py`):**
   - Capture the common scenario matrix via `kernel.run(spec, settings)` and exhaustively enumerate
     `(method, goal_subset, cut_bps_step, wait_date)` on the exact same `seed`; assert zero
     feasibility gap and bounded optimality gap against `affordability.afford()`.
   - Add a `decision_backtest` mode in `backtest.py` probing purchases at 5–50% of liquid balance on
     walk-forward origins and scoring AUC, reliability at 0.90, asymmetric regret (`c_FN = 10 * c_FP`),
     `safe_amount_today` regret, and `K=50` Lipschitz perturbation flip rate.
   - Replace point-estimate gating at 0.99 ([`affordability.py:260-261`](../../../src/arno/engine/affordability.py#L260-L261))
     with a one-sided 95% Wilson/Clopper-Pearson LCB (or `runs >= 38_000`) and assert FCA CONC 5.2A
     post-conditions as invariants.
5. **PRNG and systems CI gates (`benchmarks/`, `scripts/prng_stream.py`):**
   - **PRNG:** write a 30-line `scripts/prng_stream.py` emitting raw `uint64` (`kernel.draw`) and
     `uint32` (`kernel.below(..., B)`) bytes to stdout for nightly `RNG_test stdin64`/`stdin32`
     (PractRand) and release TestU01 BigCrush runs (`unif01_CreateExternGenBits`).
   - **ASV and `pytest-memray`:** implement the 4 macro-benchmarks (`time_walk[90d, 3650d]`,
     `peakmem_walk[3650d]` using `setup_cache`, `time_afford_worstcase_no`,
     `time_series_detect_sweep[500]`), tracking `scenario-days/sec` and enforcing
     `@pytest.mark.limit_memory` per 127-day chunk.
   - **k6 and event-loop lag:** expose a dev/bench 10 ms asyncio heartbeat metric on FastAPI and run
     a k6 `constant-arrival-rate` script at 1, 2, and 5 RPS against `/api/home` and
     `/api/projection`, gating on p99 latency, `dropped_iterations == 0`, and event-loop lag `< 100
     ms`.

### Recommended implementation sequence

> **Scorecard migration note:** adding Christoffersen independence, `VS_1`, and `Z2` will likely
> surface failures on the existing synthetic personas (breach clustering from 365-day pooling and
> rare-day replacement) before the engine is tuned against them. Decide upfront whether the
> `<!-- backtest -->` block in [`README.md`](../../../README.md) regenerates in the same change or
> after model remediation.

The table below orders implementation phases by defect-finding leverage and engineering cost.

| Phase | Deliverable | Defects caught immediately |
|---|---|---|
| 1. Quick, high-ROI | RBI Annex B test vector + Strata 400-pair day-count oracle + `series.py` prune-ceiling (`recall@8`) unit test | Confirmed `30E/360` naming bug, RBI unrounded-EMI APR sensitivity, silent top-8 candidate drops |
| 2. Core scoring | `VS_1`, 2-D Energy, WIS, Kupiec/Christoffersen, `Z2`/`FZ0`, MCS, Giacomini-White, payee-text detection, and multi-seed CI gates in `scoring.py`/`backtest.py` | Autocorrelation blindness, breach clustering, 66–77% miscalibration, ~40% DM false-positive rate |
| 3. Decision and SAA | Exact-enumeration CRN affordability oracle, LCB-gated 0.99 check, FCA CONC 5.2A post-conditions, `decision_backtest`, perturbation flip rate | Sampling-noise flips at 0.99, Pareto pruning holes under card caps, unstable verdicts |
| 4. Real data and contracts | PKDD'99 Berka harness (`series.detect` + real balance paths) + Dominick's `price_step` harness + ACTUS `ANN`/`PAM` JSON conformance | Real-world narration and cadence noise, promo-vs-step confusion, amortization schedule edge cases |
| 5. Systems and PRNG | `pytest-memray` + ASV 4 macro-workloads + k6 open-model event-loop lag gate + PractRand/BigCrush pipeline | Event-loop starvation under load, chunk RSS bloat, latency regressions, PRNG stream correlation |

---

## 5. Verification status and source confidence

The table below records how each finding in this report was verified so future readers can separate
direct code inspection and live measurements from secondary citations.

| Source group | Verification status | Notes |
|---|---|---|
| All 13 modules in `src/arno/engine/`, production callers (`tools/`, `api/`, `agent/`, `jobs/`, `cli.py`), `README.md`, `tests/invariants.py`, `docs/arno/plan.md`, `docs/arno/engine-blueprint.md` | Directly audited in repo | Backed by exact `file:line` citations at HEAD. |
| RBI KFS Annex B APR (`17.0702%` vs `17.0988%` vs `18.5046%`) and `_days_30_360` = `30E/360` | Live read-only engine execution + web-verified circular | Executed against `arno.engine.money._compute_apr` without modifying files. |
| ACTUS specs and JSON test beds, RBI 2024/2025 circulars, CFPB Reg Z Appendix J, EU Directive 2023/2225, Strata and QuantLib day counts, FCA CONC 5.2A, FinRegLab studies, ReBIT AA v2.0.0 | Verified online | Official regulatory pages, specifications, and repositories inspected. |
| PKDD'99 Berka, Dominick's Finer Foods, TCPD/TCPDBench, GIFT-Eval, `fev-bench`, M5 Uncertainty, Monash, BOOM, TIME, BBVA ICAIF '24, Sluijmers 2022, Yousfi 2021 | Verified online | Papers, dataset guides, and repositories inspected; absence of public consumer balance-path and labeled recurring-payment benchmarks verified negative. |
| Scheuerer-Hamill `VS_p`, Kupiec, Christoffersen, Acerbi-Szekely `Z2`, Fissler-Ziegel `FZ0`, Bracher WIS, Hansen-Lunde-Nason MCS, Giacomini-White, Czado PIT, Tatbul range P/R, PractRand, TestU01, ASV, `pytest-memray`, k6 | Verified online | Primary papers and tool documentation checked; standard Diebold-Mariano (1995), Newey-West (1987), and `ruptures`/PELT cited from standard statistical reference without re-fetch. |
| IBM `decTest` suite for Python `decimal`, EBA/GL/2020/06 EU loan origination guidelines | Unverified secondary mentions | Confirm exact section numbers before citing in conformance test docstrings. |
