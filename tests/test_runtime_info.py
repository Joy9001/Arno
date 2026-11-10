from __future__ import annotations

import json

from arno.api.runtime_info import ROLES, payload


def _clear_roles(monkeypatch) -> None:
    for prefix in ROLES:
        monkeypatch.delenv(f"{prefix}_BASE_URL", raising=False)
        monkeypatch.delenv(f"{prefix}_MODEL", raising=False)


def test_runtime_info_hides_credentials_path_and_query(monkeypatch):
    _clear_roles(monkeypatch)
    monkeypatch.setenv(
        "CHAT_BASE_URL",
        "https://user:sup3r-secret@models.example.com:8443/v1?api_key=sk-live-123#frag",
    )
    monkeypatch.setenv("CHAT_MODEL", "deepseek-v4.1-flash")

    result = payload()
    chat = next(role for role in result["roles"] if role["role"] == "CHAT")

    assert chat == {
        "role": "CHAT",
        "configured": True,
        "host": "models.example.com",
        "model": "deepseek-v4.1-flash",
    }
    rendered = json.dumps(result)
    for leaked in ("sup3r-secret", "sk-live-123", "user:", "/v1", "api_key"):
        assert leaked not in rendered


def test_runtime_info_lists_every_unconfigured_role_explicitly(monkeypatch):
    _clear_roles(monkeypatch)

    result = payload()

    assert [role["role"] for role in result["roles"]] == list(ROLES)
    assert all(role["configured"] is False for role in result["roles"])
    assert all(role["host"] is None and role["model"] is None for role in result["roles"])


def test_runtime_info_unsafe_model_label_is_omitted(monkeypatch):
    _clear_roles(monkeypatch)
    monkeypatch.setenv("VISION_BASE_URL", "https://vision.example.com/v1")
    monkeypatch.setenv("VISION_MODEL", "model with a secret-looking value")

    vision = next(role for role in payload()["roles"] if role["role"] == "VISION")

    assert vision["configured"] is True
    assert vision["host"] == "vision.example.com"
    assert vision["model"] is None


def test_phoenix_ui_url_is_only_the_explicit_operator_url(monkeypatch):
    _clear_roles(monkeypatch)
    monkeypatch.delenv("ARNO_PHOENIX_UI_URL", raising=False)
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix:6006")
    assert payload()["phoenix_ui_url"] is None

    monkeypatch.setenv("ARNO_PHOENIX_UI_URL", "https://admin:pw@phoenix.example.com/ui?token=abc")
    assert payload()["phoenix_ui_url"] == "https://phoenix.example.com/ui"


def test_runtime_info_requires_a_session(client):
    assert client.get("/api/runtime-info").status_code == 401


def test_runtime_info_route_returns_host_and_model(authed_client, monkeypatch):
    monkeypatch.setenv("CHAT_BASE_URL", "https://user:pw@models.example.com/v1?key=secret")
    monkeypatch.setenv("CHAT_MODEL", "deepseek-v4.1-flash")

    response = authed_client.get("/api/runtime-info")

    assert response.status_code == 200
    chat = next(role for role in response.json()["roles"] if role["role"] == "CHAT")
    assert chat["host"] == "models.example.com"
    assert chat["model"] == "deepseek-v4.1-flash"
    assert "secret" not in response.text
