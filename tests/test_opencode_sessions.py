import asyncio

import httpx2

from arno.agent.models import RoleConfig, build_model, opencode_conversation


async def test_opencode_sessions_are_stable_and_isolated_between_concurrent_conversations(
    monkeypatch,
):
    seen = []

    async def respond(request):
        await asyncio.sleep(0)
        seen.append(request.headers["x-opencode-session"])
        return httpx2.Response(200)

    original_client = httpx2.AsyncClient
    clients = []

    class MockClient(original_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx2.MockTransport(respond), **kwargs)
            clients.append(self)

    monkeypatch.setattr(httpx2, "AsyncClient", MockClient)
    monkeypatch.setenv("OPENCODE_SESSION_ID", "outside-chat")
    build_model(RoleConfig("https://opencode.ai/v1", "key", "model", 1000))
    client = clients[0]

    async def request_for(conversation_id):
        with opencode_conversation(conversation_id):
            await asyncio.sleep(0)
            await client.get("https://opencode.ai/v1")

    try:
        await asyncio.gather(request_for("first"), request_for("second"))
        first, second = seen
        await request_for("first")
        await client.get("https://opencode.ai/v1")
    finally:
        await client.aclose()

    assert first != second
    assert seen[2] == first
    assert seen[3] == "outside-chat"
