"""Receipt photos attached in chat use the one staging and capture path."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import date

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo

from arno.capture.photo import PaymentPhoto
from arno.ledger.audit import new_id
from tests.agent_helpers import streaming_model
from tests.import_fixtures import sbi_csv
from tests.test_agent_turns import _first_text_delta, _until, live_server

PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"


async def _fake_extraction(*_args, **_kwargs) -> PaymentPhoto:
    return PaymentPhoto(
        merchant="Swiggy",
        amount_paise=45_000,
        date=date(2026, 10, 31),
        method="upi",
        upi_ref="412345678901",
    )


async def _failed_extraction(*_args, **_kwargs) -> PaymentPhoto:
    raise RuntimeError("vision endpoint down")


def _upload(client, *, name: str = "receipt.png", data: bytes = PNG, purpose: str = "photo"):
    return client.post(
        "/api/attachments",
        files={"file": (name, data, "image/png")},
        data={"purpose": purpose},
    )


def _upload_photo(client, name: str = "receipt.png") -> dict:
    response = _upload(client, name=name)
    assert response.status_code == 201, response.text
    return response.json()


def _body(
    message_id: str,
    *,
    text: str | None = None,
    photos: list[dict] | None = None,
    statements: list[dict] | None = None,
    thread: str = "thread_photo",
) -> dict:
    parts: list[dict] = []
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


def _model(text: str = "The receipt is logged.", *, photo_context: bool = False):
    def script(messages, info: AgentInfo) -> ModelResponse:
        del info
        if photo_context:
            assert "Swiggy" in str(messages)
            assert "One movement was logged" in str(messages)
            assert "<outside" in str(messages)
        return ModelResponse(parts=[TextPart(text)])

    return streaming_model(script)


def _events(response) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]


def _cards(thread: dict) -> list[dict]:
    return [
        part
        for message in thread["messages"]
        for part in message.get("parts", [])
        if part.get("type") == "data-card"
    ]


def _capture_cards(thread: dict) -> list[dict]:
    return [card for card in _cards(thread) if card["data"].get("card") == "capture"]


async def test_image_only_is_staged_then_logged_once(authed_client, demo_ledger, monkeypatch):
    monkeypatch.setattr("arno.api.capture.extract_payment_photo", _fake_extraction)
    photo = _upload_photo(authed_client)
    body = _body("client_photo_only", photos=[photo])

    with authed_client.app.state.agent.override(model=_model(photo_context=True)):
        response = authed_client.post("/api/chat", json=body)
        replay = authed_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    events = _events(response)
    assert sum(event["type"] == "start" for event in events) == 1
    assert sum(event["type"] == "finish" for event in events) == 1
    assert sum(event["type"] == "data-card" for event in events) == 1
    assert (
        "".join(event["delta"] for event in events if event["type"] == "text-delta")
        == "The receipt is logged."
    )
    assert replay.status_code == 200
    assert sum(event["type"] == "data-card" for event in _events(replay)) == 1

    assert await demo_ledger.fetchval("SELECT count(*) FROM captures") == 1
    assert await demo_ledger.fetchval("SELECT count(*) FROM attachments") == 1
    assert await demo_ledger.fetchval("SELECT count(*) FROM messages") == 2

    thread = authed_client.get("/api/threads/thread_photo/messages").json()
    user = next(message for message in thread["messages"] if message["role"] == "user")
    assert [part["type"] for part in user["parts"]] == ["text", "data-photo"]
    assert "Log the attached receipt" in user["parts"][0]["text"]
    cards = _cards(thread)
    assert len(cards) == 1
    assert cards[0]["data"]["card"] == "capture"
    assert cards[0]["data"]["attachment_id"] == photo["id"]
    assert cards[0]["data"]["url"] == f"/api/attachments/{photo['id']}"
    assert "The user shared a receipt" in str(
        await demo_ledger.fetchrow(
            "SELECT model_history FROM conversations WHERE id = 'thread_photo'"
        )
    )
    outside = await demo_ledger.fetchrow(
        "SELECT outside_text_at FROM conversations WHERE id = 'thread_photo'"
    )
    assert outside["outside_text_at"] is not None


async def test_image_and_text_keeps_caption_and_single_receipt(
    authed_client, demo_ledger, monkeypatch
):
    monkeypatch.setattr("arno.api.capture.extract_payment_photo", _fake_extraction)
    photo = _upload_photo(authed_client)
    body = _body("client_photo_text", text="Log this Swiggy receipt", photos=[photo])

    with authed_client.app.state.agent.override(model=_model()):
        response = authed_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    thread = authed_client.get("/api/threads/thread_photo/messages").json()
    user = next(message for message in thread["messages"] if message["role"] == "user")
    assert user["parts"][0] == {"type": "text", "text": "Log this Swiggy receipt"}
    assert len(_capture_cards(thread)) == 1
    assert await demo_ledger.fetchval("SELECT count(*) FROM captures") == 1


async def test_mixed_photo_and_statement_starts_one_each(authed_client, demo_ledger, monkeypatch):
    monkeypatch.setattr("arno.api.capture.extract_payment_photo", _fake_extraction)
    statement = authed_client.post(
        "/api/attachments",
        files={"file": ("sbi.csv", sbi_csv(), "text/csv")},
        data={"purpose": "statement"},
    )
    assert statement.status_code == 201, statement.text
    photo = _upload_photo(authed_client)
    body = _body(
        "client_photo_statement",
        text="Review the statement and log the receipt",
        statements=[statement.json()],
        photos=[photo],
    )

    with authed_client.app.state.agent.override(model=_model()):
        response = authed_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    # The statement is handed to the parser subagent; no import starts in this turn.
    assert (
        await demo_ledger.fetchval(
            "SELECT count(*) FROM imports WHERE attachment_id = $1",
            statement.json()["id"],
        )
        == 0
    )
    assert await demo_ledger.fetchval("SELECT count(*) FROM captures") == 1
    thread = authed_client.get("/api/threads/thread_photo/messages").json()
    user = next(message for message in thread["messages"] if message["role"] == "user")
    assert [part["type"] for part in user["parts"]] == [
        "text",
        "data-statement",
        "data-photo",
    ]
    cards = _cards(thread)
    # The photo logs at once; the statement waits for the parser subagent's card.
    assert sorted(card["data"]["card"] for card in cards) == ["capture"]


async def test_a_missing_photo_ref_is_refused_before_any_capture(
    authed_client, demo_ledger, monkeypatch
):
    monkeypatch.setattr("arno.api.capture.extract_payment_photo", _fake_extraction)
    body = _body(
        "client_photo_missing",
        text="Log this receipt",
        photos=[{"id": "att_missing", "filename": "receipt.png"}],
    )

    with authed_client.app.state.agent.override(model=_model()):
        response = authed_client.post("/api/chat", json=body)

    assert response.status_code == 422
    assert "no longer available" in response.json()["detail"]
    assert await demo_ledger.fetchval("SELECT count(*) FROM captures") == 0
    assert await demo_ledger.fetchval("SELECT count(*) FROM messages") == 0


async def test_a_failed_photo_leaves_a_retry_card_and_retry_logs_once(
    authed_client, demo_ledger, monkeypatch
):
    monkeypatch.setattr("arno.api.capture.extract_payment_photo", _failed_extraction)
    photo = _upload_photo(authed_client)
    body = _body("client_photo_fail", text="Log this receipt", photos=[photo])

    with authed_client.app.state.agent.override(model=_model()):
        response = authed_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    thread = authed_client.get("/api/threads/thread_photo/messages").json()
    cards = _cards(thread)
    assert len(cards) == 1
    assert cards[0]["data"]["card"] == "capture-retry"
    assert cards[0]["data"]["kind"] == "photo"
    assert await demo_ledger.fetchval("SELECT count(*) FROM captures") == 0

    monkeypatch.setattr("arno.api.capture.extract_payment_photo", _fake_extraction)
    retried = authed_client.post(f"/api/chat/thread_photo/photo/{photo['id']}")
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "logged"
    assert await demo_ledger.fetchval("SELECT count(*) FROM captures") == 1
    assert (
        await demo_ledger.fetchval("SELECT count(*) FROM attachments WHERE id = $1", photo["id"])
        == 1
    )


async def test_photo_upload_refuses_a_non_image_and_the_size_limit(authed_client, monkeypatch):
    refused = _upload(authed_client, name="notes.txt", data=b"hello")
    assert refused.status_code == 415
    assert "PNG, JPEG" in refused.json()["detail"]

    monkeypatch.setattr("arno.api.imports.MAX_UPLOAD_BYTES", 8)
    oversized = _upload(authed_client, name="large.png", data=PNG + b"x")
    assert oversized.status_code == 413


async def test_legacy_statement_upload_still_accepts_html(authed_client):
    files = {
        "file": (
            "statement.html",
            b"<html><table><tr><td>Date</td></tr></table></html>",
            "text/html",
        )
    }
    legacy = authed_client.post("/api/attachments", files=files)
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["detected_kind"] == "html"
    chat = authed_client.post("/api/attachments", files=files, data={"purpose": "statement"})
    assert chat.status_code == 422


async def test_a_second_request_cannot_prepare_the_same_thread_twice(app, demo_ledger, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    async def slow_extraction(*_args, **_kwargs) -> PaymentPhoto:
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return await _fake_extraction()

    monkeypatch.setattr("arno.api.capture.extract_payment_photo", slow_extraction)
    async with live_server(app, monkeypatch, _model()) as client:
        upload = await client.post(
            "/api/attachments",
            files={"file": ("receipt.png", PNG, "image/png")},
            data={"purpose": "photo"},
        )
        assert upload.status_code == 201, upload.text
        body = _body("client_prepare_first", photos=[upload.json()], thread="thread_prepare")

        first = asyncio.create_task(client.post("/api/chat", json=body))
        try:
            assert await _until(started.is_set), "the photo extraction never started"

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
            assert await demo_ledger.fetchval("SELECT count(*) FROM captures") == 0

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
        assert await demo_ledger.fetchval("SELECT count(*) FROM captures") == 1
        assert await demo_ledger.fetchval("SELECT count(*) FROM messages") == 4
        history = await demo_ledger.fetchval(
            "SELECT model_history FROM conversations WHERE id = 'thread_prepare'"
        )
        assert str(history).count("The user shared a receipt") == 1
        row = await demo_ledger.fetchrow(
            "SELECT run_started_at, active_run_id, outside_text_at FROM conversations "
            "WHERE id = 'thread_prepare'"
        )
        assert row["run_started_at"] is None
        assert row["active_run_id"] is None
        assert row["outside_text_at"] is not None

        replay = await client.post("/api/chat", json=body)
        assert replay.status_code == 200
        assert sum(event["type"] == "data-card" for event in _events(replay)) == 1
        assert await _until(lambda: not app.state.turns._preparing)


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
            await _first_text_delta(first)
            assert app.state.turns.get("thread_stream_replay") is not None
            replay = await client.post("/api/chat", json=body)
            assert replay.status_code == 200, replay.text
            assert sum(event["type"] == "start" for event in _events(replay)) == 1
        await _until(lambda: app.state.turns.get("thread_stream_replay") is None)

    assert await demo_ledger.fetchval("SELECT count(*) FROM messages") == 2


async def test_stop_after_a_photo_send_keeps_one_receipt(app, demo_ledger, monkeypatch):
    monkeypatch.setattr("arno.api.capture.extract_payment_photo", _fake_extraction)

    attachment_id = await new_id(demo_ledger, "att")
    await demo_ledger.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'photo', 'receipt.png', 'image/png', $2, 'photo-stop', $3)
        """,
        attachment_id,
        len(PNG),
        PNG,
    )
    body = _body(
        "client_photo_stop",
        text="Log this receipt",
        photos=[{"id": attachment_id, "filename": "receipt.png"}],
    )
    model = streaming_model(
        lambda messages, info: ModelResponse(parts=[TextPart("slow " * 40)]),
        text_chunks=40,
        delay=0.2,
    )

    async with live_server(app, monkeypatch, model) as client:
        async with client.stream("POST", "/api/chat", json=body) as response:
            assert response.status_code == 200
            await _first_text_delta(response)
            stopped = await client.post("/api/chat/thread_photo/stop")
            assert stopped.json()["stopped"] is True
        await _until(lambda: app.state.turns.get("thread_photo") is None)
        thread = (await client.get("/api/threads/thread_photo/messages")).json()

    assert await demo_ledger.fetchval("SELECT count(*) FROM captures") == 1
    assert len(_capture_cards(thread)) == 1
