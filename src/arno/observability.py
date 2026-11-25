"""Stock OpenTelemetry tracing to Phoenix, opt-in.

The standard SDK reads `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`,
`OTEL_SERVICE_NAME`, and `OTEL_RESOURCE_ATTRIBUTES` itself. With no endpoint nothing is installed:
an unset endpoint would otherwise make the exporter retry against localhost and stall shutdown.
`OTEL_SDK_DISABLED` is the standard kill switch.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings

logger = logging.getLogger(__name__)

_provider: TracerProvider | None = None
_started = False


def bootstrap_observability(app: FastAPI | None = None) -> TracerProvider | None:
    """Install tracing once per process, or return `None` when it is off."""
    global _provider, _started
    if _started:
        return _provider
    _started = True
    disabled = os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    if disabled or not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip():
        logger.info("tracing is off: OTEL_EXPORTER_OTLP_ENDPOINT is unset or OTEL_SDK_DISABLED")
        return None
    _provider = TracerProvider(resource=Resource.create())
    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(_provider)
    if app is not None:
        FastAPIInstrumentor.instrument_app(app)
    AsyncPGInstrumentor().instrument()
    # Keep binary payloads (images) out of spans.
    Agent.instrument_all(InstrumentationSettings(include_binary_content=False))
    return _provider


def shutdown_observability() -> None:
    """Flush and close the exporter. Safe to call when nothing was started."""
    global _provider
    provider, _provider = _provider, None
    if provider is not None:
        provider.shutdown()
