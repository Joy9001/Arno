from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from arno.api.app import create_app
from arno.auth.setup import issue_setup_token
from arno.db.conn import connect
from tests.fixtures.passkeys import complete_setup
from tests.software_authenticator import SoftwareAuthenticator


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


async def _issue_token(dsn: str) -> str:
    async with connect(dsn) as conn:
        return (await issue_setup_token(conn, ttl_hours=1)).token


async def test_first_run_setup_then_passkey_login(client, setup_token, settings):
    state = client.get("/api/auth/state").json()
    assert state["setup_required"] is True
    assert state["authenticated"] is False
    assert state["user"] is None
    assert state["rp_origin"] == settings.rp_origin

    wrong = client.post("/api/auth/setup/begin", json={"token": "not-the-token"})
    assert wrong.status_code == 401
    assert wrong.headers["content-type"].startswith("application/problem+json")

    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    verified = complete_setup(client, setup_token, authenticator)
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


async def test_setup_token_is_consumed_by_setup(client, setup_token, settings):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert complete_setup(client, setup_token, authenticator).status_code == 200

    replay = client.post("/api/auth/setup/begin", json={"token": setup_token})
    assert replay.status_code == 401


def test_login_before_setup_is_a_conflict(client):
    response = client.post("/api/auth/login/begin")

    assert response.status_code == 409
    assert "Setup is not complete" in response.json()["detail"]


async def test_login_challenge_cannot_be_replayed(client, setup_token, settings):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert complete_setup(client, setup_token, authenticator).status_code == 200

    verified, challenge_id, credential = _login(client, authenticator)
    assert verified.status_code == 200

    replay = client.post(
        "/api/auth/login/verify",
        json={"challenge_id": challenge_id, "credential": credential},
    )
    assert replay.status_code == 401


async def test_unregistered_passkey_is_refused(client, setup_token, settings):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert complete_setup(client, setup_token, authenticator).status_code == 200

    stranger = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    begin = client.post("/api/auth/login/begin")
    credential = stranger.authentication_credential(begin.json()["options"])
    response = client.post(
        "/api/auth/login/verify",
        json={"challenge_id": begin.json()["challenge_id"], "credential": credential},
    )
    assert response.status_code == 401


async def test_sign_counter_regression_is_refused(client, setup_token, settings, db):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert complete_setup(client, setup_token, authenticator).status_code == 200
    assert _login(client, authenticator)[0].status_code == 200

    await db.execute("UPDATE webauthn_credentials SET sign_count = 999")

    refused, _, _ = _login(client, authenticator)
    assert refused.status_code == 401

    stored = (await db.fetchrow("SELECT sign_count FROM webauthn_credentials"))["sign_count"]
    assert stored == 999


async def test_session_cookie_is_httponly_secure_and_samesite_strict(
    clean_database, secure_settings
):
    del clean_database
    app = create_app(secure_settings)
    with TestClient(app) as client:
        token = await _issue_token(secure_settings.database_url)
        authenticator = SoftwareAuthenticator(secure_settings.rp_id, secure_settings.rp_origin)
        response = complete_setup(client, token, authenticator)

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie


async def test_only_the_session_hash_is_stored(client, setup_token, settings, db):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert complete_setup(client, setup_token, authenticator).status_code == 200

    token = client.cookies["arno_session"]
    row = await db.fetchrow("SELECT token_hash FROM sessions")

    assert row["token_hash"] == hashlib.sha256(token.encode()).digest()
    assert token.encode() != row["token_hash"]


async def test_setup_token_can_be_reissued_while_setup_is_open(client, settings):
    async with connect(client.app.state.settings.database_url) as conn:
        token = (await issue_setup_token(conn, ttl_hours=1)).token
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert complete_setup(client, token, authenticator).status_code == 200


async def _setup_authenticator(client, setup_token, settings) -> SoftwareAuthenticator:
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert complete_setup(client, setup_token, authenticator).status_code == 200
    return authenticator


def _assert_step_up_required(response) -> None:
    assert response.status_code == 403, response.text
    assert response.headers["x-arno-step-up"] == "passkey"
    assert "passkey" in response.json()["detail"]


async def test_passkey_changes_and_enrollment_need_a_fresh_passkey(client, setup_token, settings):
    authenticator = await _setup_authenticator(client, setup_token, settings)
    passkeys = client.get("/api/auth/passkeys").json()["passkeys"]
    assert len(passkeys) == 1
    passkey_id = passkeys[0]["id"]

    # A session from setup or login is authenticated but not stepped up.
    _assert_step_up_required(
        client.patch(f"/api/auth/passkeys/{passkey_id}", json={"name": "Kitchen laptop"})
    )
    _assert_step_up_required(client.delete(f"/api/auth/passkeys/{passkey_id}"))
    _assert_step_up_required(client.post("/api/auth/enrollments"))

    begin = client.post("/api/auth/reverify/begin")
    assert begin.status_code == 200, begin.text
    credential = authenticator.authentication_credential(begin.json()["options"])
    reverified = client.post(
        "/api/auth/reverify/verify",
        json={"challenge_id": begin.json()["challenge_id"], "credential": credential},
    )
    assert reverified.status_code == 200, reverified.text

    renamed = client.patch(f"/api/auth/passkeys/{passkey_id}", json={"name": "Kitchen laptop"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["passkey"]["name"] == "Kitchen laptop"

    # The step-up carried: the last-passkey guard, not a stale session, is the refusal now.
    deleted = client.delete(f"/api/auth/passkeys/{passkey_id}")
    assert deleted.status_code == 409
    assert "last passkey" in deleted.json()["detail"]


async def test_login_refuses_a_credential_from_another_origin(client, setup_token, settings):
    authenticator = await _setup_authenticator(client, setup_token, settings)

    begin = client.post("/api/auth/login/begin")
    authenticator.origin = "https://evil.example"
    try:
        credential = authenticator.authentication_credential(begin.json()["options"])
    finally:
        authenticator.origin = settings.rp_origin

    refused = client.post(
        "/api/auth/login/verify",
        json={"challenge_id": begin.json()["challenge_id"], "credential": credential},
    )

    assert refused.status_code == 401


async def test_login_refuses_a_credential_for_another_relying_party(client, setup_token, settings):
    authenticator = await _setup_authenticator(client, setup_token, settings)

    begin = client.post("/api/auth/login/begin")
    authenticator.rp_id = "evil.example"
    try:
        credential = authenticator.authentication_credential(begin.json()["options"])
    finally:
        authenticator.rp_id = settings.rp_id

    refused = client.post(
        "/api/auth/login/verify",
        json={"challenge_id": begin.json()["challenge_id"], "credential": credential},
    )

    assert refused.status_code == 401


async def test_login_refuses_an_authenticator_without_user_verification(
    client, setup_token, settings
):
    authenticator = await _setup_authenticator(client, setup_token, settings)

    begin = client.post("/api/auth/login/begin")
    authenticator.user_verified = False
    try:
        credential = authenticator.authentication_credential(begin.json()["options"])
    finally:
        authenticator.user_verified = True

    refused = client.post(
        "/api/auth/login/verify",
        json={"challenge_id": begin.json()["challenge_id"], "credential": credential},
    )

    assert refused.status_code == 401


async def test_the_sign_count_is_persisted_across_two_logins(client, setup_token, settings, db):
    authenticator = await _setup_authenticator(client, setup_token, settings)
    stored = await db.fetchrow("SELECT sign_count FROM webauthn_credentials")
    assert stored["sign_count"] == 0

    assert _login(client, authenticator)[0].status_code == 200
    stored = await db.fetchrow("SELECT sign_count FROM webauthn_credentials")
    assert stored["sign_count"] == 1

    assert _login(client, authenticator)[0].status_code == 200
    stored = await db.fetchrow("SELECT sign_count FROM webauthn_credentials")
    assert stored["sign_count"] == 2
    assert authenticator.sign_count == 2


async def test_a_reverify_challenge_cannot_answer_a_login(client, setup_token, settings):
    authenticator = await _setup_authenticator(client, setup_token, settings)

    begin = client.post("/api/auth/reverify/begin")
    assert begin.status_code == 200, begin.text
    credential = authenticator.authentication_credential(begin.json()["options"])

    refused = client.post(
        "/api/auth/login/verify",
        json={"challenge_id": begin.json()["challenge_id"], "credential": credential},
    )

    assert refused.status_code == 401


async def _reverify(client, authenticator: SoftwareAuthenticator):
    begin = client.post("/api/auth/reverify/begin")
    assert begin.status_code == 200, begin.text
    credential = authenticator.authentication_credential(begin.json()["options"])
    return client.post(
        "/api/auth/reverify/verify",
        json={"challenge_id": begin.json()["challenge_id"], "credential": credential},
    )


async def test_an_already_registered_credential_is_refused_cleanly(
    client, setup_token, settings, db
):
    authenticator = await _setup_authenticator(client, setup_token, settings)
    assert (await _reverify(client, authenticator)).status_code == 200

    enrollment = client.post("/api/auth/enrollments")
    assert enrollment.status_code == 200, enrollment.text
    token = enrollment.json()["link"].split("#t=", 1)[1]

    begin = client.post("/api/auth/setup/begin", json={"token": token})
    assert begin.status_code == 200, begin.text
    credential = authenticator.registration_credential(begin.json()["options"])
    refused = client.post(
        "/api/auth/setup/verify",
        json={"challenge_id": begin.json()["challenge_id"], "credential": credential},
    )

    assert refused.status_code == 400, refused.text
    assert "already registered" in refused.json()["detail"]
    assert await db.fetchval("SELECT count(*) FROM webauthn_credentials") == 1


async def test_an_expired_login_challenge_is_refused(client, setup_token, settings, db):
    authenticator = await _setup_authenticator(client, setup_token, settings)

    begin = client.post("/api/auth/login/begin")
    options = begin.json()["options"]
    await db.execute(
        "UPDATE webauthn_challenges SET expires_at = now() - interval '1 second' WHERE id = $1",
        begin.json()["challenge_id"],
    )
    credential = authenticator.authentication_credential(options)
    refused = client.post(
        "/api/auth/login/verify",
        json={"challenge_id": begin.json()["challenge_id"], "credential": credential},
    )

    assert refused.status_code == 401
