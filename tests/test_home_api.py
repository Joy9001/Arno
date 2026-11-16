from __future__ import annotations

from arno.ledger.accounts import create_account
from arno.ledger.common import today
from arno.ledger.schedules import create_schedule
from arno.notifications import create_notification, mark_read
from arno.tools import projection as projection_tools
from arno.tools import read as read_tools


async def test_a_fresh_balance_and_income_support_a_qualified_first_answer(authed_client, ledger):
    bank = await create_account(
        ledger, kind="bank", name="Salary bank", opening_balance_paise=1_000_000
    )
    await create_schedule(
        ledger,
        kind="income_fixed",
        name="Salary",
        amount_paise=500_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=1",
        starts=today(),
    )
    context = await read_tools.get_financial_context(ledger)
    projection = await projection_tools.project(ledger, runs=50)
    response = authed_client.get("/api/home?horizon_days=90")
    assert response.status_code == 200
    home = response.json()
    assert context["liquid_total"]["paise"] == home["spendable_paise"] == 1_000_000
    assert len(context["accounts"]) == len(context["schedules"]) == 1
    assert context["schedules"][0]["name"] == "Salary"
    assert "no income schedule" not in context["setup_gaps"]
    assert "no estimated baselines" in context["setup_gaps"]
    assert context["warnings"]
    assert projection["safe_to_spend"] is not None
    assert home["projection"]["safe_to_spend"] is not None
    assert home["cash_quality"]["status"] == "known"


async def test_home_composes_one_payload(authed_client):
    account = authed_client.post(
        "/api/accounts",
        json={"kind": "bank", "name": "SBI Salary", "opening_balance_paise": 18_420_000},
    )
    assert account.status_code == 201, account.text
    recorded = authed_client.post(
        "/api/movements",
        json={
            "kind": "expense",
            "account_id": account.json()["id"],
            "amount_paise": 25_000,
            "description": "Lunch",
        },
    )
    assert recorded.status_code == 201, recorded.text

    response = authed_client.get("/api/home?horizon_days=90")
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == {
        "as_of",
        "spendable_paise",
        "cash_quality",
        "projection",
        "unread_count",
        "needs_you",
        "recent",
        "ledger",
    }
    assert body["spendable_paise"] == 18_395_000
    assert body["cash_quality"] == {
        "status": "known",
        "known_paise": 18_395_000,
        "known_count": 1,
        "unknown_count": 0,
        "unknown_accounts": [],
    }
    assert body["as_of"] == body["projection"]["as_of"]
    assert body["projection"]["horizon_days"] == 90
    assert body["unread_count"] == 0
    assert body["needs_you"] == []
    assert body["recent"][0]["description"] == "Lunch"
    # The first-run screens read this: one spending row, and the ledger starts today or earlier.
    assert body["ledger"]["spending"] == 1
    assert body["ledger"]["movements"] >= 1
    assert body["ledger"]["first_date"] <= body["as_of"]
    assert authed_client.get("/api/ledger/span").json() == body["ledger"]


async def test_home_reports_no_accounts_rather_than_zero_cash(authed_client):
    body = authed_client.get("/api/home?horizon_days=90").json()

    assert body["spendable_paise"] == 0
    assert body["cash_quality"]["status"] == "no_accounts"
    assert body["cash_quality"]["unknown_count"] == 0
    assert body["ledger"] == {"movements": 0, "spending": 0, "first_date": None}


async def test_home_marks_an_account_without_a_balance_as_unknown(authed_client):
    created = authed_client.post("/api/accounts", json={"kind": "bank", "name": "SBI Savings"})
    assert created.status_code == 201, created.text

    body = authed_client.get("/api/home?horizon_days=90").json()

    assert body["spendable_paise"] == 0
    assert body["cash_quality"] == {
        "status": "unknown",
        "known_paise": 0,
        "known_count": 0,
        "unknown_count": 1,
        "unknown_accounts": ["SBI Savings"],
    }


async def test_home_does_not_treat_a_movement_as_a_known_balance(authed_client):
    created = authed_client.post("/api/accounts", json={"kind": "bank", "name": "SBI Savings"})
    assert created.status_code == 201, created.text
    recorded = authed_client.post(
        "/api/movements",
        json={
            "kind": "expense",
            "account_id": created.json()["id"],
            "amount_paise": 25_000,
            "description": "Lunch",
        },
    )
    assert recorded.status_code == 201, recorded.text

    body = authed_client.get("/api/home?horizon_days=90").json()

    # The legacy ledger total still carries the recorded movement, but it is not a known cash
    # balance and the UI must not present it as one.
    assert body["spendable_paise"] == -25_000
    assert body["cash_quality"] == {
        "status": "unknown",
        "known_paise": 0,
        "known_count": 0,
        "unknown_count": 1,
        "unknown_accounts": ["SBI Savings"],
    }


async def test_home_treats_an_explicit_zero_opening_as_known(authed_client):
    created = authed_client.post(
        "/api/accounts",
        json={"kind": "bank", "name": "Zero bank", "opening_balance_paise": 0},
    )
    assert created.status_code == 201, created.text

    body = authed_client.get("/api/home?horizon_days=90").json()

    assert body["spendable_paise"] == 0
    assert body["cash_quality"] == {
        "status": "known",
        "known_paise": 0,
        "known_count": 1,
        "unknown_count": 0,
        "unknown_accounts": [],
    }


async def test_home_keeps_a_stated_zero_balance_known_and_scopes_a_partial_total(authed_client):
    known_zero = authed_client.post(
        "/api/accounts", json={"kind": "bank", "name": "Zero bank"}
    ).json()
    stated = authed_client.post(
        f"/api/accounts/{known_zero['id']}/statement-balance",
        json={"balance_paise": 0},
    )
    assert stated.status_code == 200, stated.text

    known = authed_client.get("/api/home?horizon_days=90").json()
    assert known["cash_quality"] == {
        "status": "known",
        "known_paise": 0,
        "known_count": 1,
        "unknown_count": 0,
        "unknown_accounts": [],
    }
    assert known["spendable_paise"] == 0

    authed_client.post("/api/accounts", json={"kind": "bank", "name": "HDFC Savings"})
    partial = authed_client.get("/api/home?horizon_days=90").json()
    assert partial["spendable_paise"] == 0
    assert partial["cash_quality"] == {
        "status": "unknown",
        "known_paise": 0,
        "known_count": 1,
        "unknown_count": 1,
        "unknown_accounts": ["HDFC Savings"],
    }


async def test_home_puts_unread_updates_before_newer_read_ones(authed_client, db):
    unread = await create_notification(db, kind="reminder", title="Older unread reminder")
    read = await create_notification(db, kind="reminder", title="Newer read reminder")
    assert unread is not None and read is not None
    await mark_read(db, read["id"])

    body = authed_client.get("/api/home?horizon_days=90").json()

    assert body["needs_you"][0]["id"] == unread["id"]


async def test_notifications_preserve_the_payment_ids_behind_a_price_change(authed_client, db):
    data = {
        "old_paise": 49900,
        "new_paise": 59900,
        "step_date": "2026-06-05",
        "movement_ids": ["mov_one", "mov_two"],
    }
    item = await create_notification(db, kind="price_change", title="Price change", data=data)
    assert item is not None
    response = authed_client.get("/api/notifications")
    assert response.status_code == 200
    result = next(row for row in response.json()["notifications"] if row["id"] == item["id"])
    assert result["data"] == data


async def test_home_rejects_an_out_of_range_horizon(authed_client):
    response = authed_client.get("/api/home?horizon_days=0")

    assert response.status_code == 422


async def test_home_returns_the_same_safe_to_spend_as_the_projection_tool(authed_client, db):
    empty_home = authed_client.get("/api/home?horizon_days=90").json()
    assert empty_home["projection"]["safe_to_spend"] is None

    created = authed_client.post(
        "/api/accounts",
        json={"kind": "bank", "name": "SBI Salary", "opening_balance_paise": 18_420_000},
    )
    assert created.status_code == 201, created.text

    home_body = authed_client.get("/api/home?horizon_days=90").json()
    expected = await projection_tools.project(db, horizon_days=90)
    assert home_body["projection"]["safe_to_spend"] == expected["safe_to_spend"]
    assert home_body["projection"]["safe_to_spend"] is not None
    assert home_body["projection"]["safe_to_spend"]["amount_paise"] >= 0
