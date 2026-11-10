"""Serve the static Vite build. One origin, no Node server in production."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from arno.api.problems import problem
from arno.config import Settings

router = APIRouter(tags=["web"])

ASSET_CACHE = "public, max-age=31536000, immutable"
INDEX_CACHE = "no-store"


def _index(settings: Settings) -> FileResponse | Any:
    index = settings.web_dir / "index.html"
    if not index.is_file():
        return problem(
            503,
            title="Web build missing",
            detail="The web build is not present. Run `pnpm --dir web build` or use the image.",
        )
    return FileResponse(index, media_type="text/html", headers={"Cache-Control": INDEX_CACHE})


@router.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
def api_not_found(path: str) -> Any:
    return problem(404, detail=f"No API route at /api/{path}.")


@router.get("/", include_in_schema=False)
def root(request: Request) -> Any:
    return _index(request.app.state.settings)


@router.get("/{path:path}", include_in_schema=False)
def spa(path: str, request: Request) -> Any:
    settings: Settings = request.app.state.settings
    if not path:
        return _index(settings)

    candidate = (settings.web_dir / path).resolve()
    root_dir = settings.web_dir.resolve()
    if root_dir in candidate.parents and candidate.is_file():
        cache = ASSET_CACHE if path.startswith("assets/") else INDEX_CACHE
        return FileResponse(candidate, headers={"Cache-Control": cache})
    return _index(settings)
