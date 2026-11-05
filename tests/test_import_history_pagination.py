"""The additive, bounded import-history route."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb

from fin_agent.ledger.accounts import create_account
from fin_agent.ledger.audit import new_id


def _add_import(conn, *, created_at: datetime, status: str = "committed", **fields: Any) -> str:
    import_id = new_id(conn, "imp")
    values = {
        "id": import_id,
        "file_hash": f"hash-{import_id}",
        "status": status,
        "created_at": created_at,
        **fields,
    }
    columns = ", ".join(values)
    placeholders = ", ".join(["%s"] * len(values))
    conn.execute(
        f"INSERT INTO imports ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )
    return import_id


def _walk(client, *, limit: int = 25) -> tuple[list[str], int]:
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        response = client.get(
            "/api/import-history",
            params={"limit": limit, **({"cursor": cursor} if cursor else {})},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        seen.extend(item["import_id"] for item in body["items"])
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            return seen, pages
        assert body["items"], "a page with a cursor must not be empty"


def test_more_than_the_legacy_window_is_reachable_without_duplicates(authed_client, ledger):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    created = [
        _add_import(ledger, created_at=base + timedelta(minutes=index)) for index in range(205)
    ]
    ledger.commit()

    first = authed_client.get("/api/import-history").json()
    assert len(first["items"]) == 25
    assert first["next_cursor"]

    seen, pages = _walk(authed_client)

    assert len(seen) == len(set(seen)) == 205
    assert seen == list(reversed(created))
    assert pages == 9


def test_same_timestamp_pages_by_id_descending(authed_client, ledger):
    moment = datetime(2026, 2, 1, tzinfo=UTC)
    for _ in range(7):
        _add_import(ledger, created_at=moment)
    ledger.commit()

    expected = [
        row["id"]
        for row in ledger.execute(
            "SELECT id FROM imports ORDER BY created_at DESC, id DESC"
        ).fetchall()
    ]
    seen, pages = _walk(authed_client, limit=3)

    assert seen == expected
    assert pages == 3


def test_history_item_matches_legacy_fields_with_one_attachment(authed_client, ledger):
    account = create_account(ledger, kind="bank", name="History bank", opening_balance_paise=0)
    created_at = datetime(2026, 3, 1, 8, 30, tzinfo=UTC)
    committed_at = created_at + timedelta(hours=1)
    import_id = _add_import(
        ledger,
        created_at=created_at,
        account_id=account["id"],
        period_start=date(2026, 2, 1),
        period_end=date(2026, 2, 28),
        check_result=Jsonb({"status": "VERIFIED"}),
        counts=Jsonb(
            {
                "new": 3,
                "certain": 2,
                "ambiguous": 1,
                "ignored": 0,
                "logged_not_on_statement": 4,
            }
        ),
        reason="checked",
        committed_at=committed_at,
    )
    for filename, attached_at in (
        ("older.csv", created_at - timedelta(minutes=2)),
        ("latest.csv", created_at - timedelta(minutes=1)),
    ):
        ledger.execute(
            """
            INSERT INTO attachments
                (id, kind, filename, content_type, size_bytes, sha256, data, import_id, created_at)
            VALUES (%s, 'statement', %s, 'text/csv', 1, %s, %s, %s, %s)
            """,
            (new_id(ledger, "att"), filename, f"sha-{filename}", b"x", import_id, attached_at),
        )
    ledger.commit()

    body = authed_client.get("/api/import-history", params={"limit": 25}).json()

    assert body["next_cursor"] is None
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["import_id"] == import_id
    assert item["status"] == "committed"
    assert item["filename"] == "latest.csv"
    assert item["account"] == "History bank"
    assert item["period"] == {"start": "2026-02-01", "end": "2026-02-28"}
    assert item["check"] == "VERIFIED"
    assert item["counts"]["logged_not_on_statement"] == 4
    assert item["reason"] == "checked"
    assert datetime.fromisoformat(item["created_at"]) == created_at
    assert datetime.fromisoformat(item["committed_at"]) == committed_at
    assert item["undone_at"] is None

    legacy_item = next(
        row
        for row in authed_client.get("/api/imports").json()["items"]
        if row["import_id"] == import_id
    )
    assert set(item) == set(legacy_item)


def test_legacy_imports_route_keeps_its_shape_and_cap(authed_client, ledger):
    base = datetime(2026, 4, 1, tzinfo=UTC)
    created = [
        _add_import(ledger, created_at=base + timedelta(minutes=index)) for index in range(205)
    ]
    ledger.commit()

    legacy = authed_client.get("/api/imports", params={"limit": 500})

    assert legacy.status_code == 200
    body = legacy.json()
    assert "next_cursor" not in body
    assert len(body["items"]) == 200
    assert body["items"][0]["import_id"] == created[-1]


def test_malformed_cursor_is_a_422(authed_client):
    for cursor in (
        "garbage",
        "2026-03-01T00:00:00+00:00|",
        "|imp_missing",
        "not-a-date|imp_missing",
        "2026-03-01|imp_missing",
    ):
        response = authed_client.get("/api/import-history", params={"cursor": cursor})
        assert response.status_code == 422, (cursor, response.text)


def test_history_requires_a_session(client):
    assert client.get("/api/import-history").status_code == 401
