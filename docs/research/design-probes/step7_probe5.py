"""Step 7 probe 5, no LLM: the full day-one capability list plus InputGuardrail, through the adapter."""
import asyncio
import warnings
from dataclasses import replace

import pydantic_ai_harness as h
import pydantic_ai_harness.compaction as c
import pydantic_ai_harness.tool_output_limits as t
from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.capabilities import Caching
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage
from pydantic_ai_harness.guardrails.detectors import personal_data, secret_data

warnings.filterwarnings("ignore")
seen = []
summary_calls = []


async def stream(messages, info):
    seen.append(messages)
    yield "ok"


def summary_fn(messages, info):
    summary_calls.append(1)
    return ModelResponse(parts=[TextPart("SUMMARY TEXT")])


async def get_financial_context() -> dict:
    """Read."""
    return {"today": "2026-10-31"}


primary = FunctionModel(stream_function=stream, profile=ModelProfile(context_window=8_000))
target = min(100_000, c.resolve_context_window(primary) // 2)
keep = target // 5


def build(guard_position):
    guard = h.InputGuardrail(guard=[secret_data(), personal_data(only=["credit_card"], extra={"pan": r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"})])
    caps = [
        h.CodeMode(tools={"code_mode": True}),
        Caching(),
        h.WarnOnCacheBusts(),
        h.SystemReminders(dynamic_reminders=[lambda ctx: "Today is 31 October. Since your snapshot: 0 writes."]),
        t.ToolOutputLimits(bands=[t.Band(over=10_000, action=t.Truncate(max_chars=10_000))], tool_filter=["run_code"]),
        c.TieredCompaction(
            tiers=[
                c.ClearToolResults(max_tokens=1, keep_pairs=5),
                c.FallbackCompaction(
                    fallback_chain=[
                        c.SummarizingCompaction(model=FunctionModel(summary_fn), max_messages=1, keep_tokens=keep, keep_messages=20, keep_user_messages=True, receipts=True),
                        c.SlidingWindowCompaction(max_messages=1, keep_tokens=keep, receipts=True),
                    ]
                ),
            ],
            target_tokens=target,
        ),
    ]
    caps.insert(guard_position, guard)
    return Agent(primary, output_type=[str, DeferredToolRequests], toolsets=[FunctionToolset(tools=[get_financial_context]).with_metadata(code_mode=True)], capabilities=caps)


def seed(text):
    return [ModelRequest(parts=[c.pin(text)]), ModelResponse(parts=[TextPart("Snapshot read.")])]


def swap(history, text):
    out = []
    for m in history:
        if isinstance(m, ModelRequest) and any(c.is_pinned(p) for p in m.parts):
            m = replace(m, parts=[c.pin(text) if c.is_pinned(p) else p for p in m.parts])
        out.append(m)
    return out


def stored(messages):
    return ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(messages))


async def turn(agent, history, text, uid):
    out = {"errors": []}

    async def done(result):
        out["all"] = result.all_messages()
        out["new"] = result.new_messages()

    adapter = VercelAIAdapter(agent=agent, run_input=SubmitMessage(id="t", messages=[UIMessage(id=uid, role="user", parts=[TextUIPart(text=text)])], trigger="submit-message"), manage_message_history="server", sdk_version=6, allowed_file_url_schemes=frozenset())
    async for ev in adapter.run_stream(message_history=history, on_complete=done):
        if type(ev).__name__ == "ErrorChunk":
            out["errors"].append(str(getattr(ev, "error_text", ev))[:170])
    return out


def report(label, r, card):
    sent = str(seen[-1]) if seen else ""
    print(f"   {label}: errors: {r['errors']} | card sent: {card in sent} | stored: {card in str(r.get('all', ''))} | new: {card in str(r.get('new', ''))} | snapshot: {'SNAPSHOT' in sent} | reminder: {'Today is 31 October' in sent}")


async def scenario(name, position):
    print(f"== {name}")
    summary_calls.clear()
    agent = build(position)
    r = await turn(agent, seed("SNAPSHOT-V1"), "my card 4111 1111 1111 1111", "u1")
    report("first turn", r, "4111")
    if "all" not in r:
        return
    h1 = stored(r["all"])
    r = await turn(agent, h1, "second card 5500 0000 0000 0004", "u2")
    report("second turn", r, "5500")
    h2 = stored(r["all"])
    r = await turn(agent, swap(h2, "SNAPSHOT-V2"), "after a swap 4012 8888 8888 1881", "u3")
    report("after a swap", r, "4012")
    h3 = stored(r["all"])
    for i in range(12):
        r = await turn(agent, h3, f"turn {i} " + "x" * 3000, f"u{10 + i}")
        if "all" not in r:
            print("   filler failed:", r["errors"])
            return
        h3 = stored(r["all"])
    print("   summary calls so far:", len(summary_calls), "| 'Snapshot read.' left:", "Snapshot read." in str(h3))
    r = await turn(agent, h3, "after compaction 3782 822463 10005", "u99")
    report("after compaction", r, "3782")
    print("   pins sent:", sum(1 for m in seen[-1] if isinstance(m, ModelRequest) for p in m.parts if c.is_pinned(p)), "| last request's parts:", [type(p).__name__ for p in seen[-1][-1].parts])


async def main():
    await scenario("Guard first in the capability list", 0)
    await scenario("Guard last in the capability list", 6)


asyncio.run(main())
