"""Run model-written Python in the isolated sandbox service, with uploaded files beside it."""

from __future__ import annotations

import base64
import re
from typing import Any

import httpx

from arno.db.conn import Connection

OUTPUT_CAP = 20_000
TIMEOUT_SECONDS = 30


class SandboxInputError(ValueError):
    """A message the model can act on, such as an attachment ID that does not exist."""


def _safe_name(filename: str | None, attachment_id: str, taken: set[str]) -> str:
    name = re.sub(r"[\\/]", "_", (filename or "").strip())
    if not name or name in {".", "..", "script.py"} or name in taken:
        name = f"{attachment_id}_{name}".rstrip("_")
    return name


def _failure(message: str) -> dict[str, Any]:
    return {"ok": False, "stdout": "", "stderr": message, "result_json": None}


async def run_python(
    conn: Connection,
    *,
    code: str,
    attachment_ids: list[str],
    sandbox_url: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Execute `code` in the sandbox; each attachment lands in its working directory by filename."""
    files: dict[str, str] = {}
    for attachment_id in attachment_ids:
        row = await conn.fetchrow(
            "SELECT filename, data FROM attachments WHERE id = $1 AND data IS NOT NULL",
            attachment_id,
        )
        if row is None:
            raise SandboxInputError(f"No attachment {attachment_id!r} is available")
        name = _safe_name(row["filename"], attachment_id, set(files))
        files[name] = base64.b64encode(bytes(row["data"])).decode("ascii")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS + 15, transport=transport) as client:
            response = await client.post(
                f"{sandbox_url}/exec",
                json={"code": code, "files": files, "timeout_seconds": TIMEOUT_SECONDS},
            )
    except httpx.HTTPError as exc:
        return _failure(f"The code sandbox is unavailable ({type(exc).__name__}).")
    if response.status_code != 200:
        return _failure(f"The code sandbox refused the request ({response.status_code}).")
    body = response.json()
    return {
        "ok": bool(body["ok"]),
        "stdout": str(body["stdout"])[:OUTPUT_CAP],
        "stderr": str(body["stderr"])[:OUTPUT_CAP],
        "result_json": body.get("result_json"),
    }


__all__ = ["SandboxInputError", "run_python"]
