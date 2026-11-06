"""Synthetic cards produced by the real backend for the frontend contract checks.

Run directly, without a database or model. Values are fixtures, never user financial data.
"""

from __future__ import annotations

import json

from fin_agent.agent.presentation import ResultRegistry, TableColumn, TableInput, build_answer
from fin_agent.engine.money import amortize, compound, money
from fin_agent.tools.projection import card as projection_card


def fixtures() -> dict:
    registry = ResultRegistry()
    spending = registry.register_read(
        "spending",
        {
            "period": {"from": "2026-10-01", "to": "2026-10-31"},
            "groups": [{"key": "Food", "amount": money(45_000), "count": 2}],
            "total": money(45_000),
            "coverage": {"complete": False, "gaps": ["2026-10"]},
        },
        context={"group_by": "category", "filters": {"category": "Food"}},
    )
    movements = registry.register_read(
        "movements",
        {
            "rows": [
                {
                    "id": "mov_fixture",
                    "date": "2026-10-01",
                    "description": "Lunch",
                    "category": "Food",
                    "payee": None,
                    "amount_paise": 45_000,
                }
            ],
            "total_count": 1,
            "coverage": {"complete": False, "gaps": ["2026-10"]},
        },
    )
    comparison = registry.register_table(
        TableInput(
            title="Synthetic comparison",
            columns=[
                TableColumn(label="Option"),
                TableColumn(label="Amount", type="money"),
            ],
            rows=[[f"Option {index}", 45_000 if index else None] for index in range(12)],
            source_result_ids=[spending.primary_id, movements.primary_id],
        )
    )
    context = registry.register_read(
        "context",
        {
            "today": "2026-11-06",
            "accounts": [
                {
                    "id": "acc_fixture",
                    "name": "Unknown bank",
                    "kind": "bank",
                    "balance": None,
                    "as_of": None,
                }
            ],
            "liquid_total": None,
            "upcoming": [],
            "goals": [],
            "owed": [],
            "warnings": ["Unknown bank has no stated balance"],
        },
    )
    native = projection_card(
        {
            "as_of": "2026-11-06",
            "horizon_days": 90,
            "end": "2027-02-04",
            "floor_paise": 0,
            "floor_probability": 1.0,
            "floor_threshold": 0.95,
            "median_low": {"date": "2026-11-06", "paise": 300_000},
            "p10_low_paise": 200_000,
            "first_floor_crossing": None,
            "curve": [
                {
                    "date": "2026-11-06",
                    "median_paise": 300_000,
                    "p10_paise": 200_000,
                    "p90_paise": 400_000,
                }
            ],
            "goals": [],
            "obligations_met": True,
            "data_quality": {"gaps": [], "setup_gaps": ["no floor set"]},
            "sources": [],
            "source_ids": {},
            "runs": 100,
            "seed": 42,
        }
    )
    projection = registry.register_read("project", {}, context={"card": native})
    rounded = registry.register_read(
        "project",
        {},
        context={
            "card": {
                **native,
                "floor": money(100_000),
                "floor_set": True,
                "floor_probability": 0.965,
            }
        },
    )
    loan = registry.register_calculation("amortize", amortize(1_000_000, 8, 12), {})
    growth = registry.register_calculation("compound", compound(10_000, 8, 12), {})
    resources = [
        *spending.results,
        *movements.results,
        comparison,
        *context.results,
        *projection.results,
        loan,
        growth,
    ]
    answers = {
        "spending": build_answer(
            registry,
            {
                "_type": "Stack",
                "children": [
                    {"_type": view, "result_id": spending.primary_id}
                    for view in ("Summary", "Table", "Chart")
                ],
            },
        )[0],
        "comparison": build_answer(registry, {"_type": "Table", "result_id": comparison.result_id})[
            0
        ],
        "receipt": build_answer(registry, {"_type": "Receipt", "result_id": projection.primary_id})[
            0
        ],
        "rounded": build_answer(
            registry,
            {
                "_type": "Stack",
                "children": [
                    {"_type": view, "result_id": rounded.primary_id}
                    for view in ("Summary", "Receipt")
                ],
            },
        )[0],
    }
    return {
        "results": [resource.snapshot for resource in resources],
        "answers": answers,
        "comparison": comparison.snapshot,
    }


if __name__ == "__main__":
    print(json.dumps(fixtures(), allow_nan=False))
