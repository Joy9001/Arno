"""Native Pydantic AI traces exported straight to Phoenix, plus cost-only replay.

One process-wide `phoenix.otel.register()` call sets the global OpenTelemetry tracer provider
from the standard Phoenix environment (`PHOENIX_COLLECTOR_ENDPOINT`, `PHOENIX_PROJECT_NAME`,
`PHOENIX_API_KEY`), then `Agent.instrument_all()` turns on Pydantic AI's own agent, model and
tool spans. The model wrapper adds one cost-bearing attempt span below each logical model
request; Phoenix converts the native `gen_ai.*` attributes on ingestion. There is no custom
processor, sampler, metric, or log bridge.

Tracing is opt-in: with no collector endpoint configured, nothing is registered and nothing is
instrumented. `register()` would otherwise default to gRPC on `localhost:4317`, which a host CLI
with no Phoenix running answers with an exporter retry storm and a shutdown that waits it out.

`OTEL_SDK_DISABLED` is the standard SDK kill switch: when true, nothing is registered or
instrumented, traces go nowhere, and cost replay exports nothing.

Cost replay rebuilds a standard `ReadableSpan` with the attempt's retained trace/span IDs and
only its recorded cost metadata. Phoenix ignores duplicate span IDs, retaining full spans that
already arrived. Replay uses the standard OTLP exporter, not a custom processor or transport.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import SpanContext, SpanKind, TraceFlags
from phoenix.otel import register
from pydantic_ai import Agent

logger = logging.getLogger(__name__)

_provider: Any = None
_started = False


def _sdk_disabled() -> bool:
    return os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _collector_endpoint() -> str | None:
    """The endpoint the Phoenix SDK would export to, or `None` when tracing is not configured.

    The SDK's own resolver is used so a `.env.phoenix` file keeps working; process environment
    variables still win.
    """
    from phoenix.otel.settings import get_env_collector_endpoint

    return (get_env_collector_endpoint() or "").strip() or None


def _project_name() -> str:
    from phoenix.otel.settings import get_env_project_name

    return get_env_project_name()


def _headers() -> Mapping[str, str] | None:
    from phoenix.otel.settings import get_env_client_headers, get_env_phoenix_auth_header

    return {**(get_env_client_headers() or {}), **(get_env_phoenix_auth_header() or {})} or None


def bootstrap_observability() -> Any:
    """Install native Pydantic AI tracing once for this process, or return `None` if disabled."""
    global _provider, _started
    if _started:
        return _provider
    _started = True
    if _sdk_disabled():
        logger.info("Phoenix tracing disabled; spend counters and cost journal remain active")
        return None
    endpoint = _collector_endpoint()
    if endpoint is None:
        logger.info("tracing is off: no PHOENIX_COLLECTOR_ENDPOINT is set, so nothing is exported")
        return None
    # Pass the endpoint through so its `/v1/traces` path selects OTLP/HTTP; `register()`
    # alone rewrites a bare env URL to the gRPC port. The standard batch processor keeps
    # export off the request path.
    _provider = register(endpoint=endpoint, batch=True)
    Agent.instrument_all()
    return _provider


def shutdown_observability() -> None:
    """Flush and close the Phoenix exporter. Safe to call when nothing was started."""
    global _provider
    provider, _provider = _provider, None
    if provider is not None:
        provider.shutdown()


def build_span(
    *,
    name: str,
    trace_id: str,
    span_id: str,
    attributes: Mapping[str, str | int | float | bool],
    start_time_ns: int,
    end_time_ns: int,
    project_name: str | None = None,
) -> ReadableSpan:
    """A standard SDK span with retained IDs, for cost-only replay."""
    context = SpanContext(
        trace_id=int(trace_id, 16),
        span_id=int(span_id, 16),
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return ReadableSpan(
        name=name,
        context=context,
        parent=None,
        resource=Resource.create(
            {
                "openinference.project.name": project_name or _project_name(),
                "service.name": "arno",
            }
        ),
        attributes=dict(attributes),
        kind=SpanKind.INTERNAL,
        start_time=start_time_ns,
        end_time=end_time_ns,
    )


def export_replay(spans: Sequence[ReadableSpan], exporter: SpanExporter | None = None) -> int:
    """Export replay spans with the standard OTLP exporter; return how many were accepted.

    `exporter` exists so tests can inspect the exported IDs with an in-memory exporter. A
    transport failure is logged and returns zero, leaving the journal rows for a later pass.
    """
    if not spans:
        return 0
    owned = exporter is None
    if owned:
        if _sdk_disabled():
            return 0
        endpoint = _collector_endpoint()
        if endpoint is None:
            logger.info("cost replay skipped: no PHOENIX_COLLECTOR_ENDPOINT is set")
            return 0
        exporter = OTLPSpanExporter(endpoint=endpoint, headers=_headers())
    try:
        result = exporter.export(list(spans))
        return len(spans) if result == SpanExportResult.SUCCESS else 0
    except Exception as exc:  # noqa: BLE001 - telemetry loss must not fail finance work
        logger.warning("cost replay export failed: %s", exc)
        return 0
    finally:
        if owned:
            exporter.shutdown()


__all__ = [
    "bootstrap_observability",
    "build_span",
    "export_replay",
    "shutdown_observability",
]
