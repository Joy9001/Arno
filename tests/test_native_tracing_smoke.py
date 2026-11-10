"""Native Pydantic AI tracing reaches a Phoenix-compatible OTLP endpoint.

The app installs one process-wide global tracer provider, so the smoke runs in a subprocess.
A local stub records the OTLP/HTTP payload and the test inspects real parent links.
"""

from __future__ import annotations

import gzip
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import override

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.trace.v1.trace_pb2 import Span

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "arno-smoke"

CHILD = r"""
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from arno.observability import bootstrap_observability, shutdown_observability


def script(messages, info):
    del info
    answered = any(
        isinstance(part, ToolReturnPart)
        for message in messages
        for part in getattr(message, "parts", [])
    )
    if answered:
        return ModelResponse(parts=[TextPart("done")])
    return ModelResponse(parts=[ToolCallPart("echo", {"text": "hi"})])


bootstrap_observability()
agent = Agent(FunctionModel(script), name="arno-smoke")


@agent.tool_plain
def echo(text: str) -> str:
    return text


agent.run_sync("go")
shutdown_observability()
"""


class _StubPhoenix(BaseHTTPRequestHandler):
    requests: list[bytes] = []
    authorizations: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        type(self).requests.append(body)
        type(self).authorizations.append(self.headers.get("Authorization", ""))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", "0")
        self.end_headers()

    @override
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the stdlib name
        del format, args


def _spans(requests: list[bytes]) -> list[Span]:
    spans: list[Span] = []
    for body in requests:
        payload = ExportTraceServiceRequest()
        payload.ParseFromString(body)
        for resource_spans in payload.resource_spans:
            for scope_spans in resource_spans.scope_spans:
                spans.extend(scope_spans.spans)
    return spans


def test_native_pydantic_ai_spans_reach_phoenix_with_one_root():
    _StubPhoenix.requests = []
    _StubPhoenix.authorizations = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubPhoenix)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env.pop("OTEL_SDK_DISABLED", None)
        env["PHOENIX_COLLECTOR_ENDPOINT"] = f"http://127.0.0.1:{server.server_address[1]}/v1/traces"
        env["PHOENIX_PROJECT_NAME"] = PROJECT
        env["PHOENIX_API_KEY"] = "dummy-phoenix-api-key"
        env["PYDANTIC_AI_NO_BANNER"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", CHILD],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()

    assert _StubPhoenix.requests, "the stub received no OTLP export"
    assert set(_StubPhoenix.authorizations) == {"Bearer dummy-phoenix-api-key"}
    spans = _spans(_StubPhoenix.requests)
    roots = [span for span in spans if not span.parent_span_id]
    assert len(roots) == 1, [span.name for span in roots]
    root = roots[0]
    assert root.name.startswith("invoke_agent")

    by_id = {(span.trace_id, span.span_id): span for span in spans}
    assert all(span is root or (span.trace_id, span.parent_span_id) in by_id for span in spans)
    tools = [span for span in spans if span.name.startswith("execute_tool")]
    assert [span.name for span in tools] == ["execute_tool echo"]
    assert tools[0].trace_id == root.trace_id
    assert not [span for span in spans if "db.system" in span.attributes]
    assert not [span for span in spans if "SELECT" in span.name.upper()]

    resources = []
    for body in _StubPhoenix.requests:
        payload = ExportTraceServiceRequest()
        payload.ParseFromString(body)
        for resource_spans in payload.resource_spans:
            resources.append(
                {
                    attribute.key: attribute.value.string_value
                    for attribute in resource_spans.resource.attributes
                }
            )
    assert any(item.get("openinference.project.name") == PROJECT for item in resources)
