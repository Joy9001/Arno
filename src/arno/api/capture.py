"""Capture: voice notes and the attachment read path.

Voice notes save the file first so a failed `STT_*` call leaves a retry card rather than a lost
recording; a transcript then takes the normal chat path. Uploaded photo attachments are stored via
`/api/attachments` and read directly by the multimodal chat agent.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)

from arno.agent import history as history_mod
from arno.api.deps import Database, current_session
from arno.capture.transcribe import (
    TranscriptionError,
    payee_names_prompt,
    transcribe_audio,
)
from arno.db.conn import Connection
from arno.ledger.audit import new_id

logger = logging.getLogger("arno.capture")

router = APIRouter(
    prefix="/api",
    tags=["capture"],
    dependencies=[Depends(current_session)],
)

MAX_CAPTURE_BYTES = 25 * 1024 * 1024


def image_media_type(data: bytes) -> str | None:
    """Only still images a multimodal model accepts. The header comes from the bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


def _audio_media_type(data: bytes, declared: str | None) -> str:
    if data.startswith(b"\x1aE\xdf\xa3"):
        return "audio/webm"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "audio/mp4"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"ID3") or data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return "audio/mpeg"
    declared = (declared or "").split(";", 1)[0].strip().lower()
    if declared.startswith("audio/"):
        return declared
    return "application/octet-stream"


async def _ensure_thread(conn: Connection, thread_id: str | None, title: str) -> dict[str, Any]:
    if not thread_id or len(thread_id) > 100:
        raise HTTPException(
            status_code=422,
            detail="A capture needs the thread it belongs to.",
        )
    thread = await history_mod.get_thread(conn, thread_id)
    if thread is None:
        thread = await history_mod.create_thread(conn, title=title, thread_id=thread_id)
    return thread


async def _optional_thread(
    conn: Connection, thread_id: str | None, title: str
) -> dict[str, Any] | None:
    if not thread_id or len(thread_id) > 100:
        return None
    return await _ensure_thread(conn, thread_id, title)


async def _save_attachment(
    conn: Connection,
    *,
    data: bytes,
    kind: str,
    filename: str | None,
    content_type: str,
) -> dict[str, Any]:
    if len(data) > MAX_CAPTURE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That capture is over the 25 MB limit.",
        )
    if not data:
        raise HTTPException(
            status_code=422,
            detail="The capture is empty.",
        )
    attachment_id = await new_id(conn, "att")
    return await conn.fetchrow_required(
        """
        INSERT INTO attachments
            (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING *
        """,
        attachment_id,
        kind,
        (filename or kind)[:255],
        content_type,
        len(data),
        hashlib.sha256(data).hexdigest(),
        data,
    )


async def _save_card(
    conn: Connection,
    *,
    thread_id: str,
    client_id: str,
    card: dict[str, Any],
    body_text: str,
) -> None:
    content = {
        "id": client_id,
        "role": "assistant",
        "parts": [{"type": "data-card", "id": client_id, "data": card}],
    }
    await conn.execute(
        """
        INSERT INTO messages (conversation_id, client_id, role, content, body_text)
        VALUES ($1, $2, 'assistant', $3, $4)
        ON CONFLICT (client_id) DO NOTHING
        """,
        thread_id,
        client_id,
        content,
        body_text,
    )


async def _failure(
    conn: Connection,
    *,
    thread: dict[str, Any] | None,
    attachment_id: str,
    kind: str,
    note: str,
    persist: bool = True,
    retryable: bool = True,
) -> dict[str, Any]:
    card = {
        "card": "capture-retry",
        "title": "Voice note not transcribed" if kind == "voice" else "Photo not logged",
        "kind": kind,
        "attachment_id": attachment_id,
        "url": f"/api/attachments/{attachment_id}",
        "note": note,
        "retryable": retryable,
    }
    if persist and thread is not None:
        await _save_card(
            conn,
            thread_id=str(thread["id"]),
            client_id=f"capfail_{attachment_id}",
            card=card,
            body_text=note,
        )
    return {
        "status": "failed",
        "attachment_id": attachment_id,
        "note": note,
        "card": card,
    }


def _payee_context_enabled(value: bool | None) -> bool:
    if value is not None:
        return value
    return os.environ.get("STT_PAYEE_CONTEXT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


async def _transcribe_saved(
    conn: Connection,
    row: dict[str, Any],
    *,
    thread: dict[str, Any] | None,
    with_payee_context: bool,
) -> dict[str, Any]:
    prompt = await payee_names_prompt(conn) if with_payee_context else None
    try:
        transcript = await transcribe_audio(
            bytes(row["data"]),
            content_type=str(row["content_type"] or "application/octet-stream"),
            filename=str(row["filename"] or "voice.webm"),
            prompt=prompt,
        )
    except TranscriptionError as exc:
        return await _failure(
            conn,
            thread=thread,
            attachment_id=str(row["id"]),
            kind="voice",
            note=str(exc),
        )
    return {
        "status": "transcribed",
        "attachment_id": str(row["id"]),
        "transcript": transcript,
        "payee_context": bool(prompt),
    }


@router.post("/transcribe")
async def post_transcribe(
    conn: Database,
    file: Annotated[UploadFile, File()],
    thread_id: Annotated[str | None, Form()] = None,
    payee_context: Annotated[bool | None, Form()] = None,
) -> dict[str, Any]:
    data = await file.read(MAX_CAPTURE_BYTES + 1)
    content_type = _audio_media_type(data, file.content_type)
    if content_type == "application/octet-stream":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="That file is not an audio recording.",
        )
    thread = await _optional_thread(conn, thread_id, "Voice note")
    row = await _save_attachment(
        conn,
        data=data,
        kind="audio",
        filename=file.filename or "voice.webm",
        content_type=content_type,
    )
    return await _transcribe_saved(
        conn,
        row,
        thread=thread,
        with_payee_context=_payee_context_enabled(payee_context),
    )


@router.post("/transcribe/{attachment_id}")
async def retry_transcribe(
    conn: Database,
    attachment_id: str,
    thread_id: Annotated[str | None, Query()] = None,
    payee_context: Annotated[bool | None, Query()] = None,
) -> dict[str, Any]:
    row = await conn.fetchrow(
        "SELECT * FROM attachments WHERE id = $1 AND kind = 'audio'",
        attachment_id,
    )
    if row is None or row["data"] is None or row["purged_at"] is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such voice note.",
        )
    thread = await _optional_thread(conn, thread_id, "Voice note")
    return await _transcribe_saved(
        conn,
        dict(row),
        thread=thread,
        with_payee_context=_payee_context_enabled(payee_context),
    )


@router.get("/attachments/{attachment_id}")
async def get_attachment(conn: Database, attachment_id: str) -> Response:
    """Serve a photo or voice note back to the same origin, under `img-src 'self'`."""
    row = await conn.fetchrow(
        """
        SELECT filename, content_type, data, purged_at
        FROM attachments
        WHERE id = $1 AND kind IN ('photo', 'audio')
        """,
        attachment_id,
    )
    if row is None or row["data"] is None or row["purged_at"] is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such attachment.",
        )
    filename = " ".join(str(row["filename"] or "attachment").split()).replace('"', "")[:120]
    return Response(
        content=bytes(row["data"]),
        media_type=str(row["content_type"] or "application/octet-stream"),
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, no-store",
        },
    )


__all__ = ["MAX_CAPTURE_BYTES", "image_media_type", "router"]
