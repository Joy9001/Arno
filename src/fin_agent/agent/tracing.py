"""OpenInference spans, exported to Phoenix only when an endpoint is set.

With no endpoint the provider still exists so spans can be inspected in tests, but nothing leaves
the process. `include_binary_content=False` keeps images and audio out of traces.
"""

from __future__ import annotations

import os
from typing import Any

from openinference.instrumentation.pydantic_ai import OpenInferenceSpanProcessor
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor


def _endpoint() -> str | None:
    return (
        os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
        or ""
    ).strip() or None


def build_tracer_provider(
    *, span_exporter: Any = None, project_name: str = "fin-agent", set_global: bool = True
) -> TracerProvider:
    """A provider with the OpenInference processor, and OTLP only when configured.

    Eval trials pass their own project name so a failed trial is easy to find in Phoenix.
    They also pass `set_global=False`, because the global provider can only be set once.
    """
    provider = TracerProvider(
        resource=Resource.create(
            {
                "openinference.project.name": project_name,
                "service.name": "fin-agent",
            }
        )
    )
    provider.add_span_processor(OpenInferenceSpanProcessor())
    endpoint = _endpoint()
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    elif span_exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    if set_global:
        trace.set_tracer_provider(provider)
    return provider


def tracer() -> trace.Tracer:
    return trace.get_tracer("fin-agent")


def current_trace_id() -> str | None:
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return format(span_context.trace_id, "032x")


__all__ = ["build_tracer_provider", "current_trace_id", "tracer"]
