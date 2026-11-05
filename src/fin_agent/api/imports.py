"""The imports REST surface: upload, password, review, commit, reject, undo."""

from __future__ import annotations

import hashlib
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from fin_agent.api.deps import Database
from fin_agent.importers.commit import commit_import, reject_import, undo_import
from fin_agent.importers.pipeline import (
    create_import,
    import_payload,
    list_imports,
    retry_with_account,
    retry_with_opening,
    retry_with_password,
)
from fin_agent.importers.readers import DocError, sniff
from fin_agent.ledger.audit import new_id
from fin_agent.ledger.errors import ConflictLedgerError, InvalidLedgerError

router = APIRouter(prefix="/api", tags=["imports"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
CHAT_STATEMENT_KINDS = frozenset({"pdf", "text", "xlsx", "xls", "xlsb", "ods", "encrypted_ooxml"})


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartImport(ApiModel):
    attachment_id: str = Field(min_length=1)
    account_id: str | None = None


class PasswordIn(ApiModel):
    password: str = Field(min_length=1, max_length=512)


class AccountIn(ApiModel):
    account_id: str = Field(min_length=1)


class OpeningIn(ApiModel):
    opening_paise: int = Field(ge=0)


class ResolveLine(ApiModel):
    resolution: Literal["new", "ignore", "merge", "confirm_transfer", "reject_transfer"]
    movement_id: str | None = None


@router.post("/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    conn: Database,
    file: Annotated[UploadFile, File()],
    purpose: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    from fin_agent.api.capture import image_media_type

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That file is over the 25 MB limit.",
        )
    if not data:
        raise HTTPException(status_code=422, detail="The file is empty.")
    selected = (purpose or "statement").strip().lower()
    if selected == "photo":
        media_type = image_media_type(data)
        if media_type is None:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Use a PNG, JPEG, WebP, or GIF image.",
            )
        kind = "photo"
        stored_kind = "photo"
        content_type = media_type
    elif selected == "statement":
        try:
            kind = sniff(data)
        except DocError as exc:
            raise HTTPException(status_code=422, detail=f"Unreadable file: {exc.code}") from exc
        if purpose is not None and kind not in CHAT_STATEMENT_KINDS:
            raise HTTPException(
                status_code=422,
                detail="Choose a PDF, CSV, or spreadsheet statement (XLSX, XLSB, ODS, XLS).",
            )
        stored_kind = "statement"
        content_type = file.content_type or "application/octet-stream"
    else:
        raise HTTPException(status_code=422, detail="Unknown upload purpose.")
    digest = hashlib.sha256(data).hexdigest()
    attachment_id = new_id(conn, "att")
    row = conn.execute(
        """
        INSERT INTO attachments
            (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, kind, filename, content_type, size_bytes, sha256, created_at
        """,
        (
            attachment_id,
            stored_kind,
            (file.filename or selected)[:255],
            content_type,
            len(data),
            digest,
            data,
        ),
    ).fetchone()
    conn.commit()
    payload = dict(row)
    payload["detected_kind"] = kind
    payload["created_at"] = row["created_at"].isoformat()
    return payload


@router.post("/imports", status_code=status.HTTP_202_ACCEPTED)
def start_import(body: StartImport, conn: Database) -> JSONResponse:
    try:
        row = create_import(conn, attachment_id=body.attachment_id, account_id=body.account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(status_code=202, content=import_payload(conn, row["id"]))


@router.get("/imports")
def imports_list(conn: Database, limit: int = 50) -> dict[str, Any]:
    return {"items": list_imports(conn, limit=min(max(limit, 1), 200))}


@router.get("/imports/{import_id}")
def import_detail(import_id: str, conn: Database) -> dict[str, Any]:
    try:
        return import_payload(conn, import_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/imports/{import_id}/password", status_code=status.HTTP_202_ACCEPTED)
def import_password(import_id: str, body: PasswordIn, conn: Database) -> JSONResponse:
    row = conn.execute("SELECT status FROM imports WHERE id = %s", (import_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such import")
    if row["status"] not in ("needs_password", "needs_input", "failed"):
        raise HTTPException(status_code=409, detail=f"That import is {row['status']}")
    retry_with_password(conn, import_id=import_id, password=body.password)
    return JSONResponse(status_code=202, content=import_payload(conn, import_id))


@router.post("/imports/{import_id}/account", status_code=status.HTTP_202_ACCEPTED)
def import_account(import_id: str, body: AccountIn, conn: Database) -> JSONResponse:
    row = conn.execute("SELECT status FROM imports WHERE id = %s", (import_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such import")
    if row["status"] not in ("needs_input", "failed"):
        raise HTTPException(status_code=409, detail=f"That import is {row['status']}")
    try:
        retry_with_account(conn, import_id=import_id, account_id=body.account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(status_code=202, content=import_payload(conn, import_id))


@router.post("/imports/{import_id}/opening", status_code=status.HTTP_202_ACCEPTED)
def import_opening(import_id: str, body: OpeningIn, conn: Database) -> JSONResponse:
    row = conn.execute("SELECT status FROM imports WHERE id = %s", (import_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such import")
    if row["status"] not in ("needs_input", "failed", "review"):
        raise HTTPException(status_code=409, detail=f"That import is {row['status']}")
    try:
        retry_with_opening(conn, import_id=import_id, opening_paise=body.opening_paise)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(status_code=202, content=import_payload(conn, import_id))


@router.post("/imports/{import_id}/commit")
def import_commit(import_id: str, conn: Database) -> dict[str, Any]:
    try:
        result = commit_import(conn, import_id)
    except ConflictLedgerError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except InvalidLedgerError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    payload = import_payload(conn, import_id)
    payload["commit"] = result
    return payload


@router.post("/imports/{import_id}/reject")
def import_reject(import_id: str, conn: Database, reason: str | None = None) -> dict[str, Any]:
    row = conn.execute("SELECT status FROM imports WHERE id = %s", (import_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such import")
    if row["status"] == "committed":
        raise HTTPException(
            status_code=409, detail="Undo a committed import from the Imports screen."
        )
    reject_import(conn, import_id, reason)
    return import_payload(conn, import_id)


@router.post("/imports/{import_id}/undo")
def import_undo(import_id: str, conn: Database) -> dict[str, Any]:
    try:
        result = undo_import(conn, import_id)
    except ConflictLedgerError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except InvalidLedgerError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    payload = import_payload(conn, import_id)
    payload["undo"] = result
    return payload


@router.patch("/imports/{import_id}/lines/{line_id}")
def resolve_import_line(
    import_id: str, line_id: str, body: ResolveLine, conn: Database
) -> dict[str, Any]:
    from fin_agent.importers.reconcile import resolve_line

    try:
        resolve_line(
            conn,
            import_id=import_id,
            line_id=line_id,
            resolution=body.resolution,
            movement_id=body.movement_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return import_payload(conn, import_id)


@router.get("/imports/{import_id}/password")
def password_never_returned(import_id: str) -> dict[str, Any]:
    """There is no password read path. This route exists to say so plainly."""
    raise HTTPException(
        status_code=405,
        detail="A statement password is write-only and lives in memory for one import.",
        headers={"Allow": "POST"},
    )


__all__ = ["MAX_UPLOAD_BYTES", "router"]
