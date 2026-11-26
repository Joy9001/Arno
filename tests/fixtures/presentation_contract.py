"""Synthetic cards produced by the real backend for the frontend contract checks.

Run directly, without a database or model. Values are fixtures, never user financial data.
"""

from __future__ import annotations

import json
from typing import cast

from arno.agent.presentation import (
    Registration,
    ResultRegistry,
    TableColumn,
    TableInput,
    build_answer,
)
from arno.engine.money import amortize, apr_percent, card_payoff, compound, money
from arno.engine.projection import SourceIds
from arno.ledger.deletion import retention_facts
from arno.ledger.export import SECRET_TABLES
from arno.tools.projection import card as projection_card


def _id(registration: Registration) -> str:
    result_id = registration.primary_id
    assert result_id is not None
    return result_id


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
                    "transfers": [
                        {
                            "from_name": "Salary bank",
                            "from_kind": "bank",
                            "to_name": "Spending",
                            "to_kind": "spending",
                        }
                    ],
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
            source_result_ids=[_id(spending), _id(movements)],
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
            "floor_set": False,
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
            "safe_to_spend": {
                "amount_paise": 200_000,
                "binding": "floor",
                "binding_date": "2026-11-10",
            },
            "data_quality": {"gaps": [], "setup_gaps": ["no floor set"]},
            "sources": [],
            # The card passes source ids straight through; this fixture deliberately has none.
            "source_ids": cast(SourceIds, {}),
            "runs": 100,
            "seed": 42,
            "optimistic": None,
        },
        request={"horizon_days": 90},
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
    scenario_native = projection_card(
        {
            "as_of": "2026-11-06",
            "horizon_days": 90,
            "end": "2027-02-04",
            "floor_paise": 100_000,
            "floor_set": True,
            "floor_probability": 0.62,
            "floor_threshold": 0.90,
            "median_low": {"date": "2027-01-05", "paise": 45_000},
            "p10_low_paise": 0,
            "first_floor_crossing": "2026-12-20",
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
            "safe_to_spend": {
                "amount_paise": 0,
                "binding": "floor",
                "binding_date": "2026-12-20",
            },
            "data_quality": {
                "gaps": ["Assumes 1 hypothetical change; nothing in the ledger was modified."],
                "setup_gaps": [],
            },
            "sources": [],
            "source_ids": cast(SourceIds, {}),
            "runs": 100,
            "seed": 42,
            "optimistic": None,
            "scenario": {
                "changes": [
                    {
                        "kind": "stop_income",
                        "label": "Stop all income from 2026-11-10 (3 events, ₹1,20,000.00)",
                        "occurrences": 3,
                        "amount_paise": 12_000_000,
                    }
                ],
                "assumptions": ["All income flows stop during the scenario window."],
                "holds": False,
                "reason": "Drops below floor in 38% of simulated futures.",
                "runway_days": 44,
                "baseline": {
                    "floor_probability": 0.96,
                    "median_low": {"date": "2026-11-28", "paise": 210_000},
                    "first_floor_crossing": None,
                    "safe_to_spend": {
                        "amount_paise": 150_000,
                        "binding": "floor",
                        "binding_date": "2026-11-28",
                    },
                },
            },
        },
        request={
            "horizon_days": 90,
            "changes": [{"kind": "stop_income", "start": "2026-11-10"}],
        },
    )
    scenario_reg = registry.register_read("project", {}, context={"card": scenario_native})
    loan_payload = amortize(1_000_000, 8, 12)
    loan = registry.register_calculation("amortize", loan_payload, {})
    loan_fee_raw = amortize(10_000_000, 10, 12, processing_fee_paise=200_000)
    loan_fee_payload = {
        **loan_fee_raw,
        "apr_percent": apr_percent(loan_fee_raw.get("apr")),
    }
    loan_with_fee = registry.register_calculation("amortize", loan_fee_payload, {})
    card_raw = card_payoff(4_000_000, annual_rate_bps=4_200, payment="minimum")
    card_payload = {
        **card_raw,
        "balance": money(4_000_000),
        "balance_source": "as you stated",
        "monthly_rate_percent": 3.5,
        "annual_rate_bps": 4_200,
        "rate_source": "as you stated",
        "payment_mode": "minimum",
        "fixed_payment": None,
    }
    card_reg = registry.register_calculation("card_payoff", card_payload, {})
    growth = registry.register_calculation("compound", compound(10_000, 8, 12), {})
    resources = [
        *spending.results,
        *movements.results,
        comparison,
        *context.results,
        *projection.results,
        *scenario_reg.results,
        loan,
        loan_with_fee,
        card_reg,
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
        "scenario": build_answer(
            registry, {"_type": "Receipt", "result_id": scenario_reg.primary_id}
        )[0],
        "loan_with_fee": build_answer(
            registry,
            {
                "_type": "Stack",
                "children": [
                    {"_type": view, "result_id": loan_with_fee.result_id}
                    for view in ("Summary", "Table")
                ],
            },
        )[0],
        "card_payoff": build_answer(
            registry,
            {
                "_type": "Stack",
                "children": [
                    {"_type": view, "result_id": card_reg.result_id}
                    for view in ("Summary", "Table")
                ],
            },
        )[0],
    }
    return {
        "results": [resource.snapshot for resource in resources],
        "answers": answers,
        "comparison": comparison.snapshot,
        "scope": {"excluded_tables": sorted(SECRET_TABLES)},
        "deletion": retention_facts(),
        "cards": {
            "amortize_legacy": {"card": "amortize", "title": "Loan schedule", **loan_payload},
            "amortize_with_fee": {
                "card": "amortize",
                "title": "Loan schedule",
                **loan_fee_payload,
            },
            "card_payoff": {"card": "card_payoff", "title": "Card payoff", **card_payload},
        },
    }


if __name__ == "__main__":
    print(json.dumps(fixtures(), allow_nan=False))
