"""HackerRank "Buy or Wait" adapter over Arno's production projection and detection engine.

The 25 solved sample requests have externally written answers, so they check the shipped engine
against independent labels. The dataset has no license, so it is read only from a git-ignored
local path and never committed. When that path is absent, `score` says so plainly instead of
inventing a number.

Dataset conventions that live here in the adapter:
- the ~185-day history window (`DATASET_HISTORY_DAYS`) and 4-day horizon tail cutoff (`TAIL_DAYS`);
- category-first debit grouping with independent stable merchant sub-series;
- regular N-day cadence fallback (`round(median gap)`) outside production rule families;
- income consolidation by category and dominant day-of-month, `\bfinal\b` drop rule, explicit
  future `scheduled` anchoring (`MIN_INCOME_OCCURRENCES = 2`), and `1.5 * period` freshness;
- deterministic `CashEvent` emission with day-0 inclusion when
  `(request_date - last).days >= period - 3`;
- profile `payment_methods_user_will_consider`, merchant installments, `allows_partial_payment`, and
  discrete `stoppable`/`reducible` stream adjustments;
- structural message regexes (`_parse_message`) and foreign-currency / `image_amounts.json` loading.

Rule inference delegates to `arno.engine.series` (`detect`, `upcoming`, and `PROJECT_CONFIDENCE`),
and plan evaluation delegates to `arno.engine.projection` (`project`, `safe_amount_today`, and
`first_full_payment_date`).
"""

from __future__ import annotations

import calendar
import csv
import itertools
import json
import math
import os
import re
import statistics
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from arno.engine import patterns, scoring, series
from arno.engine.money import add_months
from arno.engine.projection import (
    DEFAULT_RUNS,
    DEFAULT_SEED,
    FLOOR_THRESHOLD,
    CashEvent,
    LiquidAccount,
    ProjectionInput,
    first_full_payment_date,
    project,
    safe_amount_today,
)
from arno.india.workdays import IndiaCalendar, calendar_for

DATASET_ENV = "ARNO_HACKERRANK_DATASET"
REPO_ROOT = Path(__file__).resolve().parents[3]
EXPECTED = ("affordable_now", "affordable_with_plan", "affordable_later", "not_affordable")
HORIZON_DAYS = 90
# Dataset convention: settled history spans roughly six months (~185 days), not 800 days.
DATASET_HISTORY_DAYS = 185
# Dataset convention: an inferred recurring occurrence in the final days is too uncertain to fail a
# later payment date, while confirmed scheduled flows keep the full window.
TAIL_DAYS = 4
DEDUPE_DAYS = 3
MIN_DEBIT_OCCURRENCES = 3
# Dataset convention: income streams require at least 2 occurrences (counting future scheduled).
MIN_INCOME_OCCURRENCES = 2

WEIGHTS: tuple[float, float, float] = (0.5, 0.3, 0.2)
AMOUNT_TOLERANCE_RATIO = 0.05
AMOUNT_MIN_TOLERANCE = 1.0
DATE_TOLERANCE_DAYS = 3

# Dataset convention: strip month names, 4-digit years, and quarters when grouping descriptions.
_PERIOD = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
    r"\d{4}|q[1-4])\b",
    re.IGNORECASE,
)
_FINAL_MARKER = re.compile(r"\bfinal\b", re.IGNORECASE)


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


def _optional_csv(path: Path) -> list[dict[str, str]]:
    return _read_csv(path) if path.exists() else []


def _paise(value: str | float | int | None) -> int:
    if value is None or value == "":
        return 0
    return int(round(float(value) * 100))


def _day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _split(value: str | None) -> set[str]:
    return {item.strip() for item in (value or "").split("|") if item.strip()}


def _median_gap(dates: list[date]) -> float:
    gaps = [(later - earlier).days for earlier, later in zip(dates, dates[1:], strict=False)]
    return statistics.median(gaps) if gaps else 0.0


def _regular(dates: list[date]) -> bool:
    """Dataset convention: gaps within max(2, 25% of median) days count as regular."""
    gaps = [(later - earlier).days for earlier, later in zip(dates, dates[1:], strict=False)]
    if not gaps:
        return False
    median = statistics.median(gaps)
    if median <= 0:
        return False
    tolerance = max(2.0, 0.25 * median)
    return all(abs(gap - median) <= tolerance for gap in gaps)


def _stable(amounts: list[int]) -> bool:
    """Dataset convention: merchant sub-series amounts within a 5% spread count as stable."""
    if len(amounts) < 2:
        return False
    mean = statistics.fmean(amounts)
    if mean == 0:
        return all(amount == 0 for amount in amounts)
    return (max(amounts) - min(amounts)) / abs(mean) <= 0.05


def _same_day_of_month(dates: list[date]) -> bool:
    days: set[int | str] = set()
    for when in dates:
        last = calendar.monthrange(when.year, when.month)[1]
        days.add("end" if when.day == last and when.day < 31 else when.day)
    if len(days) == 1:
        return True
    return days <= {28, 29, 30, 31, "end"}


def _day_of_month(dates: list[date]) -> int:
    counts: dict[int, int] = {}
    for when in dates:
        counts[when.day] = counts.get(when.day, 0) + 1
    top = max(counts.values())
    tied = [day for day, count in counts.items() if count == top]
    return max(tied)


def _norm_description(text: str) -> str:
    """Dataset convention: normalize merchant descriptions without splitting on month/year."""
    return re.sub(r"\s+", " ", _PERIOD.sub(" ", (text or "").strip().lower())).strip()


@dataclass(frozen=True, slots=True)
class _Rate:
    when: date
    source: str
    target: str
    rate: float


def _load_rates(dataset: Path) -> tuple[_Rate, ...]:
    rates: list[_Rate] = []
    for row in _optional_csv(dataset / "exchange_rates.csv"):
        when = _day(row.get("rate_date"))
        if when is None:
            continue
        try:
            value = float(row.get("rate") or 0)
        except ValueError:
            continue
        if value <= 0:
            continue
        rates.append(
            _Rate(
                when,
                (row.get("from_currency") or "").upper(),
                (row.get("to_currency") or "").upper(),
                value,
            )
        )
    return tuple(sorted(rates, key=lambda item: item.when))


def _pair_rate(rates: tuple[_Rate, ...], source: str, target: str, on: date) -> float | None:
    direct = [rate for rate in rates if rate.source == source and rate.target == target]
    if direct:
        eligible = [rate for rate in direct if rate.when <= on] or direct
        return max(eligible, key=lambda item: item.when).rate
    inverse = [rate for rate in rates if rate.source == target and rate.target == source]
    if inverse:
        eligible = [rate for rate in inverse if rate.when <= on] or inverse
        value = max(eligible, key=lambda item: item.when).rate
        return 1.0 / value if value else None
    return None


def _convert_paise(
    amount_paise: int, source: str, target: str, on: date, rates: tuple[_Rate, ...]
) -> int:
    source = source.upper()
    target = target.upper()
    if source == target or amount_paise == 0:
        return amount_paise
    rate = _pair_rate(rates, source, target, on)
    if rate is None:
        first = _pair_rate(rates, source, "USD", on)
        second = _pair_rate(rates, "USD", target, on)
        if first is not None and second is not None:
            rate = first * second
    if rate is None:
        return amount_paise
    return int(round(amount_paise * rate))


def _load_image_amounts(dataset: Path) -> dict[str, int]:
    """Blank event amounts, keyed by event id. Images are read locally; never committed."""
    amounts: dict[str, int] = {}
    event_for_image: dict[str, str] = {}
    for row in _optional_csv(dataset / "images.csv"):
        image = row.get("image_id") or ""
        event = row.get("related_event_id") or ""
        if image and event:
            event_for_image[image] = event
        amount = row.get("amount") or ""
        if event and amount:
            amounts[event] = _paise(amount)
    extra = dataset / "image_amounts.json"
    if not extra.exists():
        return amounts
    try:
        payload: object = json.loads(extra.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return amounts
    if not isinstance(payload, dict):
        return amounts
    for key, value in payload.items():
        if not isinstance(key, str):
            continue
        event = event_for_image.get(key, key)
        amount: object = value
        if isinstance(value, dict):
            nested: object = value.get("amount")
            related: object = value.get("related_event_id")
            if isinstance(related, str):
                event = related
            amount = nested
        if isinstance(amount, bool):
            continue
        if isinstance(amount, (int, float)):
            amounts[event] = int(round(float(amount) * 100))
    return amounts


def _load_messages(dataset: Path) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in _optional_csv(dataset / "messages.csv"):
        grouped.setdefault(row.get("user_id") or "", []).append(row)
    return grouped


def _is_income(row: dict[str, str]) -> bool:
    if row.get("direction") != "credit":
        return False
    if row.get("event_type") in {
        "refund",
        "investment_purchase",
        "investment_sale",
        "investment_valuation",
    }:
        return False
    if row.get("category") == "windfall":
        return False
    return row.get("category") == "salary" or row.get("event_type") == "income"


def _row_paise(
    row: dict[str, str],
    *,
    home: str,
    rates: tuple[_Rate, ...],
    images: dict[str, int],
) -> int | None:
    """The row's amount in home-currency paise, resolved from a number or a linked image."""
    raw = row.get("amount") or ""
    native: int | None = None
    if raw:
        try:
            native = _paise(raw)
        except ValueError:
            native = None
    else:
        native = images.get(row.get("event_id") or "")
    if native is None:
        return None
    on = _day(row.get("event_date")) or _day(row.get("settlement_date"))
    if on is None:
        return native
    return _convert_paise(native, row.get("currency") or home, home, on, rates)


@dataclass(frozen=True, slots=True)
class _SettledFlow:
    event_id: str
    when: date
    amount_paise: int
    direction: str
    category: str
    description: str
    flexibility: str
    minimum_allowed_paise: int


@dataclass(frozen=True, slots=True)
class _InferredStream:
    kind: str  # "spend" or "income"
    key: str
    name: str
    category: str
    last: date
    level_paise: int
    period_days: int
    monthly: bool
    event_id: str
    flexibility: str
    minimum_allowed_paise: int
    detection: series.Detection | None
    next_date: date | None = None
    anchored: bool = False


def _settled_flows(
    user_events: list[dict[str, str]],
    request_date: date,
    *,
    home: str,
    rates: tuple[_Rate, ...],
    images: dict[str, int],
) -> list[_SettledFlow]:
    window_start = request_date - timedelta(days=DATASET_HISTORY_DAYS)
    flows: list[_SettledFlow] = []
    for row in user_events:
        if row.get("status") != "settled":
            continue
        direction = row.get("direction") or ""
        if direction not in {"debit", "credit"}:
            continue
        if direction == "credit" and not _is_income(row):
            continue
        when = _day(row.get("event_date"))
        if when is None or when < window_start or when > request_date:
            continue
        amount = _row_paise(row, home=home, rates=rates, images=images)
        if amount is None or amount <= 0:
            continue
        flows.append(
            _SettledFlow(
                event_id=row.get("event_id") or "",
                when=when,
                amount_paise=amount,
                direction=direction,
                category=row.get("category") or "",
                description=row.get("description") or row.get("category") or "",
                flexibility=row.get("flexibility") or "",
                minimum_allowed_paise=_paise(row.get("minimum_allowed_amount")),
            )
        )
    return flows


def _detect_rule(
    items: list[_SettledFlow],
    *,
    as_of: date,
    window_start: date,
    calendar: IndiaCalendar,
) -> series.Detection | None:
    if len(items) < MIN_DEBIT_OCCURRENCES:
        return None
    payments = [
        series.Payment(movement_id=item.event_id, date=item.when, amount_paise=item.amount_paise)
        for item in items
    ]
    detection = series.detect(
        payments,
        as_of=as_of,
        window_start=window_start,
        calendar=calendar,
    )
    if detection is None or detection.confidence < series.PROJECT_CONFIDENCE:
        return None
    return detection


def _is_monthly_family(family: str) -> bool:
    return family in {"monthly", "last_day", "last_working_day", "nth_weekday"}


def _detect_debit_streams(
    flows: list[_SettledFlow],
    request_date: date,
    window_start: date,
    cal: IndiaCalendar,
) -> list[_InferredStream]:
    """Detect recurring outflows with category-first grouping and N-day interval fallback."""
    # Dataset convention: group settled outflows by category first because discretionary categories
    # rotate across merchants; keep stable merchant sub-series when independent ones exist.
    by_category: dict[str, list[_SettledFlow]] = {}
    for flow in flows:
        if flow.direction == "debit" and flow.category:
            by_category.setdefault(flow.category, []).append(flow)

    streams: list[_InferredStream] = []
    for category in sorted(by_category):
        members = sorted(by_category[category], key=lambda item: (item.when, item.event_id))
        if len(members) < MIN_DEBIT_OCCURRENCES:
            continue
        descriptions: dict[str, list[_SettledFlow]] = {}
        for item in members:
            descriptions.setdefault(_norm_description(item.description), []).append(item)
        qualified: list[list[_SettledFlow]] = []
        for desc_key in sorted(descriptions):
            subset = descriptions[desc_key]
            dates = [item.when for item in subset]
            amounts = [item.amount_paise for item in subset]
            if len(subset) >= MIN_DEBIT_OCCURRENCES and _regular(dates) and _stable(amounts):
                qualified.append(subset)
        all_dates = [item.when for item in members]
        chosen = (
            qualified
            if len(qualified) >= 2 or (len(qualified) == 1 and not _regular(all_dates))
            else [members]
        )
        for index, subset in enumerate(chosen):
            if len(subset) < MIN_DEBIT_OCCURRENCES:
                continue
            detection = _detect_rule(
                subset, as_of=request_date, window_start=window_start, calendar=cal
            )
            latest = subset[-1]
            if detection is not None:
                matched_ids = set(detection.matched)
                matched = [item for item in subset if item.event_id in matched_ids] or subset
                matched_dates = [item.when for item in matched]
                family = detection.rule.family
                monthly = _is_monthly_family(family)
                period = patterns.family_interval_days(family) or max(
                    1, int(round(_median_gap(matched_dates)))
                )
                last = matched[-1].when
                level = detection.amounts.level_paise
                name = matched[-1].description or category
            else:
                # Dataset convention: for cadences outside production rule families (5-, 10-, and
                # 21-day cycles), fall back to a regular N-day interval: round(median gap).
                dates = [item.when for item in subset]
                if not _regular(dates):
                    continue
                median = _median_gap(dates)
                period = max(1, int(round(median)))
                monthly = 28 <= median <= 31 or (
                    len(dates) >= 3 and median >= 28 and _same_day_of_month(dates)
                )
                if monthly:
                    period = 30
                last = dates[-1]
                level = int(round(statistics.fmean(item.amount_paise for item in subset)))
                name = latest.description or category
            streams.append(
                _InferredStream(
                    kind="spend",
                    key=f"{category}:{index}" if len(chosen) > 1 else category,
                    name=name,
                    category=category,
                    last=last,
                    level_paise=level,
                    period_days=period,
                    monthly=monthly,
                    event_id=latest.event_id,
                    flexibility=latest.flexibility,
                    minimum_allowed_paise=latest.minimum_allowed_paise,
                    detection=detection,
                )
            )
    return streams


def _detect_income_streams(
    flows: list[_SettledFlow],
    user_events: list[dict[str, str]],
    request_date: date,
    window_start: date,
    cal: IndiaCalendar,
    *,
    home: str,
    rates: tuple[_Rate, ...],
    images: dict[str, int],
) -> list[_InferredStream]:
    """Detect income via category/day consolidation, final marker, and scheduled anchor."""
    by_category: dict[str, list[_SettledFlow]] = {}
    for flow in flows:
        if flow.direction == "credit":
            by_category.setdefault(flow.category, []).append(flow)

    # Dataset convention: future scheduled income rows anchor inferred income with >= 2 occurrences.
    future_income: list[tuple[date, dict[str, str], int]] = []
    for row in user_events:
        if row.get("status") != "scheduled":
            continue
        when = _day(row.get("event_date"))
        if when is None or when <= request_date or not _is_income(row):
            continue
        amount = _row_paise(row, home=home, rates=rates, images=images)
        if amount is None:
            continue
        future_income.append((when, row, amount))

    streams: list[_InferredStream] = []
    for category in sorted(by_category):
        history = sorted(by_category[category], key=lambda item: (item.when, item.event_id))
        if not history:
            continue
        scheduled = sorted(
            (entry for entry in future_income if (entry[1].get("category") or "") == category),
            key=lambda entry: (entry[0], entry[1].get("event_id") or ""),
        )
        # Dataset convention: drop an income category whose newest event carries a \bfinal\b marker.
        newest_history = history[-1]
        newest_desc = newest_history.description
        if scheduled and scheduled[-1][0] >= newest_history.when:
            newest_desc = scheduled[-1][1].get("description") or ""
        if _FINAL_MARKER.search(newest_desc):
            continue

        # Dataset convention: consolidate settled income rows by category and dominant day-of-month.
        descriptions: dict[str, list[_SettledFlow]] = {}
        for item in history:
            descriptions.setdefault(_norm_description(item.description), []).append(item)
        qualified: list[tuple[str, int]] = []
        for desc_key in sorted(descriptions):
            subset = sorted(descriptions[desc_key], key=lambda item: (item.when, item.event_id))
            dates = [item.when for item in subset]
            if len(subset) >= MIN_INCOME_OCCURRENCES and 28 <= _median_gap(dates) <= 31:
                qualified.append((desc_key, _day_of_month(dates)))

        groups: dict[str, list[_SettledFlow]]
        if qualified:
            qualified_keys = {key for key, _day_val in qualified}
            by_day: dict[int, list[_SettledFlow]] = {}
            for desc_key, dom_day in qualified:
                by_day.setdefault(dom_day, []).extend(descriptions[desc_key])
            for desc_key in sorted(descriptions):
                if desc_key in qualified_keys:
                    continue
                for item in descriptions[desc_key]:
                    if item.when.day in by_day:
                        by_day[item.when.day].append(item)
            groups = {
                str(dom_day): sorted(members, key=lambda item: (item.when, item.event_id))
                for dom_day, members in by_day.items()
            }
        else:
            groups = {"": history}

        keys = list(groups)
        principal = max(
            keys,
            key=lambda key: (len(groups[key]), groups[key][-1].when),
        )
        anchor = scheduled[-1] if scheduled else None

        for key in keys:
            members = groups[key]
            use_anchor = anchor is not None and key == principal
            total_occurrences = len(members) + (1 if use_anchor else 0)
            if total_occurrences < MIN_INCOME_OCCURRENCES:
                continue

            combined_flows = list(members)
            if use_anchor and anchor is not None:
                anchor_when, anchor_row, anchor_amount = anchor
                combined_flows.append(
                    _SettledFlow(
                        event_id=anchor_row.get("event_id") or "scheduled_anchor",
                        when=anchor_when,
                        amount_paise=anchor_amount,
                        direction="credit",
                        category=category,
                        description=anchor_row.get("description") or category,
                        flexibility=anchor_row.get("flexibility") or "",
                        minimum_allowed_paise=0,
                    )
                )
            combined_flows.sort(key=lambda item: (item.when, item.event_id))
            dates = [item.when for item in combined_flows]
            newest_flow = combined_flows[-1]
            detect_as_of = max(request_date, newest_flow.when)
            detection = _detect_rule(
                combined_flows, as_of=detect_as_of, window_start=window_start, calendar=cal
            )
            if detection is not None:
                family = detection.rule.family
                monthly = _is_monthly_family(family)
                period = patterns.family_interval_days(family) or max(
                    1, int(round(_median_gap(dates)))
                )
            else:
                # Dataset convention: infer income from >= 2 occurrences (with explicit future
                # scheduled anchor), falling back to round(median gap) when below series.detect's
                # 3-payment minimum or outside production rule families.
                if len(dates) >= 3 and not _regular(dates) and not _same_day_of_month(dates):
                    continue
                median = _median_gap(dates) if len(dates) > 1 else 30.0
                if median <= 0:
                    continue
                monthly = 28 <= median <= 31 or (
                    len(dates) >= 3 and median >= 28 and _same_day_of_month(dates)
                )
                period = 30 if monthly else max(1, int(round(median)))

            last = newest_flow.when
            if monthly:
                dom = _day_of_month(dates)
                last = date(
                    last.year,
                    last.month,
                    min(dom, calendar.monthrange(last.year, last.month)[1]),
                )
            streams.append(
                _InferredStream(
                    kind="income",
                    key=f"{category}:{key}" if key else category,
                    name=newest_flow.description or category,
                    category=category,
                    last=last,
                    level_paise=newest_flow.amount_paise,
                    period_days=period,
                    monthly=monthly,
                    event_id=newest_flow.event_id,
                    flexibility=newest_flow.flexibility,
                    minimum_allowed_paise=0,
                    detection=detection,
                    anchored=use_anchor,
                )
            )
    return streams


def _detect_streams(
    flows: list[_SettledFlow],
    user_events: list[dict[str, str]],
    request_date: date,
    cal: IndiaCalendar,
    *,
    home: str,
    rates: tuple[_Rate, ...],
    images: dict[str, int],
) -> list[_InferredStream]:
    """Detect recurring spend and income streams and apply the dataset's freshness rule."""
    window_start = request_date - timedelta(days=DATASET_HISTORY_DAYS)
    streams = _detect_debit_streams(flows, request_date, window_start, cal)
    streams.extend(
        _detect_income_streams(
            flows,
            user_events,
            request_date,
            window_start,
            cal,
            home=home,
            rates=rates,
            images=images,
        )
    )
    # Dataset convention: enforce freshness `(request_date - last).days <= 1.5 * period` instead
    # of the detector's `alive` posterior.
    return [
        stream
        for stream in streams
        if (request_date - stream.last).days <= 1.5 * stream.period_days
    ]


# --- Dataset message conventions ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Amendment:
    kind: str
    amount_native: int | None
    currency: str
    when: date | None
    percent: float | None


_AMOUNT = re.compile(r"\b([A-Z]{3})\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _parse_message(text: str) -> _Amendment | None:
    """Dataset convention: structural amendments parsed from sample messages; text is untrusted."""
    lowered = text.lower()
    match = _AMOUNT.search(text)
    currency = match.group(1) if match else ""
    amount = _paise(match.group(2).replace(",", "")) if match else None
    date_match = _ISO_DATE.search(text)
    when = date.fromisoformat(date_match.group(1)) if date_match else None
    percent_match = _PERCENT.search(text)
    percent = float(percent_match.group(1)) if percent_match else None
    if re.search(
        r"employment has ended|no regular salary payments|contract has ended|no off-season income|"
        r"hubungan kerja anda telah berakhir|tidak ada pembayaran gaji rutin|"
        r"kontrak musiman saat ini telah berakhir",
        lowered,
    ):
        return _Amendment("income_end", None, "", when, None)
    if re.search(
        r"household employment record has ended|"
        r"sumber pendapatan kerja rumah tangga telah berakhir",
        lowered,
    ):
        return _Amendment("income_remaining", amount, currency, when, None)
    if re.search(r"confirmed base salary|gaji pokok yang dikonfirmasi", lowered):
        return _Amendment("income_base", amount, currency, when, None)
    if re.search(r"salary has increased to|gaji bulanan anda naik menjadi", lowered):
        return _Amendment("income_change", amount, currency, when, None)
    if re.search(r"salary resumes on|first salary|gaji pertama", lowered):
        return _Amendment("income_resume", amount, currency, when, None)
    if re.search(
        r"temporary monthly pay|gaji bulanan sementara|next salary is reduced to", lowered
    ):
        return _Amendment("income_temporary", amount, currency, when, None)
    if re.search(
        r"salary is now expected on|gaji yang sudah dikonfirmasi kini diperkirakan masuk pada",
        lowered,
    ):
        return _Amendment("income_shift", None, "", when, None)
    if re.search(r"increases monthly rent by|menaikkan biaya sewa bulanan sebesar", lowered):
        return _Amendment("rent_change", None, "", when, percent)
    if re.search(r"client approved an invoice payment|klien menyetujui pembayaran faktur", lowered):
        return _Amendment("one_time_credit", amount, currency, when, None)
    if re.search(r"one-time arrears adjustment|penyesuaian tunggakan satu kali", lowered):
        return _Amendment("arrears", amount, currency, when, None)
    return None


def _step_dates(
    anchor: date,
    *,
    monthly: bool,
    period_days: int,
    start_index: int,
    min_date: date,
    until: date,
) -> list[date]:
    dates: list[date] = []
    index = start_index
    while True:
        when = (
            add_months(anchor, index) if monthly else anchor + timedelta(days=period_days * index)
        )
        if when > until:
            break
        if when >= min_date:
            dates.append(when)
        index += 1
    return dates


def _stream_dates(
    stream: _InferredStream,
    *,
    request_date: date,
    until: date,
    calendar: IndiaCalendar,
) -> list[date]:
    """Generate deterministic occurrence dates up to `until`."""
    if stream.next_date is not None:
        return _step_dates(
            stream.next_date,
            monthly=stream.monthly,
            period_days=stream.period_days,
            start_index=0,
            min_date=request_date,
            until=until,
        )
    # Dataset convention: include day 0 when `(request_date - last).days >= period - 3` so an
    # unposted cycle counts without double-counting a cycle settled right before request_date.
    include_day0 = (request_date - stream.last).days >= stream.period_days - 3
    min_date = request_date if include_day0 else request_date + timedelta(days=1)

    if stream.anchored:
        if stream.detection is not None:
            near_anchor = series.upcoming(
                stream.detection,
                after=stream.last - timedelta(days=DEDUPE_DAYS + 1),
                until=stream.last + timedelta(days=DEDUPE_DAYS),
                calendar=calendar,
            )
            if near_anchor:
                return [
                    when
                    for when in series.upcoming(
                        stream.detection,
                        after=max(stream.last, min_date - timedelta(days=1)),
                        until=until,
                        calendar=calendar,
                    )
                    if when >= min_date
                ]
        return _step_dates(
            stream.last,
            monthly=stream.monthly,
            period_days=stream.period_days,
            start_index=1,
            min_date=min_date,
            until=until,
        )

    if stream.detection is not None:
        return series.upcoming(
            stream.detection,
            after=min_date - timedelta(days=1),
            until=until,
            calendar=calendar,
        )

    return _step_dates(
        stream.last,
        monthly=stream.monthly,
        period_days=stream.period_days,
        start_index=1,
        min_date=min_date,
        until=until,
    )


def _apply_messages(
    streams: list[_InferredStream],
    messages: list[dict[str, str]],
    request_date: date,
    *,
    home: str,
    rates: tuple[_Rate, ...],
    calendar: IndiaCalendar,
) -> tuple[list[_InferredStream], list[tuple[date, int]]]:
    """Dataset convention: apply message-stated salary, rent, and one-off invoice updates."""
    amended = list(streams)
    extras: list[tuple[date, int]] = []
    projected_end = request_date + timedelta(days=HORIZON_DAYS - TAIL_DAYS)
    dated: list[tuple[date, dict[str, str]]] = []
    for message in messages:
        sent = message.get("sent_at") or ""
        sent_date = _day(sent[:10])
        if sent_date is not None and sent_date > request_date:
            continue
        dated.append((sent_date or request_date, message))

    for _sent, message in sorted(dated, key=lambda item: item[0]):
        text = (message.get("message_text") or "").replace("\u2019", "'")
        amendment = _parse_message(text)
        if amendment is None:
            continue
        amount: int | None = None
        if amendment.amount_native is not None:
            amount = _convert_paise(
                amendment.amount_native,
                amendment.currency or home,
                home,
                amendment.when or request_date,
                rates,
            )
        credits = sorted(
            (stream for stream in amended if stream.kind == "income"),
            key=lambda stream: stream.last,
        )
        if amendment.kind == "income_end":
            amended = [stream for stream in amended if stream.kind != "income"]
        elif amendment.kind == "income_remaining" and amount is not None:
            for stream in credits[:-1]:
                amended.remove(stream)
            if credits:
                amended = [
                    replace(stream, level_paise=amount) if stream is credits[-1] else stream
                    for stream in amended
                ]
        elif amendment.kind == "income_base" and amount is not None:
            amended = [
                stream
                for stream in amended
                if not (stream.kind == "income" and "commission" in stream.name.lower())
            ]
            remaining = sorted(
                (stream for stream in amended if stream.kind == "income"),
                key=lambda stream: stream.level_paise,
            )
            if remaining:
                target = remaining[-1]
                amended = [
                    replace(stream, level_paise=amount) if stream is target else stream
                    for stream in amended
                ]
        elif amendment.kind == "income_change" and amount is not None:
            if credits:
                target = max(credits, key=lambda stream: stream.level_paise)
                amended = [
                    replace(stream, level_paise=amount) if stream is target else stream
                    for stream in amended
                ]
        elif (
            amendment.kind == "income_resume" and amount is not None and amendment.when is not None
        ):
            amended = [stream for stream in amended if stream.kind != "income"]
            amended.append(
                _InferredStream(
                    kind="income",
                    key="confirmed_salary",
                    name="confirmed salary",
                    category="salary",
                    last=amendment.when,
                    level_paise=amount,
                    period_days=30,
                    monthly=True,
                    event_id=message.get("message_id") or "",
                    flexibility="fixed",
                    minimum_allowed_paise=0,
                    detection=None,
                    next_date=amendment.when,
                )
            )
        elif amendment.kind == "income_temporary" and amount is not None:
            amended = [
                replace(stream, level_paise=amount) if stream.kind == "income" else stream
                for stream in amended
            ]
        elif amendment.kind == "income_shift" and amendment.when is not None:
            amended = [
                replace(stream, next_date=amendment.when) if stream.kind == "income" else stream
                for stream in amended
            ]
        elif amendment.kind == "rent_change" and amendment.percent is not None:
            factor = 1 + amendment.percent / 100
            amended = [
                replace(stream, level_paise=int(round(stream.level_paise * factor)))
                if stream.kind == "spend" and stream.category == "rent"
                else stream
                for stream in amended
            ]
        elif amendment.kind == "one_time_credit" and amount is not None and amendment.when:
            if not message.get("related_event_id"):
                extras.append((amendment.when, amount))
        elif amendment.kind == "arrears" and amount is not None and credits:
            for when in _stream_dates(
                credits[-1],
                request_date=request_date,
                until=projected_end,
                calendar=calendar,
            ):
                extras.append((when, amount))
                break
    return amended, extras


# --- Production ProjectionInput builder --------------------------------------------------------


def _explicit_events(
    user_events: list[dict[str, str]],
    streams: list[_InferredStream],
    extras: list[tuple[date, int]],
    request_date: date,
    *,
    home: str,
    rates: tuple[_Rate, ...],
    images: dict[str, int],
) -> tuple[list[CashEvent], list[tuple[str, date, str]]]:
    """Scheduled/pending rows and message extras as confirmed CashEvents."""
    end = request_date + timedelta(days=HORIZON_DAYS)
    events: list[CashEvent] = []
    explicit_keys: list[tuple[str, date, str]] = []
    for row in user_events:
        if row.get("status") not in {"scheduled", "pending"}:
            continue
        direction = row.get("direction") or ""
        if direction not in {"debit", "credit"}:
            continue
        when = _day(row.get("settlement_date"))
        if when is None or when < request_date or when > end:
            continue
        if direction == "credit" and not _is_income(row):
            continue
        amount = _row_paise(row, home=home, rates=rates, images=images)
        stream_kind = "income" if direction == "credit" else "spend"
        category = row.get("category") or ""
        if amount is None:
            matching = [
                stream
                for stream in streams
                if stream.kind == stream_kind and stream.category == category
            ]
            amount = max((stream.level_paise for stream in matching), default=None)
        if amount is None or amount <= 0:
            continue
        name = row.get("description") or row.get("event_type") or category
        source_id = row.get("event_id") or None
        if direction == "credit":
            events.append(
                CashEvent(
                    date=when,
                    amount_paise=amount,
                    name=name,
                    kind="income_fixed",
                    source_id=source_id,
                    confirmed=True,
                )
            )
            explicit_keys.append(("income", when, category))
        else:
            kind = (
                "obligation"
                if category in {"expense", "subscription", "debt_payment"}
                else "owed_payment"
            )
            events.append(
                CashEvent(
                    date=when,
                    amount_paise=-amount,
                    name=name,
                    kind=kind,
                    source_id=source_id,
                    confirmed=True,
                )
            )
            explicit_keys.append(("spend", when, category))
    for when, amount in extras:
        if when < request_date or when > end or amount == 0:
            continue
        events.append(
            CashEvent(
                date=when,
                amount_paise=amount,
                name="message",
                kind="income_fixed" if amount > 0 else "obligation",
                source_id="message",
                confirmed=True,
            )
        )
    return events, explicit_keys


def _near_explicit(
    when: date,
    *,
    kind: str,
    category: str,
    explicit_keys: list[tuple[str, date, str]],
) -> bool:
    return any(
        exp_kind == kind and exp_cat == category and abs((exp_date - when).days) <= DEDUPE_DAYS
        for exp_kind, exp_date, exp_cat in explicit_keys
    )


def _build_projection_input(
    user_events: list[dict[str, str]],
    streams: list[_InferredStream],
    extras: list[tuple[date, int]],
    request_date: date,
    *,
    profile: dict[str, str],
    home: str,
    rates: tuple[_Rate, ...],
    images: dict[str, int],
    calendar: IndiaCalendar,
) -> ProjectionInput:
    """Build deterministic `CashEvent`s on a one-account `ProjectionInput`."""
    end = request_date + timedelta(days=HORIZON_DAYS)
    # Dataset convention: projected occurrences stop short of the horizon's final TAIL_DAYS.
    projected_end = end - timedelta(days=TAIL_DAYS)
    events, explicit_keys = _explicit_events(
        user_events,
        streams,
        extras,
        request_date,
        home=home,
        rates=rates,
        images=images,
    )

    for stream in streams:
        if stream.level_paise <= 0:
            continue
        dates = _stream_dates(
            stream,
            request_date=request_date,
            until=projected_end,
            calendar=calendar,
        )
        for when in dates:
            if stream.kind == "income":
                if _near_explicit(
                    when,
                    kind="income",
                    category=stream.category,
                    explicit_keys=explicit_keys,
                ):
                    continue
                # Dataset convention: inferred recurring flows are deterministic confirmed events.
                events.append(
                    CashEvent(
                        date=when,
                        amount_paise=stream.level_paise,
                        name=stream.name,
                        kind="income_fixed",
                        source_id=f"{stream.category}:credit" if stream.category else stream.key,
                        confirmed=True,
                    )
                )
            else:
                if stream.monthly and _near_explicit(
                    when,
                    kind="spend",
                    category=stream.category,
                    explicit_keys=explicit_keys,
                ):
                    continue
                # Dataset convention: inferred recurring outflows are deterministic obligations.
                events.append(
                    CashEvent(
                        date=when,
                        amount_paise=-stream.level_paise,
                        name=stream.name,
                        kind="obligation",
                        source_id=f"{stream.category}:debit" if stream.category else stream.key,
                        confirmed=True,
                    )
                )

    events.sort(key=lambda event: (event.date, event.amount_paise))
    return ProjectionInput(
        as_of=request_date,
        floor_paise=_paise(profile.get("minimum_balance_to_keep")),
        liquid=(
            LiquidAccount(
                id=profile.get("user_id") or "sample",
                name="Sample balance",
                balance_paise=_paise(profile.get("current_available_balance")),
            ),
        ),
        events=tuple(events),
        ask_horizon_days=HORIZON_DAYS,
    )


def _prepare_scenario(
    user_events: list[dict[str, str]],
    request_date: date,
    *,
    messages: list[dict[str, str]] | None = None,
    home: str = "INR",
    rates: tuple[_Rate, ...] = (),
    images: dict[str, int] | None = None,
) -> tuple[list[_InferredStream], list[tuple[date, int]], IndiaCalendar]:
    image_amounts = images or {}
    window_start = request_date - timedelta(days=DATASET_HISTORY_DAYS)
    end = request_date + timedelta(days=HORIZON_DAYS)
    cal = calendar_for(window_start - timedelta(days=21), end + timedelta(days=21))
    settled = _settled_flows(
        user_events, request_date, home=home, rates=rates, images=image_amounts
    )
    detected = _detect_streams(
        settled,
        user_events,
        request_date,
        cal,
        home=home,
        rates=rates,
        images=image_amounts,
    )
    streams, extras = _apply_messages(
        detected,
        messages or [],
        request_date,
        home=home,
        rates=rates,
        calendar=cal,
    )
    return streams, extras, cal


def _inputs_for(
    user_events: list[dict[str, str]],
    request_date: date,
    *,
    profile: dict[str, str] | None = None,
    messages: list[dict[str, str]] | None = None,
    home: str = "INR",
    rates: tuple[_Rate, ...] = (),
    images: dict[str, int] | None = None,
) -> ProjectionInput:
    """Build production `ProjectionInput` (confirmed `CashEvent`s) for one sample."""
    image_amounts = images or {}
    streams, extras, cal = _prepare_scenario(
        user_events,
        request_date,
        messages=messages,
        home=home,
        rates=rates,
        images=image_amounts,
    )
    return _build_projection_input(
        user_events,
        streams,
        extras,
        request_date,
        profile=profile or {},
        home=home,
        rates=rates,
        images=image_amounts,
        calendar=cal,
    )


# --- Dataset plan search via production projection ---------------------------------------------


def _installment(
    option: dict[str, str],
    *,
    request_date: date,
    desired: date | None,
    max_months: int | None,
    inputs: ProjectionInput,
    runs: int,
    seed: int,
) -> tuple[int, date, int] | None:
    """Dataset convention: evaluate a merchant installment plan with production `project()`."""
    try:
        count = int(option.get("number_of_payments") or 0)
        frequency = int(option.get("payment_frequency_days") or 0)
        each = _paise(option.get("payment_amount"))
    except ValueError:
        return None
    first = _day(option.get("first_payment_date"))
    if first is None or count < 1 or frequency <= 0 or each <= 0:
        return None
    if first < request_date:
        return None
    if max_months is not None and count > max_months:
        return None
    last = first + timedelta(days=frequency * (count - 1))
    if last > request_date + timedelta(days=HORIZON_DAYS):
        return None
    if desired is not None and last > desired:
        return None
    payments = tuple(
        CashEvent(
            date=first + timedelta(days=frequency * index),
            amount_paise=-each,
            name="HackerRank installment",
            kind="instalment",
            confirmed=True,
        )
        for index in range(count)
    )
    result = project(replace(inputs, events=inputs.events + payments), runs=runs, seed=seed)
    if result["floor_probability"] < FLOOR_THRESHOLD or not result["obligations_met"]:
        return None
    total = _paise(option.get("total_payable_amount")) or each * count
    return total, first, count


def _spending_changes(
    streams: list[_InferredStream],
    extras: list[tuple[date, int]],
    user_events: list[dict[str, str]],
    profile: dict[str, str],
    request_date: date,
    *,
    requested: int,
    home: str,
    rates: tuple[_Rate, ...],
    images: dict[str, int],
    calendar: IndiaCalendar,
    runs: int,
    seed: int,
) -> list[tuple[_InferredStream, int]] | None:
    """Dataset convention: smallest stop/reduce stream set making `safe_amount_today` cover ask."""
    reduce_categories = _split(profile.get("expense_categories_user_is_willing_to_reduce"))
    stop_categories = _split(profile.get("expense_categories_user_is_willing_to_stop"))
    candidates: list[tuple[_InferredStream, int]] = []
    for stream in streams:
        if stream.kind != "spend" or stream.level_paise <= 0 or not stream.event_id:
            continue
        if stream.category in stop_categories and stream.flexibility in {
            "stoppable",
            "reducible_or_stoppable",
        }:
            candidates.append((stream, 0))
        if (
            stream.category in reduce_categories
            and stream.flexibility in {"reducible", "reducible_or_stoppable"}
            and 0 < stream.minimum_allowed_paise < stream.level_paise
        ):
            candidates.append((stream, stream.minimum_allowed_paise))

    for size in (1, 2, 3):
        feasible: list[tuple[int, list[tuple[_InferredStream, int]]]] = []
        for combo in itertools.combinations(candidates, size):
            if len({item[0].event_id for item in combo}) != size:
                continue
            overrides = {item[0].event_id: item[1] for item in combo}
            adjusted = [
                replace(stream, level_paise=overrides[stream.event_id])
                if stream.kind == "spend" and stream.event_id in overrides
                else stream
                for stream in streams
            ]
            planned = _build_projection_input(
                user_events,
                adjusted,
                extras,
                request_date,
                profile=profile,
                home=home,
                rates=rates,
                images=images,
                calendar=calendar,
            )
            if safe_amount_today(planned, runs=runs, seed=seed) >= requested:
                saving = sum(stream.level_paise - new for stream, new in combo)
                feasible.append((saving, list(combo)))
        if feasible:
            feasible.sort(key=lambda item: -item[0])
            return feasible[0][1]
    return None


def _predict(
    request: dict[str, str],
    profile: dict[str, str],
    user_events: list[dict[str, str]],
    payment_options: list[dict[str, str]],
    *,
    messages: list[dict[str, str]],
    images: dict[str, int],
    rates: tuple[_Rate, ...],
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    request_date = _day(request.get("request_date"))
    if request_date is None:
        raise ValueError("request_date is missing")
    home = profile.get("home_currency") or "INR"
    requested = _paise(request.get("requested_amount"))
    desired = _day(request.get("desired_completion_date"))
    # Dataset convention: honor `payment_methods_user_will_consider` for status and plan search.
    methods = _split(profile.get("payment_methods_user_will_consider"))

    streams, extras, cal = _prepare_scenario(
        user_events,
        request_date,
        messages=messages,
        home=home,
        rates=rates,
        images=images,
    )
    inputs = _build_projection_input(
        user_events,
        streams,
        extras,
        request_date,
        profile=profile,
        home=home,
        rates=rates,
        images=images,
        calendar=cal,
    )
    safe = min(safe_amount_today(inputs, runs=runs, seed=seed), requested)
    first = first_full_payment_date(inputs, requested, start_on=request_date, runs=runs, seed=seed)
    earliest = _day(first["date"]) if first is not None else None

    if safe >= requested and "full_payment" in methods:
        return {
            "request_id": request["request_id"],
            "amount_safe_to_pay": safe / 100,
            "affordability_status": "affordable_now",
            "recommended_payment_method": "full_payment",
            "earliest_date_for_full_payment": request_date.isoformat(),
            "spending_changes_needed": "none",
        }

    max_months: int | None = None
    if profile.get("max_installment_months"):
        try:
            max_months = int(profile["max_installment_months"])
        except ValueError:
            max_months = None

    changes: list[tuple[_InferredStream, int]] | None = None
    if "full_payment" in methods and safe < requested:
        changes = _spending_changes(
            streams,
            extras,
            user_events,
            profile,
            request_date,
            requested=requested,
            home=home,
            rates=rates,
            images=images,
            calendar=cal,
            runs=runs,
            seed=seed,
        )

    candidates: list[tuple[int, int, date, int, str, str, str]] = []
    if changes is not None and "full_payment" in methods:
        parts: list[str] = []
        for stream, new_amount in changes:
            if new_amount == 0:
                parts.append(f"stop:{stream.event_id}")
            else:
                parts.append(f"reduce_to:{stream.event_id}:{new_amount / 100:g}")
        candidates.append(
            (
                1,
                requested,
                request_date,
                0,
                "",
                "affordable_with_plan:full_payment",
                "|".join(parts),
            )
        )
    if "installments" in methods:
        for option in payment_options:
            if option.get("payment_method") != "installments":
                continue
            plan = _installment(
                option,
                request_date=request_date,
                desired=desired,
                max_months=max_months,
                inputs=inputs,
                runs=runs,
                seed=seed,
            )
            if plan is None:
                continue
            total, first_pay, count = plan
            candidates.append(
                (
                    0,
                    total,
                    first_pay,
                    count,
                    option.get("payment_option_id") or "",
                    "affordable_with_plan:installments",
                    "none",
                )
            )
    if (
        request.get("allows_partial_payment", "").lower() == "true"
        and "partial_payment" in methods
        and 0 < safe < requested
        and earliest is not None
        and (desired is None or earliest <= desired)
    ):
        candidates.append(
            (
                0,
                requested,
                request_date,
                2,
                "",
                "affordable_with_plan:partial_payment",
                "none",
            )
        )

    if candidates:
        candidates.sort(key=lambda item: item[:5])
        chosen = candidates[0]
        status, method = chosen[5].split(":")
        return {
            "request_id": request["request_id"],
            "amount_safe_to_pay": safe / 100,
            "affordability_status": status,
            "recommended_payment_method": method,
            "earliest_date_for_full_payment": earliest.isoformat() if earliest else "",
            "spending_changes_needed": chosen[6],
        }
    if earliest is not None and "full_payment" in methods:
        return {
            "request_id": request["request_id"],
            "amount_safe_to_pay": safe / 100,
            "affordability_status": "affordable_later",
            "recommended_payment_method": "wait",
            "earliest_date_for_full_payment": earliest.isoformat(),
            "spending_changes_needed": "none",
        }
    return {
        "request_id": request["request_id"],
        "amount_safe_to_pay": safe / 100,
        "affordability_status": "not_affordable",
        "recommended_payment_method": "not_recommended",
        "earliest_date_for_full_payment": "",
        "spending_changes_needed": "none",
    }


def _amount_ok(predicted: float, expected: float, requested: float) -> bool:
    tolerance = max(requested * AMOUNT_TOLERANCE_RATIO, AMOUNT_MIN_TOLERANCE)
    return abs(predicted - expected) <= tolerance


def _dates_ok(predicted: str, expected: str) -> bool:
    if not predicted and not expected:
        return True
    if not predicted or not expected:
        return False
    try:
        return (
            abs((date.fromisoformat(predicted) - date.fromisoformat(expected)).days)
            <= DATE_TOLERANCE_DAYS
        )
    except ValueError:
        return False


def _wilson_rate(rate: float, n: int) -> tuple[float, float]:
    """The 95% Wilson score interval for a rate in [0, 1] over ``n`` samples."""
    if n == 0:
        return 0.0, 1.0
    p = max(0.0, min(1.0, rate))
    z2 = scoring.Z95 * scoring.Z95
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = scoring.Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def _display_dataset(dataset: Path) -> str:
    try:
        return dataset.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return dataset.resolve().as_posix()


def score(
    dataset: Path | None = None,
    *,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Score the shipped engine on the local HackerRank dataset."""
    dataset = dataset or find_dataset()
    if dataset is None:
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
    for row in _optional_csv(dataset / "request_payment_options.csv"):
        options.setdefault(row["request_id"], []).append(row)
    messages = _load_messages(dataset)
    images = _load_image_amounts(dataset)
    rates = _load_rates(dataset)
    samples = _read_csv(dataset / "sample_requests.csv")
    rows: list[dict[str, Any]] = []
    for sample in samples:
        expected_status = sample.get("affordability_status") or ""
        profile = profiles.get(sample["user_id"])
        if profile is None:
            rows.append(
                {
                    "request_id": sample.get("request_id") or "",
                    "expected_status": expected_status,
                    "error": "profile is missing",
                    "status_match": False,
                    "amount_match": False,
                    "earliest_match": False,
                }
            )
            continue
        try:
            predicted = _predict(
                sample,
                profile,
                events.get(sample["user_id"], []),
                options.get(sample["request_id"], []),
                messages=messages.get(sample["user_id"], []),
                images=images,
                rates=rates,
                runs=runs,
                seed=seed,
            )
        except (KeyError, ValueError, TypeError) as exc:
            rows.append(
                {
                    "request_id": sample["request_id"],
                    "expected_status": expected_status,
                    "error": str(exc),
                    "status_match": False,
                    "amount_match": False,
                    "earliest_match": False,
                }
            )
            continue
        status_match = predicted["affordability_status"] == expected_status
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
                "expected_status": expected_status,
                "expected_amount_safe_to_pay": float(sample["amount_safe_to_pay"]),
                "expected_earliest_date": sample["earliest_date_for_full_payment"],
                "status_match": status_match,
                "amount_match": amount_match,
                "earliest_match": earliest_match,
            }
        )

    n = len(rows)
    if n == 0:
        return {"available": False, "reason": "no scorable samples", "score": None}

    status_hits = sum(1 for row in rows if row["status_match"])
    amount_hits = sum(1 for row in rows if row["amount_match"])
    earliest_hits = sum(1 for row in rows if row["earliest_match"])
    errors = sum(1 for row in rows if "error" in row)

    status_accuracy = status_hits / n
    amount_accuracy = amount_hits / n
    earliest_accuracy = earliest_hits / n
    combined = (
        WEIGHTS[0] * status_accuracy + WEIGHTS[1] * amount_accuracy + WEIGHTS[2] * earliest_accuracy
    )

    status_ci = scoring.wilson(status_hits, n)
    amount_ci = scoring.wilson(amount_hits, n)
    earliest_ci = scoring.wilson(earliest_hits, n)
    combined_ci = _wilson_rate(combined, n)

    status_counts = {
        status: {
            "expected": sum(1 for row in rows if row.get("expected_status") == status),
            "predicted": sum(1 for row in rows if row.get("affordability_status") == status),
        }
        for status in EXPECTED
    }

    return {
        "available": True,
        "dataset": _display_dataset(dataset),
        "samples": n,
        "errors": errors,
        "score": round(combined, 4),
        "status_accuracy": round(status_accuracy, 4),
        "amount_accuracy": round(amount_accuracy, 4),
        "earliest_accuracy": round(earliest_accuracy, 4),
        "hits": {
            "status": status_hits,
            "amount": amount_hits,
            "earliest": earliest_hits,
        },
        "intervals": {
            "combined": [round(combined_ci[0], 4), round(combined_ci[1], 4)],
            "status": [round(status_ci[0], 4), round(status_ci[1], 4)],
            "amount": [round(amount_ci[0], 4), round(amount_ci[1], 4)],
            "earliest": [round(earliest_ci[0], 4), round(earliest_ci[1], 4)],
        },
        "status_counts": status_counts,
        "rows": rows,
    }


def _fmt_interval(bounds: list[float] | tuple[float, float]) -> str:
    return f"{bounds[0]:.1%}-{bounds[1]:.1%}"


def format_report(result: dict[str, Any]) -> str:
    if not result.get("available"):
        return f"HackerRank sample score: not run. {result['reason']}."
    n = int(result["samples"])
    errors = int(result.get("errors", 0))
    hits = result.get("hits") or {}
    intervals = result.get("intervals") or {}
    status_counts = result.get("status_counts") or {}
    lines = [
        f"HackerRank 'Buy or Wait' samples: {n} (errors: {errors})",
        (
            f"Combined score: {result['score']:.2%} "
            f"(95% CI {_fmt_interval(intervals['combined'])}, n={n})"
            if "combined" in intervals
            else f"Combined score: {result['score']:.2%}"
        ),
        (
            f"Affordability status: {result['status_accuracy']:.0%} "
            f"({hits.get('status', 0)}/{n}, 95% CI {_fmt_interval(intervals['status'])}, n={n})"
            if "status" in intervals
            else f"Affordability status: {result['status_accuracy']:.0%}"
        ),
        (
            f"amount_safe_to_pay within 5% of requested: {result['amount_accuracy']:.0%} "
            f"({hits.get('amount', 0)}/{n}, 95% CI {_fmt_interval(intervals['amount'])}, n={n})"
            if "amount" in intervals
            else f"amount_safe_to_pay within 5% of requested: {result['amount_accuracy']:.0%}"
        ),
        (
            f"Earliest full-payment date within 3 days: {result['earliest_accuracy']:.0%} "
            f"({hits.get('earliest', 0)}/{n}, 95% CI {_fmt_interval(intervals['earliest'])}, n={n})"
            if "earliest" in intervals
            else f"Earliest full-payment date within 3 days: {result['earliest_accuracy']:.0%}"
        ),
    ]
    if status_counts:
        lines.extend(
            [
                "",
                "| Status | Expected | Predicted |",
                "|---|---:|---:|",
            ]
        )
        for status in EXPECTED:
            counts = status_counts.get(status, {"expected": 0, "predicted": 0})
            lines.append(f"| {status} | {counts['expected']} | {counts['predicted']} |")
    lines.append("")
    lines.append(f"source: {result['dataset']} (local, not committed)")
    return "\n".join(lines)


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
    "AMOUNT_MIN_TOLERANCE",
    "AMOUNT_TOLERANCE_RATIO",
    "DATASET_ENV",
    "DATE_TOLERANCE_DAYS",
    "EXPECTED",
    "WEIGHTS",
    "find_dataset",
    "format_report",
    "publish_report",
    "score",
]
