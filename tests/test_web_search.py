"""web_search: Brave behind a key, the model's own tool otherwise, and nothing when neither."""

from __future__ import annotations

import json

import httpx
import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.ui.vercel_ai.response_types import SourceUrlChunk

from arno.agent import builder
from arno.agent.deps import AgentDeps
from arno.tools.search import brave_search

pytestmark = pytest.mark.usefixtures("clean_database")

BRAVE = {
    "web": {
        "results": [
            {
                "title": "RBI policy",
                "url": "https://rbi.example/policy",
                "description": "Repo rate unchanged.",
                "page_age": "2026-10-01T00:00:00",
            },
            {"title": "News", "url": "https://news.example/a", "description": "More."},
        ]
    }
}


async def test_brave_results_map_to_cited_sources():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=BRAVE)

    results = await brave_search(
        "RBI repo rate", api_key="k", first_id=3, transport=httpx.MockTransport(handler)
    )
    assert seen[0].headers["X-Subscription-Token"] == "k"
    assert seen[0].url.params["q"] == "RBI repo rate"
    assert results == [
        {
            "id": "3",
            "title": "RBI policy",
            "url": "https://rbi.example/policy",
            "snippet": "Repo rate unchanged.",
            "published_at": "2026-10-01T00:00:00",
        },
        {
            "id": "4",
            "title": "News",
            "url": "https://news.example/a",
            "snippet": "More.",
            "published_at": None,
        },
    ]


async def test_the_agent_searches_with_brave_and_streams_source_urls(monkeypatch):
    async def fake(query, *, api_key, first_id):
        assert api_key == "k" and "balance" not in query
        return [{"id": str(first_id), "title": "T", "url": "https://t.example", "snippet": "s"}]

    monkeypatch.setattr(builder, "brave_search", fake)
    monkeypatch.setattr(builder, "get_settings", lambda: type("S", (), {"brave_api_key": "k"})())

    def script(messages, info: AgentInfo) -> ModelResponse:
        if not any(
            isinstance(m, ModelRequest) and m.parts[-1].part_kind == "tool-return" for m in messages
        ):
            return ModelResponse(parts=[ToolCallPart("web_search", {"query": "RBI repo rate"})])
        return ModelResponse(parts=[TextPart("Unchanged [1].")])

    agent = builder.build_agent(model=FunctionModel(script))
    deps = AgentDeps(conversation_id="cnv_search")
    result = await agent.run("repo rate?", deps=deps)
    returns = [
        part
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "web_search"
    ]
    assert returns and isinstance(returns[0].metadata[0], SourceUrlChunk)
    assert returns[0].metadata[0].url == "https://t.example"
    assert deps.web_sources == 1


async def test_without_a_key_or_native_search_the_agent_simply_has_no_web_search(monkeypatch):
    monkeypatch.setattr(builder, "get_settings", lambda: type("S", (), {"brave_api_key": None})())
    names: list[str] = []

    def script(messages, info: AgentInfo) -> ModelResponse:
        names.extend(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart("ok")])

    agent = builder.build_agent(model=FunctionModel(script))
    result = await agent.run("hi", deps=AgentDeps(conversation_id="cnv_nosearch"))
    assert result.output == "ok"
    assert "web_search" not in names and "run_query" in names


@pytest.mark.parametrize("key", [None, "k"])
async def test_a_chat_completions_model_never_gets_the_native_search_tool(monkeypatch, key):
    monkeypatch.setattr(builder, "get_settings", lambda: type("S", (), {"brave_api_key": key})())
    bodies: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "c1",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "hi"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    client = AsyncOpenAI(
        base_url="http://127.0.0.1:9/v1",
        api_key="x",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    model = OpenAIChatModel("m", provider=OpenAIProvider(openai_client=client))
    agent = builder.build_agent(model=model)
    with override_allow_model_requests(True):  # the mock transport never leaves the process
        result = await agent.run("hello", deps=AgentDeps(conversation_id="cnv_chat_completions"))
    assert result.output == "hi"
    assert len(bodies) == 1 and "web_search_options" not in bodies[0]
    names = {tool["function"]["name"] for tool in bodies[0]["tools"]}
    assert ("web_search" in names) is bool(key)
