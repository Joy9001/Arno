"""Sign in with ChatGPT, with credentials kept in the app's protected database.

The native provider owns refresh and single-flight rotation; this module only supplies the
application-owned credential source it asks for, plus the sign-in calls a CLI or API route can
wrap. No token is logged, returned to the browser, or stored outside the `model_credentials` table.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

import httpx2
from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.openai_chatgpt import (
    OpenAIChatGPTCredentials,
    OpenAIChatGPTOAuthFlow,
)

from arno.db.conn import connect

PROVIDER = "openai-chatgpt"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:1455/auth/callback"
_background: ContextVar[bool] = ContextVar("background_inference", default=False)


@contextmanager
def background_inference() -> Generator[None]:
    token = _background.set(True)
    try:
        yield
    finally:
        _background.reset(token)


class DatabaseChatGPTCredentialSource:
    """The native provider's `OpenAIChatGPTCredentialSource` over `model_credentials`."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn
        self._loaded_refresh: str | None = None

    async def load(self) -> OpenAIChatGPTCredentials:
        async with connect(self._dsn) as conn:
            row = await conn.fetchrow(
                """
                SELECT client_id, access_token, refresh_token, id_token, scope
                FROM model_credentials
                WHERE provider = $1
                """,
                PROVIDER,
            )
        if row is None:
            raise UserError(
                "No ChatGPT sign-in is stored. Run the ChatGPT sign-in flow before selecting the "
                "`openai-chatgpt` provider."
            )
        self._loaded_refresh = str(row["refresh_token"])
        return OpenAIChatGPTCredentials(
            client_id=str(row["client_id"]),
            access_token=str(row["access_token"]),
            refresh_token=str(row["refresh_token"]),
            id_token=None if row["id_token"] is None else str(row["id_token"]),
            scope=str(row["scope"]),
        )

    async def save(self, credentials: OpenAIChatGPTCredentials) -> None:
        async with connect(self._dsn) as conn:
            if self._loaded_refresh is not None:
                updated = await conn.fetchrow(
                    """
                    UPDATE model_credentials SET access_token = $1, refresh_token = $2,
                        id_token = $3, scope = $4, client_id = $7, updated_at = now()
                    WHERE provider = $5 AND refresh_token = $6
                    RETURNING provider
                    """,
                    credentials.access_token,
                    credentials.refresh_token,
                    credentials.id_token,
                    credentials.scope,
                    PROVIDER,
                    self._loaded_refresh,
                    credentials.client_id,
                )
                if updated is None:
                    raise UserError("ChatGPT sign-in changed or was disconnected during refresh.")
                self._loaded_refresh = credentials.refresh_token
                return
            await conn.execute(
                """
                INSERT INTO model_credentials (
                    provider, client_id, access_token, refresh_token, id_token, scope, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, now())
                ON CONFLICT (provider) DO UPDATE SET
                    client_id = EXCLUDED.client_id,
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    id_token = EXCLUDED.id_token,
                    scope = EXCLUDED.scope,
                    updated_at = now()
                """,
                PROVIDER,
                credentials.client_id,
                credentials.access_token,
                credentials.refresh_token,
                credentials.id_token,
                credentials.scope,
            )
        self._loaded_refresh = credentials.refresh_token

    async def authorize_request(self, request: httpx2.Request) -> None:
        """Check disconnect and background consent before using cached credentials."""
        if request.url.host != "api.openai.com":
            return
        async with connect(self._dsn) as conn:
            row = await conn.fetchrow(
                "SELECT access_token, allow_background FROM model_credentials WHERE provider = $1",
                PROVIDER,
            )
        if row is None:
            raise UserError("ChatGPT is disconnected. Sign in again before making a request.")
        if _background.get() and not row["allow_background"]:
            raise UserError(
                "ChatGPT background use needs consent: sign in with --allow-background."
            )
        if request.headers.get("authorization") != f"Bearer {row['access_token']}":
            raise UserError(
                "ChatGPT sign-in changed. Restart the agent to load the new credentials."
            )

    async def revoke(self) -> bool:
        """Delete the stored sign-in. Returns whether one was present."""
        async with connect(self._dsn) as conn:
            row = await conn.fetchrow(
                "DELETE FROM model_credentials WHERE provider = $1 RETURNING provider", PROVIDER
            )
        return row is not None


def chatgpt_sign_in(
    *,
    app_name: str = "Arno",
    host_id: str | None = None,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
    client_id: str | None = None,
    id_token_hint: str | None = None,
) -> tuple[str, OpenAIChatGPTOAuthFlow]:
    """Return the authorization URL and the flow that must receive its callback.

    Construction does no I/O and sends no request. The caller opens the URL, then passes the
    returned flow and the callback's code to `complete_chatgpt_sign_in`.
    """
    flow = OpenAIChatGPTOAuthFlow(
        app_name=app_name,
        host_id=host_id,
        client_id=client_id,
        id_token_hint=id_token_hint,
        redirect_uri=redirect_uri,
    )
    return flow.authorization_url(), flow


async def complete_chatgpt_sign_in(
    flow: OpenAIChatGPTOAuthFlow,
    code: str,
    *,
    client_id: str | None = None,
    source: DatabaseChatGPTCredentialSource | None = None,
) -> OpenAIChatGPTCredentials:
    """Exchange the callback code and persist the credentials for later refreshes."""
    credentials = await flow.exchange_code(code, client_id=client_id)
    await (source or DatabaseChatGPTCredentialSource()).save(credentials)
    return credentials


async def revoke_chatgpt_sign_in(
    source: DatabaseChatGPTCredentialSource | None = None,
) -> bool:
    """Revoke the stored sign-in by deleting it; a refresh already in flight cannot use it again."""
    return await (source or DatabaseChatGPTCredentialSource()).revoke()


__all__ = [
    "DEFAULT_REDIRECT_URI",
    "DatabaseChatGPTCredentialSource",
    "chatgpt_sign_in",
    "complete_chatgpt_sign_in",
    "revoke_chatgpt_sign_in",
]
