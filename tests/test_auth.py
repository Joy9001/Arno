from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from arno.api import auth
from arno.api.app import create_app
from arno.api.deps import COOKIE_NAME, sign_session
from tests.conftest import OWNER_EMAIL, PASSPHRASE

PROTECTED = "/api/settings"
SANDBOX = "10.89.250.0/24"


@pytest.fixture(autouse=True)
def _no_failure_delay(monkeypatch):
    monkeypatch.setattr(auth, "FAIL_DELAY_SECONDS", 0)


def _client(settings, clean_database, **changes) -> TestClient:
    del clean_database
    return TestClient(create_app(replace(settings, **changes)))


def test_the_owner_header_is_accepted(client):
    assert client.get(PROTECTED).status_code == 401
    state = client.get("/api/auth/state", headers={"Tailscale-User-Login": OWNER_EMAIL}).json()
    assert state == {
        "authenticated": True,
        "method": "tailscale",
        "login": OWNER_EMAIL,
        "passphrase_enabled": True,
    }
    padded = {"Tailscale-User-Login": f"  {OWNER_EMAIL.upper()} "}
    assert client.get(PROTECTED, headers=padded).status_code == 200


def test_another_or_missing_header_is_refused(client):
    assert (
        client.get(PROTECTED, headers={"Tailscale-User-Login": "x@example.com"}).status_code == 401
    )
    assert client.get(PROTECTED, headers={"Tailscale-User-Login": ""}).status_code == 401
    state = client.get("/api/auth/state").json()
    assert state["authenticated"] is False and state["method"] is None


def test_nothing_configured_fails_closed(settings, clean_database):
    with _client(settings, clean_database, owner_email=None, passphrase=None) as client:
        response = client.get(PROTECTED, headers={"Tailscale-User-Login": OWNER_EMAIL})
        assert response.status_code == 401
        assert "ARNO_OWNER_EMAIL" in response.text and "ARNO_PASSPHRASE" in response.text


def test_an_unset_owner_email_never_matches_an_empty_header(settings, clean_database):
    with _client(settings, clean_database, owner_email=None) as client:
        assert client.get(PROTECTED, headers={"Tailscale-User-Login": ""}).status_code == 401


def test_the_passphrase_sets_a_strict_http_only_cookie(settings, clean_database):
    with _client(settings, clean_database, cookie_secure=True) as client:
        wrong = client.post("/api/auth/login", json={"passphrase": "nope"})
        assert wrong.status_code == 401
        assert COOKIE_NAME not in wrong.headers.get("set-cookie", "")

        ok = client.post("/api/auth/login", json={"passphrase": PASSPHRASE})
        assert ok.status_code == 204
        cookie = ok.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie
        assert f"max-age={settings.session_ttl_days * 86400}" in cookie
        # The Secure cookie is not sent back over http, so replay it by hand.
        token = ok.cookies[COOKIE_NAME]
        client.cookies.set(COOKIE_NAME, token)
        assert client.get(PROTECTED).status_code == 200
        state = client.get("/api/auth/state").json()
        assert state["method"] == "passphrase"


def test_a_non_ascii_passphrase_is_compared_not_crashed(settings, clean_database):
    with _client(settings, clean_database, passphrase="pässwörd ₹") as client:
        assert client.post("/api/auth/login", json={"passphrase": "pässwörd ₹"}).status_code == 204
        assert client.post("/api/auth/login", json={"passphrase": "passwort"}).status_code == 401


def test_a_tampered_or_expired_cookie_is_refused(client):
    token = sign_session(PASSPHRASE, 1)
    expires, mac = token.split(".")
    for bad in (
        f"{int(expires) + 999999}.{mac}",
        f"{expires}.{mac[:-1]}0" if mac[-1] != "0" else f"{expires}.{mac[:-1]}1",
        sign_session("another passphrase", 1),
        sign_session(PASSPHRASE, -1),
        "garbage",
        "",
    ):
        client.cookies.set(COOKIE_NAME, bad)
        assert client.get(PROTECTED).status_code == 401, bad
    client.cookies.set(COOKIE_NAME, token)
    assert client.get(PROTECTED).status_code == 200


def test_login_is_refused_when_no_passphrase_is_set(settings, clean_database):
    with _client(settings, clean_database, passphrase=None) as client:
        assert client.post("/api/auth/login", json={"passphrase": ""}).status_code == 401
        token = sign_session("", 1)
        client.cookies.set(COOKIE_NAME, token)
        assert client.get(PROTECTED).status_code == 401
        assert client.get("/api/auth/state").json()["passphrase_enabled"] is False


def test_logout_clears_the_cookie(client):
    response = client.post("/api/auth/logout")
    assert response.status_code == 204
    assert f'{COOKIE_NAME}=""' in response.headers["set-cookie"]


def test_the_sandbox_network_cannot_borrow_the_owner_header(settings, clean_database):
    headers = {"Tailscale-User-Login": OWNER_EMAIL}
    token = sign_session(PASSPHRASE, 1)
    app = create_app(replace(settings, sandbox_subnet=SANDBOX))
    with TestClient(app, client=("10.89.250.7", 5000)) as inside:
        assert inside.get(PROTECTED, headers=headers).status_code == 403
        inside.cookies.set(COOKIE_NAME, token)
        assert inside.get(PROTECTED).status_code == 403
        assert inside.post("/api/auth/login", json={"passphrase": PASSPHRASE}).status_code == 403
    with TestClient(app, client=("10.89.251.7", 5000)) as outside:
        assert outside.get(PROTECTED, headers=headers).status_code == 200


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_a_cross_site_request_is_refused(session_client, site):
    assert session_client.get(PROTECTED, headers={"Sec-Fetch-Site": site}).status_code == 403
    assert (
        session_client.post("/api/auth/logout", headers={"Sec-Fetch-Site": site}).status_code == 403
    )
    login = {"Sec-Fetch-Site": site}
    assert (
        session_client.post("/api/auth/login", json={"passphrase": PASSPHRASE}, headers=login)
    ).status_code == 403


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_a_same_origin_request_is_allowed(session_client, site):
    assert session_client.get(PROTECTED, headers={"Sec-Fetch-Site": site}).status_code == 200


def test_an_unsafe_request_from_another_origin_is_refused_without_fetch_metadata(session_client):
    hostile = {"Origin": "https://evil.example"}
    assert session_client.post("/api/auth/logout", headers=hostile).status_code == 403
    assert session_client.post("/api/auth/logout", headers={"Origin": "null"}).status_code == 403
    own = {"Origin": "http://testserver"}
    assert session_client.post("/api/auth/logout", headers=own).status_code == 204
    assert session_client.get(PROTECTED, headers=hostile).status_code == 200
