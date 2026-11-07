from __future__ import annotations

import io
import json
import os
import shutil
import zipfile

import pytest

from fin_agent.ledger.common import today as ledger_today


def _category(client, name: str) -> dict:
    categories = client.get("/api/categories").json()["categories"]
    return next(category for category in categories if category["name"] == name)


def test_ledger_endpoints_require_a_session(client):
    response = client.get("/api/accounts")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_accounts_movements_and_undo_round_trip(authed_client):
    created = authed_client.post(
        "/api/accounts",
        json={
            "kind": "bank",
            "name": "SBI Salary",
            "institution": "State Bank of India",
            "last4": "7710",
            "opening_balance_paise": 5_000_000,
        },
    )
    assert created.status_code == 201, created.text
    account = created.json()
    assert account["balance_paise"] == 5_000_000
    assert account["undo_id"].startswith("und_")

    accounts = authed_client.get("/api/accounts").json()
    assert [row["name"] for row in accounts["accounts"]] == ["SBI Salary"]
    assert accounts["liquid_total_paise"] == 5_000_000

    food = _category(authed_client, "Food & Dining")
    recorded = authed_client.post(
        "/api/movements",
        json={
            "kind": "expense",
            "account_id": account["id"],
            "amount_paise": 45_000,
            "description": "Lunch",
            "category_id": food["id"],
        },
    )
    assert recorded.status_code == 201, recorded.text
    movement = recorded.json()
    assert movement["status"] == "recorded"
    assert movement["category_name"] == "Food & Dining"
    assert movement["amount_paise"] == 45_000

    listed = authed_client.get("/api/movements?kind=expense").json()
    assert listed["total_count"] == 1
    assert listed["items"][0]["id"] == movement["id"]

    undone = authed_client.post(f"/api/undo/{movement['undo_id']}")
    assert undone.status_code == 200, undone.text
    assert undone.json()["status"] == "undone"
    assert authed_client.get(f"/api/accounts/{account['id']}").json()["balance_paise"] == 5_000_000


async def test_unknown_card_reports_null_outstanding_and_available_credit(authed_client):
    created = authed_client.post(
        "/api/accounts",
        json={
            "kind": "card",
            "name": "Unknown card",
            "credit_limit_paise": 10_000_000,
            "statement_day": 5,
            "due_day": 25,
        },
    )
    assert created.status_code == 201, created.text
    card = created.json()
    assert card["card_outstanding_paise"] is None
    assert card["available_credit_paise"] is None
    assert card["credit_limit_paise"] == 10_000_000
    assert card["statement_day"] == 5
    assert card["due_day"] == 25

    summary = authed_client.get("/api/accounts").json()
    assert summary["card_owed_paise"] is None


async def test_known_zero_card_reports_numeric_zero_and_full_available_credit(authed_client):
    created = authed_client.post(
        "/api/accounts",
        json={
            "kind": "card",
            "name": "Zero card",
            "opening_balance_paise": 0,
            "credit_limit_paise": 10_000_000,
            "statement_day": 5,
            "due_day": 25,
        },
    )
    assert created.status_code == 201, created.text
    card = created.json()
    assert card["card_outstanding_paise"] == 0
    assert card["available_credit_paise"] == 10_000_000

    summary = authed_client.get("/api/accounts").json()
    assert summary["card_owed_paise"] == 0


async def test_transfer_split_and_people_payloads(authed_client):
    bank = authed_client.post(
        "/api/accounts", json={"kind": "bank", "name": "HDFC", "opening_balance_paise": 10_000_000}
    ).json()
    ravi = authed_client.post("/api/payees", json={"display_name": "Ravi", "kind": "person"}).json()
    food = _category(authed_client, "Food & Dining")

    split = authed_client.post(
        "/api/movements",
        json={
            "kind": "split",
            "account_id": bank["id"],
            "my_share_paise": 200_000,
            "people": [
                {"payee_id": ravi["id"], "amount_paise": 400_000, "expected_date": "2026-12-01"}
            ],
            "total_paise": 600_000,
            "description": "Dinner",
            "category_id": food["id"],
        },
    )
    assert split.status_code == 201, split.text
    assert split.json()["amount_paise"] == 600_000

    people = authed_client.get("/api/people").json()["people"]
    ravi_row = next(row for row in people if row["display_name"] == "Ravi")
    assert ravi_row["balance_paise"] == 400_000
    assert ravi_row["next_expected_date"] == "2026-12-01"


async def test_correction_endpoint_edits_labels_without_a_reversal(authed_client):
    bank = authed_client.post(
        "/api/accounts", json={"kind": "bank", "name": "HDFC", "opening_balance_paise": 10_000_000}
    ).json()
    food = _category(authed_client, "Food & Dining")
    groceries = _category(authed_client, "Groceries")
    movement = authed_client.post(
        "/api/movements",
        json={
            "kind": "expense",
            "account_id": bank["id"],
            "amount_paise": 100_000,
            "description": "BigBasket",
            "category_id": food["id"],
        },
    ).json()

    corrected = authed_client.post(
        f"/api/movements/{movement['id']}/correct",
        json={"category_id": groceries["id"]},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "corrected"
    assert corrected.json()["movement"]["category_name"] == "Groceries"
    assert authed_client.get(f"/api/accounts/{bank['id']}").json()["balance_paise"] == 9_900_000


async def test_schedules_goals_and_settings_endpoints(authed_client):
    # Relative to today so the next occurrence never goes stale with the calendar.
    today = ledger_today()
    bank = authed_client.post(
        "/api/accounts", json={"kind": "bank", "name": "HDFC", "opening_balance_paise": 10_000_000}
    ).json()
    rent = _category(authed_client, "Rent")
    schedule = authed_client.post(
        "/api/schedules",
        json={
            "kind": "obligation",
            "subtype": "rent",
            "name": "Rent",
            "amount_paise": 2_800_000,
            "account_id": bank["id"],
            "category_id": rent["id"],
            "rule": f"FREQ=MONTHLY;BYMONTHDAY={today.day}",
            "starts": today.isoformat(),
            "holiday_adjust": "none",
        },
    )
    assert schedule.status_code == 201, schedule.text
    assert schedule.json()["next_date"] == today.isoformat()

    upcoming = authed_client.get("/api/schedules/upcoming?days=30").json()
    assert any(event["name"] == "Rent" for event in upcoming["events"])

    goal = authed_client.post(
        "/api/goals",
        json={"name": "Goa trip", "target_paise": 12_000_000, "deadline": "2027-05-30"},
    )
    assert goal.status_code == 201, goal.text
    assert goal.json()["saved_paise"] == 0
    assert goal.json()["progress"] == 0
    goals = authed_client.get("/api/goals").json()["goals"]
    assert goals[0]["name"] == "Goa trip"

    patched = authed_client.patch("/api/settings", json={"floor_paise": 2_500_000})
    assert patched.status_code == 200, patched.text
    assert patched.json()["floor_paise"] == 2_500_000

    settings = authed_client.get("/api/settings").json()
    assert settings["floor_paise"] == 2_500_000
    assert settings["profile"]["display_name"] == "You"


async def test_category_and_rule_endpoints(authed_client):
    category = authed_client.post("/api/categories", json={"name": "Pets", "reducible": True})
    assert category.status_code == 201, category.text
    category_id = category.json()["id"]

    updated = authed_client.patch(f"/api/categories/{category_id}", json={"reducible": False})
    assert updated.status_code == 200
    assert updated.json()["reducible"] is False

    rule = authed_client.post(
        "/api/category-rules",
        json={"pattern": "Supertails", "category_id": category_id, "source": "user"},
    )
    assert rule.status_code == 201, rule.text
    rules = authed_client.get("/api/category-rules").json()["items"]
    assert any(row["pattern"] == "supertails" for row in rules)

    categories = authed_client.get("/api/categories").json()
    assert any(row["name"] == "Pets" for row in categories["categories"])
    assert any(
        row["name"] == "Food & Dining" and row["reducible"] for row in categories["categories"]
    )


async def test_backup_status_and_exports(authed_client, db):
    status = authed_client.get("/api/backup/status").json()
    assert status["due"] is True
    assert status["overdue"] is True
    assert status["last_success_at"] is None

    await db.execute(
        """
        INSERT INTO backup_runs (started_at, finished_at, status, dump_bytes, duration_ms)
        VALUES (now() - interval '2 hours', now() - interval '119 minutes', 'success', 12345, 1000)
        """
    )
    fresh = authed_client.get("/api/backup/status").json()
    assert fresh["due"] is False
    assert fresh["overdue"] is False
    assert fresh["last_attempt"]["status"] == "success"

    exported = authed_client.get("/api/export/json")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    payload = exported.json()
    assert payload["format"] == "fin-agent-export"
    assert "accounts" in payload["tables"]

    csv_export = authed_client.get("/api/export/csv")
    assert csv_export.status_code == 200
    assert csv_export.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(csv_export.content)) as archive:
        names = set(archive.namelist())
    assert {"accounts.csv", "movements.csv", "README.txt"} <= names


@pytest.mark.skipif(
    not (os.environ.get("PG_DUMP") or shutil.which("pg_dump")), reason="pg_dump is not installed"
)
async def test_pg_dump_export(authed_client):
    response = authed_client.get("/api/export/pg_dump")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert b"PostgreSQL database dump" in response.content


async def test_movement_validation_is_a_problem_document(authed_client):
    response = authed_client.post(
        "/api/movements",
        json={"kind": "expense", "account_id": "nope", "amount_paise": 0},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 422


async def test_export_json_is_valid_json(authed_client):
    payload = json.loads(authed_client.get("/api/export/json").content)
    assert payload["version"] == 1
