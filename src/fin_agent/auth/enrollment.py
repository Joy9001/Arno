"""Enrollment links: one URL that registers exactly one passkey.

The token lives in the fragment, so the browser never sends it in a request line or a Referer.
"""

from __future__ import annotations

import io
from urllib.parse import quote

import segno


def enrollment_link(origin: str, token: str) -> str:
    """`{origin}/setup#t={token}`. The origin always comes from settings, never a hardcoded host."""
    return f"{origin.rstrip('/')}/setup#t={quote(token, safe='')}"


def terminal_qr(text: str) -> str:
    """A compact Unicode QR code for a terminal, or an empty string if it can't be drawn."""
    buffer = io.StringIO()
    segno.make(text, error="m").terminal(out=buffer, compact=True)
    return buffer.getvalue()


def enrollment_message(origin: str, token: str, *, title: str) -> str:
    link = enrollment_link(origin, token)
    qr = terminal_qr(link)
    return f"{title}\n{link}\nSetup token (paste fallback): {token}\n{qr}"
