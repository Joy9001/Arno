"""Serve the static Vite build. One origin, no Node server in production."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from arno.api.problems import problem
from arno.config import Settings

router = APIRouter(tags=["web"])

ASSET_CACHE = "public, max-age=31536000, immutable"
INDEX_CACHE = "no-store"
# A process probing this port for a model server asks for these, never for a page.
NON_APP_ROOTS = frozenset({"health", "v1"})


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
    # Neither a client-side route (no extension) nor a built file: a poller asking for a health
    # or model endpoint, or a browser asking for an asset that is not there, gets a plain 404
    # instead of an HTML page that says a server is up.
    if path.split("/", 1)[0] in NON_APP_ROOTS or PurePosixPath(path).suffix:
        return problem(404, detail=f"No web page at /{path}.")
    return _index(settings)
