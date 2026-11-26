"""The imports REST surface: upload, password, review, commit, reject, undo, retry."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from arno.api.deps import Database, current_session
from arno.importers.commit import commit_import, reject_import, undo_import
from arno.importers.pipeline import (
    import_list_item,
    import_payload,
    list_imports,
    retry_with_password,
    set_manual_opening,
)
from arno.importers.readers import DocError, sniff
from arno.ledger.audit import new_id
from arno.ledger.errors import ConflictLedgerError, InvalidLedgerError

router = APIRouter(prefix="/api", tags=["imports"], dependencies=[Depends(current_session)])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
CHAT_STATEMENT_KINDS = frozenset({"pdf", "text", "xlsx", "xls", "xlsb", "ods", "encrypted_ooxml"})


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PasswordIn(ApiModel):
    password: str = Field(min_length=1, max_length=512)


class OpeningIn(ApiModel):
    # Paise. Bounded well inside the bigint columns and outside any real statement.
    opening_paise: int = Field(ge=-(10**15), le=10**15)


class ResolveLine(ApiModel):
    resolution: Literal[
        "new", "ignore", "merge", "confirm_transfer", "reject_transfer", "classify_credit"
    ]
    movement_id: str | None = None
    credit_kind: Literal["refund", "transfer", "income"] | None = None
    category_id: str | None = None
    source_account_id: str | None = None


class CommitIn(ApiModel):
    """The review screen's answer to the closing adjustment the payload stated."""

    adjustment_confirmed: bool = False


@router.post("/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    conn: Database,
    file: Annotated[UploadFile, File()],
    purpose: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    from arno.api.capture import image_media_type

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
    attachment_id = await new_id(conn, "att")
    row = await conn.fetchrow_required(
        """
        INSERT INTO attachments
            (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, kind, filename, content_type, size_bytes, sha256, created_at
        """,
        attachment_id,
        stored_kind,
        (file.filename or selected)[:255],
        content_type,
        len(data),
        digest,
        data,
    )
    payload = dict(row)
    payload["detected_kind"] = kind
    payload["created_at"] = row["created_at"].isoformat()
    return payload


@router.delete("/attachments/{attachment_id}")
async def delete_attachment(attachment_id: str, conn: Database) -> dict[str, Any]:
    """Purge a stored statement file; the replay set drops it. The password is already gone."""
    row = await conn.fetchrow("SELECT id, kind FROM attachments WHERE id = $1", attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such attachment")
    if row["kind"] != "statement":
        raise HTTPException(status_code=409, detail="Only a statement file can be deleted here")
    await conn.execute(
        "UPDATE attachments SET data = NULL, purged_at = now() WHERE id = $1",
        attachment_id,
    )
    return {"attachment_id": attachment_id, "purged": True}


@router.get("/imports")
async def imports_list(conn: Database, limit: int = 50) -> dict[str, Any]:
    return {"items": await list_imports(conn, limit=min(max(limit, 1), 200))}


@router.get("/import-history")
async def import_history(
    conn: Database,
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = None,
) -> dict[str, Any]:
    """Imports newest first, a page at a time with an opaque cursor for the next page.

    `GET /api/imports` keeps its shape and cap for every current client; older statements stay
    reachable through this keyset route. The order is `created_at DESC, id DESC`, so the id is
    the deterministic tie-break.
    """
    where = ""
    params: list[Any] = []
    if cursor:
        try:
            created_at, _, import_id = cursor.rpartition("|")
            seen = datetime.fromisoformat(created_at)
            if not import_id or seen.tzinfo is None:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid import history cursor.") from exc
        where = "WHERE (i.created_at, i.id) < ($1, $2)"
        params = [seen, import_id]
    rows = await conn.fetch(
        f"""
        SELECT
            i.id, i.status, i.period_start, i.period_end, i.check_status, i.counts,
            i.reason, i.created_at, i.committed_at, i.undone_at,
            a.filename, acc.name AS account_name,
            (a.data IS NOT NULL AND a.purged_at IS NULL) AS file_present
        FROM imports i
        LEFT JOIN attachments a ON a.id = i.attachment_id
        LEFT JOIN v_accounts acc ON acc.id = i.account_id
        {where}
        ORDER BY i.created_at DESC, i.id DESC
        LIMIT ${len(params) + 1}
        """,  # noqa: S608 - `where` is a fixed fragment, never user text
        *params,
        limit + 1,
    )
    page = rows[:limit]
    more = len(rows) > limit
    return {
        "items": [import_list_item(row) for row in page],
        "next_cursor": f"{page[-1]['created_at'].isoformat()}|{page[-1]['id']}" if more else None,
    }


@router.get("/imports/{import_id}")
async def import_detail(import_id: str, conn: Database) -> dict[str, Any]:
    try:
        return await import_payload(conn, import_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/imports/{import_id}/password", status_code=status.HTTP_202_ACCEPTED)
async def import_password(import_id: str, body: PasswordIn, conn: Database) -> JSONResponse:
    row = await conn.fetchrow("SELECT status FROM imports WHERE id = $1", import_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such import")
    if row["status"] not in ("needs_password", "needs_input", "failed"):
        raise HTTPException(status_code=409, detail=f"That import is {row['status']}")
    await retry_with_password(conn, import_id=import_id, password=body.password)
    return JSONResponse(status_code=202, content=await import_payload(conn, import_id))


@router.post("/imports/{import_id}/opening")
async def import_opening(import_id: str, body: OpeningIn, conn: Database) -> dict[str, Any]:
    """The opening the user states for a statement that prints none.

    `opening_paise` is the amount as the user reads it: an amount owed on a card (negative when
    the card is in credit), a balance on any other account. The server converts it to the
    ledger's sign once, by account kind.
    """
    try:
        return await set_manual_opening(conn, import_id=import_id, opening_paise=body.opening_paise)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/imports/{import_id}/commit")
async def import_commit(
    import_id: str, conn: Database, body: CommitIn | None = None
) -> dict[str, Any]:
    try:
        result = await commit_import(
            conn,
            import_id,
            adjustment_confirmed=bool(body and body.adjustment_confirmed),
        )
    except ConflictLedgerError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except InvalidLedgerError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    payload = await import_payload(conn, import_id)
    payload["commit"] = result
    return payload


@router.post("/imports/{import_id}/reject")
async def import_reject(
    import_id: str, conn: Database, reason: str | None = None
) -> dict[str, Any]:
    row = await conn.fetchrow("SELECT status FROM imports WHERE id = $1", import_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such import")
    if row["status"] == "committed":
        raise HTTPException(
            status_code=409, detail="Undo a committed import from the Imports screen."
        )
    await reject_import(conn, import_id, reason)
    return await import_payload(conn, import_id)


@router.post("/imports/{import_id}/undo")
async def import_undo(import_id: str, conn: Database) -> dict[str, Any]:
    try:
        result = await undo_import(conn, import_id)
    except ConflictLedgerError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except InvalidLedgerError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    payload = await import_payload(conn, import_id)
    payload["undo"] = result
    return payload


@router.patch("/imports/{import_id}/lines/{line_id}")
async def resolve_import_line(
    import_id: str, line_id: str, body: ResolveLine, conn: Database
) -> dict[str, Any]:
    from arno.importers.reconcile import resolve_line

    try:
        await resolve_line(
            conn,
            import_id=import_id,
            line_id=line_id,
            resolution=body.resolution,
            movement_id=body.movement_id,
            credit_kind=body.credit_kind,
            category_id=body.category_id,
            source_account_id=body.source_account_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await import_payload(conn, import_id)


__all__ = ["MAX_UPLOAD_BYTES", "router"]
