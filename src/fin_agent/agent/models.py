"""Model roles, built from environment variables alone.

Local (Ollama) and cloud endpoints take the same path: both are OpenAI-compatible. A fallback is
optional, and its declared context window is what Fin-Agent enforces before sending a long
history, because Ollama otherwise truncates silently.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from pydantic_ai.exceptions import ModelContextWindowExceededError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai_harness.compaction import estimate_context_tokens

DEFAULT_CONTEXT_WINDOW = 128_000
DEFAULT_FALLBACK_CONTEXT_WINDOW = 32_000


@dataclass(frozen=True, slots=True)
class RoleConfig:
    base_url: str
    api_key: str
    model: str
    context_window: int

    @property
    def configured(self) -> bool:
        return bool(self.api_key or "ollama" in self.base_url or "127.0.0.1" in self.base_url)


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def chat_config(*, fallback: bool = False) -> RoleConfig:
    prefix = "CHAT_FALLBACK" if fallback else "CHAT"
    base_url = os.environ.get(f"{prefix}_BASE_URL", "").strip()
    if not base_url:
        # A closed loopback port: constructing the agent must not touch the network, and tests
        # override the model anyway.
        base_url = "http://127.0.0.1:9/v1"
    api_key = os.environ.get(f"{prefix}_API_KEY", "").strip() or "not-needed"
    model = os.environ.get(f"{prefix}_MODEL", "").strip() or (
        "backup-unconfigured" if fallback else "chat-unconfigured"
    )
    window = _int_env(
        "CHAT_FALLBACK_CONTEXT_WINDOW" if fallback else "LLM_CONTEXT_WINDOW",
        DEFAULT_FALLBACK_CONTEXT_WINDOW if fallback else DEFAULT_CONTEXT_WINDOW,
    )
    return RoleConfig(base_url=base_url, api_key=api_key, model=model, context_window=window)


def build_model(config: RoleConfig) -> Model:
    """One code path for local and cloud: an OpenAI-compatible endpoint."""
    if "ollama" in config.base_url or os.environ.get("CHAT_PROVIDER", "").lower() == "ollama":
        from pydantic_ai.providers.ollama import OllamaProvider

        provider = OllamaProvider(base_url=config.base_url, api_key=config.api_key)
    else:
        from pydantic_ai.providers.openai import OpenAIProvider

        provider = OpenAIProvider(base_url=config.base_url, api_key=config.api_key)
    from pydantic_ai.models.openai import OpenAIChatModel

    return OpenAIChatModel(config.model, provider=provider)


class WindowLimited(WrapperModel):
    """Refuse a request that won't fit the backup's real window, before sending it.

    `ModelFallback` and `FallbackModel` both treat the refusal as an ordinary model error, so the
    summary falls through to the sliding window and the turn still answers.
    """

    def __init__(self, wrapped: Model, window: int, *, label: str | None = None):
        super().__init__(wrapped)
        self.window = window
        self.display_label = label or f"{wrapped.model_name} (window {window})"

    def _check(self, messages: list[ModelMessage], parameters: ModelRequestParameters) -> None:
        estimated = estimate_context_tokens(messages, model_request_parameters=parameters)
        if estimated > self.window:
            raise ModelContextWindowExceededError(
                self.display_label,
                f"The history is about {estimated} tokens, past the backup's "
                f"{self.window}-token window.",
            )

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self._check(messages, model_request_parameters)
        return await self.wrapped.request(messages, model_settings, model_request_parameters)

    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncIterator[StreamedResponse]:
        self._check(messages, model_request_parameters)
        async for response in self.wrapped.request_stream(
            messages, model_settings, model_request_parameters, run_context
        ):
            yield response


def primary_model() -> Model:
    return build_model(chat_config())


def backup_model() -> Model | None:
    config = chat_config(fallback=True)
    if not os.environ.get("CHAT_FALLBACK_BASE_URL") and not os.environ.get("CHAT_FALLBACK_MODEL"):
        return None
    return WindowLimited(build_model(config), config.context_window)


__all__ = [
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_FALLBACK_CONTEXT_WINDOW",
    "RoleConfig",
    "WindowLimited",
    "backup_model",
    "build_model",
    "chat_config",
    "primary_model",
]
