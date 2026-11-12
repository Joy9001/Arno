"""Metered attempts: order, cost, spans, failure, cancellation, streams, and fallback."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, cast

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from arno.agent import metering
from arno.agent.cost_journal import cost_identity
from arno.agent.metering import MeteredModel, metered_call
from arno.agent.pricing import CostObservation, price_response
from tests.agent_helpers import streaming_model

KNOWN = CostObservation(
    usd=Decimal("0.01"),
    source="provider",
    complete=True,
    price_revision="test-rates",
    provider_charge=Decimal("0.02"),
    allowance=Decimal("0.005"),
    billing_source="wallet",
    usage={"input_tokens": 10, "output_tokens": 5},
    rates={"input": "1", "output": "2"},
)
PARTIAL = CostObservation(
    usd=Decimal("0.004"),
    source="estimate",
    complete=False,
    price_revision="test-rates",
)
UNKNOWN = CostObservation(
    usd=None,
    source="unavailable",
    complete=False,
    price_revision="",
)


class FakeJournal:
    def __init__(self, log: list[str] | None = None) -> None:
        self.log = log if log is not None else []
        self.finals: list[CostObservation] = []

    async def record_intent(self, attempt) -> None:
        del attempt
        self.log.append("intent")

    async def record_final(self, attempt, observation, finished_at) -> None:
        del attempt, finished_at
        self.log.append("final")
        self.finals.append(observation)


def _tracer_fixture(monkeypatch) -> tuple[InMemorySpanExporter, Tracer]:
    # The session conftest disables the SDK for the whole test process; a private provider needs
    # the standard kill switch off to record spans.
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("arno-cost-test")
    monkeypatch.setattr(metering, "_tracer", lambda: tracer)
    return exporter, tracer


def _response(*, unmeasured: int = 0) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart("ok")],
        model_name="fake-model",
        usage=RequestUsage(input_tokens=10, output_tokens=5, unmeasured_requests=unmeasured),
    )


def _ok_function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    del messages, info
    return _response()


def _unmeasured_function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    del messages, info
    return _response(unmeasured=1)


def _observe_returning(observation: CostObservation):
    def observe(response, *, provider=None, billing_mode="metered"):
        del response, provider, billing_mode
        return observation

    return observe


def _attributes(span: ReadableSpan) -> dict[str, Any]:
    attributes = span.attributes
    assert attributes is not None
    return dict(attributes)


def _attempt_spans(exporter: InMemorySpanExporter) -> list[ReadableSpan]:
    return [
        span
        for span in exporter.get_finished_spans()
        if _attributes(span).get("arno.cost.role") == "attempt"
    ]


async def test_request_journals_before_dispatch_and_child_span_carries_cost(monkeypatch):
    exporter, tracer = _tracer_fixture(monkeypatch)
    log: list[str] = []
    journal = FakeJournal(log)

    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        log.append("request")
        return _response()

    monkeypatch.setattr(metering, "observe_cost", _observe_returning(KNOWN))
    model = MeteredModel(
        FunctionModel(function, model_name="fake-model"),
        provider="openai",
        billing_mode="metered",
        role="chat",
        journal=journal,
    )

    with (
        tracer.start_as_current_span("chat fake-model") as parent,
        cost_identity(session_id="opaque-thread", workload="job:opaque-job"),
    ):
        response = await model.request([], None, ModelRequestParameters())

    assert log == ["intent", "request", "final"]
    assert journal.finals == [KNOWN]
    assert response.usage.cost == KNOWN.usd

    attempts = _attempt_spans(exporter)
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.context is not None
    assert attempt.parent is not None
    assert attempt.parent.span_id == parent.get_span_context().span_id
    assert attempt.context.trace_id == parent.get_span_context().trace_id
    logical = [
        span
        for span in exporter.get_finished_spans()
        if _attributes(span).get("arno.cost.role") == "logical_request"
    ]
    assert len(logical) == 1
    attrs = _attributes(attempt)
    assert attrs["openinference.span.kind"] == "LLM"
    assert attrs["llm.model_name"] == "fake-model"
    assert attrs["llm.provider"] == "openai"
    assert attrs["arno.cost.attempt_id"]
    assert attrs["llm.cost.total"]
    assert attrs["arno.cost.completeness"] == "known"
    assert attrs["arno.cost.provider_charge"]
    assert attrs["arno.cost.billing_source"] == "wallet"
    assert attrs["session.id"] == "opaque-thread"
    assert attrs["arno.cost.workload"] == "job:opaque-job"


@pytest.mark.parametrize(
    "provider,settings,expected",
    [("openai", None, 8192), ("openai", {"max_tokens": 128}, 128), ("openai-chatgpt", None, None)],
)
async def test_dispatch_bounds_output_except_where_plan_api_rejects_caps(
    monkeypatch, provider, settings, expected
):
    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert (info.model_settings or {}).get("max_tokens") == expected
        return _response()

    monkeypatch.setattr(metering, "observe_cost", _observe_returning(KNOWN))
    model = MeteredModel(
        FunctionModel(function),
        provider=provider,
        billing_mode="metered",
        role="chat",
        journal=FakeJournal(),
    )
    await model.request([], settings, ModelRequestParameters())


async def test_partial_and_unknown_stay_unresolved(monkeypatch):
    for observation, expected, expected_cost in (
        (PARTIAL, "partial", PARTIAL.usd),
        (UNKNOWN, "unknown", None),
    ):
        monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("partial")
        monkeypatch.setattr(metering, "_tracer", lambda selected=tracer: selected)
        monkeypatch.setattr(metering, "observe_cost", _observe_returning(observation))
        journal = FakeJournal()
        model = MeteredModel(
            FunctionModel(_ok_function, model_name="fake-model"),
            provider="openai",
            billing_mode="metered",
            role="chat",
            journal=journal,
        )

        response = await model.request([], None, ModelRequestParameters())

        assert response.usage.cost == expected_cost
        attrs = _attributes(_attempt_spans(exporter)[0])
        assert attrs["arno.cost.completeness"] == expected


async def test_failure_records_unknown_and_keeps_the_error(monkeypatch):
    exporter, _tracer = _tracer_fixture(monkeypatch)
    journal = FakeJournal()

    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        raise ModelAPIError("fake-model", "provider failed")

    model = MeteredModel(
        FunctionModel(function, model_name="fake-model"),
        provider="openai",
        billing_mode="metered",
        role="chat",
        journal=journal,
    )

    with pytest.raises(ModelAPIError):
        await model.request([], None, ModelRequestParameters())

    assert [observation.status for observation in journal.finals] == ["unknown"]
    attempt = _attempt_spans(exporter)[0]
    assert _attributes(attempt)["arno.cost.completeness"] == "unknown"
    assert attempt.status.status_code.name == "ERROR"


async def test_fallback_keeps_the_failed_first_attempt(monkeypatch):
    _tracer_fixture(monkeypatch)
    journal = FakeJournal()

    def failing(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        raise ModelAPIError("first", "unavailable")

    failing_model = MeteredModel(
        FunctionModel(failing, model_name="first"),
        provider="openai",
        billing_mode="metered",
        role="chat",
        journal=journal,
    )
    succeeding_model = MeteredModel(
        FunctionModel(_ok_function, model_name="second"),
        provider="openai",
        billing_mode="metered",
        role="chat",
        journal=journal,
    )
    monkeypatch.setattr(metering, "observe_cost", _observe_returning(KNOWN))

    response = await FallbackModel(failing_model, succeeding_model).request(
        [], None, ModelRequestParameters()
    )

    assert response.model_name == "second"
    assert [observation.status for observation in journal.finals] == ["unknown", "known"]


async def test_cancelled_request_finalizes_unknown_then_propagates(monkeypatch):
    exporter, _tracer = _tracer_fixture(monkeypatch)
    journal = FakeJournal()
    entered = asyncio.Event()

    async def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    model = MeteredModel(
        FunctionModel(function, model_name="fake-model"),
        provider="openai",
        billing_mode="metered",
        role="chat",
        journal=journal,
    )

    task = asyncio.create_task(model.request([], None, ModelRequestParameters()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert [observation.status for observation in journal.finals] == ["unknown"]
    assert journal.finals[0].source == "cancelled"
    attempts = _attempt_spans(exporter)
    assert len(attempts) == 1
    assert _attributes(attempts[0])["arno.cost.completeness"] == "unknown"


async def test_incomplete_stream_is_partial_and_updates_usage(monkeypatch):
    exporter, _tracer = _tracer_fixture(monkeypatch)
    journal = FakeJournal()
    monkeypatch.setattr(metering, "observe_cost", _observe_returning(KNOWN))
    model = MeteredModel(
        streaming_model(lambda messages, info: _response(), text_chunks=3),
        provider="openai",
        billing_mode="metered",
        role="chat",
        journal=journal,
    )

    async with model.request_stream([], None, ModelRequestParameters()) as stream:
        async for _event in stream:
            break

    assert [observation.status for observation in journal.finals] == ["partial"]
    assert journal.finals[0].usd == KNOWN.usd
    attrs = _attributes(_attempt_spans(exporter)[0])
    assert attrs["arno.cost.completeness"] == "partial"


def test_an_incomplete_stream_cannot_become_complete(monkeypatch):
    monkeypatch.setattr(metering, "observe_cost", _observe_returning(KNOWN))

    class InterruptedStream:
        state = "interrupted"

        def get(self) -> ModelResponse:
            return _response()

    stream = cast(StreamedResponse, InterruptedStream())
    observation = metering._stream_observation(stream, provider="openai", billing_mode="metered")

    assert observation.status == "partial"
    assert observation.usd == KNOWN.usd


async def test_metered_call_finishes_or_records_unknown(monkeypatch):
    exporter, _tracer = _tracer_fixture(monkeypatch)
    journal = FakeJournal()

    async with metered_call(
        "openai", "whisper-test", "metered", "stt", "transcription", journal=journal
    ) as call:
        await call.finish(KNOWN)

    async with metered_call(
        "openai", "whisper-test", "metered", "stt", "transcription", journal=journal
    ) as _call:
        pass

    assert [observation.status for observation in journal.finals] == ["known", "unknown"]
    assert journal.finals[1].source == "unmeasured"
    spans = _attempt_spans(exporter)
    assert len(spans) == 2
    assert {_attributes(span)["gen_ai.request.model"] for span in spans} == {"whisper-test"}


async def test_local_profile_is_a_complete_zero_even_without_usage(monkeypatch):
    _tracer_fixture(monkeypatch)
    journal = FakeJournal()
    model = MeteredModel(
        FunctionModel(_unmeasured_function, model_name="llama-test"),
        provider="ollama",
        billing_mode="local",
        role="chat",
        journal=journal,
    )

    response = await model.request([], None, ModelRequestParameters())

    assert response.usage.cost == Decimal(0)
    assert response.provider_details is not None
    assert response.provider_details["arno_cost"] == {
        "provider": "ollama",
        "billing_mode": "local",
    }
    assert [observation.status for observation in journal.finals] == ["known"]
    assert journal.finals[0].source == "local_zero"
    # The native SpendLimits callback reads the stamped profile, not a catalog guess.
    assert price_response(response) == Decimal(0)
