"""The `STT_*` role: one OpenAI-compatible transcription request.

The endpoint is the same for `speaches` on the `local-stt` Compose profile and a cloud API:
`POST {STT_BASE_URL}/audio/transcriptions`. The optional `prompt` carries the user's payee names,
because those are the words a Hinglish note is most likely to mis-hear. A cloud endpoint receives
that prompt too, which is why the caller can turn it off.
"""

# TODO(L7): no live STT endpoint or Hinglish audio corpus was available. Tests prove the
# request shape with a loopback stub; real cloud-versus-local Hinglish WER and the payee-name
# prompt's effect remain unmeasured. See plan.md section 6.

from __future__ import annotations

import os

import httpx

from arno.agent.models import role_config
from arno.db.conn import Connection
from arno.tools.mark import clean_outside

DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_PROMPT_CHARS = 500
MAX_PAYEE_NAMES = 80


class TranscriptionError(RuntimeError):
    """A plain message the retry card can show. Never includes a URL, key, or raw body."""


def configured() -> bool:
    """True when both the endpoint and the model are set.

    A role is built with a closed loopback port when unconfigured, so checking the environment is
    clearer than trusting the URL.
    """
    return bool(
        os.environ.get("STT_BASE_URL", "").strip() and os.environ.get("STT_MODEL", "").strip()
    )


async def payee_names_prompt(conn: Connection, *, limit: int = MAX_PAYEE_NAMES) -> str | None:
    """Payee names as a recognition hint. Names only; no amounts, balances, or notes."""
    rows = await conn.fetch(
        """
        SELECT DISTINCT display_name
        FROM payees
        WHERE active AND display_name IS NOT NULL AND display_name <> ''
        ORDER BY display_name
        LIMIT $1
        """,
        limit,
    )
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        name = " ".join(str(row["display_name"]).split())
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        names.append(clean_outside(name))
    if not names:
        return None
    prompt = "Payees: " + ", ".join(names)
    return prompt[:MAX_PROMPT_CHARS]


async def transcribe_audio(
    data: bytes,
    *,
    content_type: str,
    filename: str = "voice.webm",
    prompt: str | None = None,
    timeout: float | None = None,
) -> str:
    """Return the transcript, or raise `TranscriptionError` with a safe message."""
    return await _transcribe_audio(
        data,
        content_type=content_type,
        filename=filename,
        prompt=prompt,
        timeout=timeout,
    )


async def _transcribe_audio(
    data: bytes,
    *,
    content_type: str,
    filename: str = "voice.webm",
    prompt: str | None = None,
    timeout: float | None = None,
) -> str:
    if not configured():
        raise TranscriptionError("Speech-to-text is not configured.")

    config = role_config("STT")
    url = f"{config.base_url.rstrip('/')}/audio/transcriptions"
    fields = {"model": config.model, "response_format": "json"}
    if prompt:
        fields["prompt"] = prompt
    # Whisper guesses the language from the first seconds, and a short clip often guesses wrong
    # (a "Hello" came back as Japanese). `STT_LANGUAGE=en` pins it; unset keeps auto-detect.
    language = os.environ.get("STT_LANGUAGE", "").strip()
    if language:
        fields["language"] = language
    headers = {}
    if config.api_key and config.api_key != "not-needed":
        headers["Authorization"] = f"Bearer {config.api_key}"

    if timeout is None:
        try:
            timeout = float(os.environ.get("STT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
        except ValueError:
            timeout = DEFAULT_TIMEOUT_SECONDS

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                data=fields,
                files={"file": (filename, data, content_type or "application/octet-stream")},
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise TranscriptionError(f"Speech-to-text request failed ({type(exc).__name__}).") from exc

    if response.status_code >= 400:
        raise TranscriptionError(f"Speech-to-text returned HTTP {response.status_code}.")

    try:
        payload = response.json()
    except ValueError:
        text = response.text
    else:
        text = str(payload.get("text") or "") if isinstance(payload, dict) else ""
    cleaned = " ".join(text.split())
    if not cleaned:
        raise TranscriptionError("The recording produced no text.")
    return cleaned


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_PAYEE_NAMES",
    "MAX_PROMPT_CHARS",
    "TranscriptionError",
    "configured",
    "payee_names_prompt",
    "transcribe_audio",
]
