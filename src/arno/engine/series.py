"""Pure Bayesian calendar-rule detector for recurring payment series."""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from arno.india.recurrence import expand_rule
from arno.india.workdays import HolidayAdjust, IndiaCalendar, calendar_for

_MAX_JITTER_HALF_WIDTH = 3
_SKIP_GRID: tuple[float, ...] = (0.05, 0.25)
_JITTER_GRID: tuple[int, ...] = (0, 1, 3)
_END_GRID: tuple[float, ...] = (0.02, 0.10)
_MAX_CANDIDATES = 8
_NEXT_DATE_HORIZON_DAYS = 400
_MAX_SAMPLES = 12
UNRELATED_SHARE: float = 0.1

# Overflow guard: scaling by 2^-512 shifts only the IEEE-754 binary exponent.
_SCALE_THRESHOLD = 1e150
_SCALE_DOWN = 2.0**-512
_SCALE_SHIFT_BITS = 512

_WEEKDAY_CODES: tuple[str, ...] = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_WEEKDAY_NAMES: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_MONTH_NAMES: tuple[str, ...] = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        "ach",
        "autopay",
        "cr",
        "dr",
        "imps",
        "india",
        "limited",
        "ltd",
        "mandate",
        "nach",
        "neft",
        "payment",
        "pos",
        "pvt",
        "ref",
        "rtgs",
        "txn",
        "upi",
    }
)


@dataclass(frozen=True, slots=True)
class Payment:
    movement_id: str
    date: date
    amount_paise: int


@dataclass(frozen=True, slots=True)
class Rule:
    family: str
    rrule: str
    starts: date
    holiday_adjust: str
    description: str


@dataclass(frozen=True, slots=True)
class Amounts:
    kind: str
    level_paise: int
    low_paise: int
    high_paise: int
    coverage: float
    samples: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Detection:
    rule: Rule
    confidence: float
    rule_posterior: float
    skip: float
    jitter: tuple[tuple[int, float], ...]
    end: float
    alive: float
    matched: tuple[str, ...]
    amounts: Amounts
    next_dates: tuple[tuple[date, float], ...]


@dataclass(frozen=True, slots=True)
class _GridResult:
    q: float
    w: int
    c: float
    lr_mantissa: float
    lr_exp2: int
    alive: float


def normalize(name: str | None) -> str:
    """Build a stable payee key from statement text while preserving non-Latin letters."""
    if name is None or len(name) == 0:
        return ""
    folded = unicodedata.normalize("NFKC", name).casefold()
    chars: list[str] = []
    for ch in folded:
        cat = unicodedata.category(ch)
        if len(cat) > 0 and cat[0] in ("L", "M", "N"):
            chars.append(ch)
        else:
            chars.append(" ")
    cleaned = "".join(chars)
    tokens: list[str] = []
    for token in cleaned.split():
        digit_count = sum(1 for ch in token if ch.isdigit())
        if digit_count == len(token) or digit_count >= 4:
            continue
        if token in _GENERIC_TOKENS:
            continue
        tokens.append(token)
    return " ".join(tokens)


def _as_holiday_adjust(value: str) -> HolidayAdjust:
    if value == "none":
        return "none"
    if value == "next":
        return "next"
    if value == "previous":
        return "previous"
    raise ValueError(f"Unsupported holiday_adjust: {value!r}")


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    tail = n % 10
    if tail == 1:
        return f"{n}st"
    if tail == 2:
        return f"{n}nd"
    if tail == 3:
        return f"{n}rd"
    return f"{n}th"


def _adjust_suffix(holiday_adjust: str) -> str:
    if holiday_adjust == "next":
        return ", moved to the next working day"
    if holiday_adjust == "previous":
        return ", moved to the previous working day"
    return ""


def _anchor_stepped(anchor: date, period_days: int, target: date) -> date:
    diff = (anchor - target).days
    if diff > 0:
        steps = (diff + period_days - 1) // period_days
        return anchor - timedelta(days=period_days * steps)
    steps = (-diff) // period_days
    return anchor + timedelta(days=period_days * steps)


def _first_of_month_at_or_before(target: date) -> date:
    return date(target.year, target.month, 1)


def _phase_month_start(anchor: date, interval_months: int, target: date) -> date:
    year = anchor.year
    month = anchor.month
    while date(year, month, 1) > target:
        total = year * 12 + (month - 1) - interval_months
        year, rem = divmod(total, 12)
        month = rem + 1
    while True:
        next_total = year * 12 + (month - 1) + interval_months
        next_y, next_rem = divmod(next_total, 12)
        next_m = next_rem + 1
        if date(next_y, next_m, 1) > target:
            break
        year, month = next_y, next_m
    return date(year, month, 1)


def _generate_candidates(
    payments: Sequence[Payment],
    *,
    window_start: date,
) -> list[Rule]:
    anchor_target = window_start - timedelta(days=_MAX_JITTER_HALF_WIDTH + 14)
    month_anchor = _first_of_month_at_or_before(anchor_target)
    year_anchor = date(max(1, anchor_target.year - 1), 1, 1)

    dates = [p.date for p in payments]
    weekdays = sorted({d.weekday() for d in dates})
    days_of_month = sorted({d.day for d in dates})
    day_counts = Counter(d.day for d in dates)
    month_days = sorted({(d.month, d.day) for d in dates})

    raw_rules: list[Rule] = []

    # weekly
    for wd in weekdays:
        code = _WEEKDAY_CODES[wd]
        raw_rules.append(
            Rule(
                family="weekly",
                rrule=f"FREQ=WEEKLY;BYDAY={code}",
                starts=anchor_target,
                holiday_adjust="none",
                description=f"weekly on {_WEEKDAY_NAMES[wd]}",
            )
        )

    # fortnightly (both week phases per weekday present)
    first_by_wd: dict[int, date] = {}
    for d in dates:
        if d.weekday() not in first_by_wd:
            first_by_wd[d.weekday()] = d
    for wd in weekdays:
        code = _WEEKDAY_CODES[wd]
        sample_date = first_by_wd[wd]
        for offset in (0, 7):
            starts = _anchor_stepped(sample_date + timedelta(days=offset), 14, anchor_target)
            raw_rules.append(
                Rule(
                    family="fortnightly",
                    rrule=f"FREQ=WEEKLY;INTERVAL=2;BYDAY={code}",
                    starts=starts,
                    holiday_adjust="none",
                    description=f"fortnightly on {_WEEKDAY_NAMES[wd]}",
                )
            )

    # four_weekly (deduped mod 28)
    seen_mod28: set[int] = set()
    for d in dates:
        phase = d.toordinal() % 28
        if phase in seen_mod28:
            continue
        seen_mod28.add(phase)
        starts = _anchor_stepped(d, 28, anchor_target)
        raw_rules.append(
            Rule(
                family="four_weekly",
                rrule="FREQ=DAILY;INTERVAL=28",
                starts=starts,
                holiday_adjust="none",
                description="every 4 weeks",
            )
        )

    # twice_monthly (two most common days-of-month)
    if len(day_counts) >= 2:
        top_two = sorted(day_counts.items(), key=lambda item: (-item[1], item[0]))[:2]
        d1, d2 = sorted((top_two[0][0], top_two[1][0]))
        for adj in ("none", "next", "previous"):
            raw_rules.append(
                Rule(
                    family="twice_monthly",
                    rrule=f"FREQ=MONTHLY;BYMONTHDAY={d1},{d2}",
                    starts=month_anchor,
                    holiday_adjust=adj,
                    description=(
                        f"twice monthly on the {_ordinal(d1)} and {_ordinal(d2)}"
                        f"{_adjust_suffix(adj)}"
                    ),
                )
            )

    # monthly
    for k in days_of_month:
        for adj in ("none", "next", "previous"):
            raw_rules.append(
                Rule(
                    family="monthly",
                    rrule=f"FREQ=MONTHLY;BYMONTHDAY={k}",
                    starts=month_anchor,
                    holiday_adjust=adj,
                    description=f"monthly on the {_ordinal(k)}{_adjust_suffix(adj)}",
                )
            )

    # last_day & last_working_day
    raw_rules.append(
        Rule(
            family="last_day",
            rrule="FREQ=MONTHLY;BYMONTHDAY=-1",
            starts=month_anchor,
            holiday_adjust="none",
            description="monthly on the last day",
        )
    )
    raw_rules.append(
        Rule(
            family="last_working_day",
            rrule="FREQ=MONTHLY;BYMONTHDAY=-1",
            starts=month_anchor,
            holiday_adjust="previous",
            description="monthly on the last working day",
        )
    )

    # nth_weekday
    nth_seen: set[tuple[int, int]] = set()
    for d in dates:
        wd = d.weekday()
        occ = (d.day - 1) // 7 + 1
        if 1 <= occ <= 4:
            nth_seen.add((occ, wd))
        if (d + timedelta(days=7)).month != d.month:
            nth_seen.add((-1, wd))
    for n, wd in sorted(nth_seen):
        code = _WEEKDAY_CODES[wd]
        label = "last" if n == -1 else _ordinal(n)
        raw_rules.append(
            Rule(
                family="nth_weekday",
                rrule=f"FREQ=MONTHLY;BYDAY={n}{code}",
                starts=month_anchor,
                holiday_adjust="none",
                description=f"monthly on the {label} {_WEEKDAY_NAMES[wd]}",
            )
        )

    # quarterly & half_yearly
    q_seen: set[tuple[int, int]] = set()
    h_seen: set[tuple[int, int]] = set()
    for d in dates:
        q_phase = (d.month - 1) % 3
        if (q_phase, d.day) not in q_seen:
            q_seen.add((q_phase, d.day))
            q_starts = _phase_month_start(d, 3, anchor_target)
            for adj in ("none", "next", "previous"):
                raw_rules.append(
                    Rule(
                        family="quarterly",
                        rrule=f"FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY={d.day}",
                        starts=q_starts,
                        holiday_adjust=adj,
                        description=f"quarterly on the {_ordinal(d.day)}{_adjust_suffix(adj)}",
                    )
                )
        h_phase = (d.month - 1) % 6
        if (h_phase, d.day) not in h_seen:
            h_seen.add((h_phase, d.day))
            h_starts = _phase_month_start(d, 6, anchor_target)
            for adj in ("none", "next", "previous"):
                raw_rules.append(
                    Rule(
                        family="half_yearly",
                        rrule=f"FREQ=MONTHLY;INTERVAL=6;BYMONTHDAY={d.day}",
                        starts=h_starts,
                        holiday_adjust=adj,
                        description=f"half-yearly on the {_ordinal(d.day)}{_adjust_suffix(adj)}",
                    )
                )

    # yearly
    for m, d in month_days:
        for adj in ("none", "next", "previous"):
            raw_rules.append(
                Rule(
                    family="yearly",
                    rrule=f"FREQ=YEARLY;BYMONTH={m};BYMONTHDAY={d}",
                    starts=year_anchor,
                    holiday_adjust=adj,
                    description=(f"yearly on {_MONTH_NAMES[m]} {_ordinal(d)}{_adjust_suffix(adj)}"),
                )
            )

    deduped: list[Rule] = []
    seen_keys: set[tuple[str, str, date, str]] = set()
    for rule in raw_rules:
        key = (rule.family, rule.rrule, rule.starts, rule.holiday_adjust)
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(rule)
    return deduped


def _expand_for_window(
    rule: Rule,
    *,
    window_start: date,
    as_of: date,
    calendar: IndiaCalendar,
) -> list[date]:
    return expand_rule(
        rule.rrule,
        rule.starts,
        start=window_start - timedelta(days=_MAX_JITTER_HALF_WIDTH),
        end=as_of,
        holiday_adjust=_as_holiday_adjust(rule.holiday_adjust),
        calendar=calendar,
    )


def _pruning_score(
    payment_ordinals: Sequence[int],
    expected_ordinals: Sequence[int],
    win_start_ord: int,
    as_of_ord: int,
) -> float:
    m = len(payment_ordinals)
    if m == 0 or len(expected_ordinals) == 0:
        return -1.0

    # Two-pointer check: payments within 3 days of any expected date.
    e_idx = 0
    n_exp = len(expected_ordinals)
    matched_payments = 0
    for po in payment_ordinals:
        while e_idx < n_exp and expected_ordinals[e_idx] < po - _MAX_JITTER_HALF_WIDTH:
            e_idx += 1
        if e_idx < n_exp and abs(expected_ordinals[e_idx] - po) <= _MAX_JITTER_HALF_WIDTH:
            matched_payments += 1

    # Two-pointer check: expected dates inside [window_start, as_of] with no payment within 3d.
    p_idx = 0
    in_window = 0
    unmatched_expected = 0
    for eo in expected_ordinals:
        if eo < win_start_ord or eo > as_of_ord:
            continue
        in_window += 1
        while p_idx < m and payment_ordinals[p_idx] < eo - _MAX_JITTER_HALF_WIDTH:
            p_idx += 1
        if not (p_idx < m and abs(payment_ordinals[p_idx] - eo) <= _MAX_JITTER_HALF_WIDTH):
            unmatched_expected += 1

    payment_share = matched_payments / m
    miss_share = (unmatched_expected / in_window) if in_window > 0 else 0.0
    return payment_share - 0.5 * miss_share


def _jitter_weights(w: int) -> tuple[float, ...]:
    denom = float((w + 1) * (w + 1))
    return tuple((w + 1 - abs(d)) / denom for d in range(-w, w + 1))


def _censoring_skip_probs(
    expected_ordinals: Sequence[int],
    *,
    win_start_ord: int,
    as_of_ord: int,
    q: float,
    w: int,
    f_weights: tuple[float, ...],
) -> list[float]:
    one_minus_q = 1.0 - q
    q_j: list[float] = []
    for eo in expected_ordinals:
        censored = 0.0
        for idx, d in enumerate(range(-w, w + 1)):
            day = eo + d
            if day < win_start_ord or day > as_of_ord:
                censored += f_weights[idx]
        q_j.append(q + one_minus_q * censored)
    return q_j


def _precompute_matches(
    payment_ordinals: Sequence[int],
    expected_ordinals: Sequence[int],
    *,
    w: int,
    f_weights: tuple[float, ...],
) -> list[list[tuple[int, float]]]:
    """For each expected index j (0-based), return [(obs_1based, f(d)), ...] with |d| <= w."""
    matches_by_j: list[list[tuple[int, float]]] = []
    m = len(payment_ordinals)
    p_lo = 0
    for eo in expected_ordinals:
        while p_lo < m and payment_ordinals[p_lo] < eo - w:
            p_lo += 1
        pairs: list[tuple[int, float]] = []
        p_idx = p_lo
        while p_idx < m and payment_ordinals[p_idx] <= eo + w:
            d = payment_ordinals[p_idx] - eo
            pairs.append((p_idx + 1, f_weights[d + w]))
            p_idx += 1
        matches_by_j.append(pairs)
    return matches_by_j


def _dp_likelihood_ratio(
    m: int,
    n: int,
    *,
    q: float,
    c: float,
    t_days: float,
    q_j: Sequence[float],
    matches_by_j: Sequence[Sequence[tuple[int, float]]],
    unrelated_share: float = UNRELATED_SHARE,
) -> tuple[float, int, float]:
    """Compute LR = L / L0 and alive probability for one grid point via exact canonical DP.

    Canonical counting rule (so every (s, t, labeling) is counted once):
    - Before the first match, all unrelated observations sit in an inactive prefix I[i] = u^(i-1)
      where each unrelated observation contributes u = unrelated_share relative to no-pattern.
    - Three conceptual states:
      1) A0 ("active, nothing matched yet"): tracks paths that started at s <= j and skipped
         every expected date up to j. Unrelated observations are disallowed in A0; an A0 path
         may end at t with all M observations unrelated (weight u^M).
      2) S ("active, last step match or skip"): S[i] holds paths with >= 1 match consuming
         i observations and expected dates up to j, ending on a match or skip at j.
      3) U ("active, last step unrelated"): U[i] holds paths ending on an unrelated observation
         after the latest match/skip at j. Because a skip may never follow an unrelated step and
         an end may never follow an unrelated step, P[i] = S[i] + U[i] =
         sum_{k=1}^{i} S[k] * u^(i-k) is the u-weighted prefix sum of S across observations.
    """
    if n == 0:
        return 0.0, 0, 0.0

    u = unrelated_share
    u_pows = [1.0] * (m + 1)
    for k in range(1, m + 1):
        u_pows[k] = u_pows[k - 1] * u

    pi_scaled = 1.0 / n
    stay = 1.0 - c
    match_scale = (1.0 - q) * t_days

    a0_prev = 0.0
    s_prev = [0.0] * (m + 1)
    p_prev = [0.0] * (m + 1)

    total_lr = 0.0
    alive_lr = 0.0
    exp2 = 0

    for j in range(n):
        qj = q_j[j]
        stay_qj = stay * qj
        a0_curr = (pi_scaled + a0_prev * stay) * qj

        s_curr = [val * stay_qj for val in s_prev]
        for obs_idx, f_val in matches_by_j[j]:
            m_factor = match_scale * f_val
            s_curr[obs_idx] += (
                (pi_scaled + a0_prev * stay) * u_pows[obs_idx - 1] + p_prev[obs_idx - 1] * stay
            ) * m_factor

        p_curr = [0.0] * (m + 1)
        running = 0.0
        max_col = a0_curr
        for i in range(1, m + 1):
            val = s_curr[i]
            if val > max_col:
                max_col = val
            running = running * u + val
            p_curr[i] = running
        if running > max_col:
            max_col = running

        end_mass = a0_curr * u_pows[m] + running
        if j == n - 1:
            alive_lr = end_mass
            total_lr += alive_lr
        else:
            total_lr += end_mass * c

        if max_col > _SCALE_THRESHOLD or total_lr > _SCALE_THRESHOLD:
            a0_curr *= _SCALE_DOWN
            s_curr = [val * _SCALE_DOWN for val in s_curr]
            p_curr = [val * _SCALE_DOWN for val in p_curr]
            total_lr *= _SCALE_DOWN
            alive_lr *= _SCALE_DOWN
            pi_scaled *= _SCALE_DOWN
            exp2 += _SCALE_SHIFT_BITS

        a0_prev = a0_curr
        s_prev = s_curr
        p_prev = p_curr

    alive = (alive_lr / total_lr) if total_lr > 0.0 else 0.0
    return total_lr, exp2, alive


def _viterbi_matched_with_bounds(
    payments: Sequence[Payment],
    expected_ordinals: Sequence[int],
    *,
    win_start_ord: int,
    as_of_ord: int,
    q: float,
    w: int,
    c: float,
    t_days: float,
    unrelated_share: float = UNRELATED_SHARE,
) -> tuple[str, ...]:
    """Find the best alignment's matched movement IDs via max-product canonical DP."""
    m = len(payments)
    n = len(expected_ordinals)
    if m == 0 or n == 0:
        return ()

    u = unrelated_share
    u_pows = [1.0] * (m + 1)
    for k in range(1, m + 1):
        u_pows[k] = u_pows[k - 1] * u

    payment_ordinals = [p.date.toordinal() for p in payments]
    f_weights = _jitter_weights(w)
    q_j = _censoring_skip_probs(
        expected_ordinals,
        win_start_ord=win_start_ord,
        as_of_ord=as_of_ord,
        q=q,
        w=w,
        f_weights=f_weights,
    )
    matches_by_j = _precompute_matches(
        payment_ordinals, expected_ordinals, w=w, f_weights=f_weights
    )

    pi_scaled = 1.0 / n
    stay = 1.0 - c
    match_scale = (1.0 - q) * t_days

    # Backpointer for S[j][i]: (-1, -1, True) = start with match i;
    # (prev_i, prev_j, matched_here: bool)
    bp_s: list[list[tuple[int, int, bool] | None]] = [[None] * (m + 1) for _ in range(n)]

    a0_prev = 0.0
    s_prev = [0.0] * (m + 1)
    p_prev = [0.0] * (m + 1)
    p_arg_prev = [0] * (m + 1)

    best_end_val = 0.0
    best_end_state: tuple[int, int] | None = None

    for j in range(n):
        qj = q_j[j]
        stay_qj = stay * qj

        a0_start = pi_scaled * qj
        a0_cont = a0_prev * stay * qj
        a0_curr = a0_start if a0_start >= a0_cont else a0_cont

        s_curr = [0.0] * (m + 1)
        for i in range(1, m + 1):
            skip_val = s_prev[i] * stay_qj
            if skip_val > 0.0:
                s_curr[i] = skip_val
                bp_s[j][i] = (i, j - 1, False)

        for obs_idx, f_val in matches_by_j[j]:
            m_factor = match_scale * f_val
            prefix_u = u_pows[obs_idx - 1]

            cand_val = pi_scaled * prefix_u * m_factor
            cand_bp: tuple[int, int, bool] = (-1, -1, True)

            if j > 0:
                from_a0 = a0_prev * stay * prefix_u * m_factor
                if from_a0 > cand_val:
                    cand_val = from_a0
                    cand_bp = (-1, -1, True)

                prev_best_i = p_arg_prev[obs_idx - 1]
                if prev_best_i > 0:
                    from_p = p_prev[obs_idx - 1] * stay * m_factor
                    if from_p > cand_val:
                        cand_val = from_p
                        cand_bp = (prev_best_i, j - 1, True)

            if cand_val > s_curr[obs_idx]:
                s_curr[obs_idx] = cand_val
                bp_s[j][obs_idx] = cand_bp

        p_curr = [0.0] * (m + 1)
        p_arg_curr = [0] * (m + 1)
        best_p = 0.0
        best_arg = 0
        max_col = a0_curr
        for i in range(1, m + 1):
            val = s_curr[i]
            if val > max_col:
                max_col = val
            best_p *= u
            if val > best_p:
                best_p = val
                best_arg = i
            elif best_p == 0.0:
                best_arg = 0
            p_curr[i] = best_p
            p_arg_curr[i] = best_arg

        p_end = 1.0 if j == n - 1 else c
        a0_end = a0_curr * u_pows[m] * p_end
        if a0_end > best_end_val:
            best_end_val = a0_end
            best_end_state = None
        for i in range(1, m + 1):
            end_val = s_curr[i] * u_pows[m - i] * p_end
            if end_val > best_end_val:
                best_end_val = end_val
                best_end_state = (i, j)

        if max_col > _SCALE_THRESHOLD or best_end_val > _SCALE_THRESHOLD:
            a0_curr *= _SCALE_DOWN
            s_curr = [val * _SCALE_DOWN for val in s_curr]
            p_curr = [val * _SCALE_DOWN for val in p_curr]
            best_end_val *= _SCALE_DOWN
            pi_scaled *= _SCALE_DOWN

        a0_prev = a0_curr
        s_prev = s_curr
        p_prev = p_curr
        p_arg_prev = p_arg_curr

    if best_end_state is None:
        return ()

    matched_indices: list[int] = []
    cur_i, cur_j = best_end_state
    while cur_i > 0 and cur_j >= 0:
        step = bp_s[cur_j][cur_i]
        if step is None:
            break
        prev_i, prev_j, matched_here = step
        if matched_here:
            matched_indices.append(cur_i - 1)
        if prev_i < 0:
            break
        cur_i, cur_j = prev_i, prev_j

    matched_indices.reverse()
    return tuple(payments[idx].movement_id for idx in matched_indices)


def _scale_pair(mantissa: float, exp2: int, target_exp2: int) -> float:
    if mantissa == 0.0:
        return 0.0
    diff = exp2 - target_exp2
    if diff < -1074:
        return 0.0
    return mantissa * (2.0**diff)


def _score_rule_grid(
    payment_ordinals: Sequence[int],
    expected_ordinals: Sequence[int],
    *,
    win_start_ord: int,
    as_of_ord: int,
    t_days: float,
    unrelated_share: float = UNRELATED_SHARE,
) -> tuple[float, int, _GridResult]:
    m = len(payment_ordinals)
    n = len(expected_ordinals)
    if n == 0:
        empty = _GridResult(q=0.05, w=0, c=0.02, lr_mantissa=0.0, lr_exp2=0, alive=0.0)
        return 0.0, 0, empty

    results: list[_GridResult] = []
    for w in _JITTER_GRID:
        f_weights = _jitter_weights(w)
        matches_by_j = _precompute_matches(
            payment_ordinals, expected_ordinals, w=w, f_weights=f_weights
        )
        for q in _SKIP_GRID:
            q_j = _censoring_skip_probs(
                expected_ordinals,
                win_start_ord=win_start_ord,
                as_of_ord=as_of_ord,
                q=q,
                w=w,
                f_weights=f_weights,
            )
            for c in _END_GRID:
                lr_m, lr_e, alive = _dp_likelihood_ratio(
                    m,
                    n,
                    q=q,
                    c=c,
                    t_days=t_days,
                    q_j=q_j,
                    matches_by_j=matches_by_j,
                    unrelated_share=unrelated_share,
                )
                results.append(
                    _GridResult(q=q, w=w, c=c, lr_mantissa=lr_m, lr_exp2=lr_e, alive=alive)
                )

    max_exp2 = max(r.lr_exp2 for r in results)
    best_res = results[0]
    best_scaled = -1.0
    sum_scaled = 0.0
    for r in results:
        scaled = _scale_pair(r.lr_mantissa, r.lr_exp2, max_exp2)
        sum_scaled += scaled
        if scaled > best_scaled:
            best_scaled = scaled
            best_res = r

    return sum_scaled / len(results), max_exp2, best_res


def _median_paise(values: Sequence[int]) -> int:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def _is_fixed_run(values: Sequence[int]) -> tuple[bool, int]:
    med = _median_paise(values)
    if med <= 0:
        return False, med
    return all(50 * abs(v - med) <= med for v in values), med


def _summarize_amounts(amounts: Sequence[int]) -> Amounts:
    n = len(amounts)
    if n == 0:
        return Amounts(
            kind="variable",
            level_paise=0,
            low_paise=0,
            high_paise=0,
            coverage=0.0,
            samples=(),
        )

    samples = tuple(amounts[-_MAX_SAMPLES:])
    fixed_ok, med = _is_fixed_run(amounts)
    if fixed_ok:
        return Amounts(
            kind="fixed",
            level_paise=med,
            low_paise=min(amounts),
            high_paise=max(amounts),
            coverage=(n - 1) / (n + 1),
            samples=samples,
        )

    if n >= 4:
        for split in range(2, n - 1):
            run1 = amounts[:split]
            run2 = amounts[split:]
            ok1, med1 = _is_fixed_run(run1)
            ok2, med2 = _is_fixed_run(run2)
            if ok1 and ok2 and 50 * abs(med2 - med1) > med1:
                r2_len = len(run2)
                return Amounts(
                    kind="stepped",
                    level_paise=med2,
                    low_paise=min(run2),
                    high_paise=max(run2),
                    coverage=(r2_len - 1) / (r2_len + 1),
                    samples=samples,
                )

    s_len = len(samples)
    return Amounts(
        kind="variable",
        level_paise=_median_paise(samples),
        low_paise=min(samples),
        high_paise=max(samples),
        coverage=(s_len - 1) / (s_len + 1),
        samples=samples,
    )


def _detect_with(
    candidates: Sequence[Rule],
    payments: Sequence[Payment],
    *,
    as_of: date,
    window_start: date,
    calendar: IndiaCalendar | None = None,
    unrelated_share: float = UNRELATED_SHARE,
    _expanded_cache: dict[tuple[str, str, date, str], list[date]] | None = None,
    _priors: Sequence[float] | None = None,
) -> Detection | None:
    if len(candidates) == 0:
        return None

    in_window = [p for p in payments if window_start <= p.date <= as_of]
    if len(in_window) < 2:
        return None
    in_window.sort(key=lambda p: (p.date, p.movement_id))

    cal = (
        calendar
        if calendar is not None
        else calendar_for(
            window_start - timedelta(days=_MAX_JITTER_HALF_WIDTH + 14),
            as_of + timedelta(days=_NEXT_DATE_HORIZON_DAYS + 14),
        )
    )

    win_start_ord = window_start.toordinal()
    as_of_ord = as_of.toordinal()
    t_days = float(as_of_ord - win_start_ord + 1)
    payment_ordinals = [p.date.toordinal() for p in in_window]

    family_counts = Counter(r.family for r in candidates)
    num_families = len(family_counts)

    scored: list[tuple[Rule, list[int], float, int, _GridResult]] = []
    for idx, rule in enumerate(candidates):
        cache_key = (rule.family, rule.rrule, rule.starts, rule.holiday_adjust)
        if _expanded_cache is not None and cache_key in _expanded_cache:
            exp_dates = _expanded_cache[cache_key]
        else:
            exp_dates = _expand_for_window(
                rule, window_start=window_start, as_of=as_of, calendar=cal
            )
        exp_ords = [d.toordinal() for d in exp_dates]
        marg_m, marg_e, best_grid = _score_rule_grid(
            payment_ordinals,
            exp_ords,
            win_start_ord=win_start_ord,
            as_of_ord=as_of_ord,
            t_days=t_days,
            unrelated_share=unrelated_share,
        )
        prior = (
            _priors[idx]
            if _priors is not None
            else 0.5 / (num_families * family_counts[rule.family])
        )
        scored.append((rule, exp_ords, prior * marg_m, marg_e, best_grid))

    max_exp2 = max([e for _, _, _, e, _ in scored] + [0])
    no_pat_scaled = _scale_pair(0.5, 0, max_exp2)
    unnorm_rules = [_scale_pair(m_val, e_val, max_exp2) for _, _, m_val, e_val, _ in scored]

    max_term = max([no_pat_scaled] + unnorm_rules)
    if max_term <= 0.0:
        return None

    no_pat_norm = no_pat_scaled / max_term
    rule_norms = [u / max_term for u in unnorm_rules]
    total_norm = no_pat_norm + sum(rule_norms)

    posterior_no_pattern = no_pat_norm / total_norm
    confidence = max(0.0, min(1.0, 1.0 - posterior_no_pattern))

    best_idx = 0
    best_post = -1.0
    for idx, norm_val in enumerate(rule_norms):
        post = norm_val / total_norm
        if post > best_post:
            best_post = post
            best_idx = idx
        elif post == best_post:
            cand_rule = scored[idx][0]
            curr_rule = scored[best_idx][0]
            if (
                cand_rule.family,
                cand_rule.rrule,
                cand_rule.holiday_adjust,
                cand_rule.starts,
            ) < (
                curr_rule.family,
                curr_rule.rrule,
                curr_rule.holiday_adjust,
                curr_rule.starts,
            ):
                best_idx = idx

    map_rule, map_exp_ords, _, _, best_grid = scored[best_idx]
    rule_posterior = rule_norms[best_idx] / total_norm

    matched = _viterbi_matched_with_bounds(
        in_window,
        map_exp_ords,
        win_start_ord=win_start_ord,
        as_of_ord=as_of_ord,
        q=best_grid.q,
        w=best_grid.w,
        c=best_grid.c,
        t_days=t_days,
        unrelated_share=unrelated_share,
    )
    matched_set = set(matched)
    matched_amounts = [p.amount_paise for p in in_window if p.movement_id in matched_set]
    amounts = _summarize_amounts(matched_amounts)

    f_weights = _jitter_weights(best_grid.w)
    jitter = tuple(
        (d, f_weights[idx]) for idx, d in enumerate(range(-best_grid.w, best_grid.w + 1))
    )

    future_dates = expand_rule(
        map_rule.rrule,
        map_rule.starts,
        start=as_of + timedelta(days=1),
        end=as_of + timedelta(days=_NEXT_DATE_HORIZON_DAYS),
        holiday_adjust=_as_holiday_adjust(map_rule.holiday_adjust),
        calendar=cal,
    )
    if len(future_dates) > 0:
        next_exp = future_dates[0]
        next_dates = tuple((next_exp + timedelta(days=d), prob) for d, prob in jitter)
    else:
        next_dates = ()

    return Detection(
        rule=map_rule,
        confidence=confidence,
        rule_posterior=rule_posterior,
        skip=best_grid.q,
        jitter=jitter,
        end=best_grid.c,
        alive=best_grid.alive,
        matched=matched,
        amounts=amounts,
        next_dates=next_dates,
    )


def detect(
    payments: Sequence[Payment],
    *,
    as_of: date,
    window_start: date,
    calendar: IndiaCalendar | None = None,
    unrelated_share: float = UNRELATED_SHARE,
) -> Detection | None:
    """Detect the most probable calendar rule and its probabilities for one payee."""
    in_window = [p for p in payments if window_start <= p.date <= as_of]
    if len(in_window) < 2:
        return None
    in_window.sort(key=lambda p: (p.date, p.movement_id))

    cal = (
        calendar
        if calendar is not None
        else calendar_for(
            window_start - timedelta(days=_MAX_JITTER_HALF_WIDTH + 14),
            as_of + timedelta(days=_NEXT_DATE_HORIZON_DAYS + 14),
        )
    )

    candidates = _generate_candidates(in_window, window_start=window_start)
    if len(candidates) == 0:
        return None

    win_start_ord = window_start.toordinal()
    as_of_ord = as_of.toordinal()
    payment_ordinals = [p.date.toordinal() for p in in_window]

    expanded_cache: dict[tuple[str, str, date, str], list[date]] = {}
    raw_cache: dict[tuple[str, date], list[date]] = {}
    next_wd_cache: dict[date, date] = {}
    prev_wd_cache: dict[date, date] = {}
    buf_start = window_start - timedelta(days=_MAX_JITTER_HALF_WIDTH + 7)
    buf_end = as_of + timedelta(days=7)
    ext_start = window_start - timedelta(days=_MAX_JITTER_HALF_WIDTH)

    scored_prune: list[tuple[float, str, str, str, date, Rule]] = []
    for rule in candidates:
        raw_key = (rule.rrule, rule.starts)
        raw_dates = raw_cache.get(raw_key)
        if raw_dates is None:
            raw_dates = expand_rule(
                rule.rrule,
                rule.starts,
                start=buf_start,
                end=buf_end,
                holiday_adjust="none",
                calendar=cal,
            )
            raw_cache[raw_key] = raw_dates

        if rule.holiday_adjust == "none":
            exp_dates = [d for d in raw_dates if ext_start <= d <= as_of]
        elif rule.holiday_adjust == "next":
            adj_set: set[date] = set()
            for d in raw_dates:
                adj_d = next_wd_cache.get(d)
                if adj_d is None:
                    adj_d = cal.next_working_day(d)
                    next_wd_cache[d] = adj_d
                if ext_start <= adj_d <= as_of:
                    adj_set.add(adj_d)
            exp_dates = sorted(adj_set)
        else:
            adj_set_p: set[date] = set()
            for d in raw_dates:
                adj_d = prev_wd_cache.get(d)
                if adj_d is None:
                    adj_d = cal.previous_working_day(d)
                    prev_wd_cache[d] = adj_d
                if ext_start <= adj_d <= as_of:
                    adj_set_p.add(adj_d)
            exp_dates = sorted(adj_set_p)

        if len(exp_dates) == 0:
            continue
        cache_key = (rule.family, rule.rrule, rule.starts, rule.holiday_adjust)
        expanded_cache[cache_key] = exp_dates
        exp_ords = [d.toordinal() for d in exp_dates]
        score = _pruning_score(payment_ordinals, exp_ords, win_start_ord, as_of_ord)
        scored_prune.append(
            (-score, rule.family, rule.rrule, rule.holiday_adjust, rule.starts, rule)
        )

    if len(scored_prune) == 0:
        return None

    full_family_counts = Counter(item[5].family for item in scored_prune)
    num_full_families = len(full_family_counts)

    scored_prune.sort()
    top_candidates = [item[5] for item in scored_prune[:_MAX_CANDIDATES]]
    top_priors = [
        0.5 / (num_full_families * full_family_counts[rule.family]) for rule in top_candidates
    ]

    return _detect_with(
        top_candidates,
        in_window,
        as_of=as_of,
        window_start=window_start,
        calendar=cal,
        unrelated_share=unrelated_share,
        _expanded_cache=expanded_cache,
        _priors=top_priors,
    )


def upcoming(
    detection: Detection,
    *,
    after: date,
    until: date,
    calendar: IndiaCalendar | None = None,
) -> list[date]:
    """Return the detected rule's expected dates in (after, until]."""
    if until <= after:
        return []
    cal = (
        calendar
        if calendar is not None
        else calendar_for(after - timedelta(days=7), until + timedelta(days=7))
    )
    return expand_rule(
        detection.rule.rrule,
        detection.rule.starts,
        start=after + timedelta(days=1),
        end=until,
        holiday_adjust=_as_holiday_adjust(detection.rule.holiday_adjust),
        calendar=cal,
    )
