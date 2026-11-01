"""Step 8 probe 1, real uvicorn, no LLM: disconnect, a detached run, replay, stop, failure, and restart of a card."""
import asyncio
import json
import warnings

import httpx
import uvicorn
from pydantic_ai import Agent, ApprovalRequired, CancellationToken, DeferredToolRequests, RunContext
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from starlette.applications import Starlette
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

warnings.filterwarnings("ignore")
PORT = 18765
BASE = f"http://127.0.0.1:{PORT}"
state: dict = {}


def body(text, uid="u1", trigger="submit-message"):
    return {"id": "t", "messages": [{"id": uid, "role": "user", "parts": [{"type": "text", "text": text}]}], "trigger": trigger}


async def slow_text(messages, info):
    """Streams 20 words over about 2 seconds."""
    state["model_calls"] = state.get("model_calls", 0) + 1
    for i in range(20):
        await asyncio.sleep(0.1)
        yield f"w{i} "


async def write_then_slow(messages, info):
    """First request: a write tool. Second: slow text."""
    has_return = any(isinstance(p, ToolReturnPart) for m in messages if isinstance(m, ModelRequest) for p in m.parts)
    if not has_return:
        yield {0: DeltaToolCall(name="record", json_args='{"amount": 5}', tool_call_id="call_write")}
        return
    for i in range(20):
        await asyncio.sleep(0.1)
        yield f"w{i} "


async def write_then_fail(messages, info):
    has_return = any(isinstance(p, ToolReturnPart) for m in messages if isinstance(m, ModelRequest) for p in m.parts)
    if not has_return:
        yield {0: DeltaToolCall(name="record", json_args='{"amount": 5}', tool_call_id="call_write")}
        return
    yield "partial "
    raise RuntimeError("provider died mid-stream")


writes: list = []


def build(stream):
    agent = Agent(FunctionModel(stream_function=stream), output_type=[str, DeferredToolRequests])

    @agent.tool
    async def record(ctx: RunContext, amount: int) -> str:
        """Writes."""
        writes.append(ctx.tool_call_id)
        return f"saved {amount}"

    return agent


class Run:
    """A detached run: one task, a buffer of encoded chunks, any number of readers."""

    def __init__(self):
        self.chunks: list[str] = []
        self.done = False
        self.wake = asyncio.Event()
        self.token = CancellationToken()
        self.saved: dict = {}

    def push(self, chunk):
        self.chunks.append(chunk)
        self.wake.set()

    async def read(self):
        i = 0
        while True:
            while i < len(self.chunks):
                yield self.chunks[i]
                i += 1
            if self.done:
                return
            self.wake.clear()
            await self.wake.wait()


async def plain_chat(request):
    state["plain"] = {"complete": False, "cancelled": False}

    async def done(result):
        state["plain"]["complete"] = True

    async def cancelled(exc):
        state["plain"]["cancelled"] = True

    return await VercelAIAdapter.dispatch_request(request, agent=build(slow_text), sdk_version=6, manage_message_history="server", on_complete=done, on_cancel=cancelled)


async def detached_chat(request):
    kind = request.query_params.get("kind", "text")
    stream = {"text": slow_text, "write": write_then_slow, "fail": write_then_fail}[kind]
    run = Run()
    state["run"] = run
    adapter = await VercelAIAdapter.from_request(request, agent=build(stream), sdk_version=6, manage_message_history="server")

    async def done(result):
        run.saved["complete"] = ModelMessagesTypeAdapter.dump_json(result.all_messages()).decode()

    async def cancelled(exc):
        run.saved["cancel_all"] = exc.all_messages()
        run.saved["cancel_new"] = exc.new_messages()

    async def go():
        try:
            async for chunk in adapter.encode_stream(adapter.run_stream(cancellation_token=run.token, on_complete=done, on_cancel=cancelled)):
                run.push(chunk)
        except BaseException as e:  # noqa: BLE001
            run.saved["task_exception"] = repr(e)
            raise
        finally:
            run.done = True
            run.wake.set()

    run.task = asyncio.create_task(go())
    return StreamingResponse(run.read(), media_type="text/event-stream", headers={"x-vercel-ai-ui-message-stream": "v1"})


async def resume(request):
    run = state.get("run")
    if run is None:
        return Response(status_code=204)
    return StreamingResponse(run.read(), media_type="text/event-stream")


async def stop(request):
    state["run"].token.cancel()
    return Response(status_code=204)


app = Starlette(routes=[Route("/plain", plain_chat, methods=["POST"]), Route("/chat", detached_chat, methods=["POST"]), Route("/resume", resume), Route("/stop", stop, methods=["POST"])])


async def wait_done(timeout=8):
    for _ in range(int(timeout / 0.05)):
        if state["run"].done:
            return True
        await asyncio.sleep(0.05)
    return False


def events(chunks):
    types = []
    for c in chunks:
        for line in c.splitlines():
            if line.startswith("data: ") and line != "data: [DONE]":
                types.append(json.loads(line[6:]).get("type"))
    return types


async def main():
    server = uvicorn.Server(uvicorn.Config(app, port=PORT, log_level="warning"))
    serve = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)

    async with httpx.AsyncClient(timeout=20) as client:
        print("== 1. Plain dispatch_request, client leaves after 3 chunks")
        async with client.stream("POST", f"{BASE}/plain", json=body("hi")) as r:
            n = 0
            async for _ in r.aiter_lines():
                n += 1
                if n > 6:
                    break
        await asyncio.sleep(3)
        print("   status:", r.status_code, "| on_complete:", state["plain"]["complete"], "| on_cancel:", state["plain"]["cancelled"])

        print("== 2. Detached run, client leaves after 3 chunks")
        async with client.stream("POST", f"{BASE}/chat", json=body("hi")) as r:
            n = 0
            async for _ in r.aiter_lines():
                n += 1
                if n > 6:
                    break
        run = state["run"]
        mid = len(run.chunks)
        print("   chunks when the client left:", mid, "| run finished by then:", run.done)
        print("   == reattach while it runs: a second reader replays from the start")
        async with client.stream("GET", f"{BASE}/resume") as r2:
            lines = [line async for line in r2.aiter_lines()]
        text = "".join(json.loads(x[6:]).get("delta", "") for x in lines if x.startswith("data: ") and x != "data: [DONE]" and json.loads(x[6:]).get("type") == "text-delta")
        print("   finished:", await wait_done(), "| on_complete saved:", "complete" in run.saved, "| replay text words:", len(text.split()), "| ends with:", [x for x in lines if x.startswith("data")][-1][:30])

        print("== 3. Stop mid-text through the token")
        async with client.stream("POST", f"{BASE}/chat", json=body("hi")) as r:
            n = 0
            async for _ in r.aiter_lines():
                n += 1
                if n > 10:
                    await client.post(f"{BASE}/stop")
                    break
        run = state["run"]
        print("   finished:", await wait_done(), "| on_cancel:", "cancel_all" in run.saved, "| on_complete:", "complete" in run.saved)
        hist = run.saved.get("cancel_all", [])
        print("   last message:", type(hist[-1]).__name__ if hist else None, "| state:", getattr(hist[-1], "state", None), "| text kept:", str(hist[-1].parts[0].content)[:30] if hist else None)
        stored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(hist))
        agent = build(slow_text)
        async with agent.run_stream("next turn", message_history=stored) as r3:
            out = await r3.get_output()
        print("   next turn on that history answered:", bool(out), "| messages sent forward:", len(r3.all_messages()))

        print("== 4. Stop while the second model request runs, after a write")
        writes.clear()
        async with client.stream("POST", f"{BASE}/chat?kind=write", json=body("log 5")) as r:
            async for line in r.aiter_lines():
                if '"text-delta"' in line:
                    await client.post(f"{BASE}/stop")
                    break
        run = state["run"]
        await wait_done()
        hist = run.saved.get("cancel_all", [])
        kept = [p.tool_name for m in hist if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        print("   writes ran:", writes, "| tool return kept in history:", kept)

        print("== 5. External task.cancel() instead of the token")
        async with client.stream("POST", f"{BASE}/chat", json=body("hi")) as r:
            n = 0
            async for _ in r.aiter_lines():
                n += 1
                if n > 6:
                    state["run"].task.cancel()
                    break
        run = state["run"]
        await wait_done()
        print("   on_cancel:", "cancel_all" in run.saved, "| on_complete:", "complete" in run.saved, "| task:", run.saved.get("task_exception"))

        print("== 6. Provider dies mid-stream after a write")
        writes.clear()
        async with client.stream("POST", f"{BASE}/chat?kind=fail", json=body("log 5")) as r:
            lines = [line async for line in r.aiter_lines()]
        run = state["run"]
        await wait_done()
        types = [json.loads(x[6:]).get("type") for x in lines if x.startswith("data: ") and x != "data: [DONE]"]
        print("   writes ran:", writes, "| on_complete:", "complete" in run.saved, "| on_cancel:", "cancel_all" in run.saved)
        print("   stream ended with:", types[-3:], "| error text:", [json.loads(x[6:]).get("errorText") for x in lines if '"error"' in x][:1])

    server.should_exit = True
    await serve


asyncio.run(main())
