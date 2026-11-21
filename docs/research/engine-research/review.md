# Audit of the `engine/` package, caller seams, and benchmark roadmap

_Applied code audit and benchmark selection for Arno's numerical core (`src/arno/engine/`),
`src/arno/india/`, and its caller and harness seams, compiled November 21, 2026. This document
belongs to `docs/research/` and builds on [`benchmarks.md`](benchmarks.md) as an established
baseline; it does not summarize that baseline or repeat its 15 known defects except where expanding
blast radius or refuting a false positive. [`plan.md`](../../arno/plan.md) owns decisions._

---

## Baseline refutations and blast-radius corrections

Every baseline claim in [`benchmarks.md`](benchmarks.md) was cross-checked directly against `HEAD`
and, where numeric, reproduced with read-only Python probes.

- **Refuted false positive (Subsystem A #4):** `curve[:, 1] + cumsum(unconfirmed)`
  ([`projection.py:1383`](../../../src/arno/engine/projection.py#L1383)) is _not_ a
  sum-of-quantiles bug. `spec.unconfirmed` is a deterministic 1-D vector identical across all
  scenarios ([`kernel.py:452,586`](../../../src/arno/engine/kernel.py#L452)), and quantiles are
  translation-equivariant (`Q_p(X_t + c_t) == Q_p(X_t) + c_t` identically). Both
  `optimistic_minima` and `optimistic_crossings` are also computed per scenario.
- **Confirmed baseline (`hackerrank.py` `runs` is effectively inert):** `_build_projection_input`
  ([`hackerrank.py:1166-1178`](../../../src/arno/engine/hackerrank.py#L1166-L1178)) supplies zero
  `categories`, zero `spend`, zero `cards`, and zero `series`, so every scenario in `kernel.run` is
  deterministic and identical even though `runs` is passed into `safe_amount_today`. The
  `_amount_ok` denominator critique
  ([`hackerrank.py:1539-1541`](../../../src/arno/engine/hackerrank.py#L1539-L1541)) is a valid
  metric smell, though immaterial on rows where `expected == requested`.
- **Corrected RBI Annex B effective-APR pairing (`benchmarks.md:44,180`):** `18.5046%` is the
  effective annual rate of the _disclosed rounded_ EMI (`17.0988%` nominal →
  `(1 + 0.170988 / 12)^12 - 1 = 18.5046%`). The _unrounded_ EMI (`17.0702%` nominal) has an
  effective annual rate of `18.4711%`. Fix this row pairing before locking Stage 1A golden vectors.
- **Narrowed `_days_30_360` and `infer_rate` blast radii (`benchmarks.md:44,183-187`):** across a
  70,080-pair monthly-schedule sweep (8 years × 24 periods), only 110 pairs differ between
  `_days_30_360` (30E/360) and US 30/360 — exclusively loans anchored on the 31st crossing February
  (by 1 day). The `"actual/365"` issue is naming-only (`"actual/365f"`), not numeric. For
  `infer_rate` (`exact=True` across a plateau at
  [`money.py:809-814`](../../../src/arno/engine/money.py#L809-L814)), its sole caller
  ([`agent/toolset.py:561-565`](../../../src/arno/agent/toolset.py#L561-L565)) takes the midpoint
  and preserves the bracket note, so impact is flag semantics (`exact = first_ge == last_le`).
- **Refined `series.py` sample-cap wording (`benchmarks.md:43`):** `_summarize_amounts` stores the
  _last_ 12 samples (`amounts[-12:]`), which the kernel then draws uniformly
  ([`kernel.py:413-414`](../../../src/arno/engine/kernel.py#L413-L414)).
- **Refined peak chunk memory scaling (`benchmarks.md:42`):** one `16384 × 127` `int64` column is
  15.87 MiB (16.6 MB), and full 3,650-day path retention (`3651` columns) is 456.5 MiB (478.7 MB).
  Because `_spend` allocates `2 + 3C` columns for `C` cards before ~10–15 chunk temporaries
  ([`kernel.py:676-683`](../../../src/arno/engine/kernel.py#L676-L683)), peak chunk memory is
  100–250 MiB only for 0–1 cards and reaches ~285–365 MiB at 2 cards (330+ MiB at 3+ cards).
- **Expanded blast radius across three known defects:**
  1. Inline event-loop blocking (Subsystem A #1) extends to _6 seams_, including background jobs
     and workbook export, and a single far goal deadline silently expands every request to 3,650
     days (see L1).
  2. Card billing split (Subsystem A #3) extends to `ledger/schedules.py:upcoming()`, propagating
     unbilled spend and duplicate bills across chat, briefings, reminders, and the web UI (see A4).
  3. Pareto dominance before `_verify` (Subsystem D #3) is a _false-negative_ path, not only cost
     suboptimality: a dominating plan can fail verification while a passing dominated plan is never
     tried, and tied plans delete each other (see A11 and A12).

---

## 1. Ranked new and expanded defects

Defects are ordered **Accuracy → Latency and memory → Maintainability**, then by severity. Every
numeric or behavioral claim below was verified in source and reproduced against `HEAD`.

### Accuracy

#### A1. Critical: Credit card bills are never checked by the 0.99 obligation gate

- **Where:** [`projection.py:1061-1112`](../../../src/arno/engine/projection.py#L1061-L1112),
  [`projection.py:673-692`](../../../src/arno/engine/projection.py#L673-L692),
  [`kernel.py:560-571`](../../../src/arno/engine/kernel.py#L560-L571).
- **Bug:** `obligation_days` records only `CashEvent` items with
  `kind in {"obligation", "instalment", "owed_payment"}`. Credit card bill due dates (`card.bills`
  and `card.due_of_day`, including hypothetical `method="card"` and `"emi"` bills) are never added
  to `obligation_days`. In addition, `kernel.py:561` skips `day == 0`
  (`if failures is not None and day > 0`).
- **Reproduction:** bank balance ₹10,000, `floor = -₹1,00,000`, ₹50,000 card bill due on day 19 →
  `median_low = -₹40,000`, yet `obligations_met = True`. With `floor = 0`, a credit card bill can
  bounce in 9.9% of scenarios and `afford()` still returns `"yes"`.
- **Minimal patch:** include every card bill due date (`due > 0` in `card.bills` and
  `np.unique(card.due_of_day)`) in `obligation_days` (or test every `due_sets` day where
  `bill_paid > 0` directly in `kernel.run`), and test day-0 obligations and bills against
  `start_balance + inflow[0]`.

#### A2. Critical: The `afford()` spending-cut solver ignores the 0.99 obligation constraint

- **Where:** [`affordability.py:333-351`](../../../src/arno/engine/affordability.py#L333-L351),
  [`affordability.py:389-397`](../../../src/arno/engine/affordability.py#L389-L397),
  [`affordability.py:481-485`](../../../src/arno/engine/affordability.py#L481-L485).
- **Bug:** `_least_cut` computes critical cut values solely for `floor - balances` at `threshold`
  (0.90), ignoring `payable >= 0` at `OBLIGATION_THRESHOLD` (0.99). When a purchase clears the 0.90
  floor (`cut == 0`) or needs a deeper cut to clear an obligation than to clear the floor, `_plans`
  drops `cut == 0` (`plan.levers > 0`) and `_verify` bumps by at most `VERIFY_STEPS = 4` bps
  (0.04%), returning a false `"no"`.
- **Reproduction:** ₹600 bank balance, ₹150 rent on day 10, ₹50/day reducible spend, ₹20 purchase
  on day 1, `floor = -₹5,000` → `floor_probability = 1.0`, `obligations_met = False`. `afford()`
  returns `verdict = "no"` and `tradeoffs = ["wait"]`, whereas a 10% cut passes 100% of scenarios
  (`verdict = "yes"`).
- **Minimal patch:** capture pre-obligation `payable` matrices in `_Matrix` (or include
  `obligation_days` in `checkpoints` with their pre-obligation balances) and take
  `max(floor_cut, obligation_cut_99)` in `_least_cut`, using the 99th-percentile rank for
  obligation days.

#### A3. High: `load_snapshot` hardcodes `attributed=True`, double-counting unlinked schedules

- **Where:** [`tools/projection.py:760`](../../../src/arno/tools/projection.py#L760),
  [`tools/projection.py:348-351`](../../../src/arno/tools/projection.py#L348-L351),
  [`inputs.py:186`](../../../src/arno/engine/inputs.py#L186).
- **Bug:** `schedule_payments` linking is manual (`create_schedule` does not back-link history),
  yet `load_snapshot` unconditionally asserts `attributed=True`. The fallback guard
  `if category.id in covered and not snapshot.attributed` in `inputs.py:186` is dead in production.
  Accepting a pattern proposal ("Add Netflix ₹649/month as an obligation") keeps 90 days of Netflix
  in `spend` _and_ adds the schedule as an obligation, double-counting the recurring charge every
  month.
- **Minimal patch:** compute per-category attribution (drop a covered category unless its schedules
  have linked `schedule_payments` in the history window, or pass `attributed_categories: set[str]`)
  as a short-term guard, and back-link matching movements inside `create_schedule` as the permanent
  fix.

#### A4. High: `schedules.upcoming()` reports full card outstanding and repeats it on every due date

_Expands Subsystem A #3 in [`benchmarks.md`](benchmarks.md)._

- **Where:** [`ledger/schedules.py:526-532`](../../../src/arno/ledger/schedules.py#L526-L532),
  [`ledger/schedules.py:558-580`](../../../src/arno/ledger/schedules.py#L558-L580).
- **Bug:** `card_amount_due` returns raw `-balance_paise` (billed plus unbilled spend) and stamps
  that full amount onto _every_ due date in `[start, end]` (up to 24 copies when `days=730`). The
  overstated figure propagates to `tools/read.py:388`, `jobs/briefing.py:87-89`,
  `jobs/reminders.py:54-67`, and the web UI, disagreeing with `projection.py`.
- **Minimal patch:** extract one `card_billing_split(conn, card, as_of)` helper in
  `ledger/common.py` shared by `tools/projection._unbilled` and `schedules.upcoming`; place
  `billed_paise` on the first due date, `unbilled_paise` on the second (if in window), and `None`
  on later due dates.

#### A5. High: Overdue and due-today unpaid obligations are excluded from projections

- **Where:** [`tools/projection.py:602-604`](../../../src/arno/tools/projection.py#L602-L604).
- **Bug:** `expand_schedules` starts at `as_of + timedelta(days=1)`. An unpaid obligation due today
  or 1–3 days late triggers `detect_missed` alerts and appears in `upcoming()`, yet vanishes from
  `inputs.events`, overstating `safe_to_spend` and `floor_probability` by the full unpaid bill.
- **Minimal patch:** expand `obligation` and `instalment` schedules from
  `as_of - timedelta(days=MISSED_AFTER_DAYS)`, keep occurrences with `remaining_paise > 0`, clamp
  `date <= as_of` to `as_of + timedelta(days=1)`, and append a data-quality gap note matching
  `_owed_events`.

#### A6. High: Date-only `UNTIL=YYYYMMDD` silently drops the final occurrence

- **Where:** [`india/recurrence.py:24-33`](../../../src/arno/india/recurrence.py#L24-L33),
  [`india/recurrence.py:66-80`](../../../src/arno/india/recurrence.py#L66-L80).
- **Bug:** `_UNTIL_DATETIME` rewrites only `UNTIL=YYYYMMDDTHHMMSS(Z?)` to `235959`. Standard RFC
  5545 date-only `UNTIL=20260305` is parsed by `dateutil` as `00:00:00` while `parse_rule` anchors
  `dtstart` at `12:00:00`, dropping the last EMI or rent payment.
- **Reproduction:** `expand_rule("FREQ=MONTHLY;BYMONTHDAY=5;UNTIL=20260305", date(2026, 1, 1), ...)`
  returns `[2026-01-05, 2026-02-05]` (missing `2026-03-05`).
- **Minimal patch:** change `_UNTIL_DATETIME` to
  `re.compile(r"UNTIL=(\d{8})(?:T(\d{6}))?(Z?)", re.IGNORECASE)` and default a missing time group
  to `"000000"` in `_inclusive_until`.

#### A7. High: `patterns.py` misclassifies normalized month-end rules as twice-monthly and treats `None` cadence as a wildcard

- **Where:** [`patterns.py:57-70`](../../../src/arno/engine/patterns.py#L57-L70),
  [`patterns.py:99-100`](../../../src/arno/engine/patterns.py#L99-L100),
  [`india/recurrence.py:55`](../../../src/arno/india/recurrence.py#L55).
- **Bug:** two compounding cadence defects:
  1. `normalize_rule("FREQ=MONTHLY;BYMONTHDAY=31")` emits
     `FREQ=MONTHLY;BYMONTHDAY=31,-1;BYSETPOS=1`. `rule_interval_days` checks
     `"," in bymonthday.group(1)` and returns `15` (twice-monthly) instead of `30`, breaking
     `same_charge` coverage on month-end (days 29–31) obligations.
  2. `rule_interval_days("FREQ=DAILY;INTERVAL=1")` returns `None`, and `same_charge` skips the
     cadence check when `schedule.interval_days is None`, silently suppressing any same-category,
     same-amount series regardless of cadence.
- **Minimal patch:** in `rule_interval_days`, return `step` for `FREQ=DAILY` and return `15` for
  comma-separated `BYMONTHDAY` only when `BYSETPOS=1` is absent. In `same_charge`, require
  `schedule.interval_days is not None and schedule.interval_days == interval_days`.

#### A8. High: `_charge_after_loose` violates intra-day priority order in shipped `MODEL` (`limit_spend=False`)

- **Where:** [`kernel.py:825-835`](../../../src/arno/engine/kernel.py#L825-L835) versus
  [`kernel.py:12-14`](../../../src/arno/engine/kernel.py#L12-L14); test oracle at
  [`tests/test_engine_kernel.py:205-219`](../../../tests/test_engine_kernel.py#L205-L219).
- **Bug:** line 826 adds discretionary resampled card spend (`used += kept[:, column]`) _before_
  computing `room` and `obligation_overflow` for that day's card obligations, contradicting the
  declared intra-day order ("obligations ... then spend") and the `limit_spend=True` path.
  `tests/test_engine_kernel.py::_reference_walk` currently enshrines the loose-first order.
- **Reproduction:** ₹10,000 card limit, ₹0 bank cash, day-1 ₹8,000 card obligation plus ₹5,000
  loose card spend → shipped `MODEL` (`limit_spend=False`) returns `obligations_met = False`
  (`misses = 1`), whereas `limit_spend=True` returns `obligations_met = True` (`misses = 0`).
- **Minimal patch:** compute `obligation_overflow` and charge `piece` against `card.limit - used`
  before adding `kept[:, column]` to `used`, and update `_reference_walk` to match the docstring
  contract.

#### A9. High: `series._summarize_amounts` invents never-paid levels, keeps pre-step samples, and disagrees with `price_step`

- **Where:** [`series.py:902-939`](../../../src/arno/engine/series.py#L902-L939),
  [`tools/patterns.py:440,468`](../../../src/arno/tools/patterns.py#L440).
- **Bug:** three defects in one function:
  1. On a two-step series (`[50000]*6 + [70000]*3 + [90000]*3`), `run1` fails `_is_fixed_run`,
     falling through to `variable` with `level_paise = 60000` (₹600 — an amount never charged),
     which becomes both the proposal amount and the `is_covered` key.
  2. On a one-step series (`[50000]*3 + [90000]*3`), `samples` still stores `amounts[-12:]`
     (including the obsolete ₹500 rows) instead of `run2[-12:]`.
  3. `_summarize_amounts` allows `split >= 2`, while `price_step` requires `_MIN_FIXED_RUN = 3`,
     so stored `amount_kind` and price-change proposals disagree on `[500, 500, 900, 900]`.
- **Minimal patch:** run `price_step(amounts)` first so single-step and multi-step series share the
  `_MIN_FIXED_RUN = 3` rule and use `amounts[step.index:]` for both `level_paise` and
  `samples=tuple(run2[-_MAX_SAMPLES:])`.

#### A10. High: `scoring.hac_estimate` centers on the pooled mean (3.45× SE inflation) and `diebold_mariano` returns `p = 0.00` for `n = 1`

- **Where:** [`scoring.py:81-88`](../../../src/arno/engine/scoring.py#L81-L88),
  [`scoring.py:105-111`](../../../src/arno/engine/scoring.py#L105-L111).
- **Bug:** centering each `persona:seed` group on the _pooled_ mean converts between-household
  level differences `c_g^2` into spurious lag-`k` autocovariance across lags `1..12`. On four
  zero-autocorrelation iid groups, SE inflates from `492.2` (`lag=0`) to `1699.7` (`lag=12`) — a
  3.45× distortion that invalidates every h=60/90 confidence interval, Diebold-Mariano p-value, and
  downstream Model Confidence Set (Rank 2E). Separately, `n = 1` produces `error == 0` and
  `diebold_mariano([[5.0]], [[0.0]], 12) -> (inf, 0.0)`.
- **Minimal patch:** center each group on its own mean `m_g` inside the lag loop, add between-group
  variance `sum(n_g * (m_g - mean)**2)` once at lag 0 only, and return `(math.nan, math.nan)` in
  `diebold_mariano` when `n < 2` or `error == 0`.

#### A11. Medium: Symmetric Pareto dominance drops both tied plans

_Expands Subsystem D #3 in [`benchmarks.md`](benchmarks.md)._

- **Where:** [`affordability.py:400`](../../../src/arno/engine/affordability.py#L400),
  [`affordability.py:414-422`](../../../src/arno/engine/affordability.py#L414-L422).
- **Bug:** `_dominates(better, plan)` uses `<=` on all four dimensions and skips only
  `better == plan`. Two passing plans with different `method` strings (for example `"card"` and a
  zero-fee no-cost `"emi"`) and identical `(goals, cut_bps, cost_paise)` satisfy
  `_dominates(card, emi) == True` _and_ `_dominates(emi, card) == True`, so line 400 deletes both
  plans and can turn a passing `"yes-but"` into a false `"no"`.
- **Minimal patch:** require strict improvement in at least one dimension, or add a total
  deterministic method tie-breaker (`asked < card < emi`) so `_dominates` is a strict partial order.

#### A12. Medium: `_least_cut`'s `impossible` mask discards feasible capped-card cuts before `_verify` bisection runs

- **Where:** [`affordability.py:346-350`](../../../src/arno/engine/affordability.py#L346-L350),
  [`affordability.py:486-496`](../../../src/arno/engine/affordability.py#L486-L496).
- **Bug:** `_measure_reducible` credits card reducible spend only on `due_of_day`. When a card is
  capped (`matrix.capped == True`), cutting card spend reduces same-day cash spill _before_ the
  bill date. Yet at an early checkpoint where `short > 0` and `reducible == 0` (or where the
  uncapped upper bound exceeds `MAX_CUT_BPS = 5000`), `_least_cut` returns `None`, so `_verify`'s
  capped-card bisection is never reached.
- **Minimal patch:** when `matrix.capped` is true and `_least_cut` returns `None`, keep a candidate
  at `cut_bps = MAX_CUT_BPS` so `_verify` can test whether a feasible cut exists below the cap and
  bisect down.

#### A13. Medium: Card-EMI principal never consumes the credit limit, and future-dated card checks ignore upcoming bill repayments

- **Where:** [`affordability.py:176-191`](../../../src/arno/engine/affordability.py#L176-L191),
  [`affordability.py:220-246`](../../../src/arno/engine/affordability.py#L220-L246).
- **Bug:** two card-limit accounting gaps:
  1. `_apply` checks `amount <= card.available_credit_paise` at start of horizon, then adds only
     monthly installment `CashEvent`s to `card.other`. In Indian card EMI, the full principal blocks
     available credit on purchase day and releases only as installments bill and pay; the engine
     leaves `principal - instalment_1` of phantom headroom early in the tenure.
  2. For a future purchase date `when > as_of`, `_apply` rejects based on _today's_
     `card.available_credit_paise`, ignoring bills that are paid between `as_of` and `when`
     (reproduced: ₹1,50,000 limit, ₹1,00,000 outstanding due in 10 days, ₹1,00,000 purchase in 20
     days → rejected with `"Card has only ₹50,000 of available credit"`).
- **Minimal patch:** for EMI, block the remaining principal on `CardSpec` from `when` and release
  principal per installment; for future card/EMI dates, evaluate available credit at `when` from
  projected card `used` rather than day-0 outstanding.

#### A14. Medium: Threshold and rounding mismatch across `_floor_share`, `_first_crossing`, `_safe_to_spend`, `safe_amount_today`, and `afford(threshold=...)`

- **Where:** [`projection.py:1183-1234`](../../../src/arno/engine/projection.py#L1183-L1234),
  [`projection.py:1283`](../../../src/arno/engine/projection.py#L1283),
  [`projection.py:1487`](../../../src/arno/engine/projection.py#L1487),
  [`projection.py:1539`](../../../src/arno/engine/projection.py#L1539),
  [`affordability.py:641`](../../../src/arno/engine/affordability.py#L641).
- **Bug:** at `S = 16,384` and `held = 14,745`, `_floor_share` rounds to `0.9000` (passing
  `afford()`), while `_first_crossing` (`1639 * 10 > 16384`) reports a non-null crossing date and
  `safe_amount_today`, `first_full_payment_date`, and `_tradeoffs` require `14,746` (at `N = 20`,
  `first_full_payment_date` returns `None` even when a 1-paisa payment passes `afford()`). In
  addition, `_safe_to_spend`, `_first_crossing`, and the borderline gap hardcode `0.90` even when
  `afford(..., threshold=...)` specifies another threshold.
- **Minimal patch:** factor out one `_min_held(scenarios, threshold)` helper (preferring exact
  `math.ceil(threshold * scenarios)` over rounded `_floor_share` comparisons) and thread
  `threshold` through `_summary`, `_safe_to_spend`, `_first_crossing`, and `safe_amount_today`.

#### A15. Medium: `inputs.py` leaks `as_of` spend, drops non-dominant card share to cash, zero-pads short-ledger `history`, and drops unknown-amount obligations silently

- **Where:** [`inputs.py:106-108`](../../../src/arno/engine/inputs.py#L106-L108),
  [`inputs.py:163`](../../../src/arno/engine/inputs.py#L163),
  [`inputs.py:184,214`](../../../src/arno/engine/inputs.py#L184),
  [`inputs.py:216-228`](../../../src/arno/engine/inputs.py#L216-L228).
- **Bug:** four deterministic input-marshaling bugs:
  1. Line 163 omits `or row.date >= snapshot.as_of`, so spend dated `as_of` inflates
     `baseline_per_day` and `card_share` while being excluded from `history`.
  2. For a category split 50% Card A and 50% Card B (0% cash), `share = chosen[1] / total_spend`
     sets `card_share = 0.50`, converting the other 50% into immediate cash outflows.
  3. `days` always spans 90 days from `window_start` instead of `first = max(window_start,
     history_start)`, diluting `LEGACY_MODEL` pools 3× on 30-day ledgers and distorting backtest
     comparisons.
  4. Obligations with `amount_paise is None` (`remaining_paise is None`) are dropped with no
     data-quality gap note.
- **Minimal patch:** add `or row.date >= snapshot.as_of` at line 163; build `days` from `first`;
  set `share = min(1.0, sum(cards.values()) / total_spend)`; emit a gap note when an `obligation`
  or `instalment` has `amount_paise is None`.

#### A16. Medium: `load_snapshot` runs 12+ autocommit queries without a snapshot transaction

- **Where:** [`tools/projection.py:561-767`](../../../src/arno/tools/projection.py#L561-L767).
- **Bug:** concurrent writes between `_balances_at` and `_unbilled` can leave `unbilled >
  outstanding`; clamping at `tools/projection.py:223` yields `billed = 0`, deferring an imminent
  card bill by a full billing cycle.
- **Minimal patch:** wrap `load_snapshot` reads in
  `async with conn.transaction(isolation="repeatable_read", readonly=True):`.

#### A17. Medium: Missing `MAX_PAISE` magnitude guard at projection trust boundaries

- **Where:** [`tools/projection.py:841-844`](../../../src/arno/tools/projection.py#L841-L844),
  [`api/ledger.py:601,643`](../../../src/arno/api/ledger.py#L601),
  [`agent/toolset.py:112,172,185,209`](../../../src/arno/agent/toolset.py#L112).
- **Bug:** `amount_paise = 10**19` raises unhandled `OverflowError` (HTTP 500 or aborted agent
  turn); 12 `one_time` changes of `8e18` paise silently wrap `int64` to `-7.34e18`.
- **Minimal patch:** enforce `<= MAX_PAISE` (matching `tools/write.py:42`) in
  `tools/projection._positive_int`, FastAPI `Query(le=...)`, and Pydantic tool models.

#### A18. Medium: Raw `{"date", "paise"}` leaks into model-facing payloads alongside `money()` shapes

- **Where:** [`tools/projection.py:1391,1444`](../../../src/arno/tools/projection.py#L1391),
  [`jobs/briefing.py:161`](../../../src/arno/jobs/briefing.py#L161).
- **Bug:** `baseline.median_low` and `briefing_numbers["median_low"]` bypass `_money_low()`.
  Because `agent/number_check.py:282-291` traces both raw and `/100` for `paise` keys, a 100× unit
  misread by the LLM passes unchecked.
- **Minimal patch:** pass both fields through `_money_low()`.

#### A19. Medium: `card_emi` crashes on early-zero small-principal loans, and `amortize` `prepay(reduce="tenure")` flips on a coincident rate reset

- **Where:** [`money.py:923-934`](../../../src/arno/engine/money.py#L923-L934),
  [`money.py:682-704`](../../../src/arno/engine/money.py#L682-L704),
  [`money.py:377-383`](../../../src/arno/engine/money.py#L377-L383).
- **Bug:** (1) `card_emi` omits `if balance <= 0: break`, so `card_emi(72, annual_rate_bps=0,
  months=48)` hits `month_principal == 0` on month 37 and raises `ValueError("this EMI never
  repays the loan")`. (2) `amortize(10_000_000, 8.0, 24, rate_changes=[(13, 12.0)], reset="emi",
  prepay={"amount_paise": 2_000_000, "at_emi": 13, "reduce": "tenure"})` picks `walk.emis[11]` then
  triggers EMI reset inside `_walk` on the post-prepayment balance, yielding `months_saved = 0` and
  a stale `prepay.new_emi = ₹4,522.73`.
- **Minimal patch:** add `if balance <= 0: break` in `card_emi`; for `reduce == "tenure"`, start
  from `walk.emis[before]` and skip the `index == start_index` EMI reset inside `_walk`.

#### A20. Medium: `amortize` coarse-unit final balloon (2.79× EMI) and out-of-horizon `rate_changes` phantom month

- **Where:** [`money.py:393-409`](../../../src/arno/engine/money.py#L393-L409),
  [`money.py:555,605-607`](../../../src/arno/engine/money.py#L555).
- **Bug:** (1) `amortize(2_000_000, 8.73, 240, emi_unit_paise=100)` yields EMI ₹176.00 and a final
  payment of ₹490.57 (2.79× EMI) with no assumption note. (2) `amortize(1_000_000, 8.0, 12,
  rate_changes=[(24, 20.0)])` sets `unbounded=True`, turning a 6-paisa residual into a 13th month
  while `assumptions` advertises `"rate resets (tenure): 20% from EMI 24"`.
- **Minimal patch:** append a note when final payment differs from `emi` by `> emi_unit_paise` (or
  bump EMI by one unit when `emi_paise is None`); reject `change_emi > months` and decouple
  residual folding from `unbounded` when `emi_paise is None`.

#### A21. Medium: `infer_rate` ignores `rest`/`day_count`, `_compute_apr` misreports negative IRR, and `card_payoff` runs 1,200 diverging months on negative amortization

- **Where:** [`money.py:736-749`](../../../src/arno/engine/money.py#L736-L749),
  [`money.py:438-474`](../../../src/arno/engine/money.py#L438-L474),
  [`money.py:1041-1069`](../../../src/arno/engine/money.py#L1041-L1069).
- **Bug:** (1) `infer_rate(..., rest="daily", day_count="actual/365")` validates both arguments and
  never uses them (returns byte-identical output to monthly rest). (2) `_compute_apr(10000, [4000,
  4000])` returns `"bracketed between 0.0000% and 0.0001%"` instead of rejecting negative IRR.
  (3) `card_payoff(50_000_000, annual_rate_bps=4200, payment="minimum", minimum_bps=300)` grows the
  balance for 1,200 iterations before failing with `"exceeds maximum horizon"`.
- **Minimal patch:** reject `rest != "monthly"` in `infer_rate` (or accept `start_date` and bisect
  `_walk`); reject `_schedule_pv(payments, 0) < target` in `_compute_apr`; raise immediately inside
  `card_payoff` when `closing >= balance`.

#### A22. Medium: Twice-monthly RRULE gaps (`jobs/patterns.py` and `india/recurrence.py`) and `series.py` `as_of` censoring inconsistency

- **Where:** [`jobs/patterns.py:28-45`](../../../src/arno/jobs/patterns.py#L28-L45),
  [`india/recurrence.py:52-57`](../../../src/arno/india/recurrence.py#L52-L57),
  [`series.py:471-486,515,997,1148`](../../../src/arno/engine/series.py#L515).
- **Bug:** (1) `_suggested_rule` has no branch for `interval == 15`, turning twice-monthly charges
  into monthly rules (halving reserved outflow). (2) `_clamp_month_end` skips `BYMONTHDAY=15,31`
  because `"15,31".isdigit()` is false, dropping the second payment in short months. (3)
  `series.detect` includes `p.date == as_of` in `in_window` while `_censoring_skip_probs` censors
  `day >= as_of_ord` (giving `q_j + (1 - q) > 1` on `as_of`), and `_pruning_score` penalizes
  due-today expected dates as misses (empirically harmless in probes, true rule still ranked #2/87).
- **Minimal patch:** pass the detected `Rule.rrule` directly into proposals in `jobs/patterns.py`;
  handle comma-separated `BYMONTHDAY` in `_clamp_month_end`; align `series.py` `in_window`,
  censoring, and `_pruning_score` on one `as_of` boundary.

#### A23. Medium: `backtest.py` `schedule_mean` reference is a deterministic 1-path point forecast

- **Where:** [`backtest.py:112-131`](../../../src/arno/engine/backtest.py#L112-L131).
- **Bug:** clearing `history=()` and running `runs=1` reduces `schedule_mean`'s CRPS to point MAE,
  mixing ensemble-versus-point gain into reported `+38.8%–46.4%` model skill.
- **Minimal patch:** retain `schedule_mean` as an explicit point baseline and add a probabilistic
  `empirical_flat` reference (`runs=DEFAULT_RUNS`) so CRPS skill isolates whole-day, rare-day, and
  series improvements.

#### A24. Medium (accuracy and modeling): `MAD == 0` disables rare-day detection even with extreme outliers

- **Where:** [`projection.py:996-1003`](../../../src/arno/engine/projection.py#L996-L1003).
- **Bug:** when more than half of positive-spend days share the median amount, `spread == 0` and
  `rare_mask` stays all-false. Reproduced: 89 days at ₹1 plus 1 day at ₹1,00,000 yields
  `rare_days = []`, leaving the ₹1,00,000 day in the everyday pool (`max = 10_000_000`).
- **Minimal patch:** when `spread == 0` and `totals.max() > median`, fall back to a positive robust
  scale (IQR/1.349 or mean absolute deviation) and test on Stage 2/Stage 4 benchmarks.

#### A25. Low: Secondary deterministic accuracy and reporting bugs

- **Stale bisection cost:** [`affordability.py:477,561`](../../../src/arno/engine/affordability.py#L477)
  updates `plan.cut_bps` without recomputing `plan.cost_paise`.
- **Partial-payment overwrite:** [`scenario.py:373-379`](../../../src/arno/engine/scenario.py#L373-L379)
  `ChangeAmount` replaces `remaining_paise` with full `amount_paise` without subtracting already
  linked partial `schedule_payments`.
- **Decimal truncation:** [`india/formatting.py:29`](../../../src/arno/india/formatting.py#L29)
  `format_paise(Decimal("123.9"))` truncates via `int()` instead of `ROUND_HALF_UP`.
- **Frozen risk state:** [`jobs/risk.py:29-34`](../../../src/arno/jobs/risk.py#L29-L34) returns
  before updating `risk_state.last_checked_at`, comparing future crossings against stale episodes.
- **Always-skipped series cited:** [`projection.py:789-791`](../../../src/arno/engine/projection.py#L789-L791)
  `_pays_in_horizon` omits `spec.skip_threshold < (1 << 32)`, reporting a `skip=1.0` series as a
  counted outflow.
- **Method-blind wait label:** [`affordability.py:602-608`](../../../src/arno/engine/affordability.py#L602-L608)
  `_wait_tradeoff` tests a cash payment even for a card or EMI ask without specifying cash.
- **Under-exercised persona estimate:** [`personas.py:1043,1060-1066`](../../../src/arno/engine/personas.py#L1043)
  `new_user`'s stated `±30%` estimate is overridden by `_derived_baseline` once `rows >= 3`, and
  `_expected_daily_paise` omits `WEEKEND_LIFT`.

---

### Latency and memory

#### L1. High: Event-loop blocking spans 6 seams, and a single far goal deadline silently expands every request to 3,650 days

_Expands Subsystem A #1 in [`benchmarks.md`](benchmarks.md)._

- **Where:** [`api/home.py:27`](../../../src/arno/api/home.py#L27),
  [`api/ledger.py:636,657-670`](../../../src/arno/api/ledger.py#L636),
  [`agent/toolset.py:469`](../../../src/arno/agent/toolset.py#L469),
  [`jobs/briefing.py:92`](../../../src/arno/jobs/briefing.py#L92),
  [`jobs/risk.py:28`](../../../src/arno/jobs/risk.py#L28) (enqueued on every ledger write via
  `ledger/audit.py:193-197`), [`tools/projection.py:100-113`](../../../src/arno/tools/projection.py#L100-L113),
  [`projection.py:542-549`](../../../src/arno/engine/projection.py#L542-L549).
- **Bug:** `JobRunner` shares FastAPI's event loop, and `horizon_days()` unconditionally takes
  `max(90, asked, furthest_goal_deadline)`. One 10-year goal silently turns every `/api/home` load
  and every post-write risk check into a 3,650-day simulation (measured: 5.13 s for `project()`,
  21.43 s for `afford()`, plus ~60k-cell synchronous xlsx generation in
  [`ledger/workbook.py:102`](../../../src/arno/ledger/workbook.py#L102)).
- **Minimal patch:** run pure `project()`, `afford()`, and `projection_workbook()` via
  `asyncio.to_thread` behind a bounded `asyncio.Semaphore(2)`; cap goal-driven horizon growth on
  fast-path callers (`/api/home`, `jobs/risk.py`) to `BASE_HORIZON_DAYS` unless explicitly
  requested.

#### L2. High: `affordability._plans` is `O(2^G)` in goals and `O(P^2)` in pairwise dominance with no goal cap

- **Where:** [`affordability.py:384-400`](../../../src/arno/engine/affordability.py#L384-L400),
  [`affordability.py:660-669`](../../../src/arno/engine/affordability.py#L660-L669).
- **Bug:** `_plans` enumerates all `2^G` subsets of active goals with a contribution in the next 31
  days across up to 3 method matrices, running `_least_cut` on each subset and then `O(P^2)`
  pairwise set comparisons before `MAX_PLANS = 4` is applied. At `G = 15`, `P` reaches ~98k plans
  (~9.6 billion comparisons), freezing the event loop.
- **Minimal patch:** cap `goals` to the top `N` deferrable goals (for example `N <= 8` by priority
  or relief) or use priority-ordered greedy expansion, and maintain the Pareto frontier
  incrementally instead of `O(P^2)` filtering.

#### L3. Medium: Unbounded `m` in `series.detect` (1.74 s per dense payee) and unbounded `months` in `money.py`

- **Where:** [`series.py:1020-1043`](../../../src/arno/engine/series.py#L1020-L1043),
  [`tools/recaps.py:423`](../../../src/arno/tools/recaps.py#L423),
  [`agent/toolset.py:508-518,652`](../../../src/arno/agent/toolset.py#L508-L518),
  [`money.py:371-373,1151,1224`](../../../src/arno/engine/money.py#L371-L373).
- **Bug:** (1) An 801-payment daily payee scores ~1.0 on `_pruning_score` across large-`n` rules,
  burning 1.74 s across `8 × 12` DP passes before returning `conf = 0.0` (reachable on chat turns
  via `tools/recaps.py:423`). (2) `amortize`, `compound`, and `compare_offers(kind="loan")` accept
  unbounded `months` synchronously on the event loop.
- **Minimal patch:** cap `in_window = in_window[-200:]` (or short-circuit when
  `len(in_window) > 1.5 * window_days / 7`) and optionally fuse the 2-point `_END_GRID` loop into
  one DP pass; add `_MAX_MONTHS = 600` in `money.py` and `Field(ge=1, le=600)` in `agent/toolset.py`.

#### L4. Low: Unused `card{k}_series` chunk columns and repeated `prepare()` in `_verify`

- **Where:** [`kernel.py:682-683`](../../../src/arno/engine/kernel.py#L682-L683),
  [`affordability.py:472-474`](../../../src/arno/engine/affordability.py#L472-L474).
- **Bug:** `_spend` allocates a 15.87 MiB `card{k}_series` column per card even when
  `limit_spend=True` (never read); `_verify` rebuilds the entire 365-day `prepare()` on every cut
  attempt (up to 17× per plan).
- **Minimal patch:** allocate `card{k}_series` only when `not card.limit_spend`, and mutate only
  `cut_bps`/`cut_until` and goal events on a cached `Prepared` spec inside `_verify`.

#### L5. Low: Uncached mutable `IndiaCalendar`, `O(n^3 log n)` `price_step`, and `O(payments)` invariant round trips

- **Where:** [`india/workdays.py:29-42`](../../../src/arno/india/workdays.py#L29-L42),
  [`series.py:968-980,1106-1113`](../../../src/arno/engine/series.py#L968-L980),
  [`tests/invariants.py:943-1062`](../../../tests/invariants.py#L943-L1062).
- **Bug:** `holidays.India(years=...)` defaults to `expand=True`, silently mutating a "frozen"
  `IndiaCalendar` when queried outside `years` (thread-safety hazard once L1 moves projection off
  the event loop; default rebuild costs 11.7 ms/call). `price_step` takes 540 ms at `n = 480`.
  `tests/invariants.py` issues 4–6 sequential queries per payment.
- **Minimal patch:** cache `IndiaCalendar` by `(first_year, last_year)` with `expand=False` (and
  document the `as_of + 400 days` span required by `series.detect`); bound the inner `start` scan
  in `price_step` to 24 samples; batch SQL joins in `tests/invariants.py`.

---

### Maintainability and harness safety

#### M1. Medium: Unsafe CLI `--publish` defaults can leak real ledger magnitudes or overwrite `README.md`

- **Where:** [`cli.py:556-570`](../../../src/arno/cli.py#L556-L570),
  [`cli.py:619-638`](../../../src/arno/cli.py#L619-L638).
- **Bug:** `arno backtest --source db --publish` writes personal rupee CRPS and balance scales into
  committed `README.md` (violating the `Local-only data` rule in `AGENTS.md`). `arno hackerrank`
  defaults to `--publish=True` and overwrites the README block with `"not run"` before exiting
  with code 3 when the gitignored dataset is absent.
- **Minimal patch:** reject `publish and source != "personas"` in `backtest_command`; move the
  `not result.get("available")` exit above `publish_report` and default `--publish` to `False` in
  `hackerrank_command`.

#### M2. Medium: Unhandled harness crashes on short personas and malformed HackerRank rows

- **Where:** [`personas.py:484-492,517`](../../../src/arno/engine/personas.py#L484-L492),
  [`hackerrank.py:1644-1653`](../../../src/arno/engine/hackerrank.py#L1644-L1653).
- **Bug:** `_calibrate_opening_and_floor` raises `IndexError` when history is under 105 days
  (`total_windows == 0`); `hackerrank.score` parses expected columns outside its per-row `try`.
- **Minimal patch:** add an early fallback when `total_windows < 2` in `personas.py`; widen the
  `try` block in `hackerrank.py` to cover expected-value parsing.

#### M3. Medium: Multi-goal tradeoff overwrite, `scenario.py` horizon clipping, and frozen-dataclass dict mutation

- **Where:** [`affordability.py:524-546,741`](../../../src/arno/engine/affordability.py#L524-L546),
  [`scenario.py:199,482`](../../../src/arno/engine/scenario.py#L199).
- **Bug:** (1) Multi-goal delay plans overwrite scalar `trade["goal"]`, `goal_id`, `slips_days`,
  and `completes_on` in a loop, leaving only the _least_ important goal in structured output while
  `result["goal_slips"]` is hardcoded `[]`. (2) `apply_changes` validates dates up to `+3620d` but
  silently clips against `inputs.ask_horizon_days` (default 90d) if callers do not pre-call
  `scenario_horizon_days`. (3) `affordability.py:741` mutates `applied.meta` in place despite
  `_Applied` being `frozen=True`.
- **Minimal patch:** add `goals: list[GoalSlip]` to `Tradeoff` (keeping scalar fields on the first
  goal); document or assert the horizon precondition in `apply_changes`; copy `meta` before
  mutating.

#### M4. Low: Dead code, latent option interactions, and missing engine invariants

- **Dead code and unused fields:** `SpendCategory.covered_by_obligation`
  ([`projection.py:411`](../../../src/arno/engine/projection.py#L411)), unreachable `else` branch
  at [`inputs.py:211-213`](../../../src/arno/engine/inputs.py#L211-L213), dead
  `_window_breach_share` ([`personas.py:415-448`](../../../src/arno/engine/personas.py#L415-L448)),
  and computed-but-discarded `pit` histograms and h=14/60 cells in
  [`backtest.py:934,999-1001`](../../../src/arno/engine/backtest.py#L934).
- **Duplicate and misleading contracts:** [`ledger/schedules.py:469-515`](../../../src/arno/ledger/schedules.py#L469-L515)
  duplicates `card_due_date` from `projection.py`; [`projection.py:1146-1154`](../../../src/arno/engine/projection.py#L1146-L1154)
  applies the first category's `reduce_ratio` globally to all reducible categories;
  [`kernel.py:286-295`](../../../src/arno/engine/kernel.py#L286-L295) drops recency weights when
  `pool_resampling=True`; [`money.py:898-903`](../../../src/arno/engine/money.py#L898-L903)
  docstring claims foreclosure-charge GST modeling that does not exist.
- **Engine gate gap in `tests/invariants.py`:** [`tests/invariants.py:104-122`](../../../tests/invariants.py#L104-L122)
  checks only database ledger tables despite `AGENTS.md` gating `engine/` changes on it, and lines
  1044–1062 contain a tautological `payment_status` assertion. Add a synchronous `check_engine()`
  pass and replace the tautology with an aggregate sum check on `schedule_payments`.

---

### Verified clean (no rework needed)

To avoid re-auditing sound paths, the following invariants were verified directly across the five
review passes and live probes:

- **Bayesian DP and Viterbi MAP:** `_viterbi_matched_with_bounds` matched a 480-instance
  brute-force max-product oracle with 0 mismatches; forcing the `2^-512` DP rescaling threshold
  from `1e150` down to `1e-3` produced `0.000e+00` `log2(LR)` drift and identical `alive` to 15
  decimal places.
- **SplitMix64 stream separation and checkpoint lemma:** pool/category streams (`8s + {0..3}`),
  rare streams (`8*999 + {4..6}`), and `_series_streams` occupy disjoint blocks mod 8; the
  checkpoint lemma (`checkpoints_for`) and `first_full_payment_date` candidate set are sound; rare
  days replace everyday draws cleanly with no double-counting.
- **Card cycle and `_safe_to_spend` obligation boundary:** `statement_close` and `card_due_date`
  agree across `tools/read.py`, `tools/write.py`, `projection.py`, and `affordability.py`;
  `obl_rank = scenarios // 100` matches `obligation_failures * 100 > scenarios` exactly; `crps`,
  Bartlett lag `ceil(h/7) - 1`, `_liquid_path` day-0 alignment, `allocate`, `scale_cumulative`, and
  re-anchored `add_months` are exact.

---

## 2. Deterministic bugs to fix before benchmarking versus open modeling hypotheses

### Deterministic bugs to fix before running or publishing benchmarks

Landing these fixes first prevents harness artifacts, mathematical errors, and known logic holes
from poisoning benchmark baselines or invalidating conformance oracles.

1. **Harness and statistical validity (prerequisite for all Rank 2 and Rank 4 metrics):**
   - **A10** (`scoring.hac_estimate` pooled-mean centering and `n = 1` Diebold-Mariano guard) —
     without this fix, every h=60/90 CI, DM p-value, and Rank 2E MCS is invalid.
   - **A15 (item 3)** (`inputs.py` 90-day zero-padding diluting `LEGACY_MODEL`) and **A23**
     (`schedule_mean` point-versus-ensemble asymmetry).
   - **Baseline leakage fixes** from [`benchmarks.md`](benchmarks.md) §2 (`backtest.py:486-493`
     `schedule.created <= as_of`, `backtest.py:365-371` normalized payee text grouping).
   - **M1 and M2** (`cli.py` `--publish` guards, `personas.py` `< 105`-day guard, `hackerrank.py`
     per-row `try`).
2. **Engine correctness invariants (prerequisite for Rank 1, Rank 3, and Rank 4 oracles):**
   - **A1, A2, A8, A11, A12, A14** (card bills in `obligation_days`, `_least_cut` 0.99 obligation
     constraint, `_charge_after_loose` intra-day priority, strict Pareto dominance, capped-card
     cut survival into `_verify`, unified `_min_held` threshold helper).
   - **A6, A7, A9, A22** (date-only `UNTIL`, normalized month-end and `None`-cadence fixes in
     `patterns.py`, `_summarize_amounts` stepped/multi-step unification, `jobs/patterns`
     `interval == 15`, `series.py` `as_of` alignment).
   - **A19–A21 plus baseline Subsystem C** (`card_emi` early-zero break, `amortize` prepay+reset,
     coarse-unit balloon note, `rate_changes > months` guard, `infer_rate` daily-rest refusal,
     `_compute_apr` negative-IRR guard, `card_payoff` negative-amortization guard, `30e/360` and
     `actual/365f` naming, pinned `Decimal` `localcontext`, explicit `apr_basis`).
   - **A3–A5, A15–A18, L1–L3** (production caller-seam integrity and stability: `REPEATABLE READ`,
     per-category `attributed` guard, `upcoming()` billing split, overdue obligations, `MAX_PAISE`
     and `_MAX_MONTHS`, `asyncio.to_thread` off-loop compute, goal-subset cap in `_plans`, dense
     payee cap in `series.detect`).

### Open modeling hypotheses to test via benchmarks

Do not hard-code guesses for these behaviors before measurement; let the target benchmark suites
adjudicate each trade-off. The table below maps each open hypothesis to its deciding benchmark.

| Hypothesis | Current shipped behavior | Benchmark that adjudicates it |
|---|---|---|
| Temporal persistence (`block_length`) | `block_length = 1` (iid daily draws) | 2A (`VS_1`, 2-D Energy) and 2B/3A (Christoffersen clustering on Berka and real DB); `personas.py` generates iid daily spend, so only real balances can test `block_length > 1`. |
| Candidate pruning ceiling (`_MAX_CANDIDATES`) | `8` ([`series.py:24`](../../../src/arno/engine/series.py#L24)) | 3B internal `recall@8` versus unpruned `C × 12` DP on personas and Berka, paired with 5B sweep latency (and S2 dense-payee cap). |
| SAA 0.99 gate precision (`LCB_95` versus `S >= 38,000`) | Point estimate at `S = 16,384` | 4B/4C (exact CRN oracle, asymmetric regret, perturbation flip rate) and 5B (wall-time cost per `afford()`) to decide whether one-sided 95% Wilson LCB at `S = 16,384` (`P_hat >= 0.9913`) matches `S >= 38,000` regret at 2.3× lower latency. |
| Variable series sample cap | Last 12 samples drawn uniformly ([`series.py:26`](../../../src/arno/engine/series.py#L26)) | 2D/3A (M5 extreme-tail pinball `0.01/0.99` on Berka recurring utilities). |
| Rare-day rate posterior and `MAD == 0` fallback | `Poisson(k)` ([`kernel.py:331-337`](../../../src/arno/engine/kernel.py#L331-L337)) putting `e^-1 = 36.8%` mass at 0 when `k = 1`; `MAD == 0` disables split ([`projection.py:1002`](../../../src/arno/engine/projection.py#L1002)) | 2C/2D (`Z2` ES and `0.01` tail pinball on Berka and personas) comparing `Poisson(k)` versus `Gamma(k + alpha, 1)` and IQR/MAD fallback. |
| Card credit-limit modeling (`limit_spend`, EMI principal block, future credit, unbilled split) | `limit_spend=False`; EMI charges installments only; future check uses day-0 credit; unknown unbilled front-loads 100% | 2E/4B/4C (MCS and decision regret on card-bearing personas and real DB). |
| Payee-only versus multi-plan splitting and generic-token matching | Payee-only grouping; raw token overlap in `patterns.same_merchant` | 3A/3C (Berka weighted P/R/F1 and BBVA downstream next-date MAE). |
| Goal-relief window start (`when` versus `as_of`) and unlinked goal contributions | Defers contributions in `[when, when + 31]` only; unlinked contributions cannot defer | 4B/4C (exact CRN lever oracle and decision regret on future-dated purchase probes). |

---

## 3. Finalized benchmark subset and execution sequence

This plan adopts **12 of the 16** benchmark items in [`benchmarks.md`](benchmarks.md), reshapes
**3** for codebase impedance surfaced during this audit, and drops or defers **2** (`1D` and `3D`)
that offer low India-specific or data-model ROI.

### Items cut, deferred, or reshaped beyond `benchmarks.md` exclusions

- **Drop 1D (CFPB Reg Z Appendix J and EU CCD Annex III):** Arno is India-first (`AGENTS.md`); RBI
  KFS (1A), Strata day-counts (1B), and ACTUS `ANN`/`PAM` (1C) already verify statutory APR,
  day-count arithmetic, and irregular/reset schedules without introducing US/EU statutory rules.
  Retain only the explicit `apr_basis` toggle from 1A.
- **Defer 3D (Dominick's Finer Foods):** `series.price_step()` accepts an index-only sequence with
  no dates or gap model and returns only the latest step, so weekly retail panels with missing
  weeks would mostly test an adapter loop rather than Arno's detector. Revisit only if `price_step`
  is upgraded to date-aware multi-step segmentation.
- **Reshape 2B (Kupiec/Christoffersen) and 2C (`Z2`/`FZ0`):** because
  [`personas.py:1963-1985`](../../../src/arno/engine/personas.py#L1963-L1985) calibrates each
  household's floor to 8–22% on the exact scored overlapping 90-day windows, _pooled_ `LR_uc`,
  `LR_cc`, and `Z2` are misspecified on personas. Report `Z2`/`FZ0` _per persona_ (pooled only on
  Berka and real DB), and treat household cluster bootstrap (or non-overlapping origins) as a hard
  prerequisite for Christoffersen independence.

### Finalized execution sequence

The table below orders adoption into six stages, listing exact code prerequisites and exit gates
for each stage.

| Stage | Adopted benchmarks | Concrete prerequisites and code seams | Gate and exit criterion |
|---|---|---|---|
| Stage 0: Pre-benchmark fixes | None (foundation) | Land deterministic bugs A1–A23, L1–L3, M1–M2, plus `backtest.py` leakage fixes (`created <= as_of`, payee text grouping). Add synchronous `check_engine()` to [`tests/invariants.py`](../../../tests/invariants.py). | Full `pytest`, `ruff`, `pyrefly`, and `tests.invariants` green. |
| Stage 1: Financial-math and prune conformance | 1A RBI KFS Annex B, 1B Strata 400-pair day-count golden JSON, 3B internal `series.detect` `recall@8` unpruned oracle | Pin `Decimal` `localcontext`, rename `30/360` → `30e/360` and `actual/365` → `actual/365f`, expose `apr_basis`, correct `18.4711%` versus `18.5046%` vector, add `prune: bool = True` at [`series.py:1232`](../../../src/arno/engine/series.py#L1232). | Exact 2-dp RBI match (`17.07%`), 0-day discrepancy on 400 Strata pairs, `recall@8` measured and budgeted. |
| Stage 2: Core scoring and statistical rigor | 2A `VS_1` and 2-D Energy, 2D WIS, M5 extreme tails (`0.01..0.99`), and Czado discrete PIT, 2E 90% MCS, Giacomini-White, and `fev-bench` block-bootstrap CIs, 2C per-persona `Z2`/`FZ0`, 2B cluster-bootstrapped Christoffersen | Requires A10 (`hac_estimate`). Add a private `_run_with_spec()` helper at [`projection.py:1429`](../../../src/arno/engine/projection.py#L1429) so `backtest.py` obtains `checkpoints_for(spec)` without re-running `prepare()`. Extend `Score` with GW instruments (`days_to_salary`, `month_end`, `emi_window`). Run 3–5 seeds per origin with CI exit gates. | Enforceable CI gates on skill, coverage, and per-regime GW stability; regenerate `<!-- backtest -->` only after Stage 2 settles. |
| Stage 3: Affordability oracle and SAA calibration | 4A FCA CONC 5.2A post-condition invariants, 4B exact-enumeration CRN lever oracle and LCB versus `S >= 38,000` ablation, 4C `decision_backtest` (AUC, asymmetric regret `c_FN = 10 * c_FP`, `safe_amount_today` regret, `K = 50` perturbation flip rate) | Requires A1, A2, A8, A11, A12, A14 so the oracle validates corrected obligation, card-bill, and dominance logic. Reuse the CRN `kernel.run` matrix in `tests/test_affordability_oracle.py`. | 0 FCA post-condition violations, 0 feasibility gap versus exhaustive CRN oracle, finalized decision on `LCB_95` versus `S >= 38,000`. |
| Stage 4: Real-world banking and contract schedules | 3A PKDD'99 Berka (`series.detect`, `patterns.is_covered`, real balance-path CRPS/`VS_1`/WIS/`Z2`) with 3C Tatbul range P/R and BBVA next-date MAE, 1C ACTUS `ANN`/`PAM` JSON conformance | Begin Berka with a gap census across ~1,700 dense accounts (verify `LiquidAccount.known`, synthetic floor choice, and `card_share=0` do not trip `UNUSABLE_SETUP_GAPS`). Use Berka to settle `block_length`, `_MAX_CANDIDATES`, `_MAX_SAMPLES`, `MAD == 0` fallback, and `Poisson`-versus-`Gamma` rare-day hypotheses. | Weighted F1, Tatbul onset/termination latency, and real-balance `VS_1`/Christoffersen baseline; 100% ACTUS date equality and `<= 1` paisa cashflow error. |
| Stage 5: Systems, concurrency, memory, and PRNG | 5A k6 open-model load test and 10 ms asyncio heartbeat lag, 5B ASV 4 macro-workloads and `pytest-memray` chunk RSS cap, 5C PractRand (`stdin64`/`stdin32`), release TestU01 BigCrush, and 2-D inter-stream `chi2` | Requires L1–L5 (`asyncio.to_thread` + semaphore, goal horizon cap, `O(2^G)` goal cap, `expand=False` cached `IndiaCalendar`, `below()` `bound < 2^32` assertion) landed first so 5A/5B verify the fix. | `dropped_iterations == 0`, event-loop lag `< 100 ms` at 5 RPS, per-chunk `pytest-memray` gate scaling by card count `C`, `<= 5%` PR wall-time gate, 0 PractRand anomalies, and 160/160 BigCrush. |
