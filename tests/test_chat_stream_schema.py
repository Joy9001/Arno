"""Our hand-built data parts must match the installed AI SDK chunk schema."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from arno.agent.cards import data_part, with_data
from arno.agent.turns import encode_saved_turn
from tests.agent_helpers import streaming_model

WEB = Path(__file__).resolve().parents[1] / "web"
VALIDATE_JS = """
import { parseJsonEventStream, uiMessageChunkSchema } from "ai";
let text = "";
for await (const piece of process.stdin) text += piece;
const bytes = new TextEncoder().encode(text);
const stream = new ReadableStream({
  start(controller) { controller.enqueue(bytes); controller.close(); }
});
for await (const part of parseJsonEventStream({ stream, schema: uiMessageChunkSchema })) {
  if (!part.success) {
    console.error(String(part.error));
    process.exit(1);
  }
}
"""

QUERY = {
    "card": "query",
    "sql": "select * from imports order by created_at desc limit 3",
    "row_count": 1,
    "truncated": False,
}
IMPORT = {
    "card": "import",
    "status": "review",
    "title": "Statement import",
    "import_id": "imp_test",
    "review_url": "/imports/imp_test",
}


def _chunks(sse: str) -> list[dict]:
    return [
        json.loads(line[6:])
        for line in sse.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def _assert_client_schema(sse: str) -> None:
    if shutil.which("node") is None or not (WEB / "node_modules" / "ai" / "package.json").exists():
        pytest.skip("Node and web dependencies are needed to validate the client schema")
    result = subprocess.run(
        ["node", "--input-type=module", "-e", VALIDATE_JS],
        input=sse,
        text=True,
        cwd=WEB,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def _body(message_id: str) -> dict:
    return {
        "id": "thread-schema",
        "trigger": "submit-message",
        "messages": [
            {"id": message_id, "role": "user", "parts": [{"type": "text", "text": "look"}]}
        ],
    }


async def test_a_run_code_query_card_streams_a_valid_client_chunk(authed_client, demo_ledger):
    def script(messages, info: AgentInfo) -> ModelResponse:
        if not any(
            getattr(part, "tool_name", None) == "run_code"
            for message in messages
            for part in getattr(message, "parts", [])
        ):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "rows = await run_query("
                                "sql='SELECT name FROM accounts LIMIT 1')\nprint(rows)"
                            )
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("I read one account.")])

    with authed_client.app.state.agent.override(model=streaming_model(script)):
        response = authed_client.post("/api/chat", json=_body("client-schema-query"))
    assert response.status_code == 200, response.text

    _assert_client_schema(response.text)
    card = next(chunk for chunk in _chunks(response.text) if chunk["type"] == "data-card")
    assert card["data"]["card"] == "query"
    assert "id" not in card and "transient" not in card


def test_saved_card_replay_drops_legacy_nulls_and_keeps_explicit_values():
    sse = "".join(
        encode_saved_turn(
            {
                "id": "msg_saved",
                "parts": [
                    {"type": "data-card", "id": None, "data": QUERY, "transient": None},
                    {
                        "type": "data-card",
                        "id": "card_import",
                        "data": IMPORT,
                        "transient": False,
                    },
                ],
            }
        )
    )

    cards = [chunk for chunk in _chunks(sse) if chunk["type"] == "data-card"]
    assert cards == [
        {"type": "data-card", "data": QUERY},
        {"type": "data-card", "id": "card_import", "data": IMPORT, "transient": False},
    ]
    _assert_client_schema(sse)


def test_data_part_omits_unset_optionals_and_keeps_boolean_false():
    assert data_part(DataChunk(type="data-card", data=QUERY)) == {
        "type": "data-card",
        "data": QUERY,
    }
    assert data_part(
        DataChunk(type="data-card", id="card_import", data=IMPORT, transient=False)
    ) == {
        "type": "data-card",
        "id": "card_import",
        "data": IMPORT,
        "transient": False,
    }

    message: dict = {}
    with_data(message, "data-card", IMPORT)
    with_data(message, "data-card", IMPORT, identifier="card_import")
    assert message["parts"] == [
        {"type": "data-card", "data": IMPORT},
        {"type": "data-card", "id": "card_import", "data": IMPORT},
    ]
