"""The backtest's scoring rules, checked against brute force and known values."""

from __future__ import annotations

import itertools
import math
import random

import numpy as np

from arno.engine import scoring


def _sorted(values: list[int]) -> np.ndarray:
    return np.sort(np.array(values, dtype=np.int64))


def test_crps_matches_the_pairwise_definition():
    rng = random.Random(7)
    for _ in range(50):
        values = [rng.randint(-500, 500) for _ in range(rng.randint(1, 9))]
        outcome = rng.randint(-600, 600)
        n = len(values)
        brute = sum(abs(x - outcome) for x in values) / n - sum(
            abs(a - b) for a, b in itertools.product(values, values)
        ) / (2 * n * n)
        assert math.isclose(scoring.crps(_sorted(values), outcome), brute, abs_tol=1e-9)


def test_a_point_forecast_scores_its_absolute_error():
    assert scoring.crps(_sorted([120]), 100) == 20


def test_quantiles_are_order_statistics_that_occurred():
    samples = _sorted(list(range(1, 11)))
    assert scoring.quantile(samples, 0.1) == 1
    assert scoring.quantile(samples, 0.5) == 5
    assert scoring.quantile(samples, 0.9) == 9
    assert scoring.quantile(samples, 0.95) == 10


def test_pinball_charges_each_side_by_its_weight():
    assert scoring.pinball(100, 110, 0.9) == 9
    assert math.isclose(scoring.pinball(100, 90, 0.9), 1)


def test_pit_counts_ties_as_half():
    samples = _sorted([1, 2, 2, 3])
    assert scoring.pit(samples, 2) == 0.5
    assert scoring.pit(samples, 0) == 0
    assert scoring.pit(samples, 9) == 1


def test_brier_decomposes_into_reliability_resolution_and_uncertainty():
    # Probabilities that sit on one value per bin decompose exactly.
    probabilities = [0.05] * 40 + [0.95] * 60
    events = [False] * 38 + [True] * 2 + [True] * 57 + [False] * 3
    result = scoring.brier(probabilities, events)
    assert math.isclose(
        result.score, result.reliability - result.resolution + result.uncertainty, abs_tol=1e-12
    )
    assert result.base_rate == 0.59


def test_hac_with_no_lag_is_the_ordinary_standard_error():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    estimate = scoring.hac_estimate([values[:3], values[3:]], 0)
    mean = 3.5
    error = math.sqrt(sum((value - mean) ** 2 for value in values)) / len(values)
    assert estimate.mean == mean
    assert math.isclose(estimate.high - mean, scoring.Z95 * error)


def test_positive_autocorrelation_widens_the_interval():
    trend = [[float(value) for value in range(20)]]
    narrow = scoring.hac_estimate(trend, 0)
    wide = scoring.hac_estimate(trend, 4)
    assert wide.high - wide.mean > narrow.high - narrow.mean


def test_diebold_mariano_favors_the_smaller_loss():
    rng = random.Random(3)
    good = [[rng.random() for _ in range(60)]]
    bad = [[value + 0.5 for value in good[0]]]
    statistic, p_value = scoring.diebold_mariano(good, bad, 2)
    assert statistic < 0
    assert p_value < 0.001


def test_wilson_interval_brackets_the_proportion():
    low, high = scoring.wilson(90, 100)
    assert 0.82 < low < 0.9 < high < 0.95
    assert scoring.wilson(0, 0) == (0.0, 1.0)
