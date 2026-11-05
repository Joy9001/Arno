"""Native Pydantic AI traces exported straight to Phoenix.

One process-wide `phoenix.otel.register()` call sets the global OpenTelemetry tracer provider
from the standard Phoenix environment (`PHOENIX_COLLECTOR_ENDPOINT`, `PHOENIX_PROJECT_NAME`,
`PHOENIX_API_KEY`), then `Agent.instrument_all()` turns on Pydantic AI's own agent, model and
tool spans. Phoenix converts the native `gen_ai.*` attributes on ingestion, so there is no
custom span, metric, sampler, or log bridge here.

`OTEL_SDK_DISABLED` is the standard SDK kill switch: when true, nothing is registered or
instrumented and traces go nowhere. No other switch exists.
"""

from __future__ import annotations

import os
from typing import Any

from phoenix.otel import register
from pydantic_ai import Agent

_provider: Any = None
_started = False


def _sdk_disabled() -> bool:
    return os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def bootstrap_observability() -> Any:
    """Install native Pydantic AI tracing once for this process, or return `None` if disabled."""
    global _provider, _started
    if _started:
        return _provider
    _started = True
    if _sdk_disabled():
        return None
    # Pass the env endpoint through so its `/v1/traces` path selects OTLP/HTTP; `register()`
    # alone rewrites a bare env URL to the gRPC port. No endpoint keeps the Phoenix default.
    endpoint = (os.environ.get("PHOENIX_COLLECTOR_ENDPOINT") or "").strip() or None
    _provider = register(endpoint=endpoint)
    Agent.instrument_all()
    return _provider


def shutdown_observability() -> None:
    """Flush and close the Phoenix exporter. Safe to call when nothing was started."""
    global _provider
    provider, _provider = _provider, None
    if provider is not None:
        provider.shutdown()


__all__ = ["bootstrap_observability", "shutdown_observability"]
