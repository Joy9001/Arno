"""L7 capture: voice notes, receipt photos, retry cards, and server-set outside text.

No STT or vision endpoint is real: the transcription path talks to a loopback HTTP stub, and the
photo path swaps in a typed extraction. Model requests stay blocked by `conftest.py`.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import override

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from fin_agent.capture.photo import PaymentPhoto
from fin_agent.ledger import movements as movements_mod
from fin_agent.ledger import payees as payees_mod
from fin_agent.ledger.audit import new_id
from tests.agent_helpers import streaming_model

PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"
WEBM = b"\x1aE\xdf\xa3" + b"test-audio"


class _SttHandler(BaseHTTPRequestHandler):
    calls: list[dict] = []
    response_text = "Swiggy par 450 rupaye"

    def do_POST(self) -> None:  # noqa: N802 - the stdlib name
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        type(self).calls.append({"path": self.path, "headers": dict(self.headers), "body": body})
        payload = json.dumps({"text": type(self).response_text}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    @override
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        del format, args


@pytest.fixture
def stt_stub():
    calls: list[dict] = []
    handler = type("Handler", (_SttHandler,), {"calls": calls})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    server.block_on_close = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield calls, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


async def _audio_attachment(conn) -> str:
    attachment_id = await new_id(conn, "att")
    await conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'audio', 'voice.webm', 'audio/webm', $2, 'hash', $3)
        """,
        attachment_id,
        len(WEBM),
        WEBM,
    )
    return attachment_id


def _has_tool_return(messages, name: str) -> bool:
    for message in messages:
        for part in getattr(message, "parts", []):
            if getattr(part, "tool_name", None) == name:
                return True
    return False


async def test_a_failed_transcription_keeps_the_audio_and_leaves_a_retry_card(
    authed_client, ledger, monkeypatch
):
    monkeypatch.setenv("STT_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("STT_MODEL", "blocked-in-tests")
    response = authed_client.post(
        "/api/transcribe",
        files={"file": ("voice.webm", WEBM, "audio/webm")},
        data={"thread_id": "cnv_voice_fail"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["attachment_id"].startswith("att_")
    assert payload["card"]["card"] == "capture-retry"
    assert payload["card"]["kind"] == "voice"

    row = await ledger.fetchrow(
        "SELECT kind, content_type, data FROM attachments WHERE id = $1",
        payload["attachment_id"],
    )
    assert row["kind"] == "audio"
    assert row["content_type"] == "audio/webm"
    assert bytes(row["data"]) == WEBM

    card = await ledger.fetchrow(
        "SELECT content FROM messages WHERE client_id = $1",
        f"capfail_{payload['attachment_id']}",
    )
    assert card is not None
    assert card["content"]["parts"][0]["data"]["card"] == "capture-retry"

    retry = authed_client.post(
        f"/api/transcribe/{payload['attachment_id']}?thread_id=cnv_voice_fail"
    )
    assert retry.status_code == 200
    assert retry.json()["status"] == "failed"
    assert retry.json()["attachment_id"] == payload["attachment_id"]
    count = await ledger.fetchval("SELECT count(*) FROM attachments")
    assert count == 1


async def test_transcription_reaches_the_endpoint_with_the_model_file_and_payee_prompt(
    authed_client, ledger, stt_stub, monkeypatch
):
    calls, port = stt_stub
    await payees_mod.create_payee(ledger, display_name="Swiggy", kind="merchant", actor="user")
    monkeypatch.setenv("STT_BASE_URL", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setenv("STT_MODEL", "stub-whisper")
    monkeypatch.setenv("STT_API_KEY", "secret")

    response = authed_client.post(
        "/api/transcribe",
        files={"file": ("voice.webm", WEBM, "audio/webm")},
        data={"thread_id": "cnv_voice_ok"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "transcribed"
    assert payload["transcript"] == "Swiggy par 450 rupaye"
    assert payload["payee_context"] is True

    assert len(calls) == 1
    call = calls[0]
    assert call["path"] == "/v1/audio/transcriptions"
    assert call["headers"]["Authorization"] == "Bearer secret"
    body = call["body"]
    assert b'name="model"' in body
    assert b"stub-whisper" in body
    assert b'name="prompt"' in body
    assert b"Swiggy" in body
    assert b'name="file"' in body
    assert WEBM in body

    without = authed_client.post(
        "/api/transcribe",
        files={"file": ("voice.webm", WEBM, "audio/webm")},
        data={"thread_id": "cnv_voice_ok", "payee_context": "false"},
    )
    assert without.status_code == 200
    assert b'name="prompt"' not in calls[1]["body"]
    assert b'name="language"' not in calls[1]["body"]

    monkeypatch.setenv("STT_LANGUAGE", "en")
    pinned = authed_client.post(
        "/api/transcribe",
        files={"file": ("voice.webm", WEBM, "audio/webm")},
        data={"thread_id": "cnv_voice_ok"},
    )
    assert pinned.status_code == 200
    assert b'name="language"' in calls[2]["body"]


async def test_a_voice_transcript_takes_the_chat_path_without_flagging_the_thread(
    authed_client, demo_ledger
):
    attachment_id = await _audio_attachment(demo_ledger)

    def script(messages, _info):
        if not _has_tool_return(messages, "record_movement"):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "record_movement",
                        {
                            "movement": {
                                "kind": "expense",
                                "amount_rupees": 450,
                                "payee": "Swiggy",
                                "category": "Food & Dining",
                            }
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Logged.")])

    with authed_client.app.state.agent.override(model=streaming_model(script)):
        response = authed_client.post(
            "/api/chat",
            json={
                "id": "cnv_voice_chat",
                "trigger": "submit-message",
                "voice_attachment_id": attachment_id,
                "messages": [
                    {
                        "id": "msg_voice_1",
                        "role": "user",
                        "parts": [{"type": "text", "text": "Swiggy par 450 rupaye"}],
                    }
                ],
            },
        )
    assert response.status_code == 200, response.text

    thread = await demo_ledger.fetchrow(
        "SELECT outside_text_at FROM conversations WHERE id = 'cnv_voice_chat'"
    )
    assert thread["outside_text_at"] is None
    capture = await demo_ledger.fetchrow(
        "SELECT kind, ref FROM captures WHERE ref = $1", attachment_id
    )
    assert capture is not None
    assert capture["kind"] == "voice"


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


async def test_a_photo_is_saved_logged_once_and_marked_by_the_server(
    authed_client, demo_ledger, monkeypatch
):
    monkeypatch.setattr("fin_agent.api.capture.extract_payment_photo", _fake_extraction)
    response = authed_client.post(
        "/api/chat/cnv_photo_1/photo",
        files={"file": ("upi.png", PNG, "image/png")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "logged"
    card = payload["card"]
    assert card["card"] == "capture"
    assert card["merchant"] == "Swiggy"
    assert card["amount_paise"] == 45_000
    assert card["upi_ref"] == "412345678901"
    assert card["url"] == f"/api/attachments/{payload['attachment_id']}"
    assert card["undo_id"]

    attachment = await demo_ledger.fetchrow(
        "SELECT kind, content_type, data FROM attachments WHERE id = $1",
        payload["attachment_id"],
    )
    assert attachment["kind"] == "photo"
    assert attachment["content_type"] == "image/png"
    assert bytes(attachment["data"]) == PNG

    movement = await movements_mod.get_movement(demo_ledger, card["movement_id"])
    assert movement["kind"] == "expense"
    assert movement["description"] == "Swiggy"
    assert movement["amount_paise"] == 45_000

    capture = await demo_ledger.fetchrow(
        "SELECT kind, ref, upi_ref FROM captures WHERE movement_id = $1",
        card["movement_id"],
    )
    assert capture["kind"] == "photo"
    assert capture["ref"] == payload["attachment_id"]
    assert capture["upi_ref"] == "412345678901"

    thread = await demo_ledger.fetchrow(
        "SELECT outside_text_at, model_history FROM conversations WHERE id = 'cnv_photo_1'"
    )
    assert thread["outside_text_at"] is not None
    history_text = json.dumps(thread["model_history"])
    assert "<outside>Swiggy</outside>" in history_text
    assert "<outside>412345678901</outside>" in history_text

    saved = await demo_ledger.fetchrow(
        "SELECT role, content FROM messages WHERE client_id = $1",
        f"cap_{payload['attachment_id']}",
    )
    assert saved["role"] == "assistant"
    assert saved["content"]["parts"][0]["data"]["card"] == "capture"

    image = authed_client.get(f"/api/attachments/{payload['attachment_id']}")
    assert image.status_code == 200
    assert image.content == PNG
    assert image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "private, no-store"
    assert "img-src 'self' blob: data:" in image.headers["content-security-policy"]

    undone = authed_client.post(f"/api/undo/{card['undo_id']}")
    assert undone.status_code == 200, undone.text
    assert (await movements_mod.get_movement(demo_ledger, card["movement_id"]))["reversed"] is True


async def test_a_failed_vision_call_leaves_the_photo_and_a_retry_card(
    authed_client, demo_ledger, monkeypatch
):
    monkeypatch.setattr("fin_agent.api.capture.extract_payment_photo", _failed_extraction)
    response = authed_client.post(
        "/api/chat/cnv_photo_fail/photo",
        files={"file": ("upi.png", PNG, "image/png")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["card"]["card"] == "capture-retry"
    assert payload["card"]["kind"] == "photo"

    attachment = await demo_ledger.fetchrow(
        "SELECT kind, data FROM attachments WHERE id = $1", payload["attachment_id"]
    )
    assert attachment["kind"] == "photo"
    assert bytes(attachment["data"]) == PNG
    assert (
        await demo_ledger.fetchval(
            "SELECT count(*) FROM captures WHERE ref = $1", payload["attachment_id"]
        )
        == 0
    )

    monkeypatch.setattr("fin_agent.api.capture.extract_payment_photo", _fake_extraction)
    retried = authed_client.post(f"/api/chat/cnv_photo_fail/photo/{payload['attachment_id']}")
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "logged"
    assert (
        await demo_ledger.fetchval(
            "SELECT count(*) FROM attachments WHERE id = $1",
            payload["attachment_id"],
        )
        == 1
    )
    assert (
        await demo_ledger.fetchval(
            "SELECT count(*) FROM captures WHERE ref = $1", payload["attachment_id"]
        )
        == 1
    )


def test_photo_extraction_is_five_capped_fields_and_has_no_line_items():
    assert set(PaymentPhoto.model_fields) == {
        "merchant",
        "amount_paise",
        "date",
        "method",
        "upi_ref",
    }
    PaymentPhoto(merchant="Swiggy", amount_paise=45_000, method="cash")
    # A UTR is often printed in groups; the caller strips the spaces before it is stored.
    assert PaymentPhoto(upi_ref="4145 1234 5678").upi_ref == "4145 1234 5678"
    with pytest.raises(ValidationError):
        PaymentPhoto(merchant="x" * 201)
    with pytest.raises(ValidationError):
        PaymentPhoto(amount_paise=0)  # pyrefly: ignore[bad-argument-type] - asserts the gt=0 refusal
    with pytest.raises(ValidationError):
        PaymentPhoto(upi_ref="4145;1234")
    with pytest.raises(ValidationError):
        PaymentPhoto(method="cheque")  # pyrefly: ignore[bad-argument-type] - asserts the enum refusal


def test_capture_endpoints_and_attachments_require_a_session(client):
    assert client.get("/api/attachments/att_missing").status_code == 401
    assert (
        client.post(
            "/api/transcribe",
            files={"file": ("voice.webm", WEBM, "audio/webm")},
        ).status_code
        == 401
    )


async def test_photo_upload_rejects_a_file_that_is_not_an_image(authed_client):
    response = authed_client.post(
        "/api/chat/cnv_photo_bad/photo",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 415
