"""Model roles, built from environment variables alone.

Local (Ollama) and cloud endpoints take the same path: both are OpenAI-compatible. A fallback is
optional, and its declared context window is what Arno enforces before sending a long
history, because Ollama otherwise truncates silently.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, cast, override

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelContextWindowExceededError
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai_harness.compaction import estimate_context_tokens

DEFAULT_CONTEXT_WINDOW = 128_000
DEFAULT_FALLBACK_CONTEXT_WINDOW = 32_000

logger = logging.getLogger("arno.agent.models")
_conversation_session: ContextVar[str | None] = ContextVar("opencode_conversation", default=None)


@contextmanager
def opencode_conversation(conversation_id: str) -> Generator[None]:
    """Bind a stable, header-safe session to this conversation's async task."""
    digest = sha256(conversation_id.encode("utf-8")).hexdigest()
    token = _conversation_session.set(f"arno-{digest}")
    try:
        yield
    finally:
        _conversation_session.reset(token)


@dataclass(frozen=True, slots=True)
class RoleConfig:
    base_url: str
    api_key: str
    model: str
    context_window: int
    provider: str = "openai"
    billing_mode: str = "metered"
    role: str = "chat"

    @property
    def configured(self) -> bool:
        return bool(
            self.model
            and not self.model.endswith("-unconfigured")
            and (
                self.api_key not in {"", "not-needed"}
                or self.provider in {"ollama", "chatgpt", "openai-chatgpt"}
                or self.billing_mode in {"local", "local_zero"}
            )
        )


def _provider_name(prefix: str, base_url: str) -> str:
    explicit = os.environ.get(f"{prefix}_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    if "opencode.ai" in base_url:
        return "opencode"
    if "ollama" in base_url:
        return "ollama"
    from genai_prices.data_snapshot import get_snapshot

    try:
        return str(get_snapshot().find_provider(None, None, base_url).id)
    except LookupError:
        pass
    return "openai"


def _billing_mode(prefix: str) -> str:
    return os.environ.get(f"{prefix}_BILLING_MODE", "").strip().lower() or "metered"


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
    return RoleConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        context_window=window,
        provider=_provider_name(prefix, base_url),
        billing_mode=_billing_mode(prefix),
        role="chat",
    )


def role_config(prefix: str) -> RoleConfig:
    """VISION and STT have their own endpoint and key, and no fallback."""
    if prefix == "PARSE" and not os.environ.get("PARSE_BASE_URL"):
        from dataclasses import replace

        parent = chat_config()
        return replace(
            parent,
            model=os.environ.get("PARSE_MODEL", "").strip() or parent.model,
            api_key=os.environ.get("PARSE_API_KEY", "").strip() or parent.api_key,
            provider=os.environ.get("PARSE_PROVIDER", "").strip() or parent.provider,
            billing_mode=os.environ.get("PARSE_BILLING_MODE", "").strip() or parent.billing_mode,
            role="parse",
        )
    base_url = os.environ.get(f"{prefix}_BASE_URL", "").strip() or "http://127.0.0.1:9/v1"
    api_key = os.environ.get(f"{prefix}_API_KEY", "").strip() or "not-needed"
    model = os.environ.get(f"{prefix}_MODEL", "").strip() or f"{prefix.lower()}-unconfigured"
    window = _int_env(f"{prefix}_CONTEXT_WINDOW", DEFAULT_CONTEXT_WINDOW)
    return RoleConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        context_window=window,
        provider=_provider_name(prefix, base_url),
        billing_mode=_billing_mode(prefix),
        role=prefix.lower(),
    )


def _provider_http_client(config: RoleConfig, session_id: str | None = None) -> Any:
    """One client for every OpenAI-compatible role, with session and billing capture hooks."""
    import httpx2

    from arno.agent.provider_usage import capture_response

    hooks = {"response": [capture_response]}
    if "opencode.ai" not in config.base_url:
        return httpx2.AsyncClient(headers={"User-Agent": "arno/0.1"}, event_hooks=hooks)

    # A shared client serves many turns, so select the session per request, not per client.
    session = session_id or os.environ.get("OPENCODE_SESSION_ID") or f"arno-{uuid.uuid4()}"

    async def set_session(request: httpx2.Request) -> None:
        request.headers["x-opencode-session"] = _conversation_session.get() or session

    return httpx2.AsyncClient(
        headers={"User-Agent": "arno/0.1"},
        event_hooks={"request": [set_session], **hooks},
    )


def _openai_provider(config: RoleConfig, session_id: str | None = None) -> Any:
    from openai import AsyncOpenAI
    from pydantic_ai.providers.openai import OpenAIProvider

    client = AsyncOpenAI(
        base_url=config.base_url,
        api_key=config.api_key,
        http_client=_provider_http_client(config, session_id),
        max_retries=0,
    )
    return OpenAIProvider(openai_client=client)


def _openrouter_model(config: RoleConfig, session_id: str | None = None) -> Any:
    from openai import AsyncOpenAI
    from pydantic_ai.models.openrouter import OpenRouterModel
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=config.api_key,
        http_client=_provider_http_client(config, session_id),
        max_retries=0,
    )
    return OpenRouterModel(config.model, provider=OpenRouterProvider(openai_client=client))


def _anthropic_model(config: RoleConfig) -> Any:
    from anthropic import AsyncAnthropic
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    default_endpoint = config.base_url.startswith("http://127.0.0.1:9")
    client = AsyncAnthropic(
        api_key=config.api_key,
        base_url=None if default_endpoint else config.base_url,
        max_retries=0,
    )
    return AnthropicModel(config.model, provider=AnthropicProvider(anthropic_client=client))


def _chatgpt_model(config: RoleConfig) -> Any:
    import httpx2
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTProvider

    from arno.agent.chatgpt_auth import DatabaseChatGPTCredentialSource
    from arno.config import get_settings

    # Eval financial databases are disposable; sign-in/rotation belongs to this installation.
    source = DatabaseChatGPTCredentialSource(get_settings().database_url)
    client = httpx2.AsyncClient(event_hooks={"request": [source.authorize_request]})
    provider = OpenAIChatGPTProvider(credential_source=source, http_client=client)
    # The provider owns its SDK client; disable its hidden inference retries for accounting.
    provider.client.max_retries = 0
    return OpenAIResponsesModel(config.model, provider=provider)


def _concrete_model(config: RoleConfig, session_id: str | None = None) -> Model:
    name = config.provider.strip().lower()
    if name == "anthropic":
        return cast(Model, _anthropic_model(config))
    if name in {"openai-responses", "openai_responses", "responses"}:
        from pydantic_ai.models.openai import OpenAIResponsesModel

        return OpenAIResponsesModel(config.model, provider=_openai_provider(config, session_id))
    if name in {"openai-chatgpt", "chatgpt"}:
        return cast(Model, _chatgpt_model(config))
    if name == "openrouter" and (
        config.base_url.startswith("http://127.0.0.1:9") or "openrouter.ai" in config.base_url
    ):
        return cast(Model, _openrouter_model(config, session_id))
    if name == "ollama":
        from openai import AsyncOpenAI
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.ollama import OllamaProvider

        client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            http_client=_provider_http_client(config, session_id),
            max_retries=0,
        )
        return OpenAIChatModel(config.model, provider=OllamaProvider(openai_client=client))

    from pydantic_ai.models.openai import OpenAIChatModel

    return OpenAIChatModel(config.model, provider=_openai_provider(config, session_id))


def _metered(model: Model, config: RoleConfig) -> Model:
    from arno.agent.metering import MeteredModel

    return MeteredModel(
        model,
        provider=config.provider,
        billing_mode=config.billing_mode,
        role=config.role,
    )


def build_model(config: RoleConfig, *, session_id: str | None = None) -> Model:
    """One code path for local and cloud, wrapped with billing identity before metering."""
    from arno.agent.provider_usage import BillingMetadataModel

    native = BillingMetadataModel(
        _concrete_model(config, session_id),
        provider=config.provider,
        billing_mode=config.billing_mode,
    )
    return _metered(native, config)


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

    @override
    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self._check(messages, model_request_parameters)
        return await self.wrapped.request(messages, model_settings, model_request_parameters)

    @override
    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        self._check(messages, model_request_parameters)
        async with self.wrapped.request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as response_stream:
            yield response_stream


def primary_model() -> Model:
    return build_model(chat_config())


def model_for(model_id: str, *, session_id: str | None = None) -> Model:
    """Build a model on the configured CHAT endpoint with this exact ID.

    Eval scorecards use one endpoint for cloud and local models; the ID is the only thing
    that changes. The caller still owns pinning a dated version.
    """
    from dataclasses import replace

    return build_model(replace(chat_config(), model=model_id), session_id=session_id)


def vision_model() -> Model:
    return build_model(role_config("VISION"))


def parse_model() -> Model:
    """The parser subagent's model, or the chat model when `PARSE_*` is unset."""
    return build_model(role_config("PARSE"))


def backup_model() -> Model | None:
    config = chat_config(fallback=True)
    if not os.environ.get("CHAT_FALLBACK_BASE_URL") and not os.environ.get("CHAT_FALLBACK_MODEL"):
        return None
    return WindowLimited(build_model(config), config.context_window)


def batch_model() -> Model | None:
    """The optional batch model, or None when `BATCH_PROVIDER` or `BATCH_MODEL` is unset.

    A batch API exists only for the providers' native APIs, so this builds a native model rather
    than the OpenAI-compatible chat path: Anthropic, OpenAI Responses, or Gemini outside Vertex.
    """
    provider_name = os.environ.get("BATCH_PROVIDER", "").strip().lower()
    model_name = os.environ.get("BATCH_MODEL", "").strip()
    if not provider_name or not model_name:
        return None
    api_key = os.environ.get("BATCH_API_KEY", "").strip() or None
    if provider_name in ("google", "gemini"):
        from google.genai.types import HttpRetryOptions
        from pydantic_ai.models.google import GoogleModel
        from pydantic_ai.providers.google import GoogleProvider

        return GoogleModel(
            model_name,
            provider=GoogleProvider(api_key=api_key, retry_options=HttpRetryOptions(attempts=1)),
        )
    if provider_name == "anthropic":
        from anthropic import AsyncAnthropic
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        client = AsyncAnthropic(api_key=api_key, max_retries=0)
        return AnthropicModel(model_name, provider=AnthropicProvider(anthropic_client=client))
    if provider_name == "openai":
        from openai import AsyncOpenAI
        from pydantic_ai.models.openai import OpenAIResponsesModel
        from pydantic_ai.providers.openai import OpenAIProvider

        client = AsyncOpenAI(api_key=api_key, max_retries=0)
        return OpenAIResponsesModel(model_name, provider=OpenAIProvider(openai_client=client))
    logger.warning("unknown BATCH_PROVIDER %r; batching is off", provider_name)
    return None


__all__ = [
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_FALLBACK_CONTEXT_WINDOW",
    "RoleConfig",
    "WindowLimited",
    "backup_model",
    "batch_model",
    "build_model",
    "chat_config",
    "model_for",
    "parse_model",
    "primary_model",
    "role_config",
    "vision_model",
]
