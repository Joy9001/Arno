"""Step 8 probe 3: real OpenAI client against a local stub: retries, fallback, hangs, mid-stream death, oversized history."""
import asyncio
import collections
import json
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.capabilities import ModelFallback
from pydantic_ai.messages import ModelResponse, ModelMessagesTypeAdapter
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage

warnings.filterwarnings("ignore")
hits = collections.Counter()
bodies: list = []
release = threading.Event()
FLIP = {"up": False}


def sse(handler, obj):
    handler.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
    handler.wfile.flush()


def chunk(model, text=None, finish=None, tool=None):
    delta = {}
    if text is not None:
        delta = {"role": "assistant", "content": text}
    if tool:
        delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": tool[0], "type": "function", "function": {"name": tool[1], "arguments": tool[2]}}]}
    return {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def fail(self, code, payload, headers=None):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def start_sse(self):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("connection", "close")
        self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        model = body["model"]
        hits[model] += 1
        bodies.append((model, body))
        n = hits[model]
        if model == "e500":
            return self.fail(500, {"error": {"message": "boom"}})
        if model == "e429":
            return self.fail(429, {"error": {"message": "slow down", "code": "rate_limit_exceeded"}}, {"retry-after": "0"})
        if model == "flaky2" and n <= 2:
            return self.fail(500, {"error": {"message": "boom"}})
        if model == "auth":
            return self.fail(401, {"error": {"message": "bad key"}})
        if model == "ctx":
            return self.fail(400, {"error": {"message": "too long", "code": "context_length_exceeded"}})
        if model == "hang":
            release.wait(40)
            self.close_connection = True
            return
        if model == "silent":
            self.start_sse()
            release.wait(40)
            self.close_connection = True
            return
        if model == "flip" and not FLIP["up"]:
            return self.fail(500, {"error": {"message": "boom"}})
        if model == "chunked":
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            payload = b"data: " + json.dumps(chunk(model, "half an ")).encode() + b"\n\n"
            self.wfile.write(hex(len(payload))[2:].encode() + b"\r\n" + payload + b"\r\n")
            self.wfile.flush()
            self.close_connection = True
            return
        self.start_sse()
        self.close_connection = True
        if model == "lengthcut":
            sse(self, chunk(model, "half an "))
            sse(self, chunk(model, finish="length"))
            self.wfile.write(b"data: [DONE]\n\n")
            return
        if model == "mid":
            sse(self, chunk(model, "half an "))
            return
        if model == "toolthengood":
            has_tool = any(m.get("role") == "tool" for m in body["messages"])
            if not has_tool:
                sse(self, chunk(model, tool=("call_1", "lookup", "{}")))
                sse(self, chunk(model, finish="tool_calls"))
                self.wfile.write(b"data: [DONE]\n\n")
                return
        sse(self, chunk(model, f"answer from {model}"))
        sse(self, chunk(model, finish="stop"))
        self.wfile.write(b"data: [DONE]\n\n")


def model(name, port):
    return OpenAIChatModel(name, provider=OpenAIProvider(base_url=f"http://127.0.0.1:{port}/v1", api_key="test-only"))


UID = {"n": 0}


async def run(agent, text="hi", history=None, settings=None, uid=None):
    out = {"errors": [], "text": ""}
    UID["n"] += 1
    uid = uid or f"u{UID['n']}"

    async def done(result):
        out["all"] = result.all_messages()

    adapter = VercelAIAdapter(agent=agent, run_input=SubmitMessage(id="t", messages=[UIMessage(id=uid, role="user", parts=[TextUIPart(text=text)])], trigger="submit-message"), manage_message_history="server", sdk_version=6)
    start = time.perf_counter()
    with capture_run_messages() as captured:
        async for ev in adapter.run_stream(message_history=history, on_complete=done, model_settings=settings):
            name = type(ev).__name__
            if name == "ErrorChunk":
                out["errors"].append(str(ev.error_text)[:110])
            if name == "TextDeltaChunk":
                out["text"] += ev.delta
    out["seconds"] = round(time.perf_counter() - start, 1)
    out["captured"] = list(captured)
    return out


def used(result):
    return [(m.model_name, [getattr(a, "model_name", str(a)) for a in (getattr(m, "failed_attempts", None) or [])]) for m in result.get("all", result["captured"]) if isinstance(m, ModelResponse)]


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def agent(primary, *backups, **kw):
        caps = [ModelFallback(*[model(b, port) for b in backups], **kw)] if backups else []
        return Agent(model(primary, port), capabilities=caps)

    print("== 7. A stream that stops: clean close, no finish reason")
    r = await run(agent("mid", "good"))
    last = r["all"][-1] if "all" in r else r["captured"][-1]
    print("   text:", r["text"], "| errors:", r["errors"], "| on_complete:", "all" in r, "| state:", last.state, "| finish_reason:", last.finish_reason)
    print("== 7b. A stream cut mid-chunk (chunked encoding, no terminal chunk)")
    hits.clear()
    r = await run(agent("chunked", "good"))
    last = r["all"][-1] if "all" in r else r["captured"][-1]
    print("   hits:", dict(hits), "| text:", r["text"], "| errors:", r["errors"], "| on_complete:", "all" in r, "| state:", last.state, "| finish_reason:", getattr(last, "finish_reason", None))
    print("== 7c. Output cap hit: finish_reason length")
    r = await run(agent("lengthcut", "good"))
    last = r["all"][-1] if "all" in r else r["captured"][-1]
    print("   text:", r["text"], "| errors:", r["errors"], "| on_complete:", "all" in r, "| state:", last.state, "| finish_reason:", last.finish_reason)

    print("== 9b. Does sticky outlive the outage? primary fails, then recovers")
    for sticky in (False, True):
        FLIP["up"] = False
        hits.clear()
        a = Agent(model("flip", port), capabilities=[ModelFallback(model("good", port), sticky=sticky)])
        r = await run(a)
        first = dict(hits)
        FLIP["up"] = True
        hits.clear()
        h = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(r["all"]))
        r2 = await run(a, "again", history=h)
        second = dict(hits)
        hits.clear()
        h = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(r2["all"]))
        r3 = await run(a, "third", history=h)
        print(f"   sticky={sticky}: outage turn {first} -> {r['text']!r} | recovered turn {second} -> {r2['text']!r} | third {dict(hits)} -> {r3['text']!r}")

    print("== 11. The request after a failed first request")
    bodies.clear()
    r = await run(agent("e500"))
    fixed = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(r["captured"]))
    r2 = await run(agent("good"), "second try", history=fixed)
    print("   sent next turn:", [(m["role"], str(m.get("content"))[:24]) for m in bodies[-1][1]["messages"]], "| answered:", r2["text"], "| errors:", r2["errors"])

    print("== 12. The request after a mid-stream death")
    bodies.clear()
    r = await run(agent("chunked"))
    print("   captured:", [(type(m).__name__, getattr(m, "state", "")) for m in r["captured"]], "| errors:", r["errors"])
    fixed = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(r["captured"]))
    r2 = await run(agent("good"), "second try", history=fixed)
    print("   sent next turn:", [(m["role"], str(m.get("content"))[:24]) for m in bodies[-1][1]["messages"]], "| answered:", r2["text"], "| errors:", r2["errors"])

    print("== 13. Retry of a failed turn with the same message ID, after a write")
    a = Agent(model("toolthengood", port))

    @a.tool_plain
    def lookup() -> str:
        """Reads."""
        return "row"

    r = await run(a, "log it", uid="same-id")
    h = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(r["all"]))
    bodies.clear()
    r2 = await run(a, "log it", history=h, uid="same-id")
    print("   same ID, sent:", [(m["role"], str(m.get("content"))[:18]) for m in bodies[0][1]["messages"]])
    bodies.clear()
    r3 = await run(a, "log it", history=h, uid="new-id")
    print("   new ID, sent:", [(m["role"], str(m.get("content"))[:18]) for m in bodies[0][1]["messages"]])

    release.set()
    server.shutdown()


asyncio.run(main())
