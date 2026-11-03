"""RFC 9457 problem responses, the convention every API endpoint uses."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

PROBLEM_MEDIA_TYPE = "application/problem+json"

TITLES = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    413: "Payload too large",
    422: "Unprocessable content",
    503: "Service unavailable",
}


def problem(
    status: int,
    *,
    title: str | None = None,
    detail: str | None = None,
    type_: str = "about:blank",
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": type_,
        "title": title or TITLES.get(status, "Error"),
        "status": status,
    }
    if detail:
        body["detail"] = detail
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_MEDIA_TYPE, headers=headers)
