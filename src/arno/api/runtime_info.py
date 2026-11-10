"""Read-only runtime facts for the settings screen.

Model endpoints are reduced to hostnames, never the configured URL: a base URL can carry a
password in its userinfo, a path, or a query string with a key. Phoenix is only exposed when an
operator explicitly names a browser URL; it is never derived from the collector endpoint.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROLES = ("CHAT", "CHAT_FALLBACK", "VISION", "STT")

# Model labels are optional. Anything with whitespace, punctuation not used in model IDs, or a
# credential-looking shape is omitted rather than echoed into the browser.
_SAFE_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,119}")


def _hostname(base_url: str) -> str | None:
    try:
        parts = urlsplit(base_url)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    return parts.hostname


def _safe_model(model: str) -> str | None:
    value = model.strip()
    return value if _SAFE_MODEL.fullmatch(value) else None


def _role(prefix: str) -> dict[str, Any]:
    base_url = os.environ.get(f"{prefix}_BASE_URL", "").strip()
    model = os.environ.get(f"{prefix}_MODEL", "").strip()
    # Both are needed for a usable role; a bare base URL or an unconfigured loopback port is not
    # "configured" to the reader.
    configured = bool(base_url and model)
    return {
        "role": prefix,
        "configured": configured,
        "host": _hostname(base_url) if configured else None,
        "model": _safe_model(model) if configured else None,
    }


def _phoenix_ui_url() -> str | None:
    raw = os.environ.get("ARNO_PHOENIX_UI_URL", "").strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname if ":" not in parts.hostname else f"[{parts.hostname}]"
    netloc = f"{host}:{port}" if port else host
    # Keep the operator's path, but never userinfo, a query, or a fragment.
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, "", ""))


def payload() -> dict[str, Any]:
    return {
        "roles": [_role(prefix) for prefix in ROLES],
        "phoenix_ui_url": _phoenix_ui_url(),
    }
