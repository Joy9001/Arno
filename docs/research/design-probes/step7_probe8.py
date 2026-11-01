"""Step 7 probe 8, no LLM: redaction on the turn where compaction really fires, and the turn after."""
import asyncio
import warnings

import pydantic_ai_harness as h
import pydantic_ai_harness.compaction as c
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage
from pydantic_ai_harness.guardrails.detectors import personal_data, secret_data

warnings.filterwarnings("ignore")
seen = []
summaries = []


async def stream(messages, info):
    seen.append(messages)
    yield "ok"


def summary_fn(messages, info):
    summaries.append(1)
    return ModelResponse(parts=[TextPart("SUMMARY TEXT")])


def guard():
    return h.InputGuardrail(guard=[secret_data(), personal_data(only=["credit_card"])])


def compaction(kind):
    if kind == "summary":
        return c.SummarizingCompaction(model=FunctionModel(summary_fn), max_messages=10, keep_tokens=60, keep_messages=4, keep_user_messages=True, receipts=True)
    return c.SlidingWindowCompaction(max_messages=10, keep_tokens=60, receipts=True)


def stored(messages):
    return ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(messages))


async def turn(agent, history, text, uid):
    out = {"errors": []}

    async def done(result):
        out["all"] = result.all_messages()

    adapter = VercelAIAdapter(agent=agent, run_input=SubmitMessage(id="t", messages=[UIMessage(id=uid, role="user", parts=[TextUIPart(text=text)])], trigger="submit-message"), manage_message_history="server", sdk_version=6)
    async for ev in adapter.run_stream(message_history=history, on_complete=done):
        if type(ev).__name__ == "ErrorChunk":
            out["errors"].append(str(getattr(ev, "error_text", ev))[:150])
    return out


async def scenario(kind, guard_first):
    summaries.clear()
    caps = [h.SystemReminders(dynamic_reminders=[lambda ctx: "Today is 31 October."]), compaction(kind)]
    caps.insert(0 if guard_first else len(caps), guard())
    agent = Agent(FunctionModel(stream_function=stream), capabilities=caps)
    print(f"== {kind}, guard {'first' if guard_first else 'last'}")
    history = [ModelRequest(parts=[c.pin("SNAPSHOT")]), ModelResponse(parts=[TextPart("Snapshot read.")])]
    failures = []
    cards = ["4111 1111 1111 1111", "5500 0000 0000 0004", "4012 8888 8888 1881"]
    for i in range(14):
        card = cards[i % 3]
        r = await turn(agent, history, f"turn {i} card {card} " + "x" * 40, f"u{i}")
        leaked = card.replace(" ", "")[:4] in str(seen[-1]) if seen else None
        if r["errors"] or leaked or "SNAPSHOT" not in str(seen[-1]):
            failures.append((i, r["errors"], "leaked" if leaked else "", "no snapshot" if "SNAPSHOT" not in str(seen[-1]) else ""))
        if "all" in r:
            history = stored(r["all"])
    sent = str(seen[-1])
    print("   summaries:", len(summaries), "| 'Snapshot read.' left:", "Snapshot read." in str(history), "| pins sent:", sum(1 for m in seen[-1] if isinstance(m, ModelRequest) for p in m.parts if c.is_pinned(p)))
    print("   any card in stored history:", any(k[:4] in str(history) for k in cards), "| failures:", failures[:4], f"({len(failures)} of 14)")


async def main():
    for kind in ("summary", "sliding"):
        for first in (True, False):
            await scenario(kind, first)


asyncio.run(main())
