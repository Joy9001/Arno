"""Metered model attempts: one journal row, one real span, one priced observation per dispatch.

`MeteredModel` wraps a concrete provider model beneath Pydantic AI's native instrumentation, so the
native model span stays the logical request and each dispatch is a child attempt span that carries
the cost once. Only `request` and `request_stream` dispatch; entering the wrapper, cancellation, and
batch submission are not billable attempts. Batch forwarding stays unmetered because the parent
batch path accounts submission and collection itself.

Every attempt writes its journal intent before the network call, then finalizes a canonical
`CostObservation` after the response (or a truthful unknown/partial on failure, cancellation, or a
missing terminal usage event). A failed first attempt keeps its own unknown row, so a successful
fallback cannot hide it.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, override

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from arno.agent.cost_journal import CostAttempt, Journal, JournalSink, current_cost_identity
from arno.agent.pricing import (
    LOCAL_BILLING_MODES,
    CostObservation,
    cost_attributes,
    observe_cost,
    validate_pricing,
)

logger = logging.getLogger("arno.agent.metering")

# A cancelled run still gets a bounded chance to record what it spent, without holding cancellation
# open on an unhealthy database.
# shortcut: a fixed bound, make it configurable if a slow store starts losing finalized rows.
CANCEL_FINALIZE_TIMEOUT = 5.0
DEFAULT_MAX_OUTPUT_TOKENS = 8192


def _tracer() -> trace.Tracer:
    return trace.get_tracer("arno.cost")


def _unknown(source: str) -> CostObservation:
    return CostObservation(
        usd=None,
        source=source,
        complete=False,
        price_revision="",
    )


def _partial(observation: CostObservation) -> CostObservation:
    """The same observation with completeness downgraded; never the reverse."""
    return CostObservation(
        usd=observation.usd,
        source=observation.source,
        complete=False,
        price_revision=observation.price_revision,
        provider_charge=observation.provider_charge,
        allowance=observation.allowance,
        billing_source=observation.billing_source,
        usage=observation.usage,
        rates=observation.rates,
    )


def _is_local(billing_mode: str) -> bool:
    return billing_mode.strip().lower() in LOCAL_BILLING_MODES


def _ids(span: Span) -> tuple[str, str]:
    """The attempt's stable trace/span IDs, real when the SDK recorded the span."""
    context = span.get_span_context()
    if context.is_valid:
        return (
            trace.format_trace_id(context.trace_id),
            trace.format_span_id(context.span_id),
        )
    return secrets.token_hex(16), secrets.token_hex(8)


class MeteredAttempt:
    """A dispatch an external caller (speech, batch item) can finish with its observation."""

    def __init__(self, attempt: _Attempt) -> None:
        self._attempt = attempt

    @property
    def attempt_id(self) -> str:
        return self._attempt.record.attempt_id

    @property
    def trace_id(self) -> str:
        return self._attempt.record.trace_id

    @property
    def span_id(self) -> str:
        return self._attempt.record.span_id

    async def finish(self, observation: CostObservation) -> None:
        """Finalize this attempt once. Repeated calls do nothing."""
        await self._attempt.finish_ok(observation)


class _Attempt:
    """One real dispatch's span plus journal row."""

    def __init__(self, span: Span, record: CostAttempt, journal: JournalSink) -> None:
        self.span = span
        self.record = record
        self.journal = journal
        self._finished = False
        self._span_ended = False

    @property
    def finished(self) -> bool:
        return self._finished

    def _end_span(self) -> None:
        if not self._span_ended:
            self._span_ended = True
            self.span.end()

    def served_model(self, model: str) -> None:
        self.record = replace(self.record, model=model)
        self.span.set_attribute("llm.model_name", model)
        self.span.set_attribute("gen_ai.response.model", model)

    async def record_intent(self) -> None:
        self.span.set_attributes(
            {
                "openinference.span.kind": "LLM",
                "llm.model_name": self.record.model,
                "llm.provider": self.record.provider,
                "arno.cost.attempt_id": self.record.attempt_id,
                "arno.cost.workload": self.record.workload or self.record.role,
                "arno.cost.modality": "audio" if self.record.role == "stt" else "text",
                "arno.cost.billing_mode": self.record.billing_mode,
            }
        )
        if self.record.session_id:
            self.span.set_attribute("session.id", self.record.session_id)
            self.span.set_attribute("gen_ai.conversation.id", self.record.session_id)
        try:
            await self.journal.record_intent(self.record)
        except BaseException as exc:
            self._finished = True
            self.span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            self._end_span()
            raise

    async def _finalize(
        self,
        observation: CostObservation,
        *,
        error: BaseException | None,
        suppress_errors: bool,
    ) -> None:
        if self._finished:
            return
        self._finished = True
        if not self._span_ended:
            attributes = cost_attributes(observation)
            attributes["arno.cost.role"] = "attempt"
            for key, value in attributes.items():
                self.span.set_attribute(key, value)
            if error is not None:
                self.span.set_status(Status(StatusCode.ERROR, type(error).__name__))
        try:
            await self.journal.record_final(self.record, observation, finished_at=datetime.now(UTC))
        except Exception:
            if suppress_errors:
                logger.exception("cost journal finalization failed for %s", self.record.attempt_id)
            else:
                raise
        finally:
            self._end_span()

    async def finish_ok(self, observation: CostObservation) -> None:
        await self._finalize(observation, error=None, suppress_errors=False)

    async def finish_bounded(self, observation: CostObservation, error: BaseException) -> None:
        """Finalize a cancellation behind a shield, then let the cancellation continue."""
        task = asyncio.create_task(self._finalize(observation, error=error, suppress_errors=True))
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=CANCEL_FINALIZE_TIMEOUT,
            )
        except TimeoutError, asyncio.CancelledError:
            task.cancel()
            logger.warning("cost journal finalization timed out for %s", self.record.attempt_id)
            self._end_span()

    async def fail(self, error: BaseException, observation: CostObservation | None = None) -> None:
        if self._finished:
            return
        if observation is not None:
            if not _is_local(self.record.billing_mode):
                observation = _partial(observation)
        else:
            observation = _unknown(
                "cancelled" if isinstance(error, asyncio.CancelledError) else "failed"
            )
        if isinstance(error, asyncio.CancelledError):
            await self.finish_bounded(observation, error)
        else:
            try:
                await self._finalize(observation, error=error, suppress_errors=True)
            except Exception:  # noqa: BLE001 - the dispatch's own error is the one to raise
                logger.exception("cost journal finalization failed for %s", self.record.attempt_id)


def _observation(response: ModelResponse, *, provider: str, billing_mode: str) -> CostObservation:
    observation = observe_cost(response, provider=provider, billing_mode=billing_mode)
    response.usage.cost = observation.usd
    return observation


def _stream_observation(
    stream: StreamedResponse, *, provider: str, billing_mode: str
) -> CostObservation:
    response = stream.get()
    observation = observe_cost(response, provider=provider, billing_mode=billing_mode)
    # Only usage the provider actually completed can be complete. A cancelled or truncated stream
    # keeps its known subtotal, marked partial. An explicit local profile stays a measured zero.
    if (
        not _is_local(billing_mode)
        and (response.state != "complete" or stream.state != "complete")
        and observation.complete
    ):
        observation = _partial(observation)
    response.usage.cost = observation.usd
    return observation


class MeteredModel(WrapperModel):
    """A concrete model whose every real dispatch is journaled, traced, and priced once."""

    provider_name: str
    billing_mode: str
    role: str
    journal: JournalSink

    def __init__(
        self,
        wrapped: Model,
        *,
        provider: str,
        billing_mode: str,
        role: str,
        journal: JournalSink | None = None,
    ) -> None:
        super().__init__(wrapped)
        self.provider_name = provider
        self.billing_mode = billing_mode
        self.role = role
        self.journal = journal or Journal()

    def _stamp_response(self, response: ModelResponse) -> None:
        """Carry the configured profile to the native `SpendLimits` price callback."""
        details = dict(response.provider_details or {})
        details["arno_cost"] = {
            "provider": self.provider_name,
            "billing_mode": self.billing_mode,
        }
        response.provider_details = details

    def _bounded_settings(self, settings: ModelSettings | None) -> ModelSettings | None:
        if self.provider_name in {"chatgpt", "openai-chatgpt"}:
            return settings
        if settings and settings.get("max_tokens") is not None:
            return settings
        return {**(settings or {}), "max_tokens": DEFAULT_MAX_OUTPUT_TOKENS}

    def _stamp_stream(self, stream: StreamedResponse) -> None:
        details = dict(stream.provider_details or {})
        details["arno_cost"] = {
            "provider": self.provider_name,
            "billing_mode": self.billing_mode,
        }
        stream.provider_details = details

    def _start(self, *, mark_parent: bool) -> _Attempt:
        if self.wrapped.system not in {"function", "test"}:
            validate_pricing(
                provider=self.provider_name,
                model=self.wrapped.model_name,
                provider_url=self.wrapped.base_url,
                billing_mode=self.billing_mode,
            )
        model = self.wrapped.model_name
        if mark_parent:
            parent = trace.get_current_span()
            if parent.is_recording():
                parent.set_attribute("arno.cost.role", "logical_request")
        span = _tracer().start_span(
            f"chat {model}",
            attributes={
                "arno.cost.role": "attempt",
                "gen_ai.request.model": model,
                "gen_ai.provider.name": self.provider_name,
            },
        )
        trace_id, span_id = _ids(span)
        session_id, workload = current_cost_identity()
        record = CostAttempt(
            attempt_id=secrets.token_hex(16),
            trace_id=trace_id,
            span_id=span_id,
            role=self.role,
            provider=self.provider_name,
            model=model,
            billing_mode=self.billing_mode,
            workload=workload,
            started_at=datetime.now(UTC),
            session_id=session_id,
        )
        return _Attempt(span, record, self.journal)

    @override
    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        attempt = self._start(mark_parent=True)
        await attempt.record_intent()
        try:
            response = await self.wrapped.request(
                messages, self._bounded_settings(model_settings), model_request_parameters
            )
        except BaseException as exc:
            await attempt.fail(exc)
            raise
        self._stamp_response(response)
        attempt.served_model(response.model_name or self.wrapped.model_name)
        try:
            observation = _observation(
                response, provider=self.provider_name, billing_mode=self.billing_mode
            )
        except BaseException as exc:
            await attempt.fail(exc)
            raise
        await attempt.finish_ok(observation)
        return response

    @override
    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        attempt = self._start(mark_parent=True)
        await attempt.record_intent()
        try:
            async with self.wrapped.request_stream(
                messages,
                self._bounded_settings(model_settings),
                model_request_parameters,
                run_context,
            ) as stream:
                self._stamp_stream(stream)
                try:
                    yield stream
                except BaseException as exc:
                    await attempt.fail(exc, self._safe_observation(stream))
                    raise
                else:
                    attempt.served_model(stream.get().model_name or self.wrapped.model_name)
                    await attempt.finish_ok(
                        _stream_observation(
                            stream, provider=self.provider_name, billing_mode=self.billing_mode
                        )
                    )
        except BaseException as exc:
            await attempt.fail(exc)
            raise

    def _safe_observation(self, stream: StreamedResponse) -> CostObservation | None:
        try:
            return _stream_observation(
                stream, provider=self.provider_name, billing_mode=self.billing_mode
            )
        except Exception:
            return None


@asynccontextmanager
async def metered_call(
    provider: str,
    model: str,
    billing_mode: str,
    role: str,
    workload: str | None = None,
    *,
    journal: JournalSink | None = None,
) -> AsyncGenerator[MeteredAttempt]:
    """Meter one direct inference call (speech, batch item) outside the agent loop.

    The caller awaits `finish(observation)` with the shared pricing result. Leaving without one
    finalizes an unknown observation rather than assuming a free call.
    """
    sink = journal or Journal()
    span = _tracer().start_span(
        f"{role} {model}",
        attributes={
            "arno.cost.role": "attempt",
            "gen_ai.request.model": model,
            "gen_ai.provider.name": provider,
        },
    )
    trace_id, span_id = _ids(span)
    session_id, inherited_workload = current_cost_identity()
    record = CostAttempt(
        attempt_id=secrets.token_hex(16),
        trace_id=trace_id,
        span_id=span_id,
        role=role,
        provider=provider,
        model=model,
        billing_mode=billing_mode,
        workload=inherited_workload or workload,
        started_at=datetime.now(UTC),
        session_id=session_id,
    )
    attempt = _Attempt(span, record, sink)
    await attempt.record_intent()
    public = MeteredAttempt(attempt)
    try:
        yield public
    except BaseException as exc:
        await attempt.fail(exc)
        raise
    else:
        if not attempt.finished:
            await attempt.finish_ok(_unknown("unmeasured"))


__all__ = [
    "CANCEL_FINALIZE_TIMEOUT",
    "MeteredAttempt",
    "MeteredModel",
    "metered_call",
]
