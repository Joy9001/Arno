"""Model roles, built from environment variables alone.

Local (Ollama) and cloud endpoints take the same path: both are OpenAI-compatible unless
`<ROLE>_PROVIDER` names a native API. The SDK clients retry transient errors themselves.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, replace
from typing import Any, cast

from pydantic_ai.models import Model

DEFAULT_CONTEXT_WINDOW = 128_000
CLOSED_URL = "http://127.0.0.1:9/v1"
"""A closed loopback port: building a model must not touch the network, and tests override it."""
SDK_RETRIES = 2


@dataclass(frozen=True, slots=True)
class RoleConfig:
    base_url: str
    api_key: str
    model: str
    context_window: int
    provider: str = "openai"

    @property
    def configured(self) -> bool:
        return bool(
            self.model
            and not self.model.endswith("-unconfigured")
            and (self.api_key not in {"", "not-needed"} or self.base_url != CLOSED_URL)
        )


def _provider_name(prefix: str, base_url: str) -> str:
    explicit = os.environ.get(f"{prefix}_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if "ollama" in base_url:
        return "ollama"
    from genai_prices.data_snapshot import get_snapshot

    try:
        return str(get_snapshot().find_provider(None, None, base_url).id)
    except LookupError:
        return "openai"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def role_config(prefix: str) -> RoleConfig:
    """One role's endpoint, key, and model. `CHAT_FALLBACK` is its own role and inherits nothing."""
    if prefix == "PARSE" and not os.environ.get("PARSE_BASE_URL"):
        parent = role_config("CHAT")
        return replace(
            parent,
            model=os.environ.get("PARSE_MODEL", "").strip() or parent.model,
            api_key=os.environ.get("PARSE_API_KEY", "").strip() or parent.api_key,
            provider=os.environ.get("PARSE_PROVIDER", "").strip() or parent.provider,
        )
    base_url = os.environ.get(f"{prefix}_BASE_URL", "").strip() or CLOSED_URL
    return RoleConfig(
        base_url=base_url,
        api_key=os.environ.get(f"{prefix}_API_KEY", "").strip() or "not-needed",
        model=os.environ.get(f"{prefix}_MODEL", "").strip() or f"{prefix.lower()}-unconfigured",
        context_window=_int_env(
            "LLM_CONTEXT_WINDOW" if prefix == "CHAT" else f"{prefix}_CONTEXT_WINDOW",
            DEFAULT_CONTEXT_WINDOW,
        ),
        provider=_provider_name(prefix, base_url),
    )


def chat_config(*, fallback: bool = False) -> RoleConfig:
    return role_config("CHAT_FALLBACK" if fallback else "CHAT")


def build_model(config: RoleConfig) -> Model:
    """One code path for local and cloud. The SDK clients own retries."""
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    default_headers = (
        {"x-opencode-session": os.environ.get("OPENCODE_SESSION_ID") or f"arno-{uuid.uuid4()}"}
        if "opencode.ai" in config.base_url
        else None
    )
    name = config.provider.strip().lower()
    if name == "anthropic":
        from anthropic import AsyncAnthropic
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        client = AsyncAnthropic(
            api_key=config.api_key,
            base_url=None if config.base_url == CLOSED_URL else config.base_url,
            default_headers=default_headers,
            max_retries=SDK_RETRIES,
        )
        return cast(
            Model, AnthropicModel(config.model, provider=AnthropicProvider(anthropic_client=client))
        )
    openai_client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1"
        if name == "openrouter" and config.base_url == CLOSED_URL
        else config.base_url,
        api_key=config.api_key,
        default_headers=default_headers,
        max_retries=SDK_RETRIES,
    )
    provider: Any
    if name == "openrouter":
        from pydantic_ai.models.openrouter import OpenRouterModel
        from pydantic_ai.providers.openrouter import OpenRouterProvider

        return OpenRouterModel(
            config.model, provider=OpenRouterProvider(openai_client=openai_client)
        )
    if name == "ollama":
        from pydantic_ai.providers.ollama import OllamaProvider

        return OpenAIChatModel(config.model, provider=OllamaProvider(openai_client=openai_client))
    provider = OpenAIProvider(openai_client=openai_client)
    if name in {"openai-responses", "openai_responses", "responses"}:
        return OpenAIResponsesModel(config.model, provider=provider)
    return OpenAIChatModel(config.model, provider=provider)


def primary_model() -> Model:
    return build_model(chat_config())


def model_for(model_id: str) -> Model:
    """Build a model on the configured CHAT endpoint with this exact ID.

    Eval scorecards use one endpoint for cloud and local models; the ID is the only thing
    that changes. The caller still owns pinning a dated version.
    """
    return build_model(replace(chat_config(), model=model_id))


def vision_model() -> Model:
    return build_model(role_config("VISION"))


def parse_model() -> Model:
    """The parser subagent's model, or the chat model when `PARSE_*` is unset."""
    return build_model(role_config("PARSE"))


def backup_model() -> Model | None:
    if not os.environ.get("CHAT_FALLBACK_BASE_URL") and not os.environ.get("CHAT_FALLBACK_MODEL"):
        return None
    return build_model(chat_config(fallback=True))


__all__ = [
    "DEFAULT_CONTEXT_WINDOW",
    "RoleConfig",
    "backup_model",
    "build_model",
    "chat_config",
    "model_for",
    "parse_model",
    "primary_model",
    "role_config",
    "vision_model",
]
