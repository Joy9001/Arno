"""Affordability: the yes, yes-but, or no verdict and its four tradeoffs.

The verdict is never prose from a model. It is a rerun of the tested projection: pay as asked,
then test whether the floor probability stays at the settled 90% threshold, no obligation is
missed, and no goal is pushed past where it was. When the answer is "yes-but", each listed
tradeoff was computed by rerunning the same engine with that change applied.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

from fin_agent.engine.money import money
from fin_agent.engine.projection import (
    DEFAULT_RUNS,
    DEFAULT_SEED,
    FLOOR_THRESHOLD,
    MAX_CUT_RATIO,
    MAX_HORIZON_DAYS,
    CardAccount,
    CashEvent,
    ProjectionInput,
    card_due_date,
    first_full_payment_date,
    project,
    reduce_categories,
)

METHODS = ("account", "card", "emi")


@dataclass(frozen=True, slots=True)
class Hypothetical:
    amount_paise: int
    date: date
    method: str = "account"
    emi_months: int | None = None
    account: str | None = None


def _month_add(day: date, months: int) -> date:
    from fin_agent.engine.money import _add_months  # noqa: PLC0415 - shared date arithmetic

    return _add_months(day, months)


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


def _hypothetical_inputs(
    inputs: ProjectionInput, hypothetical: Hypothetical
) -> tuple[ProjectionInput, dict[str, Any], str | None]:
    """Apply the purchase and return the new inputs, its metadata, and an infeasible reason."""
    amount = int(hypothetical.amount_paise)
    if amount <= 0:
        return inputs, {}, "the amount must be positive"
    if hypothetical.method not in METHODS:
        return inputs, {}, "method is account, card, or emi"
    when = max(hypothetical.date, inputs.as_of)
    at_start = hypothetical.date <= inputs.as_of
    name = "Hypothetical purchase"
    meta: dict[str, Any] = {
        "amount_paise": amount,
        "date": hypothetical.date.isoformat(),
        "method": hypothetical.method,
        "emi_months": hypothetical.emi_months if hypothetical.method == "emi" else None,
        "account": hypothetical.account,
    }

    if hypothetical.method == "account":
        meta["last_cash_date"] = when.isoformat()
        event = CashEvent(
            date=when,
            amount_paise=-amount,
            name=name,
            kind="hypothetical",
            at_start=at_start,
        )
        return replace(inputs, events=inputs.events + (event,)), meta, None

    months = hypothetical.emi_months if hypothetical.emi_months is not None else 3
    if hypothetical.method == "emi":
        if not 2 <= months <= 48:
            return inputs, meta, "an EMI tenure is 2 to 48 months"
        meta["emi_months"] = months

    card = _pick_card(inputs, hypothetical.account)
    if card is None:
        return inputs, meta, "no card with available credit was found"
    if not card.known:
        return (
            inputs,
            meta,
            (f"{card.name} has no known outstanding balance, so its available credit is unknown."),
        )
    if amount > card.available_credit_paise:
        available = money(card.available_credit_paise)["text"]
        return (
            inputs,
            meta | {"available_credit_paise": card.available_credit_paise},
            f"{card.name} has only {available} of available credit",
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
        return replace(inputs, events=inputs.events + (event,)), meta, None

    base = amount // months
    remainder = amount - base * months
    installments: list[dict[str, Any]] = []
    events: list[CashEvent] = []
    for index in range(months):
        part = base + (remainder if index == months - 1 else 0)
        due_day = _month_add(when, index)
        installments.append({"date": due_day.isoformat(), "amount_paise": part})
        events.append(
            CashEvent(
                date=due_day,
                amount_paise=-part,
                name=f"{name} instalment {index + 1}",
                kind="hypothetical_emi",
                card_id=card.id,
                at_start=due_day <= inputs.as_of,
            )
        )
    meta["installments"] = installments
    meta["total_paise"] = amount
    meta["last_cash_date"] = card_due_date(card, _month_add(when, months - 1)).isoformat()
    return replace(inputs, events=inputs.events + tuple(events)), meta, None


def _extend_horizon(inputs: ProjectionInput, meta: dict[str, Any]) -> ProjectionInput:
    """Cover the plan's last cash date, so an EMI's later installments are not ignored."""
    last_cash = meta.get("last_cash_date")
    if not last_cash:
        return inputs
    needed = (date.fromisoformat(last_cash) - inputs.as_of).days + 1
    if needed > (inputs.ask_horizon_days or 0):
        return replace(inputs, ask_horizon_days=needed)
    return inputs


def _goal_slips(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[dict[str, Any]]:
    old = {goal["id"]: goal for goal in before}
    slips: list[dict[str, Any]] = []
    for goal in after:
        previous = old.get(goal["id"])
        if previous is None:
            continue
        old_date = previous.get("completes_on")
        new_date = goal.get("completes_on")
        if old_date and new_date:
            days = (date.fromisoformat(new_date) - date.fromisoformat(old_date)).days
            if days > 0:
                slips.append(
                    {
                        "id": goal["id"],
                        "name": goal["name"],
                        "slips_days": days,
                        "completes_on": new_date,
                    }
                )
        elif old_date and not new_date:
            slips.append(
                {
                    "id": goal["id"],
                    "name": goal["name"],
                    "slips_days": None,
                    "completes_on": None,
                }
            )
    return slips


def _passes(result: dict[str, Any], *, threshold: float) -> bool:
    return result["floor_probability"] >= threshold and result["obligations_met"]


def _wait_tradeoff(
    inputs: ProjectionInput,
    hypothetical: Hypothetical,
    *,
    runs: int,
    seed: int,
) -> dict[str, Any] | None:
    found = first_full_payment_date(
        inputs,
        int(hypothetical.amount_paise),
        start_on=max(hypothetical.date, inputs.as_of),
        runs=runs,
        seed=seed,
    )
    if not found:
        return None
    return {
        "kind": "wait",
        "date": found["date"],
        "days": found["days"],
        "detail": (f"Wait until {found['date']} ({found['days']} days), then pay the full amount."),
    }


def _card_tradeoff(
    inputs: ProjectionInput,
    hypothetical: Hypothetical,
    *,
    runs: int,
    seed: int,
    threshold: float,
) -> dict[str, Any] | None:
    for method in ("card", "emi"):
        if hypothetical.method == method:
            continue
        candidate = replace(
            hypothetical,
            method=method,
            emi_months=(hypothetical.emi_months or 3) if method == "emi" else None,
        )
        card = _pick_card(inputs, candidate.account)
        if card is None or not card.known:
            continue
        modified, meta, reason = _hypothetical_inputs(inputs, candidate)
        if reason is not None:
            continue
        result = project(_extend_horizon(modified, meta), runs=runs, seed=seed)
        if not _passes(result, threshold=threshold):
            continue
        if method == "card":
            detail = (
                f"Put it on {meta['card_name']} and pay the bill on {meta['due_date']}; "
                "the available limit covers it."
            )
        else:
            count = len(meta.get("installments") or [])
            detail = (
                f"Split it into {count} card instalments of about "
                f"{money(meta['amount_paise'] // count)['text']} each, starting {meta['date']}."
            )
        return {
            "kind": "pay_by_card",
            "method": method,
            "account": meta.get("card_name"),
            "due_date": meta.get("due_date"),
            "installments": meta.get("installments"),
            "detail": detail,
        }
    return None


def _delay_goal_tradeoff(
    inputs: ProjectionInput,
    hypothetical: Hypothetical,
    *,
    runs: int,
    seed: int,
    threshold: float,
    baseline_goals: list[dict[str, Any]],
) -> dict[str, Any] | None:
    when = max(hypothetical.date, inputs.as_of)
    window_end = when + timedelta(days=31)
    for goal in sorted(inputs.goals, key=lambda item: (item.priority, item.name)):
        removed = tuple(
            event
            for event in inputs.events
            if not (
                event.kind == "goal_contribution"
                and event.goal_id == goal.id
                and when <= event.date <= window_end
            )
        )
        if len(removed) == len(inputs.events):
            continue
        modified_goal = replace(inputs, events=removed)
        with_purchase, _meta, reason = _hypothetical_inputs(modified_goal, hypothetical)
        if reason is not None:
            continue
        result = project(with_purchase, runs=runs, seed=seed)
        if not _passes(result, threshold=threshold):
            continue
        slips = _goal_slips(baseline_goals, result["goals"])
        delay = next((slip for slip in slips if slip["id"] == goal.id), None)
        if delay is None:
            continue
        if delay["slips_days"] is None:
            detail = (
                f"Delay the {goal.name} contribution until after the purchase; "
                "the goal no longer completes within the projection."
            )
        else:
            detail = (
                f"Delay the {goal.name} contribution until after the purchase; "
                f"the goal completes {delay['slips_days']} days later."
            )
        return {
            "kind": "delay_goal",
            "goal": goal.name,
            "goal_id": goal.id,
            "slips_days": delay["slips_days"],
            "completes_on": delay["completes_on"],
            "detail": detail,
        }
    return None


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


def _cut_category_tradeoff(
    inputs: ProjectionInput,
    hypothetical: Hypothetical,
    *,
    runs: int,
    seed: int,
    threshold: float,
    with_purchase: dict[str, Any],
) -> dict[str, Any] | None:
    floor = inputs.floor_paise
    headroom = int(with_purchase.get("p10_low_paise") or 0) - floor
    if headroom >= 0:
        return None
    shortfall = -headroom
    when = max(hypothetical.date, inputs.as_of)
    payday = _next_payday(inputs, when) or (when + timedelta(days=30))
    days = max(1, (payday - when).days)
    needed_daily = math.ceil(shortfall / days)
    reducible = [
        category
        for category in inputs.categories
        if category.reducible
        and category.baseline_per_day_paise > 0
        and not category.covered_by_obligation
    ]
    total_daily = sum(category.baseline_per_day_paise for category in reducible)
    if total_daily <= 0:
        return None
    if needed_daily > total_daily * MAX_CUT_RATIO:
        return None
    start_ratio = min(max(needed_daily / total_daily, 0.0), MAX_CUT_RATIO)
    for step in range(6):
        ratio = min(MAX_CUT_RATIO, start_ratio + step * 0.02)
        reduced, rows = reduce_categories(inputs, ratio, until=payday)
        with_purchase, _meta, reason = _hypothetical_inputs(reduced, hypothetical)
        if reason is not None:
            continue
        result = project(with_purchase, runs=runs, seed=seed)
        if not _passes(result, threshold=threshold):
            continue
        names = ", ".join(row["category"] for row in rows)
        return {
            "kind": "cut_category",
            "until": payday.isoformat(),
            "ratio": round(ratio, 4),
            "categories": rows,
            "detail": (
                f"Cut {names} by about {round(ratio * 100)}% until {payday.isoformat()}, "
                f"then back to normal spending, to free the shortfall."
            ),
        }
    return None


def afford(
    inputs: ProjectionInput,
    hypothetical: Hypothetical,
    *,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
    threshold: float = FLOOR_THRESHOLD,
) -> dict[str, Any]:
    """Return the projection with the purchase applied and a tested verdict and tradeoffs."""
    if hypothetical.amount_paise <= 0:
        raise ValueError("the amount must be positive")
    if hypothetical.method not in METHODS:
        raise ValueError("method is account, card, or emi")
    if hypothetical.method == "emi":
        tenure = hypothetical.emi_months if hypothetical.emi_months is not None else 3
        if not 2 <= tenure <= 48:
            raise ValueError("an EMI tenure is 2 to 48 months")
    if (hypothetical.date - inputs.as_of).days > MAX_HORIZON_DAYS - 30:
        raise ValueError("the purchase date is beyond the projection horizon")
    known_start = any(account.known for account in inputs.liquid)
    modified, meta, reason = _hypothetical_inputs(inputs, hypothetical)
    meta["last_cash_date"] = max(
        meta.get("last_cash_date") or hypothetical.date.isoformat(),
        hypothetical.date.isoformat(),
    )
    inputs = _extend_horizon(inputs, meta)
    modified = _extend_horizon(modified, meta) if reason is None else inputs
    baseline = project(inputs, runs=runs, seed=seed)
    if reason is not None:
        result = baseline
        slips: list[dict[str, Any]] = []
        tradeoffs: list[dict[str, Any]] = []
        wait = _wait_tradeoff(inputs, hypothetical, runs=runs, seed=seed)
        if wait:
            tradeoffs.append(wait)
        result["verdict"] = "yes-but" if tradeoffs else "no"
        result["verdict_reason"] = reason
        result["hypothetical"] = meta | {"feasible": False}
        result["tradeoffs"] = tradeoffs
        result["goal_slips"] = slips
        result["floor_threshold"] = threshold
        result["baseline"] = {
            "floor_probability": baseline["floor_probability"],
            "median_low": baseline["median_low"],
            "first_floor_crossing": baseline["first_floor_crossing"],
        }
    else:
        result = project(modified, runs=runs, seed=seed)
        slips = _goal_slips(baseline["goals"], result["goals"])
        approved = _passes(result, threshold=threshold) and not slips and result["obligations_met"]
        if approved:
            verdict = "yes"
            tradeoffs: list[dict[str, Any]] = []
            verdict_reason = (
                f"{result['floor_probability']:.0%} of simulated futures "
                "stay at or above the floor, no obligation is missed, and no goal slips."
            )
        else:
            tradeoffs = []
            wait = _wait_tradeoff(inputs, hypothetical, runs=runs, seed=seed)
            if wait:
                tradeoffs.append(wait)
            card = _card_tradeoff(inputs, hypothetical, runs=runs, seed=seed, threshold=threshold)
            if card:
                tradeoffs.append(card)
            delayed = _delay_goal_tradeoff(
                inputs,
                hypothetical,
                runs=runs,
                seed=seed,
                threshold=threshold,
                baseline_goals=baseline["goals"],
            )
            if delayed:
                tradeoffs.append(delayed)
            cut = _cut_category_tradeoff(
                inputs,
                hypothetical,
                runs=runs,
                seed=seed,
                threshold=threshold,
                with_purchase=result,
            )
            if cut:
                tradeoffs.append(cut)
            verdict = "yes-but" if tradeoffs else "no"
            if slips:
                verdict_reason = "A goal would slip past its current completion date."
            elif not result["obligations_met"]:
                verdict_reason = "An obligation cannot be met on its date in some futures."
            else:
                where = "the floor" if inputs.floor_paise > 0 else "zero"
                verdict_reason = (
                    f"Only {result['floor_probability']:.0%} of simulated futures stay above "
                    f"{where} at the {threshold:.0%} threshold."
                )
        result["verdict"] = verdict
        result["verdict_reason"] = verdict_reason
        result["hypothetical"] = meta | {"feasible": True}
        result["tradeoffs"] = tradeoffs
        result["goal_slips"] = slips
        result["floor_threshold"] = threshold
        result["baseline"] = {
            "floor_probability": baseline["floor_probability"],
            "median_low": baseline["median_low"],
            "first_floor_crossing": baseline["first_floor_crossing"],
        }
    result["verdict_threshold"] = threshold
    if not known_start:
        # There is no starting balance to test, so there is no verdict to issue.
        result.pop("verdict", None)
        result["tradeoffs"] = []
        result["verdict_reason"] = reason or (
            "No liquid account has a known balance, so there is no affordability verdict."
        )
    result["data_quality"]["gaps"].append(
        f"the verdict uses {runs} seeded walks at a {threshold:.0%} floor probability threshold"
    )
    return result


__all__ = ["METHODS", "Hypothetical", "afford"]
