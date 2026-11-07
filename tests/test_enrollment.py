from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from fin_agent.api.app import create_app
from fin_agent.auth.enrollment import enrollment_link, enrollment_message
from fin_agent.auth.setup import (
    ensure_setup_token,
    issue_add_device_token,
    issue_setup_token,
    resolve_setup_token,
)
from fin_agent.cli import app as cli_app
from fin_agent.db.conn import connect
from tests.software_authenticator import SoftwareAuthenticator
from tests.test_auth import _complete_setup


def _token_from_link(link: str) -> str:
    assert "#t=" in link
    return link.split("#t=", 1)[1]


async def _mark_fresh(client: TestClient) -> None:
    """Simulate the fresh passkey check these tests are not exercising."""
    async with connect(client.app.state.settings.database_url) as conn:
        await conn.execute("UPDATE sessions SET reverified_at = now() WHERE revoked_at IS NULL")


async def _create_enrollment(client: TestClient) -> dict:
    await _mark_fresh(client)
    response = client.post("/api/auth/enrollments")
    assert response.status_code == 200, response.text
    return response.json()


def _redeem_add_device(client: TestClient, token: str, authenticator: SoftwareAuthenticator):
    begin = client.post("/api/auth/setup/begin", json={"token": token})
    assert begin.status_code == 200, begin.text
    assert begin.json()["kind"] == "add_device"
    credential = authenticator.registration_credential(begin.json()["options"])
    return client.post(
        "/api/auth/setup/verify",
        json={"challenge_id": begin.json()["challenge_id"], "credential": credential},
    )


def _passkeys(client: TestClient) -> list[dict]:
    response = client.get("/api/auth/passkeys")
    assert response.status_code == 200, response.text
    return response.json()["passkeys"]


def test_enrollment_link_uses_the_configured_origin():
    assert enrollment_link("https://example.ts.net", "abc") == "https://example.ts.net/setup#t=abc"
    assert (
        enrollment_link("https://example.ts.net/", "a b") == "https://example.ts.net/setup#t=a%20b"
    )

    message = enrollment_message("https://example.ts.net", "abc", title="Open this")
    assert "https://example.ts.net/setup#t=abc" in message
    assert "Setup token (paste fallback): abc" in message
    assert any(block in message for block in "█▀▄")


async def test_fragment_token_completes_first_run_setup(client, setup_token, settings):
    link = enrollment_link(settings.rp_origin, setup_token)
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)

    response = _complete_setup(client, _token_from_link(link), authenticator)

    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "first_run"


async def test_restart_keeps_a_valid_first_run_token(client, settings):
    async with connect(settings.database_url) as conn:
        await conn.execute("DELETE FROM setup_tokens")
        first = await ensure_setup_token(conn, ttl_hours=1)
        second = await ensure_setup_token(conn, ttl_hours=1)
        assert first is not None
        assert second is None
        assert await resolve_setup_token(conn, first) is not None

    restarted = create_app(settings)
    with TestClient(restarted) as other:
        begin = other.post("/api/auth/setup/begin", json={"token": first})
        assert begin.status_code == 200, begin.text


def test_add_device_requires_a_session(app):
    with TestClient(app) as fresh:
        response = fresh.post("/api/auth/enrollments")

    assert response.status_code == 401


async def test_add_device_registers_a_second_passkey(client, app, setup_token, settings, db):
    first = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, first).status_code == 200

    created = await _create_enrollment(client)
    assert created["link"].startswith(f"{settings.rp_origin}/setup#t=")
    assert created["kind"] == "add_device"

    row = await db.fetchrow(
        "SELECT kind, user_id, consumed_at FROM setup_tokens WHERE id = $1",
        created["id"],
    )
    assert row["kind"] == "add_device"
    assert row["user_id"] is not None
    assert row["consumed_at"] is None

    token = _token_from_link(created["link"])
    second = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    with TestClient(app) as new_device:
        assert new_device.get("/api/auth/session").status_code == 401
        response = _redeem_add_device(new_device, token, second)

        assert response.status_code == 200, response.text
        assert response.json()["kind"] == "add_device"
        assert new_device.get("/api/auth/session").status_code == 200
        assert len(_passkeys(new_device)) == 2

    assert len(_passkeys(client)) == 2

    with TestClient(app) as third:
        replay = third.post("/api/auth/setup/begin", json={"token": token})
    assert replay.status_code == 401


async def test_add_device_token_expires(client, app, setup_token, settings, db):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200
    created = await _create_enrollment(client)

    await db.execute(
        "UPDATE setup_tokens SET expires_at = now() - interval '1 minute' WHERE id = $1",
        created["id"],
    )

    with TestClient(app) as new_device:
        response = new_device.post(
            "/api/auth/setup/begin",
            json={"token": _token_from_link(created["link"])},
        )
    assert response.status_code == 401


async def test_add_device_token_dies_when_sessions_are_revoked(client, app, setup_token, settings):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200
    created = await _create_enrollment(client)

    assert client.post("/api/auth/logout-all").status_code == 204

    with TestClient(app) as new_device:
        response = new_device.post(
            "/api/auth/setup/begin",
            json={"token": _token_from_link(created["link"])},
        )
    assert response.status_code == 401


async def test_add_device_token_dies_on_passkey_reset(client, app, setup_token, settings):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200
    created = await _create_enrollment(client)

    result = await asyncio.to_thread(CliRunner().invoke, cli_app, ["passkeys", "reset"])
    assert result.exit_code == 0, result.output

    with TestClient(app) as new_device:
        response = new_device.post(
            "/api/auth/setup/begin",
            json={"token": _token_from_link(created["link"])},
        )
    assert response.status_code == 401


async def test_first_run_token_cannot_add_a_device_after_setup(client, app, setup_token, settings):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200

    async with connect(settings.database_url) as conn:
        late_token = (await issue_setup_token(conn, ttl_hours=1)).token

    with TestClient(app) as new_device:
        response = new_device.post("/api/auth/setup/begin", json={"token": late_token})

    assert response.status_code == 409
    assert "already complete" in response.json()["detail"]


async def test_add_device_token_cannot_do_first_run_setup(client, app, setup_token, settings, db):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200

    user_id = (await db.fetchrow("SELECT id FROM users"))["id"]
    async with connect(settings.database_url) as conn:
        await conn.execute("DELETE FROM webauthn_credentials")
        issued = await issue_add_device_token(conn, user_id, ttl_minutes=10)

    with TestClient(app) as new_device:
        response = new_device.post("/api/auth/setup/begin", json={"token": issued.token})

    assert response.status_code == 409
    assert "not complete" in response.json()["detail"]


async def test_deleting_the_last_passkey_is_refused(client, app, setup_token, settings):
    first = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, first).status_code == 200
    await _mark_fresh(client)

    passkeys = _passkeys(client)
    assert len(passkeys) == 1
    refused = client.delete(f"/api/auth/passkeys/{passkeys[0]['id']}")
    assert refused.status_code == 409
    assert "last passkey" in refused.json()["detail"]

    created = await _create_enrollment(client)
    with TestClient(app) as new_device:
        second = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
        assert (
            _redeem_add_device(new_device, _token_from_link(created["link"]), second).status_code
            == 200
        )

    passkeys = _passkeys(client)
    assert len(passkeys) == 2
    assert client.delete(f"/api/auth/passkeys/{passkeys[0]['id']}").status_code == 204
    last = client.delete(f"/api/auth/passkeys/{passkeys[1]['id']}")
    assert last.status_code == 409


async def test_passkeys_can_be_listed_and_renamed(client, setup_token, settings):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200
    await _mark_fresh(client)

    passkey = _passkeys(client)[0]
    renamed = client.patch(
        f"/api/auth/passkeys/{passkey['id']}",
        json={"name": "Work laptop"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["passkey"]["name"] == "Work laptop"
    assert _passkeys(client)[0]["name"] == "Work laptop"

    assert client.patch("/api/auth/passkeys/nope", json={"name": "x"}).status_code == 404
    assert client.delete("/api/auth/passkeys/nope").status_code == 404


def test_passkey_management_requires_a_session(app):
    with TestClient(app) as fresh:
        assert fresh.get("/api/auth/passkeys").status_code == 401
        assert fresh.post("/api/auth/enrollments").status_code == 401
        assert fresh.delete("/api/auth/passkeys/nope").status_code == 401
        assert fresh.patch("/api/auth/passkeys/nope", json={"name": "x"}).status_code == 401


async def test_enrollment_is_bound_to_the_user_and_lasts_ten_minutes(
    client, setup_token, settings, db
):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200
    user_id = (await db.fetchrow("SELECT id FROM users"))["id"]

    created = await _create_enrollment(client)
    expires_at = datetime.fromisoformat(created["expires_at"])
    remaining = expires_at - datetime.now(UTC)

    assert timedelta(minutes=8) < remaining <= timedelta(minutes=10, seconds=5)
    row = await db.fetchrow(
        "SELECT kind, user_id FROM setup_tokens WHERE id = $1",
        created["id"],
    )
    assert row == {"kind": "add_device", "user_id": user_id}
