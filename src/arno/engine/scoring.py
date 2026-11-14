"""Proper scoring rules for the backtest: how good a whole forecast distribution was.

A forecast here is a sorted list of scenario values (whole paise) and an outcome. Scores are
floats because they are statistics, not money. Every mean comes with a standard error that
allows for the overlap of forecasts made a week apart over longer horizons (Newey-West), so a
published skill carries an honest interval.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Z95 = 1.959963984540054


def crps(samples: npt.NDArray[np.int64], outcome: int) -> float:
    """Continuous ranked probability score of an ensemble: E|X - y| - E|X - X'| / 2.

    ``samples`` must be sorted. A single sample is a point forecast and scores its absolute error.
    """
    n = len(samples)
    if n == 0:
        raise ValueError("a forecast needs at least one sample")
    values = samples.astype(np.float64)
    ranks = 2.0 * np.arange(n, dtype=np.float64) - n + 1
    error = float(np.abs(values - outcome).mean())
    return error - float(ranks @ values) / (n * n)


def quantile(samples: npt.NDArray[np.int64], tau: float) -> int:
    """The order statistic at rank ceil(tau * n): a value that occurred, never interpolated."""
    n = len(samples)
    rank = max(1, math.ceil(tau * n))
    return int(samples[min(rank, n) - 1])


def pinball(predicted: int, outcome: int, tau: float) -> float:
    """Quantile loss of one predicted quantile."""
    gap = outcome - predicted
    return tau * gap if gap >= 0 else (tau - 1) * gap


def pit(samples: npt.NDArray[np.int64], outcome: int) -> float:
    """Where the outcome fell in its forecast: 0 is below every scenario, 1 above every one.

    ``samples`` must be sorted. Ties count half, so a calibrated forecast spreads these evenly
    over [0, 1].
    """
    below = int(np.searchsorted(samples, outcome, side="left"))
    upto = int(np.searchsorted(samples, outcome, side="right"))
    return (below + (upto - below) / 2) / len(samples)


@dataclass(frozen=True, slots=True)
class Estimate:
    """A mean with a 95% interval and the count it came from."""

    mean: float
    low: float
    high: float
    n: int


def hac_estimate(groups: Sequence[Sequence[float]], lag: int) -> Estimate:
    """Mean over every value, with a Newey-West standard error within each group.

    Groups are independent (one persona and seed each); inside a group the values are ordered by
    origin and correlated up to ``lag`` steps, because their horizons overlap.
    """
    values = [value for group in groups for value in group]
    n = len(values)
    if n == 0:
        return Estimate(math.nan, math.nan, math.nan, 0)
    mean = sum(values) / n
    long_run = 0.0
    for group in groups:
        centered = [value - mean for value in group]
        long_run += sum(value * value for value in centered)
        for k in range(1, min(lag, len(centered) - 1) + 1):
            weight = 1 - k / (lag + 1)
            covariance = sum(centered[i] * centered[i - k] for i in range(k, len(centered)))
            long_run += 2 * weight * covariance
    error = math.sqrt(max(long_run, 0.0)) / n
    return Estimate(mean, mean - Z95 * error, mean + Z95 * error, n)


def diebold_mariano(
    losses_a: Sequence[Sequence[float]], losses_b: Sequence[Sequence[float]], lag: int
) -> tuple[float, float]:
    """Test whether forecaster A's mean loss differs from B's: (statistic, two-sided p-value).

    A negative statistic favors A. The variance is the same Newey-West estimate as
    `hac_estimate`, so overlapping horizons do not overstate the evidence.
    """
    differences = [
        [a - b for a, b in zip(group_a, group_b, strict=True)]
        for group_a, group_b in zip(losses_a, losses_b, strict=True)
    ]
    estimate = hac_estimate(differences, lag)
    if estimate.n == 0:
        return math.nan, math.nan
    error = (estimate.high - estimate.mean) / Z95
    if error == 0:
        return (0.0, 1.0) if estimate.mean == 0 else (math.copysign(math.inf, estimate.mean), 0.0)
    statistic = estimate.mean / error
    return statistic, math.erfc(abs(statistic) / math.sqrt(2))


def wilson(successes: int, n: int) -> tuple[float, float]:
    """The 95% Wilson score interval for a proportion."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    z2 = Z95 * Z95
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True, slots=True)
class Brier:
    """The Brier score of a binary event and its Murphy decomposition.

    ``score = reliability - resolution + uncertainty`` (up to binning). Lower reliability is
    better calibrated; higher resolution is sharper.
    """

    score: float
    reliability: float
    resolution: float
    uncertainty: float
    base_rate: float
    n: int


def brier(probabilities: Sequence[float], events: Sequence[bool], bins: int = 10) -> Brier:
    """Brier score with a reliability, resolution, and uncertainty split over equal-width bins."""
    n = len(probabilities)
    if n == 0:
        return Brier(math.nan, math.nan, math.nan, math.nan, math.nan, 0)
    outcomes = [1.0 if event else 0.0 for event in events]
    score = sum((p - o) ** 2 for p, o in zip(probabilities, outcomes, strict=True)) / n
    base = sum(outcomes) / n
    buckets: dict[int, list[tuple[float, float]]] = {}
    for p, o in zip(probabilities, outcomes, strict=True):
        buckets.setdefault(min(int(p * bins), bins - 1), []).append((p, o))
    reliability = 0.0
    resolution = 0.0
    for items in buckets.values():
        count = len(items)
        forecast = sum(p for p, _o in items) / count
        observed = sum(o for _p, o in items) / count
        reliability += count * (forecast - observed) ** 2
        resolution += count * (observed - base) ** 2
    return Brier(score, reliability / n, resolution / n, base * (1 - base), base, n)


def histogram(values: Sequence[float], bins: int = 10) -> list[int]:
    """Counts of values in [0, 1] over equal-width bins; a calibrated PIT is flat."""
    counts = [0] * bins
    for value in values:
        counts[min(int(value * bins), bins - 1)] += 1
    return counts


__all__ = [
    "Brier",
    "Estimate",
    "brier",
    "crps",
    "diebold_mariano",
    "hac_estimate",
    "histogram",
    "pinball",
    "pit",
    "quantile",
    "wilson",
]
