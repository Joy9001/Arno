"""Projection workbook contents and download contract."""

from datetime import date
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

from arno.engine.projection import (
    CALCULATION_VERSION,
    CashEvent,
    GoalPlan,
    LiquidAccount,
    ProjectionInput,
    project,
)
from arno.ledger.common import today as ledger_today
from arno.ledger.workbook import projection_workbook

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _archive():
    inputs = ProjectionInput(
        as_of=date(2026, 1, 1),
        floor_paise=10000,
        liquid=tuple(
            LiquidAccount(str(i), name, amount, date(2025, 12, 31))
            for i, (name, amount) in enumerate(
                [("=account", 12345), ("+account", 4567), ("-account", 100), ("@account", 200)]
            )
        ),
        events=(CashEvent(date(2026, 1, 5), -1000, "=payee", "expense"),),
        goals=(GoalPlan("g", "@goal", 100000, 25000, date(2026, 5, 1)),),
        data_quality={"gaps": ["Example data quality note"]},
    )
    result = project(inputs, runs=4, seed=234)
    result["scenario"] = {
        "changes": [
            {"kind": "one_time", "label": "+scenario", "occurrences": 1, "amount_paise": -500}
        ],
        "assumptions": ["Example scenario assumption"],
        "runway_days": None,
        "baseline": {
            "floor_probability": result["floor_probability"],
            "median_low": result["median_low"],
            "first_floor_crossing": result["first_floor_crossing"],
            "safe_to_spend": result["safe_to_spend"],
        },
    }
    return inputs, result, ZipFile(BytesIO(projection_workbook(inputs, result)))


def test_workbook_contains_the_projection_snapshot_assumptions_and_versions():
    inputs, result, archive = _archive()
    with archive:
        shared = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        strings = ["".join(node.itertext()) for node in shared.findall("x:si", NS)]
        assert {"Inputs", "Assumptions", "Result", "About"} <= {
            item.attrib["name"]
            for item in ElementTree.fromstring(archive.read("xl/workbook.xml")).findall(
                "x:sheets/x:sheet", NS
            )
        }
        assert {
            "2025-12-31",
            result["as_of"],
            "Example data quality note",
            "Example scenario assumption",
            "Calculation version",
        } <= set(strings)
        assert {"liquid[1].balance_paise", "events[1].date", "goals[1].name", "seed"} <= set(
            strings
        )
        xml = archive.read("xl/worksheets/sheet1.xml").decode()
        assert f">{result['seed']}<" in xml
        about = ElementTree.fromstring(archive.read("xl/worksheets/sheet4.xml"))
        cells = {}
        for cell in about.findall(".//x:c[@t='s']", NS):
            value = cell.find("x:v", NS)
            assert value is not None and value.text is not None
            cells[cell.attrib["r"]] = strings[int(value.text)]
        assert cells["A2"] == "Calculation version"
        assert cells["B2"] == CALCULATION_VERSION
        assert cells["A3"] == "Result as of"
        assert cells["B3"] == result["as_of"]
        assert inputs.floor_paise == 10000


def test_workbook_formulas_have_verified_cached_values_and_names_are_strings():
    inputs, _, archive = _archive()
    with archive:
        shared = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        strings = ["".join(node.itertext()) for node in shared.findall("x:si", NS)]
        found = set()
        formulas = []
        for name in archive.namelist():
            if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"):
                root = ElementTree.fromstring(archive.read(name))
                for cell in root.findall(".//x:c", NS):
                    formula = cell.find("x:f", NS)
                    value = cell.find("x:v", NS)
                    if formula is not None:
                        assert value is not None and value.text is not None
                        formulas.append((formula.text, float(value.text)))
                    if cell.get("t") == "s" and value is not None and value.text:
                        found.add(strings[int(value.text)])
        assert formulas == [("SUM(B2:B5)", inputs.starting_balance_paise / 100)]
        assert {"=account", "+account", "-account", "@account", "=payee", "@goal"} <= found


async def test_workbook_route_requires_authentication_and_returns_xlsx(
    session_client, client, demo_ledger
):
    response = session_client.get("/api/projection/workbook", params={"horizon_days": 90})
    assert response.status_code == 200
    assert (
        response.headers["content-type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert 'attachment; filename="arno-projection-' in response.headers["content-disposition"]
    assert ZipFile(BytesIO(response.content)).testzip() is None
    assert (
        session_client.get("/api/projection/workbook", params={"amount_paise": 10}).status_code
        == 422
    )
    assert (
        session_client.get("/api/projection/workbook", params={"changes": "{bad"}).status_code
        == 422
    )
    client.cookies.clear()
    assert client.get("/api/projection/workbook").status_code in (401, 403)


async def test_the_projection_routes_take_the_emi_terms_the_card_was_asked_with(
    session_client, demo_ledger
):
    params = {
        "amount_paise": 300_000,
        "date": ledger_today().isoformat(),
        "method": "emi",
        "emi_months": 6,
        "account": "Slice card",
        "emi_rate_percent": 14,
        "emi_fee_paise": 19_900,
    }
    # The card needs a known balance for its available credit, or the EMI is not tried.
    await demo_ledger.execute(
        "UPDATE accounts SET statement_balance_paise = 0, statement_balance_as_of = $1 "
        "WHERE name = 'Slice card'",
        ledger_today(),
    )
    projected = session_client.get("/api/projection", params=params)
    assert projected.status_code == 200, projected.text
    hypothetical = projected.json()["hypothetical"]
    assert hypothetical.get("emi_rate_bps") == 1_400
    assert session_client.get("/api/projection/workbook", params=params).status_code == 200
