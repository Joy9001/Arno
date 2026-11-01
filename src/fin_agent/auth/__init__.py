"""Authentication: passkeys, opaque sessions, and first-run setup."""

from fin_agent.auth.sessions import (
    COOKIE_NAME,
    create_session,
    revoke_all_sessions,
    revoke_session,
    session_from_token,
)
from fin_agent.auth.setup import (
    SETUP_TOKEN_TTL,
    consume_setup_tokens,
    issue_setup_token,
    setup_required,
    verify_setup_token,
)

__all__ = [
    "COOKIE_NAME",
    "SETUP_TOKEN_TTL",
    "consume_setup_tokens",
    "create_session",
    "issue_setup_token",
    "revoke_all_sessions",
    "revoke_session",
    "session_from_token",
    "setup_required",
    "verify_setup_token",
]
