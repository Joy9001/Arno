"""Sign-in: Tailscale identity, or an optional passphrase that sets a signed cookie."""

from __future__ import annotations

import asyncio
import hmac

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from arno.api.deps import COOKIE_NAME, identify, refuse_hostile, sign_session

router = APIRouter(prefix="/api/auth", tags=["auth"])

FAIL_DELAY_SECONDS = 1.0


class LoginBody(BaseModel):
    passphrase: str = Field(max_length=1000)


@router.get("/state")
async def auth_state(request: Request) -> dict[str, object]:
    session = identify(request) or {"method": None, "login": None}
    return {
        "authenticated": session["method"] is not None,
        "method": session["method"],
        "login": session["login"],
        "passphrase_enabled": bool(request.app.state.settings.passphrase),
    }


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
async def login(request: Request, body: LoginBody, response: Response) -> None:
    refuse_hostile(request)
    settings = request.app.state.settings
    expected = settings.passphrase
    if not expected or not hmac.compare_digest(body.passphrase.encode(), expected.encode()):
        await asyncio.sleep(FAIL_DELAY_SECONDS)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That passphrase is wrong.")
    response.set_cookie(
        COOKIE_NAME,
        sign_session(expected, settings.session_ttl_days),
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        path="/",
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> None:
    refuse_hostile(request)
    response.delete_cookie(COOKIE_NAME, path="/")
