"""Images attached in chat run natively through ImageAttachments without persisting binary blobs."""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from pydantic_ai.messages import (
    BinaryContent,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo

from arno.api import chat as chat_mod
from tests.agent_helpers import streaming_model
from tests.fixtures.chat_server import first_text_delta, live_server, wait_until
from tests.import_fixtures import sbi_csv

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _upload(
    client: Any,
    *,
    name: str = "receipt.png",
    data: bytes | None = None,
    purpose: str = "photo",
) -> Any:
    payload = PNG if data is None else data
    return client.post(
        "/api/attachments",
        files={"file": (name, payload, "image/png")},
        data={"purpose": purpose},
    )


def _upload_photo(
    client: Any, name: str = "receipt.png", *, data: bytes | None = None
) -> dict[str, Any]:
    response = _upload(client, name=name, data=data)
    assert response.status_code == 201, response.text
    return response.json()


def _body(
    message_id: str,
    *,
    text: str | None = None,
    photos: list[dict[str, Any]] | None = None,
    statements: list[dict[str, Any]] | None = None,
    thread: str = "thread_photo",
) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    if text is not None:
        parts.append({"type": "text", "text": text})
    for statement in statements or []:
        parts.append(
            {
                "type": "data-statement",
                "data": {
                    "attachment_id": statement["id"],
                    "filename": statement["filename"],
                    "detected_kind": "text",
                },
            }
        )
    for photo in photos or []:
        parts.append(
            {
                "type": "data-photo",
                "data": {
                    "attachment_id": photo["id"],
                    "filename": photo["filename"],
                },
            }
        )
    return {
        "id": thread,
        "trigger": "submit-message",
        "messageId": message_id,
        "messages": [{"id": message_id, "role": "user", "parts": parts}],
    }


def _events(response: Any) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def _cards(thread: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        part
        for message in thread["messages"]
        for part in message.get("parts", [])
        if part.get("type") == "data-card"
    ]


def _has_tool_return(messages: list[Any], name: str) -> bool:
    return any(
        getattr(part, "tool_name", None) == name
        for msg in messages
        for part in getattr(msg, "parts", [])
        if getattr(part, "part_kind", "") == "tool-return"
    )


def _last_user_binary_parts(messages: list[Any]) -> tuple[list[str], list[BinaryContent]]:
    for msg in reversed(messages):
        for part in getattr(msg, "parts", []):
            if isinstance(part, UserPromptPart) and not isinstance(part.content, str):
                texts = [item for item in part.content if isinstance(item, str)]
                bins = [item for item in part.content if isinstance(item, BinaryContent)]
                if bins:
                    return texts, bins
    return [], []


async def test_image_only_hydrates_binary_and_keeps_the_stored_history_clean(
    session_client, demo_ledger
):
    photo = _upload_photo(session_client, data=PNG)
    body = _body("client_photo_only", photos=[photo])
    seen_images: list[int] = []

    def script(messages: list[Any], info: AgentInfo) -> ModelResponse:
        del info
        texts, bins = _last_user_binary_parts(messages)
        seen_images.append(len(bins))
        joined = "\n".join(texts)
        assert "See the attached image." in joined
        assert f"[Attached image: {photo['id']}" in joined
        assert bins[0].data == PNG
        assert bins[0].media_type == "image/png"
        return ModelResponse(parts=[TextPart("Paid ₹450 to Swiggy.")])

    with session_client.app.state.agent.override(model=streaming_model(script)):
        response = session_client.post("/api/chat", json=body)
        replay = session_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    assert seen_images == [1]
    events = _events(response)
    assert sum(event["type"] == "start" for event in events) == 1
    assert sum(event["type"] == "finish" for event in events) == 1
    assert (
        "".join(event["delta"] for event in events if event["type"] == "text-delta")
        == "Paid ₹450 to Swiggy."
    )
    assert replay.status_code == 200
    assert sum(event["type"] == "start" for event in _events(replay)) == 1
    assert await demo_ledger.fetchval("SELECT count(*) FROM messages") == 2

    thread = session_client.get("/api/threads/thread_photo/messages").json()
    user = next(message for message in thread["messages"] if message["role"] == "user")
    assert [part["type"] for part in user["parts"]] == ["text", "data-photo"]
    assert user["parts"][0]["text"] == "See the attached image."

    row = await demo_ledger.fetchrow_required(
        "SELECT model_history FROM conversations WHERE id = 'thread_photo'"
    )
    history_json = json.dumps(row["model_history"])
    assert f"[Attached image: {photo['id']}" in history_json
    assert "iVBOR" not in history_json
    assert '"binary"' not in history_json


async def test_multi_row_screenshot_batch_logs_all_items_and_undoes_atomically(
    session_client, demo_ledger
):
    photo = _upload_photo(session_client, name="unbilled.png", data=PNG)
    body = _body(
        "client_batch_photo",
        text="Log all transactions from this screenshot",
        photos=[photo],
        thread="thread_photo_batch",
    )
    saw_images: list[int] = []

    def script(messages: list[Any], info: AgentInfo) -> ModelResponse:
        del info
        if _has_tool_return(messages, "record_movements"):
            return ModelResponse(
                parts=[TextPart("Logged 3 transactions totaling ₹991 from your screenshot.")]
            )
        _texts, bins = _last_user_binary_parts(messages)
        saw_images.append(len(bins))
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "record_movements",
                    {
                        "attachment_id": photo["id"],
                        "movements": [
                            {
                                "kind": "expense",
                                "amount_rupees": 2,
                                "payee": "Google",
                                "date": "2026-11-01",
                            },
                            {
                                "kind": "expense",
                                "amount_rupees": 2,
                                "payee": "Google",
                                "date": "2026-11-01",
                            },
                            {
                                "kind": "expense",
                                "amount_rupees": 987,
                                "payee": "Opencode San Francisc Us",
                                "date": "2026-11-01",
                            },
                        ],
                    },
                )
            ]
        )

    before_payees = await demo_ledger.fetchval("SELECT count(*) FROM payees")
    with session_client.app.state.agent.override(model=streaming_model(script)):
        response = session_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    assert saw_images == [1]

    thread = session_client.get("/api/threads/thread_photo_batch/messages").json()
    cards = _cards(thread)
    assert len(cards) == 1
    card = cards[0]["data"]
    assert card["card"] == "movements_batch"
    assert card["count"] == 3
    assert card["total_outflow"] == {"paise": 99_100, "text": "₹991"}
    assert "attachment_ids" not in card
    assert [item["payee"] for item in card["items"]] == [
        "Google",
        "Google",
        "Opencode San Francisc Us",
    ]
    assert (
        await demo_ledger.fetchval(
            "SELECT count(*) FROM captures WHERE kind = 'photo' AND ref = $1", photo["id"]
        )
        == 3
    )

    undo = session_client.post(f"/api/undo/{card['undo_id']}")
    assert undo.status_code == 200, undo.text
    assert await demo_ledger.fetchval("SELECT count(*) FROM payees WHERE active") == before_payees
    for item in card["items"]:
        assert not await demo_ledger.fetchval(
            "SELECT count(*) FROM movements WHERE id = $1", item["movement_id"]
        )


async def test_mixed_photo_and_statement_carries_both(session_client, demo_ledger):
    statement = session_client.post(
        "/api/attachments",
        files={"file": ("sbi.csv", sbi_csv(), "text/csv")},
        data={"purpose": "statement"},
    )
    assert statement.status_code == 201, statement.text
    photo = _upload_photo(session_client, data=PNG)
    body = _body(
        "client_photo_statement",
        text="Review the statement and the screenshot",
        statements=[statement.json()],
        photos=[photo],
        thread="thread_photo_mixed",
    )

    def script(messages: list[Any], info: AgentInfo) -> ModelResponse:
        del info
        _texts, bins = _last_user_binary_parts(messages)
        assert len(bins) == 1
        return ModelResponse(parts=[TextPart("I see both the statement and the image.")])

    with session_client.app.state.agent.override(model=streaming_model(script)):
        response = session_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    assert (
        await demo_ledger.fetchval(
            "SELECT count(*) FROM imports WHERE attachment_id = $1",
            statement.json()["id"],
        )
        == 1
    )
    thread = session_client.get("/api/threads/thread_photo_mixed/messages").json()
    user = next(message for message in thread["messages"] if message["role"] == "user")
    assert [part["type"] for part in user["parts"]] == [
        "text",
        "data-statement",
        "data-photo",
    ]


async def test_a_missing_photo_ref_is_refused_before_any_turn(session_client, demo_ledger):
    body = _body(
        "client_photo_missing",
        text="Log this receipt",
        photos=[{"id": "att_missing", "filename": "receipt.png"}],
    )

    def script(messages: list[Any], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[TextPart("Should never run")])

    with session_client.app.state.agent.override(model=streaming_model(script)):
        response = session_client.post("/api/chat", json=body)

    assert response.status_code == 422
    assert "no longer available" in response.json()["detail"]
    assert await demo_ledger.fetchval("SELECT count(*) FROM messages") == 0


async def test_photo_upload_refuses_a_non_image_and_the_size_limit(session_client, monkeypatch):
    refused = _upload(session_client, name="notes.txt", data=b"hello")
    assert refused.status_code == 415
    assert "PNG, JPEG" in refused.json()["detail"]

    monkeypatch.setattr("arno.api.imports.MAX_UPLOAD_BYTES", 8)
    oversized = _upload(session_client, name="large.png", data=PNG + b"x")
    assert oversized.status_code == 413


async def test_legacy_statement_upload_still_accepts_html(session_client):
    files = {
        "file": (
            "statement.html",
            b"<html><table><tr><td>Date</td></tr></table></html>",
            "text/html",
        )
    }
    legacy = session_client.post("/api/attachments", files=files)
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["detected_kind"] == "html"
    chat = session_client.post("/api/attachments", files=files, data={"purpose": "statement"})
    assert chat.status_code == 422


async def test_a_second_request_cannot_prepare_the_same_thread_twice(app, demo_ledger, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    orig_validated = chat_mod._validated

    async def slow_validate(
        conn: Any, refs: list[dict[str, str]], kind: str
    ) -> list[dict[str, str]]:
        if refs:
            started.set()
            await release.wait()
        return await orig_validated(conn, refs, kind)

    monkeypatch.setattr("arno.api.chat._validated", slow_validate)
    model = streaming_model(lambda messages, info: ModelResponse(parts=[TextPart("Done.")]))
    async with live_server(app, monkeypatch, model) as client:
        upload = await client.post(
            "/api/attachments",
            files={"file": ("receipt.png", PNG, "image/png")},
            data={"purpose": "photo"},
        )
        assert upload.status_code == 201, upload.text
        body = _body("client_prepare_first", photos=[upload.json()], thread="thread_prepare")

        first = asyncio.create_task(client.post("/api/chat", json=body))
        try:
            await asyncio.wait_for(started.wait(), timeout=5.0)

            blocked = await client.post(
                "/api/chat",
                json=_body("client_prepare_second", text="Second message", thread="thread_prepare"),
            )
            assert blocked.status_code == 409
            assert "still being prepared" in blocked.json()["detail"]
            assert (
                await demo_ledger.fetchval(
                    "SELECT count(*) FROM messages WHERE client_id = $1",
                    "client_prepare_second",
                )
                == 0
            )

            other = await client.post(
                "/api/chat",
                json=_body(
                    "client_prepare_other", text="Other thread", thread="thread_prepare_other"
                ),
            )
            assert other.status_code == 200, other.text
        finally:
            release.set()
            response = await first

        assert response.status_code == 200, response.text
        assert sum(event["type"] == "finish" for event in _events(response)) == 1
        assert await demo_ledger.fetchval("SELECT count(*) FROM messages") == 4
        row = await demo_ledger.fetchrow_required(
            "SELECT run_started_at, active_run_id FROM conversations WHERE id = 'thread_prepare'"
        )
        assert row["run_started_at"] is None
        assert row["active_run_id"] is None

        replay = await client.post("/api/chat", json=body)
        assert replay.status_code == 200
        assert sum(event["type"] == "start" for event in _events(replay)) == 1
        assert await wait_until(lambda: not app.state.turns._preparing)


async def test_a_replay_during_streaming_is_not_blocked(app, demo_ledger, monkeypatch):
    model = streaming_model(
        lambda messages, info: ModelResponse(parts=[TextPart("streaming reply")]),
        text_chunks=12,
        delay=0.1,
    )
    body = _body("client_stream_replay", text="hello", thread="thread_stream_replay")
    async with live_server(app, monkeypatch, model) as client:
        async with client.stream("POST", "/api/chat", json=body) as first:
            assert first.status_code == 200
            await first_text_delta(first)
            assert app.state.turns.get("thread_stream_replay") is not None
            replay = await client.post("/api/chat", json=body)
            assert replay.status_code == 200, replay.text
            assert sum(event["type"] == "start" for event in _events(replay)) == 1
        await wait_until(lambda: app.state.turns.get("thread_stream_replay") is None)

    assert await demo_ledger.fetchval("SELECT count(*) FROM messages") == 2


async def test_stop_after_a_photo_send_persists_clean_history(app, demo_ledger, monkeypatch):
    upload = PNG
    attachment_response = None
    model = streaming_model(
        lambda messages, info: ModelResponse(parts=[TextPart("slow " * 40)]),
        text_chunks=40,
        delay=0.2,
    )

    async with live_server(app, monkeypatch, model) as client:
        attachment_response = await client.post(
            "/api/attachments",
            files={"file": ("receipt.png", upload, "image/png")},
            data={"purpose": "photo"},
        )
        assert attachment_response.status_code == 201, attachment_response.text
        photo = attachment_response.json()
        body = _body("client_photo_stop", text="Log this receipt", photos=[photo])

        async with client.stream("POST", "/api/chat", json=body) as response:
            assert response.status_code == 200
            await first_text_delta(response)
            stopped = await client.post("/api/chat/thread_photo/stop")
            assert stopped.json()["stopped"] is True
        await wait_until(lambda: app.state.turns.get("thread_photo") is None)

    row = await demo_ledger.fetchrow_required(
        "SELECT model_history FROM conversations WHERE id = 'thread_photo'"
    )
    assert "iVBOR" not in json.dumps(row["model_history"])
