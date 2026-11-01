"""OpenInference spans: a model call is exported, and redacted text stays out."""

from __future__ import annotations

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from fin_agent.agent.builder import build_agent
from fin_agent.agent.tracing import build_tracer_provider


def test_a_turn_exports_model_spans_and_no_card_number():
    exporter = InMemorySpanExporter()
    provider = build_tracer_provider(span_exporter=exporter)

    def script(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("ok")])

    agent = build_agent(tracer_provider=provider)
    with agent.override(model=FunctionModel(script)):
        agent.run_sync("my card is 4111 1111 1111 1111")

    spans = exporter.get_finished_spans()
    assert spans
    payload = " ".join(str(span.attributes) for span in spans)
    assert "4111" not in payload
    names = {span.name for span in spans}
    assert any("chat" in name or "function" in name or "model" in name for name in names)
