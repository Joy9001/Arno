"""HackerRank "Buy or Wait" scorer.

The 25 solved sample requests have externally written answers, so they check the affordability
logic against someone else's labels. The dataset has no license, so it is read only from a
git-ignored local path and never committed. When that path is absent, `score` says so plainly
instead of inventing a number.
"""

from __future__ import annotations

import csv
import os
import statistics
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fin_agent.engine.projection import (
    DEFAULT_RUNS,
    DEFAULT_SEED,
    CashEvent,
    LiquidAccount,
    ProjectionInput,
    SpendCategory,
    first_full_payment_date,
    project,
    safe_amount_today,
)

DATASET_ENV = "FIN_AGENT_HACKERRANK_DATASET"
REPO_ROOT = Path(__file__).resolve().parents[3]
EXPECTED = ("affordable_now", "affordable_with_plan", "affordable_later", "not_affordable")


def find_dataset() -> Path | None:
    candidates: list[Path] = []
    if os.environ.get(DATASET_ENV):
        candidates.append(Path(os.environ[DATASET_ENV]))
    candidates.extend(
        [
            REPO_ROOT / "private" / "hackerrank-orchestrate-september26" / "dataset",
            REPO_ROOT / "private" / "hackerrank-dataset",
            REPO_ROOT / "hackerrank-orchestrate-september26" / "dataset",
        ]
    )
    for candidate in candidates:
        if (candidate / "sample_requests.csv").exists():
            return candidate
    return None


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _paise(value: str | float | int | None) -> int:
    if value is None or value == "":
        return 0
    return int(round(float(value) * 100))


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _split(value: str | None) -> set[str]:
    return {item.strip() for item in (value or "").split("|") if item.strip()}


def _recurrence(events: list[dict[str, str]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Infer weekly, monthly, or yearly cadence from settled history for each category."""
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for event in events:
        if event["status"] != "settled" or event["direction"] == "non_cash":
            continue
        if not event.get("event_date") or not event.get("amount"):
            continue
        key = (event["category"], event["direction"], event["event_type"])
        groups.setdefault(key, []).append(event)
    found: dict[tuple[str, str, str], dict[str, Any]] = {}
    for key, rows in groups.items():
        rows.sort(key=lambda row: row["event_date"])
        if len(rows) < 2:
            continue
        gaps = [
            (
                date.fromisoformat(rows[index]["event_date"])
                - date.fromisoformat(rows[index - 1]["event_date"])
            ).days
            for index in range(1, len(rows))
        ]
        median = statistics.median(gaps)
        if 6 <= median <= 8:
            step, months = "weekly", 0
        elif 27 <= median <= 33:
            step, months = "monthly", 1
        elif 360 <= median <= 370:
            step, months = "yearly", 12
        else:
            continue
        amounts = [abs(_paise(row["amount"])) for row in rows[-3:]]
        found[key] = {
            "step": step,
            "months": months,
            "amount": int(statistics.median(amounts)),
            "last": date.fromisoformat(rows[-1]["event_date"]),
            "category": key[0],
            "direction": key[1],
            "event_type": key[2],
        }
    return found


def _add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    last_day = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last_day))


def _recurring_date(pattern: dict[str, Any], index: int) -> date:
    """The pattern's occurrence ``index`` steps after its last settled date, anchored there."""
    anchor = pattern["last"]
    if pattern["step"] == "monthly":
        return _add_months(anchor, index)
    if pattern["step"] == "yearly":
        return _add_months(anchor, 12 * index)
    return anchor + timedelta(days=7 * index)


def _events_for(
    user_events: list[dict[str, str]], request_date: date
) -> tuple[list[CashEvent], set[str], list[dict[str, Any]]]:
    end = request_date + timedelta(days=90)
    future = [
        event
        for event in user_events
        if event.get("settlement_date")
        and _day(event["settlement_date"]) is not None
        and request_date < _day(event["settlement_date"]) <= end
        and event["status"] in {"scheduled", "pending"}
        and event["direction"] in {"debit", "credit"}
    ]
    events: list[CashEvent] = []
    covered: set[str] = set()
    sources: list[dict[str, Any]] = []
    explicit: set[tuple[str, str]] = set()
    for event in future:
        when = _day(event["settlement_date"])
        assert when is not None
        amount = _paise(event["amount"]) if event.get("amount") else 0
        if amount <= 0:
            continue
        signed = -amount if event["direction"] == "debit" else amount
        kind = (
            "income_fixed"
            if signed > 0
            else (
                "obligation"
                if event["event_type"] in {"expense", "subscription", "debt_payment"}
                else "owed_payment"
            )
        )
        events.append(
            CashEvent(
                date=when,
                amount_paise=signed,
                name=event.get("description") or event["event_type"],
                kind=kind,
                source_id=event["event_id"],
                confirmed=True,
            )
        )
        explicit.add((event["category"], event["direction"]))
        if signed < 0:
            covered.add(event["category"])
        sources.append(
            {
                "kind": "event",
                "id": event["event_id"],
                "name": event.get("description") or event["event_type"],
                "detail": f"{event['direction']} · {event['category']} · {when.isoformat()}",
                "amount_paise": abs(signed),
            }
        )

    recurrence = _recurrence(user_events)
    for key, pattern in recurrence.items():
        category, direction, event_type = key
        if (category, direction) in explicit:
            continue
        step = 1
        when = _recurring_date(pattern, step)
        while when <= request_date:
            step += 1
            when = _recurring_date(pattern, step)
        while when <= end:
            signed = -pattern["amount"] if direction == "debit" else pattern["amount"]
            kind = (
                "income_fixed"
                if signed > 0
                else "obligation"
                if event_type in {"expense", "subscription", "debt_payment"}
                else "owed_payment"
            )
            events.append(
                CashEvent(
                    date=when,
                    amount_paise=signed,
                    name=f"{category} (recurring)",
                    kind=kind,
                    source_id=f"{category}:{direction}",
                    confirmed=True,
                )
            )
            if signed < 0:
                covered.add(category)
            step += 1
            when = _recurring_date(pattern, step)
    return events, covered, sources


def _inputs_for(
    profile: dict[str, str],
    user_events: list[dict[str, str]],
    request: dict[str, str],
    *,
    category_list: list[str] | None = None,
) -> ProjectionInput:
    request_date = _day(request["request_date"])
    assert request_date is not None
    events, covered, _sources = _events_for(user_events, request_date)
    history_start = request_date - timedelta(days=90)
    daily: dict[str, dict[date, int]] = {}
    for event in user_events:
        if event["status"] != "settled" or event["direction"] != "debit":
            continue
        when = _day(event.get("event_date"))
        if when is None or not (history_start <= when < request_date):
            continue
        if not event.get("amount") or not event.get("category"):
            continue
        bucket = daily.setdefault(event["category"], {})
        bucket[when] = bucket.get(when, 0) + _paise(event["amount"])

    willing = _split(profile.get("expense_categories_user_is_willing_to_reduce"))
    stoppable = _split(profile.get("expense_categories_user_is_willing_to_stop"))
    categories: list[SpendCategory] = []
    for category in category_list or sorted(daily):
        if category in covered:
            continue
        by_day = daily.get(category, {})
        history = tuple(
            (
                history_start + timedelta(days=index),
                by_day.get(history_start + timedelta(days=index), 0),
            )
            for index in range(90)
        )
        total = sum(amount for _day_value, amount in history)
        categories.append(
            SpendCategory(
                id=category,
                name=category,
                baseline_per_day_paise=round(total / 90),
                baseline_source="computed",
                reducible=category in willing or category in stoppable,
                card_share=0.0,
                history=history,
            )
        )
    return ProjectionInput(
        as_of=request_date,
        floor_paise=_paise(profile["minimum_balance_to_keep"]),
        liquid=(
            LiquidAccount(
                id=profile["user_id"],
                name=profile["user_id"],
                balance_paise=_paise(profile["current_available_balance"]),
                as_of=request_date,
                statement_age_days=0,
            ),
        ),
        events=tuple(events),
        categories=tuple(categories),
        ask_horizon_days=90,
        data_quality={
            "balance_ages": [],
            "baseline_sources": [],
            "gaps": ["HackerRank sample data; this is a local external check"],
            "setup_gaps": [],
        },
    )


def _installment_feasible(
    inputs: ProjectionInput,
    option: dict[str, str],
    *,
    desired: date | None,
    runs: int,
    seed: int,
) -> bool:
    first = _day(option.get("first_payment_date"))
    if first is None:
        return False
    count = int(option.get("number_of_payments") or 0)
    if count < 1:
        return False
    freq = int(option.get("payment_frequency_days") or 0)
    each = _paise(option.get("payment_amount"))
    if each <= 0:
        return False
    last = first + timedelta(days=freq * (count - 1))
    if desired is not None and last > desired:
        return False
    events = tuple(
        CashEvent(
            date=first + timedelta(days=freq * index),
            amount_paise=-each,
            name="HackerRank installment",
            kind="hypothetical",
        )
        for index in range(count)
    )
    result = project(replace(inputs, events=inputs.events + events), runs=runs, seed=seed)
    return result["floor_probability"] >= 0.90 and result["obligations_met"]


def _predict(
    request: dict[str, str],
    profile: dict[str, str],
    user_events: list[dict[str, str]],
    payment_options: list[dict[str, str]],
    *,
    runs: int,
    seed: int,
) -> dict[str, Any]:
    inputs = _inputs_for(profile, user_events, request)
    requested = _paise(request["requested_amount"])
    request_date = _day(request["request_date"])
    assert request_date is not None
    desired = _day(request.get("desired_completion_date"))
    safe = min(safe_amount_today(inputs, runs=runs, seed=seed), requested)
    earliest = first_full_payment_date(
        inputs, requested, start_on=request_date, runs=runs, seed=seed
    )
    methods = _split(profile.get("payment_methods_user_will_consider"))
    installment = next(
        (
            option
            for option in payment_options
            if option.get("payment_method") == "installments"
            and _installment_feasible(inputs, option, desired=desired, runs=runs, seed=seed)
        ),
        None,
    )
    partial_ok = (
        request.get("allows_partial_payment", "").lower() == "true"
        and 0 < safe < requested
        and earliest is not None
        and (desired is None or date.fromisoformat(earliest["date"]) <= desired)
        and "partial_payment" in methods
    )
    if safe >= requested and "full_payment" in methods:
        status = "affordable_now"
        method = "full_payment"
        earliest_date = request_date.isoformat()
    elif installment is not None and "installments" in methods:
        status = "affordable_with_plan"
        method = "installments"
        earliest_date = earliest["date"] if earliest else ""
    elif partial_ok:
        status = "affordable_with_plan"
        method = "partial_payment"
        earliest_date = earliest["date"] if earliest else ""
    elif earliest is not None and "full_payment" in methods:
        status = "affordable_later"
        method = "wait"
        earliest_date = earliest["date"]
    else:
        status = "not_affordable"
        method = "not_recommended"
        earliest_date = ""
    return {
        "request_id": request["request_id"],
        "amount_safe_to_pay": safe / 100,
        "affordability_status": status,
        "recommended_payment_method": method,
        "earliest_date_for_full_payment": earliest_date,
    }


def _amount_ok(predicted: float, expected: float, requested: float) -> bool:
    tolerance = max(requested * 0.05, 1.0)
    return abs(predicted - expected) <= tolerance


def _dates_ok(predicted: str, expected: str) -> bool:
    if not predicted and not expected:
        return True
    if not predicted or not expected:
        return False
    try:
        return abs((date.fromisoformat(predicted) - date.fromisoformat(expected)).days) <= 3
    except ValueError:
        return False


def score(
    dataset: Path | None = None,
    *,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    dataset = dataset or find_dataset()
    if dataset is None:
        # TODO(L5): run the 25 labeled samples on a machine that has the local dataset, publish
        # the score in the README, and flip this to available. The dataset has no license and is
        # never committed.
        return {
            "available": False,
            "reason": (
                "the HackerRank sample dataset is not on this machine; set "
                f"{DATASET_ENV} to the dataset folder (it has no license and is never committed)"
            ),
            "score": None,
        }
    profiles = {row["user_id"]: row for row in _read_csv(dataset / "financial_profiles.csv")}
    events: dict[str, list[dict[str, str]]] = {}
    for row in _read_csv(dataset / "financial_events.csv"):
        events.setdefault(row["user_id"], []).append(row)
    options: dict[str, list[dict[str, str]]] = {}
    for row in _read_csv(dataset / "request_payment_options.csv"):
        options.setdefault(row["request_id"], []).append(row)
    samples = _read_csv(dataset / "sample_requests.csv")
    rows: list[dict[str, Any]] = []
    for sample in samples:
        profile = profiles.get(sample["user_id"])
        if profile is None:
            continue
        try:
            predicted = _predict(
                sample,
                profile,
                events.get(sample["user_id"], []),
                options.get(sample["request_id"], []),
                runs=runs,
                seed=seed,
            )
        except (KeyError, ValueError, TypeError) as exc:
            rows.append(
                {
                    "request_id": sample["request_id"],
                    "error": str(exc),
                    "status_match": False,
                    "amount_match": False,
                    "earliest_match": False,
                }
            )
            continue
        status_match = predicted["affordability_status"] == sample["affordability_status"]
        amount_match = _amount_ok(
            float(predicted["amount_safe_to_pay"]),
            float(sample["amount_safe_to_pay"]),
            float(sample["requested_amount"]),
        )
        earliest_match = _dates_ok(
            predicted["earliest_date_for_full_payment"],
            sample["earliest_date_for_full_payment"],
        )
        rows.append(
            {
                **predicted,
                "expected_status": sample["affordability_status"],
                "expected_amount_safe_to_pay": float(sample["amount_safe_to_pay"]),
                "expected_earliest_date": sample["earliest_date_for_full_payment"],
                "status_match": status_match,
                "amount_match": amount_match,
                "earliest_match": earliest_match,
            }
        )
    scored = [row for row in rows if "error" not in row]
    n = len(scored)
    if n == 0:
        return {"available": False, "reason": "no scorable samples", "score": None}
    status_accuracy = sum(1 for row in scored if row["status_match"]) / n
    amount_accuracy = sum(1 for row in scored if row["amount_match"]) / n
    earliest_accuracy = sum(1 for row in scored if row["earliest_match"]) / n
    return {
        "available": True,
        "dataset": str(dataset),
        "samples": n,
        "score": round(0.5 * status_accuracy + 0.3 * amount_accuracy + 0.2 * earliest_accuracy, 4),
        "status_accuracy": round(status_accuracy, 4),
        "amount_accuracy": round(amount_accuracy, 4),
        "earliest_accuracy": round(earliest_accuracy, 4),
        "rows": rows,
    }


def format_report(result: dict[str, Any]) -> str:
    if not result.get("available"):
        return f"HackerRank sample score: not run. {result['reason']}."
    return "\n".join(
        [
            f"HackerRank 'Buy or Wait' samples: {result['samples']}",
            f"Combined score: {result['score']:.2%}",
            f"Affordability status: {result['status_accuracy']:.0%}",
            f"amount_safe_to_pay within 5% of requested: {result['amount_accuracy']:.0%}",
            f"Earliest full-payment date within 3 days: {result['earliest_accuracy']:.0%}",
            f"source: {result['dataset']} (local, not committed)",
        ]
    )


def publish_report(result: dict[str, Any], readme: Path) -> None:
    text = format_report(result)
    start = "<!-- hackerrank:start -->"
    end = "<!-- hackerrank:end -->"
    body = f"{start}\n{text}\n{end}"
    content = readme.read_text(encoding="utf-8")
    if start in content and end in content:
        before, rest = content.split(start, 1)
        _old, after = rest.split(end, 1)
        readme.write_text(f"{before}{body}{after}", encoding="utf-8")
    else:
        heading = "## HackerRank Buy or Wait score"
        readme.write_text(f"{content.rstrip()}\n\n{heading}\n\n{body}\n", encoding="utf-8")


__all__ = [
    "DATASET_ENV",
    "EXPECTED",
    "find_dataset",
    "format_report",
    "publish_report",
    "score",
]
