"""Step 9 probe 4: our agent's turn, with a script, a write, and redaction, exported over OTLP to a tracing tool. No real LLM."""
import asyncio
import json
import os
import sys
import threading
import time
import uuid
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pydantic_ai_harness as h
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.capabilities import Instrumentation
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage
from pydantic_ai_harness.guardrails.detectors import personal_data, secret_data

warnings.filterwarnings("ignore")
ENDPOINT, HEADERS, MODE = sys.argv[1], json.loads(sys.argv[2]), (sys.argv[3] if len(sys.argv) > 3 else "plain")


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
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("connection", "close")
        self.end_headers()
        self.close_connection = True
        time.sleep(0.1)
        tools_done = sum(1 for m in body["messages"] if m.get("role") == "tool")
        usage = {"prompt_tokens": 9000, "completion_tokens": 120, "total_tokens": 9120, "prompt_tokens_details": {"cached_tokens": 8000}}
        if tools_done == 0:
            args = json.dumps({"code": "bal = await get_balance(account='SBI')\nbal"})
            self.sse(chunk(model, {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_script", "type": "function", "function": {"name": "run_code", "arguments": args}}]}))
            self.sse(chunk(model, finish="tool_calls"))
        elif tools_done == 1:
            self.sse(chunk(model, {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_write", "type": "function", "function": {"name": "record_movement", "arguments": '{"amount_rupees": 450, "note": "chai"}'}}]}))
            self.sse(chunk(model, finish="tool_calls"))
        else:
            self.sse(chunk(model, {"role": "assistant", "content": "Logged ₹450 for chai. Your SBI balance is ₹1,24,873."}))
            self.sse(chunk(model, finish="stop"))
        self.sse(chunk(model, usage=usage))
        self.wfile.write(b"data: [DONE]\n\n")


async def get_balance(account: str) -> dict:
    """Reads a balance."""
    return {"account": account, "balance_paise": 12487300, "text": "₹1,24,873"}


async def main():
    stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    model = OpenAIChatModel("gpt-5.4", provider=OpenAIProvider(base_url=f"http://127.0.0.1:{stub.server_address[1]}/v1", api_key="test-only"))

    memory = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "fin-agent", "openinference.project.name": "fin-agent"}))
    if MODE == "openinference":
        from openinference.instrumentation.pydantic_ai import OpenInferenceSpanProcessor

        provider.add_span_processor(OpenInferenceSpanProcessor())
    provider.add_span_processor(SimpleSpanProcessor(memory))
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=ENDPOINT, headers=HEADERS)))

    agent = Agent(
        model,
        name="fin-agent",
        instructions="You are Fin-Agent.",
        toolsets=[FunctionToolset(tools=[get_balance]).with_metadata(code_mode=True)],
        capabilities=[
            h.InputGuardrail(guard=[secret_data(), personal_data(only=["credit_card"])]),
            h.CodeMode(tools={"code_mode": True}),
            Instrumentation(settings=InstrumentationSettings(tracer_provider=provider, include_content=True)),
        ],
    )

    @agent.tool_plain
    async def record_movement(amount_rupees: int, note: str) -> dict:
        """Writes a movement."""
        return {"status": "saved", "undo_id": "u1", "summary": f"₹{amount_rupees} for {note}"}

    adapter = VercelAIAdapter(agent=agent, run_input=SubmitMessage(id="t", messages=[UIMessage(id=uuid.uuid4().hex, role="user", parts=[TextUIPart(text="chai 450, paid with card 4111 1111 1111 1111. Balance?")])], trigger="submit-message"), manage_message_history="server", sdk_version=6)
    text = ""
    async for ev in adapter.run_stream(conversation_id="thread-1", run_id=uuid.uuid4().hex):
        if type(ev).__name__ == "TextDeltaChunk":
            text += ev.delta
    provider.force_flush()
    spans = memory.get_finished_spans()
    dumped = " ".join(str(dict(s.attributes or {})) + " ".join(str(e.attributes) for e in s.events) for s in spans)
    print("reply:", text)
    print("spans:", [s.name for s in spans])
    print("raw card number in any span:", "4111" in dumped, "| redacted marker in spans:", "[redacted" in dumped)
    print("nested read tool span:", any("get_balance" in s.name for s in spans))
    stub.shutdown()


asyncio.run(main())
