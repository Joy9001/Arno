"""Portable snapshot of one projection and its computed result."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from io import BytesIO
from typing import Any

import xlsxwriter
from xlsxwriter.format import Format
from xlsxwriter.worksheet import Worksheet

from arno import __version__
from arno.engine.projection import CALCULATION_VERSION, Projection, ProjectionInput


def _rows(sheet: Worksheet, value: Any, money: Format, row: int = 0, path: str = "") -> int:
    """Flatten the engine snapshot without interpreting user text as spreadsheet syntax."""
    if isinstance(value, dict):
        for key, item in value.items():
            row = _rows(sheet, item, money, row, f"{path}.{key}" if path else key)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value, 1):
            row = _rows(sheet, item, money, row, f"{path}[{index}]")
    else:
        sheet.write_string(row, 0, path)
        if isinstance(value, bool):
            sheet.write_string(row, 1, str(value))
        elif isinstance(value, (int, float)):
            if (
                path.endswith(("_paise", ".paise"))
                or (".history[" in path and path.endswith("[2]"))
                or ".amounts[" in path
            ):
                sheet.write_number(row, 1, value / 100, money)
            else:
                sheet.write_number(row, 1, value)
        else:
            sheet.write_string(row, 1, "" if value is None else str(value))
        row += 1
    return row


def projection_workbook(
    inputs: ProjectionInput, result: Projection, request: dict[str, Any] | None = None
) -> bytes:
    """Write the original inputs and outputs, with a cached total for offline viewers."""
    output = BytesIO()
    with xlsxwriter.Workbook(
        output,
        {
            "in_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
            "strings_to_numbers": False,
        },
    ) as book:
        money = book.add_format({"num_format": "#,##0.00"})
        sheet = book.add_worksheet("Inputs")
        sheet.set_column(0, 0, 48)
        sheet.set_column(1, 1, 35)
        sheet.write_string(0, 0, "Liquid account")
        sheet.write_string(0, 1, "Balance (rupees)")
        sheet.write_string(0, 2, "Balance as of")
        for row, account in enumerate(inputs.liquid, 1):
            sheet.write_string(row, 0, account.name)
            sheet.write_number(row, 1, account.balance_paise / 100, money)
            sheet.write_string(row, 2, account.as_of.isoformat() if account.as_of else "")
        total_row = len(inputs.liquid) + 1
        sheet.write_string(total_row, 0, "Total starting cash (rupees)")
        formula = f"=SUM(B2:B{total_row})" if inputs.liquid else "=SUM(0)"
        sheet.write_formula(total_row, 1, formula, money, inputs.starting_balance_paise / 100)
        next_row = _rows(sheet, asdict(inputs), money, total_row + 2)
        _rows(
            sheet,
            {
                "request": request,
                "horizon_days": result["horizon_days"],
                "seed": result["seed"],
                "runs": result["runs"],
            },
            money,
            next_row + 1,
        )

        assumptions = book.add_worksheet("Assumptions")
        assumptions.set_column(0, 0, 48)
        assumptions.set_column(1, 1, 90)
        _rows(
            assumptions,
            {
                "scenario_assumptions": result.get("scenario", {}).get("assumptions", []),
                "data_quality": result["data_quality"],
            },
            money,
        )

        results = book.add_worksheet("Result")
        results.set_column(0, 0, 48)
        results.set_column(1, 1, 70)
        _rows(results, result, money)

        about = book.add_worksheet("About")
        about.set_column(0, 0, 30)
        about.set_column(1, 1, 60)
        _rows(
            about,
            {
                "Arno version": __version__,
                "Calculation version": CALCULATION_VERSION,
                "Result as of": result["as_of"],
                "Generated at (UTC)": datetime.now(UTC).isoformat(),
                "Money unit": "rupees (INR); 100 paise per rupee",
                "Interpretation": (
                    "Original model results are snapshots; editing inputs does not "
                    "recalculate the projection."
                ),
            },
            money,
        )
    return output.getvalue()
