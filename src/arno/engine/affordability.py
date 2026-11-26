"""Affordability: the yes, yes-but, or no verdict, and the smallest changes that make it a yes.

The verdict is never prose from a model. The purchase is applied as asked and the scenario kernel
walks every future once: it passes when the floor probability reaches the threshold and every
obligation is met. When it does not, each lever is tried by rerunning `project`:

- paying by card or card EMI moves the cash onto bill due dates (one variant per method);
- skipping goals' contributions, the least important goal first, stopping at the first plan
  that passes;
- cutting reducible spend by the least share, found by bisection, that passes.

Every reported plan is a rerun on the same scenarios that passed, and every number shown comes
from that rerun. A later date alone stays a "no": the wait date is shown, but a yes-but needs a
change that keeps the date.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import partial

from arno.engine.money import DEFAULT_GST_BPS, add_months, card_emi, check_paise, money
from arno.engine.projection import (
    DEFAULT_RUNS,
    DEFAULT_SEED,
    FLOOR_THRESHOLD,
    MAX_CUT_RATIO,
    MAX_HORIZON_DAYS,
    OBLIGATION_THRESHOLD,
    CardAccount,
    CashEvent,
    GoalRow,
    GoalSlip,
    HypotheticalMeta,
    Installment,
    Projection,
    ProjectionInput,
    Scenario,
    ScenarioBaseline,
    ScenarioChange,
    Tradeoff,
    card_bills,
    card_due_date,
    goal_rows,
    horizon_days,
    project,
    reduce_categories,
)
from arno.engine.scenario import (
    ScenarioChangeSpec,
    apply_changes,
    scenario_horizon_days,
)

METHODS = ("account", "card", "emi")
DEFAULT_EMI_RATE_BPS = 1_600
DEFAULT_EMI_MONTHS = 3
GOAL_WINDOW_DAYS = 31
MAX_CUT_BPS = round(MAX_CUT_RATIO * 10_000)
MAX_PLANS = 4


@dataclass(frozen=True, slots=True)
class Hypothetical:
    amount_paise: int
    date: date
    method: str = "account"
    emi_months: int | None = None
    account: str | None = None
    # An EMI's annual rate in basis points and its processing fee, when the offer states them.
    emi_rate_bps: int | None = None
    emi_fee_paise: int | None = None
    # A no-cost EMI: the merchant pays the interest, so only the fee and its GST remain.
    no_cost: bool = False


def _pick_card(inputs: ProjectionInput, account: str | None) -> CardAccount | None:
    if not account:
        return max(
            inputs.cards,
            key=lambda card: (card.known, card.available_credit_paise),
            default=None,
        )
    wanted = account.strip().lower()
    return next(
        (card for card in inputs.cards if card.id == account or card.name.lower() == wanted),
        None,
    )


def _available_at(card: CardAccount, as_of: date, when: date) -> int:
    """Available credit on ``when``, after every bill due before the purchase is paid."""
    cleared = sum(amount for due, amount in card_bills(card, as_of) if due <= when)
    return max(card.credit_limit_paise - card.outstanding_paise + cleared + card.credit_paise, 0)


@dataclass(frozen=True, slots=True)
class _Applied:
    """The inputs with the purchase applied, what the purchase was, and why it cannot happen."""

    inputs: ProjectionInput
    meta: HypotheticalMeta
    reason: str | None
    cost_paise: int = 0
    gaps: tuple[str, ...] = ()


def _emi_terms(hypothetical: Hypothetical, card: CardAccount) -> tuple[int, int, list[str]]:
    """The EMI's rate and fee: the offer's, else the card's, else a labeled default."""
    # shortcut: hypothetical card EMI charges installments to limit instead of blocking full
    # principal on day 1, revisit when modeling card EMI near a tight credit limit
    gaps: list[str] = []
    if hypothetical.no_cost:
        rate = 0
    elif hypothetical.emi_rate_bps is not None:
        rate = hypothetical.emi_rate_bps
    elif card.emi_rate_bps is not None:
        rate = card.emi_rate_bps
    else:
        # An unknown rate assumed to be zero is the one value known to be wrong the unsafe way.
        rate = DEFAULT_EMI_RATE_BPS
        gaps.append(
            f"the EMI's cost assumes {DEFAULT_EMI_RATE_BPS / 100:g}% a year plus 18% GST on the "
            f"interest; {card.name}'s EMI rate is not recorded"
        )
    if hypothetical.emi_fee_paise is not None:
        fee = hypothetical.emi_fee_paise
    elif card.emi_fee_paise is not None:
        fee = card.emi_fee_paise
    else:
        fee = 0
        gaps.append("no EMI processing fee is assumed")
    return rate, fee, gaps


def _apply(inputs: ProjectionInput, hypothetical: Hypothetical) -> _Applied:
    """Apply the purchase and return the new inputs, its metadata, and an infeasible reason."""
    amount = int(hypothetical.amount_paise)
    if amount <= 0:
        return _Applied(inputs, {}, "the amount must be positive")
    if hypothetical.method not in METHODS:
        return _Applied(inputs, {}, "method is account, card, or emi")
    when = max(hypothetical.date, inputs.as_of)
    at_start = hypothetical.date <= inputs.as_of
    name = "Hypothetical purchase"
    meta: HypotheticalMeta = {
        "amount_paise": amount,
        "date": hypothetical.date.isoformat(),
        "method": hypothetical.method,
        "emi_months": hypothetical.emi_months if hypothetical.method == "emi" else None,
        "account": hypothetical.account,
    }

    if hypothetical.method == "account":
        meta["last_cash_date"] = when.isoformat()
        event = CashEvent(
            date=when, amount_paise=-amount, name=name, kind="hypothetical", at_start=at_start
        )
        return _Applied(replace(inputs, events=(*inputs.events, event)), meta, None)

    months = hypothetical.emi_months if hypothetical.emi_months is not None else DEFAULT_EMI_MONTHS
    if hypothetical.method == "emi":
        if not 2 <= months <= 48:
            return _Applied(inputs, meta, "an EMI tenure is 2 to 48 months")
        meta["emi_months"] = months

    card = _pick_card(inputs, hypothetical.account)
    if card is None:
        return _Applied(inputs, meta, "no card with available credit was found")
    if not card.known:
        return _Applied(
            inputs,
            meta,
            f"{card.name} has no known outstanding balance, so its available credit is unknown.",
        )
    available = _available_at(card, inputs.as_of, when)
    if amount > available:
        return _Applied(
            inputs,
            {**meta, "available_credit_paise": available},
            f"{card.name} has only {money(available)['text']} of available credit",
        )
    meta["card_id"] = card.id
    meta["card_name"] = card.name

    if hypothetical.method == "card":
        due = card_due_date(card, when)
        meta["due_date"] = due.isoformat()
        meta["last_cash_date"] = due.isoformat()
        event = CashEvent(
            date=when,
            amount_paise=-amount,
            name=name,
            kind="hypothetical",
            card_id=card.id,
            at_start=at_start,
        )
        return _Applied(replace(inputs, events=(*inputs.events, event)), meta, None)

    rate, fee, gaps = _emi_terms(hypothetical, card)
    try:
        plan = card_emi(
            amount,
            annual_rate_bps=rate,
            months=months,
            processing_fee_paise=fee,
            gst_bps=DEFAULT_GST_BPS,
        )
    except ValueError as exc:
        return _Applied(inputs, meta, str(exc))
    installments: list[Installment] = []
    events: list[CashEvent] = []
    for row in plan["schedule"]:
        due_day = add_months(when, row["n"] - 1)
        installments.append({"date": due_day.isoformat(), "amount_paise": row["total_paise"]})
        events.append(
            CashEvent(
                date=due_day,
                amount_paise=-row["total_paise"],
                name=f"{name} instalment {row['n']}",
                kind="hypothetical_emi",
                card_id=card.id,
                at_start=due_day <= inputs.as_of,
            )
        )
    meta["installments"] = installments
    meta["total_paise"] = plan["total_paise"]
    meta["emi_rate_bps"] = rate
    meta["emi_cost_paise"] = plan["cost_paise"]
    meta["last_cash_date"] = card_due_date(card, add_months(when, months - 1)).isoformat()
    return _Applied(
        replace(inputs, events=(*inputs.events, *events)),
        meta,
        None,
        cost_paise=plan["cost_paise"],
        gaps=tuple(gaps),
    )


def _extend_horizon(inputs: ProjectionInput, meta: HypotheticalMeta) -> ProjectionInput:
    """Cover the plan's last cash date, so an EMI's later installments are not ignored."""
    last_cash = meta.get("last_cash_date")
    if not last_cash:
        return inputs
    needed = (date.fromisoformat(last_cash) - inputs.as_of).days + 1
    if needed > (inputs.ask_horizon_days or 0):
        return replace(inputs, ask_horizon_days=needed)
    return inputs


def _passes(result: Projection, *, threshold: float) -> bool:
    # shortcut: point estimate at S=2048 has a +-1.3pp 95% CI on the 0.90 gate, revisit with a
    # one-sided Wilson LCB or more runs if verdicts flip between seeds
    return float(result["floor_probability"]) >= threshold and bool(result["obligations_met"])


# --- The levers ---------------------------------------------------------------------------------


def _goal_relief(inputs: ProjectionInput, goal_ids: tuple[str, ...], when: date) -> ProjectionInput:
    """The inputs without these goals' contributions in the month after the purchase."""
    window_end = when + timedelta(days=GOAL_WINDOW_DAYS)
    kept = tuple(
        event
        for event in inputs.events
        if not (
            event.kind == "goal_contribution"
            and event.goal_id in goal_ids
            and when <= event.date <= window_end
        )
    )
    return replace(inputs, events=kept)


def _least(
    low: int, high: int, test: Callable[[int], Projection | None]
) -> tuple[int, Projection] | None:
    """The least setting in ``[low, high]`` that passes, and its rerun, by bisection.

    The rerun that is returned passed, so every number a plan reports comes from a real run.
    More is assumed to be at least as safe, which holds up to a paisa of rounding.
    """
    best = test(high)
    if best is None:
        return None
    while low < high:
        middle = (low + high) // 2
        better = test(middle)
        if better is None:
            low = middle + 1
        else:
            high, best = middle, better
    return high, best


def _cut_passes(
    inputs: ProjectionInput, until: date, bps: int, *, runs: int, seed: int, threshold: float
) -> Projection | None:
    cut_inputs, _rows = reduce_categories(inputs, bps / 10_000, until=until)
    result = project(cut_inputs, runs=runs, seed=seed, threshold=threshold)
    return result if _passes(result, threshold=threshold) else None


# --- Plans -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Plan:
    """A set of levers that was rerun and passed, with the run it passed on."""

    method: str  # "asked", "card", or "emi"
    goals: tuple[str, ...]
    cut_bps: int
    cost_paise: int
    result: Projection
    inputs: ProjectionInput
    meta: HypotheticalMeta

    @property
    def levers(self) -> int:
        return int(self.method != "asked") + len(self.goals) + int(self.cut_bps > 0)


def _variant_plans(
    method: str,
    variant: _Applied,
    goals: tuple[str, ...],
    *,
    when: date,
    until: date | None,
    spend_paise: int,
    runs: int,
    seed: int,
    threshold: float,
) -> list[_Plan]:
    """Plans for one payment method: defer goals from the least important up, cutting as needed.

    Each deferral step reruns the plan. It ends at the first plan that passes without a cut; a
    step with a cut is kept only if it needs a smaller cut than the step before.
    """
    plans: list[_Plan] = []
    smallest = MAX_CUT_BPS + 1
    deferred: tuple[str, ...] = ()
    for goal in (None, *reversed(goals)):
        if goal is not None:
            deferred = (*deferred, goal)
        relieved = _goal_relief(variant.inputs, deferred, when)
        result = project(relieved, runs=runs, seed=seed, threshold=threshold)
        if _passes(result, threshold=threshold):
            plans.append(
                _Plan(method, deferred, 0, variant.cost_paise, result, relieved, variant.meta)
            )
            break
        if until is None:
            continue
        test = partial(_cut_passes, relieved, until, runs=runs, seed=seed, threshold=threshold)
        found = _least(1, MAX_CUT_BPS, test)
        if found is not None and found[0] < smallest:
            cut, rerun = found
            smallest = cut
            cost = variant.cost_paise + cut * spend_paise // 10_000
            plans.append(_Plan(method, deferred, cut, cost, rerun, relieved, variant.meta))
    return [item for item in plans if item.levers > 0]


def _dominates(better: _Plan, plan: _Plan) -> bool:
    """Whether ``better`` changes no more on any lever, costs no more, and differs somewhere.

    Card float beside a paid EMI is the case: the EMI leaves the reader nothing the card does not.
    """
    asks_no_more = (
        (better.method != "asked") <= (plan.method != "asked")
        and set(better.goals) <= set(plan.goals)
        and better.cut_bps <= plan.cut_bps
        and better.cost_paise <= plan.cost_paise
    )
    same = (better.method != "asked", set(better.goals), better.cut_bps, better.cost_paise) == (
        plan.method != "asked",
        set(plan.goals),
        plan.cut_bps,
        plan.cost_paise,
    )
    return asks_no_more and not same


def _next_payday(inputs: ProjectionInput, when: date) -> date | None:
    income_days = sorted(
        event.date
        for event in inputs.events
        if event.amount_paise > 0
        and event.kind.startswith("income")
        and event.confirmed
        and event.date > when
    )
    return income_days[0] if income_days else None


def _describe(
    plan: _Plan, inputs: ProjectionInput, baseline_goals: list[GoalRow], until: date
) -> Tradeoff:
    meta = plan.meta
    parts: list[str] = []
    kinds: list[str] = []
    trade: Tradeoff = {"kind": "", "detail": ""}
    if plan.method != "asked":
        kinds.append("pay_by_card")
        trade["method"] = str(meta.get("method"))
        trade["account"] = meta.get("card_name")
        trade["due_date"] = meta.get("due_date")
        trade["installments"] = meta.get("installments")
        if meta.get("method") == "card":
            parts.append(
                f"put it on {meta.get('card_name')} and pay the bill on {meta.get('due_date')}"
            )
        else:
            count = len(meta.get("installments") or [])
            cost = money(int(meta.get("emi_cost_paise") or 0))["text"]
            parts.append(
                f"split it into {count} {meta.get('card_name')} instalments, which cost {cost} in "
                "interest, GST, and fees"
            )
    if plan.goals:
        kinds.append("delay_goal")
        rows = {row["id"]: row for row in goal_rows(plan.inputs)}
        before = {row["id"]: row for row in baseline_goals}
        goals: list[GoalSlip] = []
        skipped: list[str] = []
        for goal_id in reversed(plan.goals):  # most important first
            row = rows[goal_id]
            old = before[goal_id]["completes_on"] if goal_id in before else None
            new = row["completes_on"]
            slip: int | None = None
            if old is not None and new is not None:
                slip = max(0, (date.fromisoformat(new) - date.fromisoformat(old)).days)
            goals.append(
                {
                    "id": goal_id,
                    "name": row["name"],
                    "slips_days": slip,
                    "completes_on": new,
                }
            )
            if new is None:
                status = "no longer completes within the projection"
            elif slip:
                status = f"completes {slip} days later"
            else:
                status = "still completes on time"
            skipped.append(f"skip the next {row['name']} contribution (the goal {status})")
        trade["goals"] = goals
        # The scalar fields repeat the first goal for callers that predate the list.
        trade["goal"] = goals[0]["name"]
        trade["goal_id"] = goals[0]["id"]
        trade["slips_days"] = goals[0]["slips_days"]
        trade["completes_on"] = goals[0]["completes_on"]
        parts.append("; ".join(skipped))
    if plan.cut_bps > 0:
        kinds.append("cut_category")
        _reduced, rows_cut = reduce_categories(inputs, plan.cut_bps / 10_000, until=until)
        trade["until"] = until.isoformat()
        trade["ratio"] = round(plan.cut_bps / 10_000, 4)
        trade["categories"] = rows_cut
        names = ", ".join(row["category"] for row in rows_cut)
        parts.append(
            f"cut {names} by {plan.cut_bps / 100:g}% until {until.isoformat()}, then spend as usual"
        )
    trade["kind"] = kinds[0] if len(kinds) == 1 else "combined"
    if len(kinds) > 1:
        trade["levers"] = kinds
    trade["cost_paise"] = plan.cost_paise
    trade["floor_probability"] = plan.result["floor_probability"]
    detail = "; and ".join(parts)
    trade["detail"] = detail[:1].upper() + detail[1:] + "."
    return trade


def _pay_on(inputs: ProjectionInput, amount_paise: int, when: date) -> ProjectionInput:
    """The inputs with an account payment of ``amount_paise`` on ``when``."""
    return replace(
        inputs,
        events=(
            *inputs.events,
            CashEvent(
                date=when,
                amount_paise=-amount_paise,
                name="Hypothetical purchase",
                kind="hypothetical",
            ),
        ),
    )


def _wait_tradeoff(
    inputs: ProjectionInput,
    hypothetical: Hypothetical,
    *,
    runs: int,
    seed: int,
    threshold: float,
) -> Tradeoff | None:
    """The earliest later date the purchase passes on, floor and obligations both.

    Paying later is assumed at least as safe, so the earliest passing day inside the horizon is
    found by bisection; each day tried is paid for real and re-projected.
    """
    when = max(hypothetical.date, inputs.as_of)
    amount = int(hypothetical.amount_paise)
    first = (when - inputs.as_of).days

    def passes(day: int) -> Projection | None:
        paid = _pay_on(inputs, amount, inputs.as_of + timedelta(days=day))
        result = project(paid, runs=runs, seed=seed, threshold=threshold)
        return result if _passes(result, threshold=threshold) else None

    found = _least(first, horizon_days(inputs), passes)
    if found is None or found[0] == first:
        # Paying on the asked date is the ask, not a tradeoff.
        return None
    days, _result = found
    day = (inputs.as_of + timedelta(days=days)).isoformat()
    return {
        "kind": "wait",
        "date": day,
        "days": days,
        "detail": f"Wait until {day} ({days} days), then pay the full amount.",
    }


def _tradeoffs(
    inputs: ProjectionInput,
    hypothetical: Hypothetical,
    applied: _Applied,
    baseline_goals: list[GoalRow],
    *,
    runs: int,
    seed: int,
    threshold: float,
    allow_cut: bool = True,
) -> list[Tradeoff]:
    when = max(hypothetical.date, inputs.as_of)
    payday = _next_payday(inputs, when) or (when + timedelta(days=30))
    reducible = sum(
        category.baseline_per_day_paise
        for category in inputs.categories
        if category.reducible and category.baseline_per_day_paise > 0
    )
    cuttable = allow_cut and reducible > 0

    variants: list[tuple[str, _Applied]] = [("asked", applied)]
    for method in ("card", "emi"):
        if method == hypothetical.method:
            continue
        alternative = replace(
            hypothetical,
            method=method,
            emi_months=(hypothetical.emi_months or DEFAULT_EMI_MONTHS) if method == "emi" else None,
        )
        other = _apply(inputs, alternative)
        if other.reason is None:
            extended = replace(other, inputs=_extend_horizon(other.inputs, other.meta))
            variants.append((method, extended))
    goals = tuple(
        goal.id
        for goal in sorted(inputs.goals, key=lambda item: (item.priority, item.name))
        if any(
            event.kind == "goal_contribution"
            and event.goal_id == goal.id
            and when <= event.date <= when + timedelta(days=GOAL_WINDOW_DAYS)
            for event in inputs.events
        )
    )
    plans: list[_Plan] = []
    for method, variant in variants:
        plans.extend(
            _variant_plans(
                method,
                variant,
                goals,
                when=when,
                until=payday if cuttable else None,
                spend_paise=reducible * (payday - inputs.as_of).days,
                runs=runs,
                seed=seed,
                threshold=threshold,
            )
        )
    plans = [plan for plan in plans if not any(_dominates(other, plan) for other in plans)]
    plans.sort(key=lambda plan: (plan.levers, plan.cost_paise, plan.method, plan.goals))
    return [_describe(plan, inputs, baseline_goals, payday) for plan in plans[:MAX_PLANS]]


def validate_hypothetical(as_of: date, hypothetical: Hypothetical) -> None:
    if hypothetical.amount_paise <= 0:
        raise ValueError("the amount must be positive")
    check_paise(hypothetical.amount_paise)
    if hypothetical.emi_fee_paise is not None:
        check_paise(hypothetical.emi_fee_paise)
    if hypothetical.method not in METHODS:
        raise ValueError("method is account, card, or emi")
    if hypothetical.method == "emi":
        tenure = hypothetical.emi_months if hypothetical.emi_months is not None else 3
        if not 2 <= tenure <= 48:
            raise ValueError("an EMI tenure is 2 to 48 months")
    if (hypothetical.date - as_of).days > MAX_HORIZON_DAYS - 30:
        raise ValueError("the purchase date is beyond the projection horizon")


def _outcome_reason(result: Projection, *, floor_paise: int, threshold: float) -> tuple[bool, str]:
    if _passes(result, threshold=threshold):
        return (
            True,
            f"{result['floor_probability']:.0%} of simulated futures stay at or above the "
            "floor and every obligation is met.",
        )
    if not result["obligations_met"]:
        return (
            False,
            "An obligation cannot be paid on its date in more than "
            f"{1 - OBLIGATION_THRESHOLD:.0%} of futures.",
        )
    where = "the floor" if floor_paise > 0 else "zero"
    return (
        False,
        f"Only {result['floor_probability']:.0%} of simulated futures stay above "
        f"{where} at the {threshold:.0%} threshold.",
    )


def afford(
    inputs: ProjectionInput,
    hypothetical: Hypothetical,
    *,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
    threshold: float = FLOOR_THRESHOLD,
    allow_cut_tradeoffs: bool = True,
) -> Projection:
    """Return the projection with the purchase applied, a tested verdict, and minimal plans."""
    validate_hypothetical(inputs.as_of, hypothetical)
    known_start = any(account.known for account in inputs.liquid)
    applied = _apply(inputs, hypothetical)
    # `_Applied` is frozen but its meta dict is not; copy it before extending the horizon.
    meta: HypotheticalMeta = {**applied.meta}
    meta["last_cash_date"] = max(
        meta.get("last_cash_date") or hypothetical.date.isoformat(),
        hypothetical.date.isoformat(),
    )
    inputs = _extend_horizon(inputs, meta)
    applied = replace(
        applied,
        meta=meta,
        inputs=_extend_horizon(applied.inputs, meta) if applied.reason is None else inputs,
    )
    baseline = project(inputs, runs=runs, seed=seed, threshold=threshold)
    if applied.reason is not None:
        result = baseline
        verdict_reason = applied.reason
    else:
        result = project(applied.inputs, runs=runs, seed=seed, threshold=threshold)
        _passed_flag, verdict_reason = _outcome_reason(
            result, floor_paise=inputs.floor_paise, threshold=threshold
        )
    feasible = applied.reason is None
    passed = feasible and _passes(result, threshold=threshold)
    tradeoffs: list[Tradeoff] = []
    if known_start and not passed:
        wait = _wait_tradeoff(inputs, hypothetical, runs=runs, seed=seed, threshold=threshold)
        if wait is not None:
            tradeoffs.append(wait)
        if feasible:
            tradeoffs.extend(
                _tradeoffs(
                    inputs,
                    hypothetical,
                    applied,
                    baseline["goals"],
                    runs=runs,
                    seed=seed,
                    threshold=threshold,
                    allow_cut=allow_cut_tradeoffs,
                )
            )
    if passed:
        result["verdict"] = "yes"
    else:
        same_day = any(trade["kind"] != "wait" for trade in tradeoffs)
        result["verdict"] = "yes-but" if same_day else "no"
    result["verdict_reason"] = verdict_reason
    result["hypothetical"] = {**meta, "feasible": feasible}
    result["tradeoffs"] = tradeoffs
    # A goal slips only through a delay-a-goal plan, which reports its own slip days.
    result["goal_slips"] = []
    result["floor_threshold"] = threshold
    result["verdict_threshold"] = threshold
    result["baseline"] = {
        "floor_probability": baseline["floor_probability"],
        "median_low": baseline["median_low"],
        "first_floor_crossing": baseline["first_floor_crossing"],
    }
    gaps = result["data_quality"].setdefault("gaps", [])
    gaps.extend(applied.gaps)
    if not known_start:
        # There is no starting balance to test, so there is no verdict and no lever runs.
        result.pop("verdict", None)
        result["tradeoffs"] = []
        result["verdict_reason"] = applied.reason or (
            "No liquid account has a known balance, so there is no affordability verdict."
        )
    gaps.append(
        f"the verdict uses {runs:,} seeded scenarios at a {threshold:.0%} floor probability "
        "threshold"
    )
    return result


def project_scenario(
    inputs: ProjectionInput,
    changes: Sequence[ScenarioChangeSpec],
    *,
    hypothetical: Hypothetical | None = None,
    income_source_ids: Collection[str] = (),
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
    threshold: float = FLOOR_THRESHOLD,
    allow_cut_tradeoffs: bool = True,
) -> Projection:
    """Run a multi-event what-if (and optional purchase) against an unchanged baseline."""
    needed = scenario_horizon_days(inputs.as_of, changes, ask_horizon_days=horizon_days(inputs))
    if hypothetical is not None:
        validate_hypothetical(inputs.as_of, hypothetical)
        probe = _apply(inputs, hypothetical)
        last_cash = max(
            probe.meta.get("last_cash_date") or hypothetical.date.isoformat(),
            hypothetical.date.isoformat(),
        )
        hyp_days = (date.fromisoformat(last_cash) - inputs.as_of).days + 1
        needed = max(needed, min(MAX_HORIZON_DAYS, hyp_days))
    base_inputs = replace(inputs, ask_horizon_days=needed)
    applied = apply_changes(base_inputs, changes, income_source_ids=income_source_ids)
    unchanged = project(base_inputs, runs=runs, seed=seed, threshold=threshold)
    if hypothetical is not None:
        result = afford(
            applied.inputs,
            hypothetical,
            runs=runs,
            seed=seed,
            threshold=threshold,
            allow_cut_tradeoffs=allow_cut_tradeoffs,
        )
    else:
        result = project(applied.inputs, runs=runs, seed=seed, threshold=threshold)

    change_rows: list[ScenarioChange] = [
        {
            "kind": item.kind,
            "label": item.label,
            "occurrences": item.occurrences,
            "amount_paise": item.amount_paise,
        }
        for item in applied.changes
    ]
    assumptions: list[str] = [
        "Existing schedule timing and everyday spend baselines stay unchanged outside the "
        "listed scenario changes."
    ]
    if applied.has_added_flow:
        assumptions.append("Added flows are dated as asked without bank-holiday adjustment.")
    if applied.adds_or_raises_inflow:
        assumptions.append(
            "This scenario counts added or raised income that is assumed, not confirmed."
        )
    crossing = result["first_floor_crossing"]
    runway_days = (
        (date.fromisoformat(crossing) - inputs.as_of).days if crossing is not None else None
    )
    baseline_row: ScenarioBaseline = {
        "floor_probability": unchanged["floor_probability"],
        "median_low": unchanged["median_low"],
        "first_floor_crossing": unchanged["first_floor_crossing"],
        "safe_to_spend": unchanged["safe_to_spend"],
    }
    scenario_row: Scenario = {
        "changes": change_rows,
        "assumptions": assumptions,
        "runway_days": runway_days,
        "baseline": baseline_row,
    }
    if hypothetical is None:
        known_start = any(account.known for account in applied.inputs.liquid)
        if known_start:
            holds, reason = _outcome_reason(
                result, floor_paise=applied.inputs.floor_paise, threshold=threshold
            )
            scenario_row["holds"] = holds
            scenario_row["reason"] = reason
        else:
            scenario_row["reason"] = (
                "No liquid account has a known balance, so there is no floor test."
            )
    gaps = result["data_quality"].setdefault("gaps", [])
    gaps.append("this what-if assumes the listed scenario changes, which have not happened")
    for item in applied.changes:
        if item.occurrences == 0:
            gaps.append(f"scenario change matched no occurrence in the horizon: {item.label}")
    result["scenario"] = scenario_row
    return result


__all__ = ["METHODS", "Hypothetical", "afford", "project_scenario", "validate_hypothetical"]
