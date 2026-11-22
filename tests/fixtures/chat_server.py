"""Real loopback SSE server and stream probes for chat integration tests."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from contextlib import asynccontextmanager
from typing import override

import httpx
import uvicorn

from arno.agent.builder import build_agent
from arno.api import app as app_module
from arno.api.deps import current_session


@asynccontextmanager
async def live_server(app, monkeypatch, model):
    monkeypatch.setattr(
        app_module, "build_agent", lambda **kwargs: build_agent(model=model, **kwargs)
    )
    app.dependency_overrides[current_session] = lambda: {"user_id": "test"}
    ready: Future[int] = Future()

    class TestServer(uvicorn.Server):
        @override
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                ready.set_result(self.servers[0].sockets[0].getsockname()[1])
            else:
                ready.set_exception(RuntimeError("server did not start"))

    server = TestServer(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        port = await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(ready)), timeout=30)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
            yield client
    finally:
        server.should_exit = True
        await asyncio.to_thread(thread.join, 10)
        assert not thread.is_alive(), "server did not exit"


async def first_text_delta(response):
    async for line in response.aiter_lines():
        if '"type":"text-delta"' in line:
            return
    raise AssertionError("stream ended before the first text delta")


async def wait_until(predicate, timeout: float = 5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.02)
    return predicate()
