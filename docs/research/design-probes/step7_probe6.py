"""Step 7 probe 6: every outbound connection during a real provider-client run, against a local stub."""
import asyncio
import json
import socket
import threading
import warnings
from http.server import BaseHTTPRequestHandler, HTTPServer

attempts = []
_connect = socket.socket.connect
_getaddrinfo = socket.getaddrinfo
_connect_ex = socket.socket.connect_ex


def spy(self, address):
    if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1"):
        attempts.append(("connect", address))
        raise OSError("blocked by probe")
    return _connect(self, address)


def spy_ex(self, address):
    if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1"):
        attempts.append(("connect_ex", address))
        return 111
    return _connect_ex(self, address)


def spy_dns(host, *a, **k):
    if host not in ("127.0.0.1", "::1", "localhost", None, ""):
        attempts.append(("dns", host))
    return _getaddrinfo(host, *a, **k)


socket.socket.connect = spy
socket.socket.connect_ex = spy_ex
socket.getaddrinfo = spy_dns

import pydantic_ai_harness as h  # noqa: E402
from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.capabilities import Caching  # noqa: E402
from pydantic_ai.models.fallback import FallbackModel  # noqa: E402
from pydantic_ai.models.openai import OpenAIChatModel  # noqa: E402
from pydantic_ai.providers.openai import OpenAIProvider  # noqa: E402

warnings.filterwarnings("ignore")
requests_seen = []


class Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(n))
        requests_seen.append(self.path)
        data = json.dumps(
            {
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


async def main():
    server = HTTPServer(("127.0.0.1", 0), Stub)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    provider = OpenAIProvider(base_url=f"http://127.0.0.1:{port}/v1", api_key="test-only")
    model = FallbackModel(OpenAIChatModel("gpt-5.4", provider=provider), OpenAIChatModel("local-model", provider=provider))
    agent = Agent(
        model,
        instructions="RULES",
        capabilities=[Caching(), h.WarnOnCacheBusts(), h.SpendLimits()],
    )
    try:
        r = await agent.run("hello")
        print("answer:", r.output, "| requests to the stub:", requests_seen, "| usage:", r.usage().input_tokens)
    except Exception as e:
        print("run failed:", type(e).__name__, str(e)[:300])
    print("outside connections and lookups:", attempts)
    server.shutdown()


asyncio.run(main())
