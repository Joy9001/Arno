"""The critical-values seam between the projection kernel and affordability.

Affordability reasons in domain terms: a payment method, skipped goals, and a spend cut. Every
kernel detail stays here. A spec is prepared once per plan; a cut is then a change to exactly two
spec fields (``cut_bps`` and ``cut_until``), so reusing the spec across a verification bisection is
bit-for-bit identical to calling ``project(reduce_categories(inputs, cut), ...)`` at every step.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

import numpy as np
import numpy.typing as npt

from arno.engine import kernel
from arno.engine import projection as projection_mod
from arno.engine.projection import CategoryCutRow, HypotheticalMeta, Projection, ProjectionInput


@dataclass(frozen=True, slots=True)
class CriticalMatrix:
    """One method's scenario balances at the checkpoints, and what a cut frees by each."""

    method: str
    inputs: ProjectionInput
    cost_paise: int
    meta: HypotheticalMeta
    marks: tuple[int, ...]
    balances: npt.NDArray[np.int64]  # (scenarios, checkpoints)
    reducible: npt.NDArray[np.int64] | None  # cash-realized reducible spend, same shape
    capped: bool  # a card went past its limit in some scenario, so a cut frees cash sooner
    # Obligation days, and each scenario's payable there before the day's spending, with the
    # reducible cash a cut frees by then.
    obligation_days: tuple[int, ...]
    payables: npt.NDArray[np.int64] | None
    obligation_reducible: npt.NDArray[np.int64] | None


def build_critical_matrix(
    method: str,
    inputs: ProjectionInput,
    *,
    cost_paise: int,
    meta: HypotheticalMeta,
    cut_until: int,
    runs: int,
    seed: int,
) -> CriticalMatrix:
    """Score one method's levers on the checkpoints a minimum can fall on."""
    model = projection_mod.MODEL
    prepared = projection_mod.prepare(inputs, model)
    obligation_days = kernel.obligation_days(prepared.spec)
    marks = projection_mod.checkpoints_for(
        prepared.spec, tuple(int(day) for day in obligation_days)
    )
    settings = kernel.Settings(
        scenarios=runs,
        seed=seed,
        block_length=model.block_length,
        checkpoints=marks,
        reducible_until=cut_until,
        payable_days=tuple(int(day) for day in obligation_days),
    )
    run = kernel.run(prepared.spec, settings)
    assert run.checkpoints is not None
    columns = {day: index for index, day in enumerate(marks)}
    obligation_reducible = (
        run.reducible_before[:, [columns[int(day)] for day in obligation_days]]
        if run.reducible_before is not None
        else None
    )
    return CriticalMatrix(
        method=method,
        inputs=inputs,
        cost_paise=cost_paise,
        meta=meta,
        marks=marks,
        balances=run.checkpoints,
        reducible=run.reducible,
        capped=run.capped is not None and bool(run.capped.any()),
        obligation_days=tuple(int(day) for day in obligation_days),
        payables=run.payables,
        obligation_reducible=obligation_reducible,
    )


def min_hold_count(scenarios: int, threshold: float) -> int:
    """The fewest scenarios that must hold for the share to reach ``threshold``."""
    return projection_mod._min_held(scenarios, threshold)


@dataclass(frozen=True, slots=True)
class PreparedPlan:
    """A plan's spec, prepared once; each cut only replaces ``cut_bps`` and ``cut_until``."""

    inputs: ProjectionInput
    prepared: projection_mod.Prepared
    until: date
    runs: int
    seed: int
    threshold: float

    def evaluate_cut(self, cut_bps: int) -> Projection:
        """Rerun the plan at ``cut_bps`` and return the same Projection ``project`` would."""
        spec = self.prepared.spec
        # reduce_categories clamps the ratio before prepare() turns it back into basis points;
        # mirror that so the reused spec matches a full rerun even past the cap.
        ratio = max(0.0, min(cut_bps / 10_000, projection_mod.MAX_CUT_RATIO))
        effective_bps = int(round(ratio * 10_000))
        cut_until = (
            min(spec.days, (self.until - self.inputs.as_of).days) if effective_bps > 0 else -1
        )
        run_spec = replace(spec, cut_bps=effective_bps, cut_until=cut_until)
        settings = kernel.Settings(
            scenarios=self.runs,
            seed=self.seed,
            block_length=self.prepared.model.block_length,
        )
        run = kernel.run(run_spec, settings)
        return projection_mod._summary(self.inputs, self.prepared, run, self.seed, self.threshold)


def prepare_plan(
    inputs: ProjectionInput,
    *,
    until: date,
    runs: int,
    seed: int,
    threshold: float,
) -> PreparedPlan:
    """Prepare a plan's goal-relieved inputs once, before any cut is scored."""
    return PreparedPlan(
        inputs,
        projection_mod.prepare(inputs, projection_mod.MODEL),
        until,
        runs,
        seed,
        threshold,
    )


def cut_rows(inputs: ProjectionInput, cut_bps: int, until: date) -> list[CategoryCutRow]:
    """The daily-rate rows shown for a cut, keeping ``reduce_categories`` behind the seam."""
    _reduced, rows = projection_mod.reduce_categories(inputs, cut_bps / 10_000, until=until)
    return rows


__all__ = [
    "CriticalMatrix",
    "PreparedPlan",
    "build_critical_matrix",
    "cut_rows",
    "min_hold_count",
    "prepare_plan",
]
