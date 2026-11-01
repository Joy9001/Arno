"""Step 8 probe 9, no LLM: a window limit on the backup model itself, and which request holds your text after a failure."""
import asyncio
import inspect
import uuid
import warnings
from contextlib import asynccontextmanager

import pydantic_ai_harness as h
import pydantic_ai_harness.compaction as c
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.capabilities import ModelFallback
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage
from pydantic_ai_harness.guardrails.detectors import personal_data, secret_data

warnings.filterwarnings("ignore")
calls = []


class WindowLimited(WrapperModel):
    """Raises before sending anything the model's configured window can't fit."""

    def __init__(self, wrapped, window):
        super().__init__(wrapped)
        self.window = window

    def _check(self, messages):
        size = len(str(messages)) // 4
        if size > self.window:
            calls.append((self.wrapped.model_name, f"refused {size} > {self.window}"))
            raise ModelAPIError(self.wrapped.model_name, f"{size} tokens over this model's {self.window}-token window")

    async def request(self, messages, model_settings, model_request_parameters):
        self._check(messages)
        return await super().request(messages, model_settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
        self._check(messages)
        async with super().request_stream(messages, model_settings, model_request_parameters, run_context) as stream:
            yield stream


def make(name, ok):
    def fn(messages, info):
        calls.append((name, "plain"))
        if not ok:
            raise ModelAPIError(name, "down")
        return ModelResponse(parts=[TextPart(f"SUMMARY from {name}")])

    async def stream(messages, info):
        calls.append((name, "stream"))
        if not ok:
            raise ModelAPIError(name, "down")
        yield f"answer from {name}"

    return FunctionModel(function=fn, stream_function=stream, model_name=name)


def long_history():
    out = [ModelRequest(parts=[c.pin("SNAPSHOT")]), ModelResponse(parts=[TextPart("Snapshot read.")])]
    for i in range(10):
        out += [ModelRequest.user_text_prompt(f"turn {i} " + "x" * 400), ModelResponse(parts=[TextPart("y" * 400)])]
    return out


async def outage(label, window):
    calls.clear()
    primary = make("primary", False)
    backup = make("backup", True)
    limited = WindowLimited(backup, window) if window else backup
    agent = Agent(
        primary,
        capabilities=[
            c.TieredCompaction(
                tiers=[
                    c.FallbackCompaction(
                        fallback_chain=[
                            c.SummarizingCompaction(model=FallbackModel(primary, limited), max_messages=1, keep_tokens=60, keep_messages=2, keep_user_messages=True, receipts=True),
                            c.SlidingWindowCompaction(max_messages=1, keep_tokens=60, receipts=True),
                        ]
                    )
                ],
                target_tokens=200,
            ),
            ModelFallback(limited),
        ],
    )
    text, error, messages = None, None, []
    try:
        async with agent.run_stream("next question", message_history=long_history()) as r:
            text = await r.get_output()
            messages = r.all_messages()
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {str(e)[:90]}"
    dumped = str(messages)
    print(f"== {label}")
    print("   calls:", calls)
    print("   answer:", text, "| error:", error, "| summary from backup:", "SUMMARY from backup" in dumped, "| messages:", len(messages))


async def transcript():
    print("== Which request holds your text after a failed first request, with the full config")

    async def die(messages, info):
        raise ModelAPIError("m", "provider down")
        yield ""  # noqa

    agent = Agent(
        FunctionModel(stream_function=die),
        capabilities=[
            h.InputGuardrail(guard=[secret_data(), personal_data(only=["credit_card"])]),
            h.SystemReminders(dynamic_reminders=[lambda ctx: "Today is 1 November."]),
        ],
    )
    run_id = uuid.uuid4().hex
    seed = [ModelRequest(parts=[c.pin("SNAPSHOT")]), ModelResponse(parts=[TextPart("Snapshot read.")])]
    adapter = VercelAIAdapter(agent=agent, run_input=SubmitMessage(id="t", messages=[UIMessage(id="ui-1", role="user", parts=[TextUIPart(text="MARKER paid with 4111 1111 1111 1111")])], trigger="submit-message"), manage_message_history="server", sdk_version=6)
    with capture_run_messages() as captured:
        async for _ in adapter.run_stream(message_history=seed, run_id=run_id):
            pass
    for m in captured:
        parts = [(type(p).__name__, str(getattr(p, "content", ""))[:44]) for p in m.parts]
        print("  ", type(m).__name__, "| run_id is this run:", m.run_id == run_id, "| metadata:", getattr(m, "metadata", None), "|", parts)


async def stop_path():
    print("== Stop: does new_messages() hold your message?")
    from pydantic_ai import CancellationToken

    token = CancellationToken()

    async def slow(messages, info):
        for i in range(50):
            await asyncio.sleep(0.02)
            if i == 3:
                token.cancel()
            yield f"w{i} "

    saved = {}

    async def cancelled(exc):
        saved["new"] = exc.new_messages()

    agent = Agent(FunctionModel(stream_function=slow), capabilities=[h.SystemReminders(dynamic_reminders=[lambda ctx: "Today is 1 November."])])
    seed = [ModelRequest(parts=[c.pin("SNAPSHOT")]), ModelResponse(parts=[TextPart("Snapshot read.")])]
    adapter = VercelAIAdapter(agent=agent, run_input=SubmitMessage(id="t", messages=[UIMessage(id="ui-2", role="user", parts=[TextUIPart(text="MARKER question")])], trigger="submit-message"), manage_message_history="server", sdk_version=6)
    async for _ in adapter.run_stream(message_history=seed, cancellation_token=token, on_cancel=cancelled):
        pass
    print("   holds your message:", "MARKER" in str(saved.get("new")), "| holds the snapshot:", "SNAPSHOT" in str(saved.get("new")), "| kinds:", [type(m).__name__ for m in saved.get("new", [])])


async def nested_timeout():
    print("== A toolset timeout on a read tool called from a script")

    async def script(messages, info):
        from pydantic_ai.messages import ToolReturnPart
        from pydantic_ai.models.function import DeltaToolCall

        returns = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returns:
            import json

            yield {0: DeltaToolCall(name="run_code", json_args=json.dumps({"code": "await slow_read()"}), tool_call_id="c1")}
            return
        yield "saw: " + str(returns[-1].content)[:120]

    async def slow_read() -> str:
        """Sleeps."""
        await asyncio.sleep(5)
        return "finished"

    agent = Agent(FunctionModel(stream_function=script), toolsets=[FunctionToolset(tools=[slow_read], timeout=1).with_metadata(code_mode=True)], capabilities=[h.CodeMode(tools={"code_mode": True})])
    start = asyncio.get_running_loop().time()
    async with agent.run_stream("go") as r:
        out = await r.get_output()
    print("   seconds:", round(asyncio.get_running_loop().time() - start, 1), "| output:", out)


async def main():
    await outage("Main down, long thread, backup with a large window", None)
    await outage("Main down, long thread, backup window 600", 600)
    await outage("Main down, long thread, backup window 200", 200)
    await transcript()
    await stop_path()
    await nested_timeout()
    print("== Per-tool and per-toolset timeouts")
    print("   FunctionToolset:", [p for p in inspect.signature(FunctionToolset.__init__).parameters if "timeout" in p])
    print("   tool_plain:", [p for p in inspect.signature(Agent.tool_plain).parameters if "timeout" in p])


asyncio.run(main())
