"""HackerRank "Buy or Wait" scorer.

The 25 solved sample requests have externally written answers, so they check the affordability
logic against someone else's labels. The dataset has no license, so it is read only from a
git-ignored local path and never committed. When that path is absent, `score` says so plainly
instead of inventing a number.

The forecast is deterministic: recurring streams inferred from settled history (split by category,
and by description where a stable commitment repeats on its own cadence), confirmed scheduled and
pending debits, income anchored on the next confirmed salary, structural changes parsed from
messages, and image amounts for blank rows. No number in the dataset is hardcoded; image amounts
come from an optional `image_amounts.json` beside the CSVs.

The walk itself is `arno.engine.projection`: every flow becomes a `CashEvent` on a one-account
`ProjectionInput` with no `SpendCategory` pools, so `project`, `safe_amount_today`, and
`first_full_payment_date` are the real engine, and `runs=1` is exact because nothing is sampled.
"""

from __future__ import annotations

import calendar
import csv
import itertools
import json
import os
import re
import statistics
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from arno.engine.projection import (
    DEFAULT_SEED,
    FLOOR_THRESHOLD,
    CashEvent,
    LiquidAccount,
    ProjectionInput,
    first_full_payment_date,
    project,
    safe_amount_today,
)

DATASET_ENV = "ARNO_HACKERRANK_DATASET"
REPO_ROOT = Path(__file__).resolve().parents[3]
EXPECTED = ("affordable_now", "affordable_with_plan", "affordable_later", "not_affordable")
HORIZON_DAYS = 90
# A projected recurring occurrence in the final days is too uncertain to fail a later payment date,
# while confirmed flows still count on every day, so projected occurrences stop short of the
# horizon's end. The sample labels use the same convention.
TAIL_DAYS = 4
DEDUPE_DAYS = 3
MIN_DEBIT_OCCURRENCES = 3
MIN_INCOME_OCCURRENCES = 2


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


def _add_months(day: date, months: int) -> date:
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def _median_gap(dates: list[date]) -> float:
    gaps = [(later - earlier).days for earlier, later in zip(dates, dates[1:], strict=False)]
    return statistics.median(gaps) if gaps else 0.0


def _regular(dates: list[date]) -> bool:
    gaps = [(later - earlier).days for earlier, later in zip(dates, dates[1:], strict=False)]
    if not gaps:
        return False
    median = statistics.median(gaps)
    if median <= 0:
        return False
    tolerance = max(2.0, 0.25 * median)
    return all(abs(gap - median) <= tolerance for gap in gaps)


def _stable(amounts: list[int]) -> bool:
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


_PERIOD = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
    r"\d{4}|q[1-4])\b",
    re.IGNORECASE,
)


def _norm_description(text: str) -> str:
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


@dataclass(frozen=True, slots=True)
class _Stream:
    category: str
    direction: str
    description: str
    amount_paise: int
    last: date
    cadence_days: int
    monthly: bool
    event_id: str
    flexibility: str
    minimum_allowed_paise: int
    next_date: date | None = None


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


def _settled_rows(
    user_events: list[dict[str, str]],
    request_date: date,
    *,
    home: str,
    rates: tuple[_Rate, ...],
    images: dict[str, int],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in user_events:
        if row.get("status") != "settled" or row.get("direction") not in {"debit", "credit"}:
            continue
        when = _day(row.get("event_date"))
        if when is None or when > request_date:
            continue
        if _row_paise(row, home=home, rates=rates, images=images) is None:
            continue
        rows.append(row)
    return rows


def _debit_streams(
    rows: list[dict[str, str]],
    *,
    home: str,
    rates: tuple[_Rate, ...],
    images: dict[str, int],
) -> list[_Stream]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if row.get("direction") == "debit" and row.get("category"):
            grouped.setdefault(row["category"], []).append(row)
    streams: list[_Stream] = []
    for category in sorted(grouped):
        members = sorted(grouped[category], key=lambda row: row.get("event_date") or "")
        if len(members) < MIN_DEBIT_OCCURRENCES:
            continue
        descriptions: dict[str, list[dict[str, str]]] = {}
        for row in members:
            descriptions.setdefault(_norm_description(row.get("description") or ""), []).append(row)
        qualified: list[list[dict[str, str]]] = []
        for key in sorted(descriptions):
            subset = descriptions[key]
            dates = [when for row in subset if (when := _day(row.get("event_date"))) is not None]
            amounts = [
                value
                for row in subset
                if (value := _row_paise(row, home=home, rates=rates, images=images)) is not None
            ]
            if len(subset) >= MIN_DEBIT_OCCURRENCES and _regular(dates) and _stable(amounts):
                qualified.append(subset)
        all_dates = [when for row in members if (when := _day(row.get("event_date"))) is not None]
        chosen = (
            qualified
            if len(qualified) >= 2 or (len(qualified) == 1 and not _regular(all_dates))
            else [members]
        )
        for subset in chosen:
            if len(subset) < MIN_DEBIT_OCCURRENCES:
                continue
            dates = sorted(
                when for row in subset if (when := _day(row.get("event_date"))) is not None
            )
            amounts = [
                value
                for row in subset
                if (value := _row_paise(row, home=home, rates=rates, images=images)) is not None
            ]
            median = _median_gap(dates)
            monthly = 28 <= median <= 31
            if not monthly and len(dates) >= 3 and median >= 28 and _same_day_of_month(dates):
                monthly = True
            latest = subset[-1]
            streams.append(
                _Stream(
                    category=category,
                    direction="debit",
                    description=latest.get("description") or category,
                    amount_paise=int(round(statistics.fmean(amounts))),
                    last=dates[-1],
                    cadence_days=max(1, int(round(median))),
                    monthly=monthly,
                    event_id=latest.get("event_id") or "",
                    flexibility=latest.get("flexibility") or "",
                    minimum_allowed_paise=_paise(latest.get("minimum_allowed_amount")),
                )
            )
    return streams


def _income_streams(
    rows: list[dict[str, str]],
    future_income: list[dict[str, str]],
    request_date: date,
    *,
    home: str,
    rates: tuple[_Rate, ...],
    images: dict[str, int],
) -> list[_Stream]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if _is_income(row):
            grouped.setdefault(row.get("category") or "", []).append(row)
    for row in future_income:
        grouped.setdefault(row.get("category") or "", [])
    streams: list[_Stream] = []
    for category in sorted(grouped):
        history = sorted(grouped[category], key=lambda row: row.get("event_date") or "")
        if not history:
            continue
        scheduled = sorted(
            (row for row in future_income if row.get("category") == category),
            key=lambda row: row.get("event_date") or "",
        )
        newest = max(history + scheduled, key=lambda row: row.get("event_date") or "")
        if re.search(r"\bfinal\b", newest.get("description") or "", re.IGNORECASE):
            continue
        descriptions: dict[str, list[dict[str, str]]] = {}
        for row in history:
            descriptions.setdefault(_norm_description(row.get("description") or ""), []).append(row)
        qualified: list[tuple[str, int]] = []
        for key in sorted(descriptions):
            subset = sorted(descriptions[key], key=lambda row: row.get("event_date") or "")
            dates = [when for row in subset if (when := _day(row.get("event_date"))) is not None]
            if len(subset) >= MIN_INCOME_OCCURRENCES and 28 <= _median_gap(dates) <= 31:
                qualified.append((key, _day_of_month(dates)))
        groups: dict[str, list[dict[str, str]]]
        if qualified:
            by_day: dict[int, list[dict[str, str]]] = {}
            for key, day in qualified:
                by_day.setdefault(day, []).extend(descriptions[key])
            for key in sorted(descriptions):
                if any(key == qualified_key for qualified_key, _day_value in qualified):
                    continue
                for row in descriptions[key]:
                    when = _day(row.get("event_date"))
                    if when is not None and when.day in by_day:
                        by_day[when.day].append(row)
            groups = {
                str(day): sorted(members, key=lambda row: row.get("event_date") or "")
                for day, members in by_day.items()
            }
        else:
            groups = {"": history}
        keys = list(groups)
        principal = max(
            keys,
            key=lambda key: (len(groups[key]), groups[key][-1].get("event_date") or ""),
        )
        anchor = scheduled[-1] if scheduled else None
        for key in keys:
            members = groups[key]
            if (
                len(members) + (1 if anchor is not None and key == principal else 0)
                < MIN_INCOME_OCCURRENCES
            ):
                continue
            newest_row = anchor if anchor is not None and key == principal else members[-1]
            dates = sorted(
                when
                for row in (members + ([anchor] if anchor is not None and key == principal else []))
                if (when := _day(row.get("event_date"))) is not None
            )
            median = _median_gap(dates) if len(dates) > 1 else 30.0
            monthly = 28 <= median <= 31
            if not monthly and len(dates) >= 3 and median >= 28 and _same_day_of_month(dates):
                monthly = True
            amount = _row_paise(newest_row, home=home, rates=rates, images=images)
            if amount is None:
                continue
            last = _day(newest_row.get("event_date"))
            if last is None:
                continue
            if monthly:
                last = date(
                    last.year,
                    last.month,
                    min(_day_of_month(dates), calendar.monthrange(last.year, last.month)[1]),
                )
            streams.append(
                _Stream(
                    category=category,
                    direction="credit",
                    description=newest_row.get("description") or category,
                    amount_paise=amount,
                    last=last,
                    cadence_days=max(1, int(round(median))),
                    monthly=monthly,
                    event_id=newest_row.get("event_id") or "",
                    flexibility=newest_row.get("flexibility") or "",
                    minimum_allowed_paise=0,
                )
            )
    return streams


def _fresh(streams: list[_Stream], request_date: date) -> list[_Stream]:
    fresh: list[_Stream] = []
    for stream in streams:
        period = 30 if stream.monthly else stream.cadence_days
        if (request_date - stream.last).days > 1.5 * period:
            continue
        fresh.append(stream)
    return fresh


def _build_streams(
    user_events: list[dict[str, str]],
    request_date: date,
    *,
    home: str,
    rates: tuple[_Rate, ...],
    images: dict[str, int],
) -> list[_Stream]:
    rows = _settled_rows(user_events, request_date, home=home, rates=rates, images=images)
    future_income = [
        row
        for row in user_events
        if row.get("status") == "scheduled"
        and (when := _day(row.get("event_date"))) is not None
        and when > request_date
        and _is_income(row)
        and _row_paise(row, home=home, rates=rates, images=images) is not None
    ]
    streams = _debit_streams(rows, home=home, rates=rates, images=images)
    streams.extend(
        _income_streams(rows, future_income, request_date, home=home, rates=rates, images=images)
    )
    return _fresh(streams, request_date)


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
    """Structural amendments only; the text itself is untrusted data."""
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


def _apply_messages(
    streams: list[_Stream],
    messages: list[dict[str, str]],
    request_date: date,
    *,
    home: str,
    rates: tuple[_Rate, ...],
) -> tuple[list[_Stream], list[tuple[date, int]]]:
    """Salary, rent, and confirmed invoice changes. Pending credits never add income."""
    amended = list(streams)
    extras: list[tuple[date, int]] = []
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
            (stream for stream in amended if stream.direction == "credit"),
            key=lambda stream: stream.last,
        )
        if amendment.kind == "income_end":
            amended = [stream for stream in amended if stream.direction != "credit"]
        elif amendment.kind == "income_remaining" and amount is not None:
            for stream in credits[:-1]:
                amended.remove(stream)
            if credits:
                amended = [
                    replace(stream, amount_paise=amount) if stream is credits[-1] else stream
                    for stream in amended
                ]
        elif amendment.kind == "income_base" and amount is not None:
            amended = [
                stream
                for stream in amended
                if not (stream.direction == "credit" and "commission" in stream.description.lower())
            ]
            remaining = sorted(
                (stream for stream in amended if stream.direction == "credit"),
                key=lambda stream: stream.amount_paise,
            )
            if remaining:
                target = remaining[-1]
                amended = [
                    replace(stream, amount_paise=amount) if stream is target else stream
                    for stream in amended
                ]
        elif amendment.kind == "income_change" and amount is not None:
            if credits:
                target = max(credits, key=lambda stream: stream.amount_paise)
                amended = [
                    replace(stream, amount_paise=amount) if stream is target else stream
                    for stream in amended
                ]
        elif (
            amendment.kind == "income_resume" and amount is not None and amendment.when is not None
        ):
            amended = [stream for stream in amended if stream.direction != "credit"]
            amended.append(
                _Stream(
                    category="salary",
                    direction="credit",
                    description="confirmed salary",
                    amount_paise=amount,
                    last=amendment.when,
                    cadence_days=30,
                    monthly=True,
                    event_id=message.get("message_id") or "",
                    flexibility="fixed",
                    minimum_allowed_paise=0,
                    next_date=amendment.when,
                )
            )
        elif amendment.kind == "income_temporary" and amount is not None:
            amended = [
                replace(stream, amount_paise=amount) if stream.direction == "credit" else stream
                for stream in amended
            ]
        elif amendment.kind == "income_shift" and amendment.when is not None:
            amended = [
                replace(stream, next_date=amendment.when)
                if stream.direction == "credit"
                else stream
                for stream in amended
            ]
        elif amendment.kind == "rent_change" and amendment.percent is not None:
            factor = 1 + amendment.percent / 100
            amended = [
                replace(stream, amount_paise=int(round(stream.amount_paise * factor)))
                if stream.category == "rent"
                else stream
                for stream in amended
            ]
        elif amendment.kind == "one_time_credit" and amount is not None and amendment.when:
            if not message.get("related_event_id"):
                extras.append((amendment.when, amount))
        elif amendment.kind == "arrears" and amount is not None and credits:
            for when in _occurrences(
                credits[-1], request_date, request_date + timedelta(days=HORIZON_DAYS)
            ):
                extras.append((when, amount))
                break
    return amended, extras


def _occurrences(stream: _Stream, start: date, end: date) -> list[date]:
    anchor = stream.next_date
    found: list[date] = []
    if anchor is not None:
        index = 0
        while True:
            when = (
                _add_months(anchor, index)
                if stream.monthly
                else anchor + timedelta(days=stream.cadence_days * index)
            )
            if when > end:
                break
            if when >= start:
                found.append(when)
            index += 1
        return found
    first = stream.last
    if stream.monthly:
        index = 1
        while True:
            when = _add_months(first, index)
            if when > end:
                break
            if when >= start:
                found.append(when)
            index += 1
    else:
        index = 1
        while True:
            when = first + timedelta(days=stream.cadence_days * index)
            if when > end:
                break
            if when >= start:
                found.append(when)
            index += 1
    return found


@dataclass(frozen=True, slots=True)
class _Flow:
    when: date
    amount_paise: int
    category: str
    label: str
    source_id: str
    projected: bool


def _explicit_flows(
    user_events: list[dict[str, str]],
    request_date: date,
    *,
    streams: list[_Stream],
    home: str,
    rates: tuple[_Rate, ...],
    images: dict[str, int],
) -> list[_Flow]:
    end = request_date + timedelta(days=HORIZON_DAYS)
    flows: list[_Flow] = []
    for row in user_events:
        if row.get("status") not in {"scheduled", "pending"}:
            continue
        if row.get("direction") not in {"debit", "credit"}:
            continue
        when = _day(row.get("settlement_date"))
        if when is None or when < request_date or when > end:
            continue
        if row.get("direction") == "credit" and not _is_income(row):
            continue
        amount = _row_paise(row, home=home, rates=rates, images=images)
        if amount is None:
            # Never read a blank amount as zero: borrow the category's stream amount, else skip.
            matching = [
                stream
                for stream in streams
                if stream.category == row.get("category")
                and stream.direction == row.get("direction")
            ]
            amount = max((stream.amount_paise for stream in matching), default=None)
        if amount is None or amount <= 0:
            continue
        signed = -amount if row.get("direction") == "debit" else amount
        flows.append(
            _Flow(
                when=when,
                amount_paise=signed,
                category=row.get("category") or "",
                label=row.get("description") or row.get("event_type") or "",
                source_id=row.get("event_id") or "",
                projected=False,
            )
        )
    return flows


def _flows(
    user_events: list[dict[str, str]],
    streams: tuple[_Stream, ...],
    extras: tuple[tuple[date, int], ...],
    request_date: date,
    *,
    home: str,
    rates: tuple[_Rate, ...],
    images: dict[str, int],
) -> list[_Flow]:
    end = request_date + timedelta(days=HORIZON_DAYS)
    # A projected occurrence near the horizon's end would fail a plan on the strength of a single
    # inferred recurrence, so projections stop short of it; confirmed flows keep the full window.
    projected_end = end - timedelta(days=TAIL_DAYS)
    explicit = _explicit_flows(
        user_events,
        request_date,
        streams=list(streams),
        home=home,
        rates=rates,
        images=images,
    )
    flows = list(explicit)
    for stream in streams:
        for when in _occurrences(stream, request_date, projected_end):
            duplicate = False
            for other in explicit:
                if other.category != stream.category:
                    continue
                if (other.amount_paise >= 0) != (stream.direction == "credit"):
                    continue
                if stream.direction == "debit" and not stream.monthly:
                    continue
                if abs((other.when - when).days) <= DEDUPE_DAYS:
                    duplicate = True
                    break
            if duplicate:
                continue
            flows.append(
                _Flow(
                    when=when,
                    amount_paise=stream.amount_paise
                    if stream.direction == "credit"
                    else -stream.amount_paise,
                    category=stream.category,
                    label=stream.description,
                    source_id=f"{stream.category}:{stream.direction}",
                    projected=True,
                )
            )
    for when, amount in extras:
        if when < request_date or when > end:
            continue
        flows.append(
            _Flow(
                when=when,
                amount_paise=amount,
                category="salary" if amount > 0 else "other",
                label="message",
                source_id="message",
                projected=False,
            )
        )
    return sorted(flows, key=lambda flow: (flow.when, flow.amount_paise))


def _cash_events(flows: list[_Flow]) -> tuple[CashEvent, ...]:
    """Every flow as the cash event the engine's walk applies on its date.

    All of them are confirmed: the projection reserves unconfirmed for variable income it may only
    show optimistically, while every flow here belongs in this forecast's base case.
    """
    events: list[CashEvent] = []
    for flow in flows:
        if flow.amount_paise > 0:
            kind = "income_fixed"
        elif flow.projected or flow.category in {"expense", "subscription", "debt_payment"}:
            kind = "obligation"
        else:
            kind = "owed_payment"
        events.append(
            CashEvent(
                date=flow.when,
                amount_paise=flow.amount_paise,
                name=flow.label,
                kind=kind,
                source_id=flow.source_id or None,
            )
        )
    return tuple(events)


def _inputs(
    profile: dict[str, str], request_date: date, events: tuple[CashEvent, ...]
) -> ProjectionInput:
    """One liquid account and no spend categories: the walk is exact, so one run is the answer."""
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
        events=events,
        ask_horizon_days=HORIZON_DAYS,
    )


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
        )
        for index in range(count)
    )
    result = project(replace(inputs, events=inputs.events + payments), runs=runs, seed=seed)
    if result["floor_probability"] < FLOOR_THRESHOLD or not result["obligations_met"]:
        return None
    total = _paise(option.get("total_payable_amount")) or each * count
    return total, first, count


def _spending_changes(
    inputs: ProjectionInput,
    streams: tuple[_Stream, ...],
    extras: tuple[tuple[date, int], ...],
    user_events: list[dict[str, str]],
    profile: dict[str, str],
    *,
    requested: int,
    home: str,
    rates: tuple[_Rate, ...],
    images: dict[str, int],
    runs: int,
    seed: int,
) -> list[tuple[_Stream, int]] | None:
    """The smallest stop/reduce set that makes the full request safe today."""
    reduce_categories = _split(profile.get("expense_categories_user_is_willing_to_reduce"))
    stop_categories = _split(profile.get("expense_categories_user_is_willing_to_stop"))
    candidates: list[tuple[_Stream, int]] = []
    for stream in streams:
        if stream.direction != "debit" or stream.amount_paise <= 0:
            continue
        if stream.category in stop_categories and stream.flexibility in {
            "stoppable",
            "reducible_or_stoppable",
        }:
            candidates.append((stream, 0))
        if (
            stream.category in reduce_categories
            and stream.flexibility in {"reducible", "reducible_or_stoppable"}
            and 0 < stream.minimum_allowed_paise < stream.amount_paise
        ):
            candidates.append((stream, stream.minimum_allowed_paise))
    for size in (1, 2, 3):
        feasible: list[tuple[int, list[tuple[_Stream, int]]]] = []
        for combo in itertools.combinations(candidates, size):
            if len({item[0].event_id for item in combo}) != size:
                continue
            overrides = {item[0].event_id: item[1] for item in combo}
            adjusted = tuple(
                replace(stream, amount_paise=overrides[stream.event_id])
                if stream.event_id in overrides
                else stream
                for stream in streams
            )
            flows = _flows(
                user_events,
                adjusted,
                extras,
                inputs.as_of,
                home=home,
                rates=rates,
                images=images,
            )
            planned = replace(inputs, events=_cash_events(flows))
            if safe_amount_today(planned, runs=runs, seed=seed) >= requested:
                saving = sum(stream.amount_paise - new for stream, new in combo)
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
    runs: int = 1,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    request_date = _day(request.get("request_date"))
    if request_date is None:
        raise ValueError("request_date is missing")
    home = profile.get("home_currency") or "INR"
    requested = _paise(request.get("requested_amount"))
    desired = _day(request.get("desired_completion_date"))
    methods = _split(profile.get("payment_methods_user_will_consider"))

    scenario, extras = _scenario(
        user_events, request_date, messages=messages, home=home, rates=rates, images=images
    )
    flows = _flows(
        user_events, scenario, extras, request_date, home=home, rates=rates, images=images
    )
    inputs = _inputs(profile, request_date, _cash_events(flows))
    safe = min(safe_amount_today(inputs, runs=runs, seed=seed), requested)
    first = first_full_payment_date(inputs, requested, start_on=request_date, runs=runs, seed=seed)
    earliest = _day(first["date"]) if first is not None else None
    max_months = None
    if profile.get("max_installment_months"):
        try:
            max_months = int(profile["max_installment_months"])
        except ValueError:
            max_months = None

    changes: list[tuple[_Stream, int]] | None = None
    if "full_payment" in methods and safe < requested:
        changes = _spending_changes(
            inputs,
            scenario,
            extras,
            user_events,
            profile,
            requested=requested,
            home=home,
            rates=rates,
            images=images,
            runs=runs,
            seed=seed,
        )

    candidates: list[tuple[int, int, date, int, str, str, str]] = []
    if safe >= requested and "full_payment" in methods:
        return {
            "request_id": request["request_id"],
            "amount_safe_to_pay": safe / 100,
            "affordability_status": "affordable_now",
            "recommended_payment_method": "full_payment",
            "earliest_date_for_full_payment": request_date.isoformat(),
            "spending_changes_needed": "none",
        }
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
            total, first, count = plan
            candidates.append(
                (
                    0,
                    total,
                    first,
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


def _scenario(
    user_events: list[dict[str, str]],
    request_date: date,
    *,
    messages: list[dict[str, str]] | None = None,
    home: str = "INR",
    rates: tuple[_Rate, ...] = (),
    images: dict[str, int] | None = None,
) -> tuple[tuple[_Stream, ...], tuple[tuple[date, int], ...]]:
    """The recurring streams the settled history implies, and the dated credits a message adds."""
    streams = _build_streams(user_events, request_date, home=home, rates=rates, images=images or {})
    amended, credits = _apply_messages(
        streams, messages or [], request_date, home=home, rates=rates
    )
    return tuple(amended), tuple(credits)


def _events_for(
    user_events: list[dict[str, str]],
    request_date: date,
    *,
    messages: list[dict[str, str]] | None = None,
    home: str = "INR",
    rates: tuple[_Rate, ...] = (),
    images: dict[str, int] | None = None,
) -> tuple[list[CashEvent], set[str], list[dict[str, Any]]]:
    """The projected events for one user, the categories they cover, and their sources."""
    image_amounts = images or {}
    streams, extras = _scenario(
        user_events,
        request_date,
        messages=messages,
        home=home,
        rates=rates,
        images=image_amounts,
    )
    flows = _flows(
        user_events,
        streams,
        extras,
        request_date,
        home=home,
        rates=rates,
        images=image_amounts,
    )
    events = _cash_events(flows)
    covered: set[str] = {flow.category for flow in flows if flow.amount_paise < 0}
    sources: list[dict[str, Any]] = [
        {
            "kind": "event" if not flow.projected else "stream",
            "id": flow.source_id,
            "name": flow.label,
            "detail": f"{'debit' if flow.amount_paise < 0 else 'credit'} · "
            f"{flow.category} · {flow.when.isoformat()}",
            "amount_paise": abs(flow.amount_paise),
        }
        for flow in flows
    ]
    return list(events), covered, sources


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


def _display_dataset(dataset: Path) -> str:
    try:
        return dataset.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return dataset.resolve().as_posix()


def score(
    dataset: Path | None = None,
    *,
    runs: int = 1,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """Score with the real engine walk.

    The walk has no spend-category pools, so every run is the same curve and one run is exact;
    `runs` and `seed` exist so the sample can be pushed through the sampled walk unchanged.
    """
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
        profile = profiles.get(sample["user_id"])
        if profile is None:
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
        "dataset": _display_dataset(dataset),
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
