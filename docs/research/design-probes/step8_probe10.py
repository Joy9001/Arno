"""Step 8 probe 10, no LLM: on each ending, does new_messages() hold your message, and does the UI message ID find it?"""
import asyncio
import warnings

import pydantic_ai_harness as h
import pydantic_ai_harness.compaction as c
from pydantic_ai import Agent, CancellationToken, capture_run_messages
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage

warnings.filterwarnings("ignore")


def from_ui_id(messages, ui_id):
    for i, m in enumerate(messages):
        meta = getattr(m, "metadata", None) or {}
        if meta.get("__pydantic_ai__", {}).get("ui_message_id") == ui_id:
            return list(messages[i:])
    return []


def seed():
    out = [ModelRequest(parts=[c.pin("SNAPSHOT")]), ModelResponse(parts=[TextPart("Snapshot read.")])]
    for i in range(3):
        out += [ModelRequest.user_text_prompt(f"old {i}"), ModelResponse(parts=[TextPart("old reply")])]
    return out


def agent(stream):
    return Agent(FunctionModel(stream_function=stream), capabilities=[h.SystemReminders(dynamic_reminders=[lambda ctx: "Today is 1 November."])])


def adapter(a, uid):
    return VercelAIAdapter(agent=a, run_input=SubmitMessage(id="t", messages=[UIMessage(id=uid, role="user", parts=[TextUIPart(text=f"MARKER {uid}")])], trigger="submit-message"), manage_message_history="server", sdk_version=6)


def report(label, new, whole, uid):
    anchored = from_ui_id(whole, uid)
    print(f"== {label}")
    print("   new_messages() holds your message:", "MARKER" in str(new), "| kinds:", [type(m).__name__ for m in new])
    print("   from the UI message ID holds it:", "MARKER" in str(anchored), "| kinds:", [type(m).__name__ for m in anchored], "| holds older turns:", "old 2" in str(anchored))


async def main():
    async def fine(messages, info):
        yield "the answer"

    saved = {}

    async def done(result):
        saved["new"], saved["all"] = result.new_messages(), result.all_messages()

    async for _ in adapter(agent(fine), "ok-1").run_stream(message_history=seed(), on_complete=done):
        pass
    report("Finished", saved["new"], saved["all"], "ok-1")

    token = CancellationToken()

    async def slow(messages, info):
        for i in range(50):
            await asyncio.sleep(0.02)
            if i == 3:
                token.cancel()
            yield f"w{i} "

    saved = {}

    async def cancelled(exc):
        saved["new"], saved["all"] = exc.new_messages(), exc.all_messages()

    async for _ in adapter(agent(slow), "stop-1").run_stream(message_history=seed(), cancellation_token=token, on_cancel=cancelled):
        pass
    report("Stop", saved["new"], saved["all"], "stop-1")

    async def dies(messages, info):
        yield "partial "
        raise ModelAPIError("m", "provider died")

    with capture_run_messages() as captured:
        async for _ in adapter(agent(dies), "fail-1").run_stream(message_history=seed()):
            pass
    report("Failure", [], list(captured), "fail-1")


asyncio.run(main())
