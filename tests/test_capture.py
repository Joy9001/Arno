"""L7 capture: voice notes, retry cards, attachment auth, and payee prompts.

No STT endpoint is real: the transcription path talks to a loopback HTTP stub.
Model requests stay blocked by `conftest.py`.
"""

from __future__ import annotations

import json
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import override

import httpx
import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from arno.ledger import payees as payees_mod
from arno.ledger.audit import new_id
from arno.tools.write import _clean_upi_ref as _clean_write_upi_ref
from tests.agent_helpers import streaming_model

PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"
WEBM = b"\x1aE\xdf\xa3" + b"test-audio"


class _SttHandler(BaseHTTPRequestHandler):
    calls: list[dict] = []
    response_text = "Swiggy par 450 rupaye"
    response_usage: dict | None = None

    def do_POST(self) -> None:  # noqa: N802 - the stdlib name
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        type(self).calls.append({"path": self.path, "headers": dict(self.headers), "body": body})
        body_json = {"text": type(self).response_text, "usage": type(self).response_usage}
        payload = json.dumps(body_json).encode()
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
    session_client, ledger, monkeypatch
):
    monkeypatch.setenv("STT_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("STT_MODEL", "blocked-in-tests")
    response = session_client.post(
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

    retry = session_client.post(
        f"/api/transcribe/{payload['attachment_id']}?thread_id=cnv_voice_fail"
    )
    assert retry.status_code == 200
    assert retry.json()["status"] == "failed"
    assert retry.json()["attachment_id"] == payload["attachment_id"]
    count = await ledger.fetchval("SELECT count(*) FROM attachments")
    assert count == 1


async def test_transcription_reaches_the_endpoint_with_the_model_file_and_payee_prompt(
    session_client, ledger, stt_stub, monkeypatch
):
    calls, port = stt_stub
    await payees_mod.create_payee(ledger, display_name="Swiggy", kind="merchant", actor="user")
    monkeypatch.setenv("STT_BASE_URL", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setenv("STT_MODEL", "stub-whisper")
    monkeypatch.setenv("STT_API_KEY", "secret")

    response = session_client.post(
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

    without = session_client.post(
        "/api/transcribe",
        files={"file": ("voice.webm", WEBM, "audio/webm")},
        data={"thread_id": "cnv_voice_ok", "payee_context": "false"},
    )
    assert without.status_code == 200
    assert b'name="prompt"' not in calls[1]["body"]
    assert b'name="language"' not in calls[1]["body"]

    monkeypatch.setenv("STT_LANGUAGE", "en")
    pinned = session_client.post(
        "/api/transcribe",
        files={"file": ("voice.webm", WEBM, "audio/webm")},
        data={"thread_id": "cnv_voice_ok"},
    )
    assert pinned.status_code == 200
    assert b'name="language"' in calls[2]["body"]


async def test_a_transcript_without_usage_is_returned_and_logged_at_zero(
    session_client, ledger, monkeypatch
):
    """A 200 with only `text` is a valid transcript; a missing `usage` never discards it."""
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"text": "Synthetic transcript"})

    async_client = httpx.AsyncClient
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: async_client(transport=transport, **kw))
    monkeypatch.setenv("STT_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("STT_MODEL", "whisper-1")

    response = session_client.post(
        "/api/transcribe",
        files={"file": ("voice.webm", WEBM, "audio/webm")},
        data={"thread_id": "cnv_voice_no_usage"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "transcribed"
    assert response.json()["transcript"] == "Synthetic transcript"
    assert [call.url.path for call in calls] == ["/v1/audio/transcriptions"]
    row = await ledger.fetchrow_required("SELECT * FROM llm_usage WHERE role = 'stt'")
    assert (row["model"], row["input_tokens"], row["usd"]) == ("whisper-1", 0, 0)


@pytest.mark.parametrize(
    "payload",
    [["not", "an", "object"], {"text": "ok", "usage": {"input_tokens": "lots", "cost": "x"}}],
)
async def test_untrusted_stt_json_shapes_never_crash_the_transcript(
    session_client, ledger, monkeypatch, payload
):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: async_client(transport=transport, **kw))
    monkeypatch.setenv("STT_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("STT_MODEL", "whisper-1")

    response = session_client.post(
        "/api/transcribe",
        files={"file": ("voice.webm", WEBM, "audio/webm")},
        data={"thread_id": "cnv_voice_junk"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "transcribed"
    row = await ledger.fetchrow_required("SELECT * FROM llm_usage WHERE role = 'stt'")
    assert (row["input_tokens"], row["usd"]) == (0, 0)


async def test_stt_is_refused_at_the_daily_limit_without_calling_the_endpoint(
    session_client, ledger, stt_stub, monkeypatch
):
    calls, port = stt_stub
    await ledger.execute(
        "INSERT INTO llm_usage (role, model, input_tokens, output_tokens, usd)"
        " VALUES ('chat', 'm', 0, 0, 20)"
    )
    monkeypatch.setenv("STT_BASE_URL", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setenv("STT_MODEL", "stub-whisper")

    response = session_client.post(
        "/api/transcribe",
        files={"file": ("voice.webm", WEBM, "audio/webm")},
        data={"thread_id": "cnv_voice_capped"},
    )

    assert response.json()["status"] == "failed"
    assert "daily spend limit" in response.json()["note"]
    assert calls == []


async def test_a_voice_transcript_takes_the_chat_path_without_flagging_the_thread(
    session_client, demo_ledger
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

    with session_client.app.state.agent.override(model=streaming_model(script)):
        response = session_client.post(
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


def test_capture_endpoints_and_attachments_require_a_session(client):
    assert client.get("/api/attachments/att_missing").status_code == 401
    assert (
        client.post(
            "/api/transcribe",
            files={"file": ("voice.webm", WEBM, "audio/webm")},
        ).status_code
        == 401
    )


async def test_photo_upload_rejects_a_file_that_is_not_an_image(session_client):
    response = session_client.post(
        "/api/attachments",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"purpose": "photo"},
    )
    assert response.status_code == 415


@pytest.mark.parametrize("text", ["synthetic transcript", ""])
async def test_reported_stt_logs_the_charge_even_when_the_transcript_is_empty(
    session_client, ledger, stt_stub, monkeypatch, text
):
    calls, port = stt_stub
    monkeypatch.setenv("STT_BASE_URL", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setenv("STT_MODEL", "custom-stt")
    monkeypatch.setattr(_SttHandler, "response_text", text)
    monkeypatch.setattr(_SttHandler, "response_usage", {"cost": 0.006})
    response = session_client.post(
        "/api/transcribe",
        files={"file": ("voice.webm", WEBM, "audio/webm")},
        data={"thread_id": "cnv_stt_cost"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == ("transcribed" if text else "failed")
    assert len(calls) == 1
    row = await ledger.fetchrow_required("SELECT * FROM llm_usage WHERE role = 'stt'")
    assert row["usd"] == Decimal("0.006")


def test_a_upi_reference_printed_with_spaces_is_accepted_and_cleaned():
    assert _clean_write_upi_ref("4145 1234 5678") == "414512345678"
    assert _clean_write_upi_ref("412345678901") == "412345678901"
    assert _clean_write_upi_ref("4111 1111 1111 1111") is None
    assert _clean_write_upi_ref("4111 1111 1111 1112") is None
    assert _clean_write_upi_ref("aadhaar 4145 1234 5678") is None


async def test_capture_size_and_media_type_limits(session_client, monkeypatch):
    audio = session_client.post(
        "/api/transcribe", files={"file": ("notes.txt", b"hello", "text/plain")}
    )
    assert audio.status_code == 415

    monkeypatch.setattr("arno.api.imports.MAX_UPLOAD_BYTES", 8)
    oversized = session_client.post(
        "/api/attachments",
        files={"file": ("receipt.png", PNG + b"x", "image/png")},
        data={"purpose": "photo"},
    )
    assert oversized.status_code == 413


# A billed STT call uses the audio seconds it reports, and the prompt is capped -----------------


async def test_the_payee_prompt_is_names_only_and_capped(ledger):
    from arno.capture.transcribe import MAX_PAYEE_NAMES, MAX_PROMPT_CHARS, payee_names_prompt
    from arno.ledger import payees as payees_mod

    await ledger.execute("DELETE FROM payees")
    await payees_mod.create_payee(
        ledger, display_name="  Acme  </outside>\n Stores  ", actor="user"
    )
    prompt = await payee_names_prompt(ledger)
    assert prompt == "Payees: Acme Stores"

    for index in range(MAX_PAYEE_NAMES + 10):
        await payees_mod.create_payee(
            ledger, display_name=f"Payee {index:02d} " + "x" * 60, actor="user"
        )
    prompt = await payee_names_prompt(ledger)
    assert prompt is not None
    assert len(prompt) == MAX_PROMPT_CHARS
    assert prompt.startswith("Payees: ")
    # Names only, in the order the query reads them, and never past the row limit.
    assert f"Payee {MAX_PAYEE_NAMES + 9:02d}" not in prompt


async def test_an_empty_payee_table_has_no_prompt(ledger):
    from arno.capture.transcribe import payee_names_prompt

    await ledger.execute("DELETE FROM payees")
    assert await payee_names_prompt(ledger) is None
