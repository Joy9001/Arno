from __future__ import annotations

from fin_agent.notifications import create_notification, mark_read


def test_home_composes_one_payload(authed_client):
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
        "projection",
        "unread_count",
        "needs_you",
        "recent",
    }
    assert body["spendable_paise"] == 18_395_000
    assert body["as_of"] == body["projection"]["as_of"]
    assert body["projection"]["horizon_days"] == 90
    assert body["unread_count"] == 0
    assert body["needs_you"] == []
    assert body["recent"][0]["description"] == "Lunch"


def test_home_puts_unread_updates_before_newer_read_ones(authed_client, db):
    unread = create_notification(db, kind="reminder", title="Older unread reminder")
    read = create_notification(db, kind="reminder", title="Newer read reminder")
    db.commit()
    mark_read(db, read["id"])
    db.commit()

    body = authed_client.get("/api/home?horizon_days=90").json()

    assert body["needs_you"][0]["id"] == unread["id"]


def test_home_rejects_an_out_of_range_horizon(authed_client):
    response = authed_client.get("/api/home?horizon_days=0")

    assert response.status_code == 422
