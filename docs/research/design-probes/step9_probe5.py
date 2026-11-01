"""Step 9 probe 5, no LLM: one parent span per turn with our own facts, a number check inside it, and a tracing tool that's down."""
import asyncio
import json
import sys
import time
import uuid
import warnings

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.capabilities import Instrumentation
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage

warnings.filterwarnings("ignore")
ENDPOINT, HEADERS = sys.argv[1], json.loads(sys.argv[2])


async def good(messages, info):
    yield "Your balance is ₹1,24,873."


async def dies(messages, info):
    yield "partial "
    raise ModelAPIError("m", "provider died")


async def turn(provider, stream, label):
    tracer = provider.get_tracer("fin-agent")
    agent = Agent(FunctionModel(stream_function=stream), name="fin-agent", capabilities=[Instrumentation(settings=InstrumentationSettings(tracer_provider=provider, include_content=True))])
    adapter = VercelAIAdapter(agent=agent, run_input=SubmitMessage(id="t", messages=[UIMessage(id=uuid.uuid4().hex, role="user", parts=[TextUIPart(text="balance?")])], trigger="submit-message"), manage_message_history="server", sdk_version=6)
    start, first, ending = time.perf_counter(), None, "failed"

    async def done(result):
        nonlocal ending
        ending = "finished"

    with tracer.start_as_current_span("chat turn", attributes={"session.id": "thread-1", "fin_agent.message_id": "m1"}) as span:
        with capture_run_messages():
            async for ev in adapter.run_stream(conversation_id="thread-1", on_complete=done):
                if first is None and type(ev).__name__ == "TextDeltaChunk":
                    first = round((time.perf_counter() - start) * 1000)
        span.set_attribute("fin_agent.ending", ending)
        span.set_attribute("fin_agent.first_output_ms", first or -1)
        span.set_attribute("fin_agent.untraced_numbers", ["₹999"] if label == "good" else [])
        trace_id = format(span.get_span_context().trace_id, "032x")
    return trace_id, round((time.perf_counter() - start) * 1000)


async def main():
    memory = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "fin-agent", "openinference.project.name": "fin-agent"}))
    provider.add_span_processor(SimpleSpanProcessor(memory))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=ENDPOINT, headers=HEADERS, timeout=2)))
    t1, ms1 = await turn(provider, good, "good")
    t2, ms2 = await turn(provider, dies, "dies")
    start = time.perf_counter()
    provider.force_flush(timeout_millis=5000)
    flush = round(time.perf_counter() - start, 2)
    spans = memory.get_finished_spans()
    roots = {s.context.trace_id: s for s in spans if s.name == "chat turn"}
    print("turn ms:", ms1, ms2, "| flush s:", flush, "| spans:", len(spans))
    for s in roots.values():
        kids = [x.name for x in spans if x.context.trace_id == s.context.trace_id and x is not s]
        print("  ", dict(s.attributes), "| children:", kids)
    print("trace ids:", t1, t2)
    provider.shutdown()


asyncio.run(main())
