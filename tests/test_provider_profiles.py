"""Provider profiles: which native adapter a role's endpoint gets, and the SDK retry policy."""

from __future__ import annotations

from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.models.openrouter import OpenRouterModel

from arno.agent.models import (
    RoleConfig,
    backup_model,
    build_model,
    chat_config,
    role_config,
)

CLOSED = "http://127.0.0.1:9/v1"


def test_role_config_keeps_the_four_positional_fields_and_defaults_the_provider():
    config = RoleConfig(CLOSED, "key", "model", 1000)

    assert config.context_window == 1000
    assert config.provider == "openai"


def test_env_parser_fills_the_explicit_provider(monkeypatch):
    monkeypatch.setenv("CHAT_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("CHAT_PROVIDER", "openai-responses")
    monkeypatch.setenv("CHAT_MODEL", "gpt-5")

    config = chat_config()

    assert config.provider == "openai-responses"
    assert config.model == "gpt-5"


def test_an_unset_role_is_unconfigured(monkeypatch):
    monkeypatch.delenv("VISION_BASE_URL", raising=False)
    monkeypatch.delenv("VISION_MODEL", raising=False)

    config = role_config("VISION")

    assert config.provider == "openai"
    assert not config.configured


def test_openai_compatible_build_lets_the_sdk_retry():
    model = build_model(RoleConfig(CLOSED, "key", "gpt-5", 1000))

    assert isinstance(model, OpenAIChatModel)
    assert model.provider is not None
    assert model.provider.client.max_retries == 2


def test_anthropic_profile_selects_the_native_messages_adapter():
    model = build_model(RoleConfig(CLOSED, "key", "claude-sonnet-5", 1000, provider="anthropic"))

    assert isinstance(model, AnthropicModel)
    assert model.provider is not None
    assert model.provider.client.max_retries == 2


def test_openai_responses_profile_selects_the_native_responses_adapter():
    config = RoleConfig(
        "https://api.openai.com/v1", "key", "gpt-5", 1000, provider="openai-responses"
    )

    assert isinstance(build_model(config), OpenAIResponsesModel)


def test_openrouter_profile_selects_the_native_router_adapter():
    config = RoleConfig(CLOSED, "key", "openai/gpt-5", 1000, provider="openrouter")

    assert isinstance(build_model(config), OpenRouterModel)


def test_ollama_profile_keeps_the_local_wire():
    model = build_model(
        RoleConfig("http://127.0.0.1:11434/v1", "not-needed", "llama3", 1000, provider="ollama")
    )

    assert isinstance(model, OpenAIChatModel)
    assert model.provider is not None
    assert model.provider.name == "ollama"


def test_backup_is_a_plain_model_and_absent_when_unset(monkeypatch):
    monkeypatch.delenv("CHAT_FALLBACK_BASE_URL", raising=False)
    monkeypatch.delenv("CHAT_FALLBACK_MODEL", raising=False)
    assert backup_model() is None

    monkeypatch.setenv("CHAT_FALLBACK_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("CHAT_FALLBACK_MODEL", "gpt-5")
    monkeypatch.setenv("CHAT_FALLBACK_API_KEY", "key")

    assert isinstance(backup_model(), OpenAIChatModel)


def test_loopback_is_not_evidence_that_a_role_is_configured():
    assert not RoleConfig(CLOSED, "not-needed", "chat-unconfigured", 128000).configured
    assert RoleConfig(
        "http://127.0.0.1:11434/v1", "not-needed", "qwen3", 128000, provider="ollama"
    ).configured


def test_parser_inherits_the_chat_endpoint_when_only_the_model_changes(monkeypatch):
    monkeypatch.setenv("CHAT_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("CHAT_PROVIDER", "ollama")
    monkeypatch.setenv("PARSE_MODEL", "parser-local")
    monkeypatch.delenv("PARSE_BASE_URL", raising=False)

    config = role_config("PARSE")

    assert config.provider == "ollama"
    assert config.model == "parser-local"


def test_separate_parser_endpoint_does_not_inherit_chat_credentials(monkeypatch):
    monkeypatch.setenv("CHAT_API_KEY", "chat-only-secret")
    monkeypatch.setenv("PARSE_BASE_URL", "https://another.example/v1")
    monkeypatch.delenv("PARSE_API_KEY", raising=False)

    assert role_config("PARSE").api_key == "not-needed"


def test_an_alternate_fallback_does_not_inherit_the_primary_provider(monkeypatch):
    monkeypatch.setenv("CHAT_PROVIDER", "anthropic")
    monkeypatch.setenv("CHAT_FALLBACK_BASE_URL", "https://api.cohere.ai/compatibility/v1")
    monkeypatch.delenv("CHAT_FALLBACK_PROVIDER", raising=False)

    assert chat_config(fallback=True).provider == "cohere"
