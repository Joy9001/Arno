"""Step 7 probe 4, no LLM: seed the snapshot as its own exchange so redaction works on a thread's first turn."""
import asyncio
import warnings
from dataclasses import replace

import pydantic_ai_harness as h
import pydantic_ai_harness.compaction as c
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage
from pydantic_ai_harness.guardrails.detectors import personal_data, secret_data

warnings.filterwarnings("ignore")
seen = []


async def stream(messages, info):
    seen.append(messages)
    yield "ok"


guard = h.InputGuardrail(guard=[secret_data(), personal_data(only=["credit_card"], extra={"pan": r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", "aadhaar": r"\b[2-9]\d{3}[ -]\d{4}[ -]\d{4}\b"})])
agent = Agent(
    FunctionModel(stream_function=stream),
    capabilities=[guard, c.SlidingWindowCompaction(max_messages=8, keep_tokens=20, preserve_first_user_message=False)],
)


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


async def turn(history, text, uid):
    out = {"errors": []}

    async def done(result):
        out["all"] = result.all_messages()
        out["new"] = result.new_messages()

    adapter = VercelAIAdapter(agent=agent, run_input=SubmitMessage(id="t", messages=[UIMessage(id=uid, role="user", parts=[TextUIPart(text=text)])], trigger="submit-message"), manage_message_history="server")
    async for ev in adapter.run_stream(message_history=history, on_complete=done):
        if type(ev).__name__ == "ErrorChunk":
            out["errors"].append(str(getattr(ev, "error_text", ev))[:200])
    return out


async def main():
    print("== First turn, with a card number, on a seeded snapshot")
    r = await turn(seed("SNAPSHOT-V1"), "my card 4111 1111 1111 1111 and ramesh@okaxis", "u1")
    sent = str(seen[-1])
    print("   errors:", r["errors"], "| card sent:", "4111" in sent, "| card stored:", "4111" in str(r["all"]), "| UPI ID:", "ramesh@okaxis" in sent, "| snapshot:", "SNAPSHOT-V1" in sent)
    print("   new_messages has the snapshot:", "SNAPSHOT" in str(r["new"]), "| messages sent:", len(seen[-1]))
    h1 = stored(r["all"])

    print("== Second turn, another card, after JSON storage")
    r = await turn(h1, "other card 5500 0000 0000 0004", "u2")
    print("   errors:", r["errors"], "| card sent:", "5500" in str(seen[-1]), "| snapshot:", "SNAPSHOT-V1" in str(seen[-1]))
    h2 = stored(r["all"])

    print("== Swap the snapshot, then a turn with a card")
    r = await turn(swap(h2, "SNAPSHOT-V2"), "third card 4111 1111 1111 1111", "u3")
    sent = str(seen[-1])
    print("   errors:", r["errors"], "| V1:", "SNAPSHOT-V1" in sent, "| V2:", "SNAPSHOT-V2" in sent, "| card sent:", "4111" in sent)
    h3 = stored(r["all"])

    print("== Enough turns to compact")
    for i in range(10):
        r = await turn(h3, f"turn {i} " + "x" * 400, f"u{10 + i}")
        h3 = stored(r["all"])
    sent = str(seen[-1])
    print("   errors:", r["errors"], "| messages sent:", len(seen[-1]), "| V2:", "SNAPSHOT-V2" in sent, "| pins:", sum(1 for m in seen[-1] if isinstance(m, ModelRequest) for p in m.parts if c.is_pinned(p)), "| 'Snapshot read.' kept:", "Snapshot read." in sent)


asyncio.run(main())
