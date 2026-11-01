"""Step 9 probe 3: a trace row built from messages on every ending, with the real OpenAI client against a stub; and what OTel exports."""
import asyncio
import json
import threading
import time
import uuid
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.capabilities import Instrumentation, ModelFallback
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage

warnings.filterwarnings("ignore")
MODELS = {"primary": "gpt-5.4", "down": "e500"}


def chunk(model, delta=None, finish=None, usage=None):
    out = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": model, "choices": ([{"index": 0, "delta": delta or {}, "finish_reason": finish}] if usage is None else [])}
    if usage:
        out["usage"] = usage
    return out


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def sse(self, obj):
        self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
        self.wfile.flush()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        model = body["model"]
        if model == "e500":
            data = b'{"error":{"message":"boom"}}'
            self.send_response(500)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True
        time.sleep(0.15)
        usage = {"prompt_tokens": 9000, "completion_tokens": 120, "total_tokens": 9120, "prompt_tokens_details": {"cached_tokens": 8000}}
        if not any(m.get("role") == "tool" for m in body["messages"]):
            self.sse(chunk(model, {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "record", "arguments": '{"amount": 45000}'}}]}))
            self.sse(chunk(model, finish="tool_calls"))
        else:
            self.sse(chunk(model, {"role": "assistant", "content": "Logged SECRET-NOTE for ₹450."}))
            self.sse(chunk(model, finish="stop"))
        self.sse(chunk(model, usage=usage))
        self.wfile.write(b"data: [DONE]\n\n")


def trace_row(messages, turn, ending, first_output_ms):
    """Everything here comes from the same messages every ending already saves."""
    responses = [m for m in messages if isinstance(m, ModelResponse) and m.run_id == turn["run_id"]]
    returns = {p.tool_call_id: p for m in messages if isinstance(m, ModelRequest) and m.run_id == turn["run_id"] for p in m.parts if isinstance(p, ToolReturnPart)}
    calls = [p for m in responses for p in m.parts if isinstance(p, ToolCallPart)]
    cost = 0.0
    priced = True
    for m in responses:
        try:
            cost += float(m.cost().total_price)
        except Exception:  # noqa: BLE001
            priced = False
    return {
        "ending": ending,
        "models": [m.model_name for m in responses],
        "fell_back_from": [getattr(a, "model_name", str(a)) for m in responses for a in (m.failed_attempts or [])],
        "input_tokens": sum(m.usage.input_tokens for m in responses),
        "cached_tokens": sum(m.usage.cache_read_tokens for m in responses),
        "output_tokens": sum(m.usage.output_tokens for m in responses),
        "cost_usd": round(cost, 5) if priced else None,
        "finish_reasons": [m.finish_reason for m in responses],
        "tool_calls": [(c.tool_name, getattr(returns.get(c.tool_call_id), "outcome", "no return")) for c in calls],
        "first_output_ms": first_output_ms,
        "duration_s": round((responses[-1].timestamp - turn["started"]).total_seconds(), 2) if responses and responses[-1].timestamp else None,
    }


def agent_for(port, primary, capabilities=()):
    def m(name):
        return OpenAIChatModel(name, provider=OpenAIProvider(base_url=f"http://127.0.0.1:{port}/v1", api_key="test-only"))

    a = Agent(m(primary), capabilities=[*capabilities])

    @a.tool_plain
    def record(amount: int) -> str:
        """Writes."""
        return "saved SECRET-RETURN"

    return a, m


async def run(a, ending_label):
    from datetime import datetime, timezone

    turn = {"run_id": uuid.uuid4().hex, "started": datetime.now(timezone.utc)}
    saved = {}

    async def done(result):
        saved["all"] = result.all_messages()

    adapter = VercelAIAdapter(agent=a, run_input=SubmitMessage(id="t", messages=[UIMessage(id=uuid.uuid4().hex, role="user", parts=[TextUIPart(text="log SECRET-INPUT 450")])], trigger="submit-message"), manage_message_history="server", sdk_version=6)
    start, first = time.perf_counter(), None
    with capture_run_messages() as captured:
        async for ev in adapter.run_stream(run_id=turn["run_id"], on_complete=done):
            if first is None and type(ev).__name__ in ("TextDeltaChunk", "TextStartChunk", "ToolInputStartChunk"):
                first = round((time.perf_counter() - start) * 1000)
    return trace_row(saved.get("all", list(captured)), turn, ending_label, first)


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    a, m = agent_for(port, "gpt-5.4")
    print("== Finished, a tool then an answer")
    print("  ", await run(a, "finished"))

    a, m = agent_for(port, "e500")
    a2 = Agent(a.model, capabilities=[ModelFallback(m("gpt-5.4"))])

    @a2.tool_plain
    def record(amount: int) -> str:
        """Writes."""
        return "saved"

    print("== Main model down, backup answers")
    print("  ", await run(a2, "finished"))

    print("== All models down")
    print("  ", await run(agent_for(port, "e500")[0], "failed"))

    print("== OpenTelemetry, off unless configured: what leaves with include_content=False")
    for include in (True, False):
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        a, _ = agent_for(port, "gpt-5.4", [Instrumentation(settings=InstrumentationSettings(tracer_provider=provider, include_content=include))])
        await run(a, "finished")
        text = " ".join(str(dict(s.attributes or {})) + str(s.name) for s in exporter.get_finished_spans())
        print(f"   include_content={include}: spans {len(exporter.get_finished_spans())} | your text: {'SECRET-INPUT' in text} | reply: {'SECRET-NOTE' in text} | tool return: {'SECRET-RETURN' in text} | tool args (45000): {'45000' in text} | tool name: {'record' in text}")
    server.shutdown()


asyncio.run(main())
