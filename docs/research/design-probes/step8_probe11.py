"""Step 8 probe 11, no LLM: the transcript rule when compaction fires in the same turn, and on a turn resumed from a card."""
import asyncio
import uuid
import warnings

import pydantic_ai_harness.compaction as c
from pydantic_ai import Agent, ApprovalRequired, DeferredToolRequests, DeferredToolResults, RunContext, capture_run_messages
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage

warnings.filterwarnings("ignore")


def this_turn(messages, run_id, ui_id):
    """Your request, found by its UI message ID, plus everything tagged with this run."""
    out = []
    for m in messages:
        meta = getattr(m, "metadata", None) or {}
        if m.run_id == run_id or (ui_id and meta.get("__pydantic_ai__", {}).get("ui_message_id") == ui_id):
            out.append(m)
    return out


def seed():
    out = [ModelRequest(parts=[c.pin("SNAPSHOT")]), ModelResponse(parts=[TextPart("Snapshot read.")])]
    for i in range(10):
        out += [ModelRequest.user_text_prompt(f"old {i} " + "x" * 400), ModelResponse(parts=[TextPart("y" * 400)])]
    return out


def compacting():
    return c.TieredCompaction(tiers=[c.SlidingWindowCompaction(max_messages=1, keep_tokens=60, receipts=True)], target_tokens=200)


def show(label, messages, run_id, ui_id):
    turn = this_turn(messages, run_id, ui_id)
    print(f"== {label}")
    print("   old turns left in history:", sum("x" * 400 in str(m) for m in messages), "of 10 | this turn:", [type(m).__name__ for m in turn], "| your message:", "MARKER" in str(turn), "| older turns in it:", any("old " in str(m) for m in turn))


async def main():
    async def fine(messages, info):
        yield "the answer"

    async def dies(messages, info):
        yield "partial "
        raise ModelAPIError("m", "provider died")

    for label, stream in (("Finished, compaction fired", fine), ("Failure, compaction fired", dies)):
        run_id, saved = uuid.uuid4().hex, {}

        async def done(result):
            saved["all"] = result.all_messages()

        a = Agent(FunctionModel(stream_function=stream), capabilities=[compacting()])
        adapter = VercelAIAdapter(agent=a, run_input=SubmitMessage(id="t", messages=[UIMessage(id="ui-1", role="user", parts=[TextUIPart(text="MARKER new")])], trigger="submit-message"), manage_message_history="server", sdk_version=6)
        with capture_run_messages() as captured:
            async for _ in adapter.run_stream(message_history=seed(), run_id=run_id, on_complete=done):
                pass
        show(label, saved.get("all", list(captured)), run_id, "ui-1")

    async def flow(messages, info):
        if not any(isinstance(p, ToolReturnPart) for m in messages if isinstance(m, ModelRequest) for p in m.parts):
            yield {0: DeltaToolCall(name="lasting", json_args="{}", tool_call_id="call_l")}
            return
        yield "done"

    a = Agent(FunctionModel(stream_function=flow), output_type=[str, DeferredToolRequests])

    @a.tool
    async def lasting(ctx: RunContext) -> str:
        """Lasting write."""
        if not ctx.tool_call_approved:
            raise ApprovalRequired()
        return "saved"

    run1, saved = uuid.uuid4().hex, {}

    async def done1(result):
        saved["all"] = result.all_messages()

    adapter = VercelAIAdapter(agent=a, run_input=SubmitMessage(id="t", messages=[UIMessage(id="ui-2", role="user", parts=[TextUIPart(text="MARKER remember")])], trigger="submit-message"), manage_message_history="server", sdk_version=6)
    async for _ in adapter.run_stream(message_history=[], run_id=run1, on_complete=done1):
        pass
    print("== A confirm card")
    print("   first turn:", [type(m).__name__ for m in this_turn(saved["all"], run1, "ui-2")], "| your message:", "MARKER" in str(this_turn(saved["all"], run1, "ui-2")))
    history = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(saved["all"]))
    run2, saved2 = uuid.uuid4().hex, {}

    async def done2(result):
        saved2["all"] = result.all_messages()

    adapter = VercelAIAdapter(agent=a, run_input=SubmitMessage(id="t", messages=[UIMessage(id="x", role="assistant", parts=[])], trigger="submit-message"), manage_message_history="server", sdk_version=6)
    async for _ in adapter.run_stream(message_history=history, run_id=run2, on_complete=done2, deferred_tool_results=DeferredToolResults(approvals={"call_l": True})):
        pass
    turn2 = this_turn(saved2["all"], run2, None)
    print("   after the approval:", [type(m).__name__ for m in turn2], "| holds the result:", "saved" in str(turn2), "| your earlier message repeated:", "MARKER" in str(turn2))


asyncio.run(main())
