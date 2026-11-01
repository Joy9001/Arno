"""Step 8 probe 2, no LLM: saving history on failure, a tool that throws, and a confirm card across a restart."""
import asyncio
import warnings

from pydantic_ai import Agent, ApprovalRequired, DeferredToolRequests, RunContext, capture_run_messages
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart, repair_messages
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage

warnings.filterwarnings("ignore")
writes: list = []


def describe(messages):
    out = []
    for m in messages:
        if isinstance(m, ModelRequest):
            out.append("req[" + ",".join(type(p).__name__ + (f":{p.outcome}" if isinstance(p, ToolReturnPart) else "") for p in m.parts) + "]")
        else:
            out.append(f"resp({getattr(m, 'state', '')})[" + ",".join(type(p).__name__ for p in m.parts) + "]")
    return " ".join(out)


def stored(messages):
    return ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(messages))


def has_return(messages):
    return any(isinstance(p, ToolReturnPart) for m in messages if isinstance(m, ModelRequest) for p in m.parts)


async def write_then_fail(messages, info):
    if not has_return(messages):
        yield {0: DeltaToolCall(name="record", json_args='{"amount": 5}', tool_call_id="call_w")}
        return
    yield "partial answer "
    raise RuntimeError("provider died mid-stream")


async def fail_first_request(messages, info):
    raise RuntimeError("provider down")
    yield ""  # noqa


async def answer(messages, info):
    yield "ok"


def make_agent(stream, capabilities=()):
    agent = Agent(FunctionModel(stream_function=stream), output_type=[str, DeferredToolRequests], capabilities=list(capabilities))

    @agent.tool
    async def record(ctx: RunContext, amount: int) -> str:
        """Writes."""
        writes.append(ctx.tool_call_id)
        return f"saved {amount}"

    return agent


async def turn(agent, text=None, history=None, approvals=None, uid="u1", capture=True):
    msgs = []
    if text:
        msgs = [UIMessage(id=uid, role="user", parts=[TextUIPart(text=text)])]
    out = {"errors": [], "types": []}

    async def done(result):
        out["all"] = result.all_messages()
        out["output"] = result.output

    adapter = VercelAIAdapter(agent=agent, run_input=SubmitMessage(id="t", messages=msgs or [UIMessage(id="x", role="assistant", parts=[])], trigger="submit-message"), manage_message_history="server", sdk_version=6)
    if capture:
        with capture_run_messages() as captured:
            async for ev in adapter.run_stream(message_history=history, on_complete=done, deferred_tool_results=approvals):
                if type(ev).__name__ == "ErrorChunk":
                    out["errors"].append(str(getattr(ev, "error_text", ev))[:90])
        out["captured"] = list(captured)
    return out


class ToolErrorsToModel(AbstractCapability):
    """A tool that throws something unexpected becomes a failed result the model can read."""

    async def on_tool_execute_error(self, ctx, *, call, tool_def, args, error):
        if isinstance(error, (ToolFailed,)):
            raise error
        raise ToolFailed(f"{call.tool_name} failed: {type(error).__name__}. Nothing was written.")


async def main():
    print("== A. capture_run_messages, provider dies mid-stream after a write")
    r = await turn(make_agent(write_then_fail), "log 5")
    print("   errors:", r["errors"], "| on_complete:", "all" in r)
    print("   captured:", describe(r["captured"]))
    fixed = repair_messages(stored(r["captured"]))
    print("   repaired:", describe(fixed))
    r2 = await turn(make_agent(answer), "what happened?", history=fixed, uid="u2")
    print("   next turn:", r2.get("output"), "| errors:", r2["errors"], "| history:", describe(r2.get("all", []))[:150])
    print("   same, with the unrepaired history:")
    r2b = await turn(make_agent(answer), "what happened?", history=stored(r["captured"]), uid="u3")
    print("   next turn:", r2b.get("output"), "| errors:", r2b["errors"])

    print("== B. capture_run_messages, provider down on the first request")
    r = await turn(make_agent(fail_first_request), "log 5")
    print("   errors:", r["errors"], "| captured:", describe(r["captured"]))
    r2 = await turn(make_agent(answer), "again?", history=repair_messages(stored(r["captured"])), uid="u2")
    print("   next turn:", r2.get("output"), "| errors:", r2["errors"], "| your first message kept:", "log 5" in str(r2.get("all", "")))

    print("== C. A tool throws an unexpected error")

    async def calls_boom(messages, info):
        if not has_return(messages):
            yield {0: DeltaToolCall(name="boom", json_args="{}", tool_call_id="call_b")}
            return
        last = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)][-1]
        yield f"model saw: {last.content} ({last.outcome})"

    for label, caps in [("no capability", []), ("ToolErrorsToModel", [ToolErrorsToModel()])]:
        agent = Agent(FunctionModel(stream_function=calls_boom), capabilities=caps)

        @agent.tool_plain
        async def boom() -> str:
            """Throws."""
            raise ConnectionError("db down at 10.0.0.5:5432")

        r = await turn(agent, "go")
        print(f"   {label}: output:", r.get("output"), "| errors:", r["errors"])

    print("== D. A confirm card across a restart, then a failure after the approval")
    writes.clear()

    async def gated_flow(messages, info):
        calls = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        if not calls:
            yield {0: DeltaToolCall(name="lasting", json_args='{"x": 1}', tool_call_id="call_l")}
            return
        yield "done"

    def gated_agent(stream):
        agent = Agent(FunctionModel(stream_function=stream), output_type=[str, DeferredToolRequests])

        @agent.tool
        async def lasting(ctx: RunContext, x: int) -> str:
            """Lasting write."""
            if not ctx.tool_call_approved:
                raise ApprovalRequired()
            writes.append(ctx.tool_call_id)
            return "saved"

        return agent

    r = await turn(gated_agent(gated_flow), "remember this")
    saved = ModelMessagesTypeAdapter.dump_json(r["all"])
    print("   card raised; run ended with:", type(r.get("output")).__name__, "| writes:", writes)

    from pydantic_ai import DeferredToolResults

    # A new Agent object and a history read back from JSON: what a restart leaves behind.
    history = ModelMessagesTypeAdapter.validate_json(saved)

    async def approved_then_fail(messages, info):
        calls = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        if calls:
            raise RuntimeError("provider died after the approved write")
        yield "unreachable"

    r = await turn(gated_agent(approved_then_fail), history=history, approvals=DeferredToolResults(approvals={"call_l": True}))
    print("   approved after restart, provider then fails: writes:", writes, "| errors:", r["errors"])
    print("   the stored history still shows the card pending, so the same answer can arrive again")
    r = await turn(gated_agent(gated_flow), history=ModelMessagesTypeAdapter.validate_json(saved), approvals=DeferredToolResults(approvals={"call_l": True}))
    print("   approved again: writes:", writes, "| same tool_call_id, so the idempotency key catches it:", len(set(writes)) == 1, "| output:", r.get("output"))
    print("   == or save the captured history after the failure instead")
    writes.clear()
    r = await turn(gated_agent(approved_then_fail), history=ModelMessagesTypeAdapter.validate_json(saved), approvals=DeferredToolResults(approvals={"call_l": True}))
    print("   captured:", describe(r["captured"]))
    r2 = await turn(gated_agent(answer), "next", history=repair_messages(stored(r["captured"])), uid="u9")
    print("   next turn:", r2.get("output"), "| errors:", r2["errors"], "| writes:", writes)


asyncio.run(main())
