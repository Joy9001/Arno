"""Preserve the billing fields a generic OpenAI-compatible SDK mapping drops.

OpenAI's SDK keeps unknown ``usage`` keys only as untyped extras, and Pydantic AI's usage mapping
does not forward them, so OpenCode's ``opencode`` block and a reported zero ``usage.cost`` would be
lost before pricing. This module reads only allowlisted billing metadata from the public HTTP
response at the transport boundary; it never stores a raw body or overrides the SDK's model
mapping. A response that omits the fields simply stays unknown.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from decimal import Decimal
from typing import Any, cast, override

import httpx2
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse, ModelResponseStreamEvent
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

_MAX_EVENT_LINE = 256 * 1024
_MAX_JSON_BODY = 1024 * 1024
_MAX_BILLING_SOURCE = 64

_captured: ContextVar[dict[str, Any] | None] = ContextVar("opencode_billing_metadata", default=None)


class CapturingSSEStream(httpx2.AsyncByteStream):
    """Forward an SSE byte stream while retaining only its terminal billing metadata."""

    def __init__(self, stream: httpx2.AsyncByteStream) -> None:
        self._stream = stream
        self._line = b""

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._stream:
            self._feed(chunk)
            yield chunk

    @override
    async def aclose(self) -> None:
        await self._stream.aclose()

    def _feed(self, chunk: bytes) -> None:
        self._line += chunk
        while (newline := self._line.find(b"\n")) >= 0:
            line, self._line = self._line[:newline].strip(), self._line[newline + 1 :]
            if line.startswith(b"data:"):
                _capture_json(line[5:].strip())
        if len(self._line) > _MAX_EVENT_LINE:
            self._line = b""


async def capture_response(response: httpx2.Response) -> None:
    """A public httpx response hook: retain billing fields before the SDK consumes the body."""
    if _captured.get() is None:
        return
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        if isinstance(response.stream, httpx2.AsyncByteStream):
            response.stream = CapturingSSEStream(response.stream)
        return
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_JSON_BODY:
        return
    await response.aread()
    _capture_json(response.content)


def _capture_json(raw: bytes) -> None:
    if not raw or len(raw) > _MAX_JSON_BODY:
        return
    try:
        payload = json.loads(raw, parse_float=Decimal)
    except ValueError:
        return
    if isinstance(payload, dict):
        _capture_payload(cast(dict[str, Any], payload))


def _capture_payload(payload: dict[str, Any]) -> None:
    usage = payload.get("usage")
    if not isinstance(usage, dict) and isinstance(payload.get("response"), dict):
        usage = payload["response"].get("usage")
    if not isinstance(usage, dict):
        return
    usage = cast(dict[str, Any], usage)
    metadata: dict[str, Any] = {}

    if "cost" in usage:
        metadata["cost"] = usage["cost"]

    block = usage.get("opencode")
    if not isinstance(block, dict):
        block = payload.get("opencode")
    if isinstance(block, dict):
        clean: dict[str, Any] = {}
        for key in ("list_cost", "allowance"):
            if key in block:
                clean[key] = block[key]
        source = block.get("billing_source")
        if isinstance(source, str) and 0 < len(source) <= _MAX_BILLING_SOURCE:
            clean["billing_source"] = source
        if isinstance(block.get("estimated"), bool):
            clean["estimated"] = block["estimated"]
        tokens = block.get("tokens")
        if isinstance(tokens, dict):
            clean["tokens"] = {
                key: value
                for key, value in tokens.items()
                if key
                in {
                    "input",
                    "output",
                    "reasoning",
                    "cache_read",
                    "cache_write_5m",
                    "cache_write_1h",
                }
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            }
        if clean:
            metadata["opencode"] = clean

    if not metadata:
        return
    current = _captured.get()
    if current is None:
        return
    # Streaming can be drained by a child task; its copied context shares this request's dict.
    current.update(metadata)


def _attach(response: ModelResponse, metadata: dict[str, Any] | None) -> None:
    if not metadata:
        return
    details = dict(response.provider_details or {})
    cost = metadata.get("cost")
    if cost is not None:
        existing_usage = details.get("provider_usage")
        provider_usage: dict[str, Any] = existing_usage if isinstance(existing_usage, dict) else {}
        provider_usage["cost"] = cost
        details["provider_usage"] = provider_usage
    block = metadata.get("opencode")
    if isinstance(block, dict):
        existing = details.get("opencode")
        details["opencode"] = {
            **(existing if isinstance(existing, dict) else {}),
            **cast(dict[str, Any], block),
        }
        tokens = block.get("tokens")
        if isinstance(tokens, dict):
            cache_read = tokens.get("cache_read", response.usage.cache_read_tokens)
            cache_write_1h = tokens.get("cache_write_1h", response.usage.cache_write_1h_tokens)
            cache_write = (
                tokens.get("cache_write_5m", 0) + tokens.get("cache_write_1h", 0)
                if "cache_write_5m" in tokens or "cache_write_1h" in tokens
                else response.usage.cache_write_tokens
            )
            if "input" in tokens:
                response.usage.input_tokens = tokens["input"] + cache_read + cache_write
            response.usage.cache_read_tokens = cache_read
            response.usage.cache_write_tokens = cache_write
            response.usage.cache_write_1h_tokens = cache_write_1h
            if "output" in tokens:
                response.usage.output_tokens = tokens["output"]
            if "reasoning" in tokens:
                response.usage.output_reasoning_tokens = tokens["reasoning"]
    response.provider_details = details


class BillingMetadataModel(WrapperModel):
    """Attach configured identity and captured billing fields before the model is priced."""

    def __init__(self, wrapped: Model, *, provider: str, billing_mode: str) -> None:
        super().__init__(wrapped)
        self.provider_name = provider
        self.billing_mode = billing_mode

    def _marker(self) -> dict[str, str]:
        return {"provider": self.provider_name, "billing_mode": self.billing_mode}

    def _finish(self, response: ModelResponse) -> ModelResponse:
        details = dict(response.provider_details or {})
        details["arno_cost"] = self._marker()
        response.provider_details = details
        _attach(response, _captured.get())
        return response

    @override
    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        token = _captured.set({})
        try:
            return self._finish(
                await self.wrapped.request(messages, model_settings, model_request_parameters)
            )
        finally:
            _captured.reset(token)

    @override
    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        token = _captured.set({})
        try:
            async with self.wrapped.request_stream(
                messages, model_settings, model_request_parameters, run_context
            ) as stream:
                yield cast(StreamedResponse, _BillingStream(stream, self._finish))
        finally:
            _captured.reset(token)


class _BillingStream:
    """A streamed response whose final `get()` carries the billing metadata."""

    def __init__(
        self, wrapped: StreamedResponse, finish: Callable[[ModelResponse], ModelResponse]
    ) -> None:
        self._wrapped = wrapped
        self._finish = finish

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def __aiter__(self) -> AsyncIterator[ModelResponseStreamEvent]:
        return self._wrapped.__aiter__()

    def get(self) -> ModelResponse:
        return self._finish(self._wrapped.get())


__all__ = ["BillingMetadataModel", "CapturingSSEStream", "capture_response"]
