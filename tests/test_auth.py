from __future__ import annotations

import hashlib

import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from fin_agent.api.app import create_app
from fin_agent.auth.setup import issue_setup_token
from tests.software_authenticator import SoftwareAuthenticator


def _complete_setup(client: TestClient, token: str, authenticator: SoftwareAuthenticator):
    begin = client.post("/api/auth/setup/begin", json={"token": token})
    assert begin.status_code == 200, begin.text
    challenge_id = begin.json()["challenge_id"]
    credential = authenticator.registration_credential(begin.json()["options"])
    return client.post(
        "/api/auth/setup/verify",
        json={"challenge_id": challenge_id, "credential": credential},
    )


def _login(client: TestClient, authenticator: SoftwareAuthenticator):
    begin = client.post("/api/auth/login/begin")
    assert begin.status_code == 200, begin.text
    challenge_id = begin.json()["challenge_id"]
    credential = authenticator.authentication_credential(begin.json()["options"])
    return (
        client.post(
            "/api/auth/login/verify",
            json={"challenge_id": challenge_id, "credential": credential},
        ),
        challenge_id,
        credential,
    )


def _issue_token(dsn: str) -> str:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return issue_setup_token(conn, ttl_hours=1)


def test_first_run_setup_then_passkey_login(client, setup_token, settings):
    state = client.get("/api/auth/state").json()
    assert state == {"setup_required": True, "authenticated": False, "user": None}

    wrong = client.post("/api/auth/setup/begin", json={"token": "not-the-token"})
    assert wrong.status_code == 401
    assert wrong.headers["content-type"].startswith("application/problem+json")

    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    verified = _complete_setup(client, setup_token, authenticator)
    assert verified.status_code == 200, verified.text
    assert verified.json()["user"]["display_name"] == "You"

    state = client.get("/api/auth/state").json()
    assert state["setup_required"] is False
    assert state["authenticated"] is True
    assert client.get("/api/auth/session").status_code == 200

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/session").status_code == 401
    assert client.get("/api/auth/state").json()["authenticated"] is False

    logged_in, _, _ = _login(client, authenticator)
    assert logged_in.status_code == 200, logged_in.text
    assert client.get("/api/auth/session").json()["user"]["display_name"] == "You"


def test_setup_token_is_consumed_by_setup(client, setup_token, settings):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200

    replay = client.post("/api/auth/setup/begin", json={"token": setup_token})
    assert replay.status_code == 401


def test_login_before_setup_is_a_conflict(client):
    response = client.post("/api/auth/login/begin")

    assert response.status_code == 409
    assert "Setup is not complete" in response.json()["detail"]


def test_login_challenge_cannot_be_replayed(client, setup_token, settings):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200

    verified, challenge_id, credential = _login(client, authenticator)
    assert verified.status_code == 200

    replay = client.post(
        "/api/auth/login/verify",
        json={"challenge_id": challenge_id, "credential": credential},
    )
    assert replay.status_code == 401


def test_unregistered_passkey_is_refused(client, setup_token, settings):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200

    stranger = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    begin = client.post("/api/auth/login/begin")
    credential = stranger.authentication_credential(begin.json()["options"])
    response = client.post(
        "/api/auth/login/verify",
        json={"challenge_id": begin.json()["challenge_id"], "credential": credential},
    )
    assert response.status_code == 401


def test_sign_counter_regression_is_refused(client, setup_token, settings, db):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200
    assert _login(client, authenticator)[0].status_code == 200

    db.execute("UPDATE webauthn_credentials SET sign_count = 999")
    db.commit()

    refused, _, _ = _login(client, authenticator)
    assert refused.status_code == 401

    stored = db.execute("SELECT sign_count FROM webauthn_credentials").fetchone()["sign_count"]
    assert stored == 999


def test_session_cookie_is_httponly_secure_and_samesite_strict(secure_settings):
    app = create_app(secure_settings)
    with TestClient(app) as client:
        token = _issue_token(secure_settings.database_url)
        authenticator = SoftwareAuthenticator(secure_settings.rp_id, secure_settings.rp_origin)
        response = _complete_setup(client, token, authenticator)

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie


def test_only_the_session_hash_is_stored(client, setup_token, settings, db):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200

    token = client.cookies["fin_agent_session"]
    row = db.execute("SELECT token_hash FROM sessions").fetchone()

    assert row["token_hash"] == hashlib.sha256(token.encode()).digest()
    assert token.encode() != row["token_hash"]


def test_setup_token_can_be_reissued_while_setup_is_open(client, settings):
    with psycopg.connect(client.app.state.settings.database_url, row_factory=dict_row) as conn:
        token = issue_setup_token(conn, ttl_hours=1)
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, token, authenticator).status_code == 200
