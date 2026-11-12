"""Explicit provider/billing profiles, native adapters, and ChatGPT credential persistence."""

from __future__ import annotations

import base64
import json

import httpx2
import pytest
from pydantic_ai.exceptions import UserError
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openai_chatgpt import (
    OpenAIChatGPTCredentials,
    OpenAIChatGPTProvider,
)

from arno.agent.chatgpt_auth import (
    DatabaseChatGPTCredentialSource,
    chatgpt_sign_in,
)
from arno.agent.metering import MeteredModel
from arno.agent.models import (
    RoleConfig,
    WindowLimited,
    backup_model,
    batch_model,
    build_model,
    chat_config,
    role_config,
)
from arno.agent.provider_usage import BillingMetadataModel


def _concrete(model) -> object:
    assert isinstance(model, MeteredModel)
    assert isinstance(model.wrapped, BillingMetadataModel)
    return model.wrapped.wrapped


def test_role_config_keeps_the_four_positional_fields_and_defaults_the_profile():
    config = RoleConfig("http://127.0.0.1:9/v1", "key", "model", 1000)

    assert config.context_window == 1000
    assert config.provider == "openai"
    assert config.billing_mode == "metered"
    assert config.role == "chat"


def test_env_parser_fills_explicit_role_provider_and_billing_mode(monkeypatch):
    monkeypatch.setenv("CHAT_BASE_URL", "https://opencode.ai/zen/v1")
    monkeypatch.setenv("CHAT_PROVIDER", "opencode")
    monkeypatch.setenv("CHAT_BILLING_MODE", "local")
    monkeypatch.setenv("CHAT_MODEL", "gpt-5")

    config = chat_config()

    assert config.provider == "opencode"
    assert config.billing_mode == "local"
    assert config.role == "chat"


def test_role_prefix_fills_the_workload_name_and_keeps_metered_by_default(monkeypatch):
    monkeypatch.delenv("VISION_PROVIDER", raising=False)
    monkeypatch.delenv("VISION_BILLING_MODE", raising=False)

    config = role_config("VISION")

    assert config.provider == "openai"
    assert config.billing_mode == "metered"
    assert config.role == "vision"


def test_openai_compatible_build_disables_sdk_retries_and_meters_the_native_model():
    model = build_model(RoleConfig("http://127.0.0.1:9/v1", "key", "gpt-5", 1000))
    concrete = _concrete(model)

    assert isinstance(concrete, OpenAIChatModel)
    assert concrete.provider is not None
    assert concrete.provider.client.max_retries == 0
    assert isinstance(model, MeteredModel)
    assert model.provider_name == "openai"


def test_anthropic_profile_selects_the_native_messages_adapter():
    model = build_model(
        RoleConfig(
            "http://127.0.0.1:9/v1",
            "key",
            "claude-sonnet-5",
            1000,
            provider="anthropic",
        )
    )

    assert isinstance(_concrete(model), AnthropicModel)


def test_openai_responses_profile_selects_the_native_responses_adapter():
    model = build_model(
        RoleConfig(
            "https://api.openai.com/v1",
            "key",
            "gpt-5",
            1000,
            provider="openai-responses",
        )
    )

    assert isinstance(_concrete(model), OpenAIResponsesModel)


def test_openrouter_profile_selects_the_native_router_adapter():
    model = build_model(
        RoleConfig(
            "http://127.0.0.1:9/v1",
            "key",
            "openai/gpt-5",
            1000,
            provider="openrouter",
        )
    )

    assert isinstance(_concrete(model), OpenRouterModel)


def test_chatgpt_profile_selects_the_native_plan_provider():
    model = build_model(
        RoleConfig(
            "http://127.0.0.1:9/v1",
            "unused",
            "gpt-5",
            1000,
            provider="openai-chatgpt",
            billing_mode="metered",
        )
    )
    concrete = _concrete(model)

    assert isinstance(concrete, OpenAIResponsesModel)
    assert concrete.provider is not None
    assert concrete.provider.name == "openai-chatgpt"
    assert concrete.provider.client.max_retries == 0


def test_ollama_profile_keeps_the_local_wire_and_still_needs_an_explicit_billing_mode():
    model = build_model(
        RoleConfig(
            "http://127.0.0.1:11434/v1",
            "not-needed",
            "llama3",
            1000,
            provider="ollama",
        )
    )
    concrete = _concrete(model)

    assert isinstance(concrete, OpenAIChatModel)
    assert concrete.provider is not None
    assert concrete.provider.name == "ollama"


def test_backup_keeps_its_window_wrapper_around_the_metered_model(monkeypatch):
    monkeypatch.setenv("CHAT_FALLBACK_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("CHAT_FALLBACK_MODEL", "gpt-5")
    monkeypatch.setenv("CHAT_FALLBACK_API_KEY", "key")

    model = backup_model()

    assert model is not None
    assert isinstance(model, WindowLimited)
    assert isinstance(model.wrapped, MeteredModel)


def test_batch_model_stays_a_native_unmetered_batch_model(monkeypatch):
    monkeypatch.setenv("BATCH_PROVIDER", "anthropic")
    monkeypatch.setenv("BATCH_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("BATCH_API_KEY", "key")

    model = batch_model()

    assert isinstance(model, AnthropicModel)
    assert not isinstance(model, MeteredModel)


def test_chatgpt_sign_in_builds_the_official_authorization_url_without_io():
    url, flow = chatgpt_sign_in()

    assert url.startswith("https://auth.openai.com/api/accounts/authorize?")
    assert "chatgpt.tokens.use.direct" in url
    assert flow.client_id is None


async def test_chatgpt_credentials_persist_and_revoke():
    source = DatabaseChatGPTCredentialSource()
    credentials = OpenAIChatGPTCredentials(
        client_id="client-1",
        access_token="access-1",
        refresh_token="refresh-1",
        id_token="id-1",
        scope="openid chatgpt.tokens.use.direct",
    )

    try:
        await source.revoke()
        with pytest.raises(UserError):
            await source.load()
        await source.save(credentials)
        loaded = await source.load()
        assert loaded.client_id == "client-1"
        assert loaded.refresh_token == "refresh-1"
        assert loaded.plan_usage_granted is True
        assert await source.revoke() is True
        assert await source.revoke() is False
        with pytest.raises(UserError):
            await source.load()
    finally:
        await source.revoke()


async def test_chatgpt_refresh_rotates_and_saves_through_the_provider():
    source = DatabaseChatGPTCredentialSource()
    expired = _jwt(exp=1)
    fresh = _jwt(exp=4_102_444_800)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.host == "auth.openai.com"
        return httpx2.Response(
            200,
            json={"access_token": fresh, "refresh_token": "refresh-2"},
        )

    try:
        await source.revoke()
        await source.save(
            OpenAIChatGPTCredentials(
                client_id="client-1",
                access_token=expired,
                refresh_token="refresh-1",
                scope="openid chatgpt.tokens.use.direct",
            )
        )
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        provider = OpenAIChatGPTProvider(credential_source=source, http_client=client)
        try:
            credentials, _replay = await provider._prepare_request_credentials()
        finally:
            await client.aclose()
        assert credentials.access_token == fresh
        loaded = await source.load()
        assert loaded.refresh_token == "refresh-2"
        assert loaded.access_token == fresh
    finally:
        await source.revoke()


def _jwt(*, exp: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"e30.{payload}.signature"


async def test_disconnect_cannot_be_undone_by_an_inflight_refresh():
    source = DatabaseChatGPTCredentialSource()
    credentials = OpenAIChatGPTCredentials(
        client_id="race-client",
        access_token="before",
        refresh_token="refresh-before",
        scope="openid chatgpt.tokens.use.direct",
    )
    await source.save(credentials)
    await source.load()
    await DatabaseChatGPTCredentialSource().revoke()
    with pytest.raises(UserError, match="disconnected"):
        await source.save(credentials)
    with pytest.raises(UserError):
        await DatabaseChatGPTCredentialSource().load()


async def test_dispatch_checks_background_consent_and_disconnect(db):
    from arno.agent.chatgpt_auth import background_inference

    source = DatabaseChatGPTCredentialSource()
    await source.save(
        OpenAIChatGPTCredentials(
            client_id="consent-client",
            access_token="consented",
            refresh_token="refresh",
            scope="openid chatgpt.tokens.use.direct",
        )
    )
    request = httpx2.Request(
        "POST", "https://api.openai.com/v1/responses", headers={"Authorization": "Bearer consented"}
    )
    await source.authorize_request(request)
    with background_inference():
        with pytest.raises(UserError, match="background"):
            await source.authorize_request(request)
        await db.execute("UPDATE model_credentials SET allow_background=true")
        await source.authorize_request(request)
    await source.revoke()
    with pytest.raises(UserError, match="disconnected"):
        await source.authorize_request(request)


def test_chatgpt_plan_cannot_silently_fall_back_to_paid_api():
    from pydantic_ai.models.test import TestModel

    from arno.agent.builder import build_agent

    primary = build_model(
        RoleConfig(
            "https://api.openai.com/v1", "not-needed", "gpt-5", 128000, provider="openai-chatgpt"
        )
    )
    with pytest.raises(UserError, match="fall back"):
        build_agent(model=primary, backup=TestModel())


def test_loopback_is_not_evidence_that_a_role_is_configured():
    assert not RoleConfig(
        "http://127.0.0.1:9/v1", "not-needed", "chat-unconfigured", 128000
    ).configured
    assert RoleConfig(
        "http://127.0.0.1:11434/v1",
        "not-needed",
        "qwen3",
        128000,
        provider="ollama",
        billing_mode="local",
    ).configured


def test_parser_inherits_chat_billing_when_only_the_model_changes(monkeypatch):
    monkeypatch.setenv("CHAT_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("CHAT_PROVIDER", "ollama")
    monkeypatch.setenv("CHAT_BILLING_MODE", "local")
    monkeypatch.setenv("PARSE_MODEL", "parser-local")
    monkeypatch.delenv("PARSE_BASE_URL", raising=False)
    config = role_config("PARSE")
    assert config.provider == "ollama"
    assert config.billing_mode == "local"
    assert config.model == "parser-local"
    assert config.role == "parse"


def test_separate_parser_endpoint_does_not_inherit_chat_credentials(monkeypatch):
    monkeypatch.setenv("CHAT_API_KEY", "chat-only-secret")
    monkeypatch.setenv("PARSE_BASE_URL", "https://another.example/v1")
    monkeypatch.delenv("PARSE_API_KEY", raising=False)
    assert role_config("PARSE").api_key == "not-needed"


def test_an_alternate_fallback_does_not_inherit_the_primary_provider(monkeypatch):
    monkeypatch.setenv("CHAT_PROVIDER", "opencode")
    monkeypatch.setenv("CHAT_FALLBACK_BASE_URL", "https://api.cohere.ai/compatibility/v1")
    monkeypatch.delenv("CHAT_FALLBACK_PROVIDER", raising=False)
    assert chat_config(fallback=True).provider == "cohere"


@pytest.mark.parametrize("vendor", ["openai", "anthropic", "google"])
def test_batch_submission_clients_do_not_retry_an_uncertain_acknowledgement(monkeypatch, vendor):
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider

    monkeypatch.setenv("BATCH_PROVIDER", vendor)
    monkeypatch.setenv("BATCH_MODEL", "synthetic-model")
    monkeypatch.setenv("BATCH_API_KEY", "synthetic-key")
    model = batch_model()
    if isinstance(model, (OpenAIResponsesModel, AnthropicModel)):
        assert model.provider is not None
        assert model.provider.client.max_retries == 0
    else:
        assert isinstance(model, GoogleModel)
        assert isinstance(model.provider, GoogleProvider)
        options = model.provider.client._api_client._http_options.retry_options
        assert options is not None
        assert options.attempts == 1
