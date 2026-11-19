"""The native root span carries the current message and reply for Phoenix's trace list."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from opentelemetry.util.types import AnyValue
from pydantic_ai import Agent, CancellationToken
from pydantic_ai.capabilities import AgentCapability, Instrumentation
from pydantic_ai.exceptions import ModelAPIError, RunCancelled
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionDef, FunctionModel, StreamFunctionDef
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai_harness.compaction import pin

from arno.agent.capabilities import RootSpanIO, redaction_guardrail
from arno.agent.deps import AgentDeps
from arno.agent.parser import ParseReport, build_parser_agent
from arno.importers.readers import open_documents


@pytest.fixture(autouse=True)
def _enable_otel(monkeypatch: pytest.MonkeyPatch) -> None:
    # conftest disables the SDK for the app; these tests build their own in-memory provider.
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)


def _reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    del messages, info
    return ModelResponse(parts=[TextPart("Anytime.")])


async def _stream_reply(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    del messages, info
    yield "Anytime."


def _fail(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    del messages, info
    raise ModelAPIError("test", "down")


def _agent(
    script: FunctionDef,
    *,
    include_content: bool = True,
    stream_script: StreamFunctionDef | None = None,
    redact: bool = False,
) -> tuple[Agent[Any, str], InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # No tools here, so a run may omit deps: some tests below pass them, some do not.
    capabilities: list[AgentCapability[Any]] = [RootSpanIO()]
    if redact:
        capabilities.append(redaction_guardrail())
    capabilities.append(
        Instrumentation(
            settings=InstrumentationSettings(
                tracer_provider=provider, include_content=include_content
            )
        )
    )
    model = (
        FunctionModel(script, stream_function=stream_script)
        if stream_script is not None
        else FunctionModel(script)
    )
    agent = Agent(model, capabilities=capabilities)
    return agent, exporter


def _root(exporter: InMemorySpanExporter) -> ReadableSpan:
    roots = [span for span in exporter.get_finished_spans() if span.name.startswith("invoke_agent")]
    assert len(roots) == 1
    return roots[0]


def _attributes(span: ReadableSpan) -> Mapping[str, AnyValue]:
    # The SDK types the property as `Mapping | None`; it always returns a mapping proxy.
    attributes = span.attributes
    assert attributes is not None
    return attributes


def _text(span: ReadableSpan, key: str) -> str:
    value = _attributes(span)[key]
    assert isinstance(value, str)
    return value


def _history() -> list[ModelMessage]:
    return [
        ModelRequest(parts=[pin('{"liquid_total": {"text": "₹0"}}')]),
        ModelResponse(parts=[TextPart("Snapshot read.")]),
        ModelRequest(parts=[UserPromptPart("Hey")]),
        ModelResponse(parts=[TextPart("Hello.")]),
        ModelRequest(parts=[UserPromptPart("Thanks")]),
    ]


async def test_root_span_preview_uses_the_current_message_not_the_snapshot():
    agent, exporter = _agent(_reply)

    result = await agent.run(message_history=_history())

    assert result.output == "Anytime."
    root = _root(exporter)
    assert _text(root, "input.value") == "Thanks"
    assert _text(root, "input.mime_type") == "text/plain"
    assert _text(root, "output.value") == "Anytime."
    assert _text(root, "output.mime_type") == "text/plain"
    assert root.status.status_code == StatusCode.UNSET
    assert "liquid_total" not in _text(root, "input.value")


async def test_root_span_preview_uses_the_run_prompt_when_there_is_one():
    agent, exporter = _agent(_reply)

    await agent.run("Hello prompt")

    root = _root(exporter)
    assert _text(root, "input.value") == "Hello prompt"
    assert _text(root, "output.value") == "Anytime."
    assert _text(root, "user.id") == "local-user"


async def test_root_span_preview_redacts_input_before_the_guardrail():
    seen: list[str] = []

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        for message in messages:
            for part in getattr(message, "parts", []):
                content = getattr(part, "content", "")
                if isinstance(content, str):
                    seen.append(content)
        return ModelResponse(parts=[TextPart("Anytime.")])

    agent, exporter = _agent(script, redact=True)
    secret = "sk-" + "z" * 24

    await agent.run(f"my key is {secret}")

    root = _root(exporter)
    assert secret not in _text(root, "input.value")
    assert secret not in "\n".join(seen)
    assert "[redacted:openai_key]" in _text(root, "input.value")
    assert _text(root, "input.value") in "\n".join(seen)
    assert _text(root, "input.mime_type") == "text/plain"
    assert _text(root, "output.value") == "Anytime."
    assert _text(root, "user.id") == "local-user"


async def test_a_parser_document_return_keeps_a_card_number_out_of_every_span(monkeypatch):
    doc = open_documents(b"Date,Description\n2026-01-01,Card 4111 1111 1111 1111\n")[0]

    async def progress(*args: object) -> None:
        return None

    monkeypatch.setattr("arno.agent.parser._progress", progress)

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returns = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if getattr(part, "part_kind", "") == "tool-return"
        ]
        if not returns:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code", {"code": "print(await doc_cells(import_id='synthetic'))"}
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(info.output_tools[0].name, ParseReport(status="gave_up").model_dump())
            ]
        )

    agent = build_parser_agent(model=FunctionModel(script))
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    agent.instrument = InstrumentationSettings(tracer_provider=provider, include_content=True)

    await agent.run(
        "inspect", deps=AgentDeps(conversation_id="unused", parse_docs={"synthetic": doc})
    )

    recorded = "\n".join(
        json.dumps(span.attributes, default=str) for span in exporter.get_finished_spans()
    )
    assert "[redacted:credit_card]" in recorded
    assert "4111 1111 1111 1111" not in recorded


async def test_root_span_preview_respects_include_content_off():
    agent, exporter = _agent(_reply, include_content=False)

    await agent.run(
        message_history=_history(), deps=AgentDeps(conversation_id="cnv", user_id="usr_opaque")
    )

    root = _root(exporter)
    assert "input.value" not in _attributes(root)
    assert "output.value" not in _attributes(root)
    assert _text(root, "user.id") == "usr_opaque"


async def test_root_span_counts_approvals_even_when_content_is_off():
    agent, exporter = _agent(_reply, include_content=False)

    await agent.run(
        message_history=_history(),
        deps=AgentDeps(conversation_id="cnv", approvals_offered=2, approvals=1),
    )

    attributes = _attributes(_root(exporter))
    assert attributes["arno.approvals.offered"] == 2
    assert attributes["arno.approvals.approved"] == 1


async def test_root_span_preview_keeps_one_user_across_conversations():
    agent, exporter = _agent(_reply)

    await agent.run(
        message_history=_history(), deps=AgentDeps(conversation_id="cnv_a", user_id="usr_opaque")
    )
    await agent.run(
        message_history=_history(), deps=AgentDeps(conversation_id="cnv_b", user_id="usr_opaque")
    )

    roots = [span for span in exporter.get_finished_spans() if span.name.startswith("invoke_agent")]
    assert len(roots) == 2
    assert {_text(root, "user.id") for root in roots} == {"usr_opaque"}
    assert {_text(root, "input.value") for root in roots} == {"Thanks"}


async def test_root_span_preview_leaves_output_absent_when_the_run_fails():
    agent, exporter = _agent(_fail)

    with pytest.raises(ModelAPIError):
        await agent.run(message_history=_history())

    root = _root(exporter)
    assert _text(root, "input.value") == "Thanks"
    assert "output.value" not in _attributes(root)
    assert root.status.status_code == StatusCode.ERROR


async def test_root_span_preview_leaves_output_absent_when_the_run_is_cancelled():
    agent, exporter = _agent(_reply)
    token = CancellationToken()
    token.cancel()

    with pytest.raises(RunCancelled):
        await agent.run(message_history=_history(), cancellation_token=token)

    root = _root(exporter)
    assert _text(root, "input.value") == "Thanks"
    assert "output.value" not in _attributes(root)


async def test_root_span_preview_works_for_a_streamed_run():
    agent, exporter = _agent(_reply, stream_script=_stream_reply)

    async with agent.run_stream(message_history=_history()) as stream:
        assert await stream.get_output() == "Anytime."

    root = _root(exporter)
    assert _text(root, "input.value") == "Thanks"
    assert _text(root, "output.value") == "Anytime."
