"""The `STT_*` role: one OpenAI-compatible transcription request.

The endpoint is the same for a self-hosted whisper server and a cloud API:
`POST {STT_BASE_URL}/audio/transcriptions`. The optional `prompt` carries the user's payee names,
because those are the words a Hinglish note is most likely to mis-hear. A cloud endpoint receives
that prompt too, which is why the caller can turn it off.

Speech is not an `Agent` run, so it checks the shared daily cap before the request and logs the
usage the endpoint reported (zero tokens and no cost when it reported none) after it.
"""

# TODO(L7): measure cloud-versus-local Hinglish WER and payee-name prompt effects on real audio.
# Loopback tests prove the request shape; synthetic local recordings do not establish either.
# See plan.md section 6.

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation

import httpx
from pydantic_ai.usage import RequestUsage

from arno.agent.models import role_config
from arno.agent.usage import DailyBudgetExceeded, check_daily_budget, record_usage
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
    conn: Connection,
    data: bytes,
    *,
    content_type: str,
    filename: str = "voice.webm",
    prompt: str | None = None,
) -> str:
    """Return the transcript, or raise `TranscriptionError` with a safe message."""
    if not configured():
        raise TranscriptionError("Speech-to-text is not configured.")
    config = role_config("STT")
    try:
        await check_daily_budget(conn)
    except DailyBudgetExceeded as exc:
        raise TranscriptionError("Speech-to-text is paused at the daily spend limit.") from exc

    fields = {"model": config.model, "response_format": "json"}
    if prompt:
        fields["prompt"] = prompt
    # Whisper guesses the language from the first seconds, and a short clip often guesses wrong
    # (a "Hello" came back as Japanese). `STT_LANGUAGE=en` pins it; unset keeps auto-detect.
    if language := os.environ.get("STT_LANGUAGE", "").strip():
        fields["language"] = language
    headers = (
        {"Authorization": f"Bearer {config.api_key}"} if config.api_key != "not-needed" else {}
    )
    try:
        timeout = float(os.environ.get("STT_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{config.base_url.rstrip('/')}/audio/transcriptions",
                data=fields,
                files={"file": (filename, data, content_type or "application/octet-stream")},
                headers=headers,
            )
    except httpx.HTTPError as exc:
        raise TranscriptionError(f"Speech-to-text request failed ({type(exc).__name__}).") from exc
    if response.status_code >= 400:
        raise TranscriptionError(f"Speech-to-text returned HTTP {response.status_code}.")

    try:
        payload = response.json(parse_float=Decimal)
    except ValueError:
        payload = None
    body = payload if isinstance(payload, dict) else {}
    text = str(body.get("text") or "") if body else response.text
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    try:
        cost = Decimal(str(usage["cost"])) if "cost" in usage else None
        tokens = [max(int(usage.get(key) or 0), 0) for key in ("input_tokens", "output_tokens")]
    except InvalidOperation, ValueError, TypeError, OverflowError:
        cost, tokens = None, [0, 0]
    await record_usage(
        conn,
        "stt",
        config.model,
        RequestUsage(input_tokens=tokens[0], output_tokens=tokens[1]),
        cost,
    )
    transcript = " ".join(text.split())
    if not transcript:
        raise TranscriptionError("The recording produced no text.")
    return transcript


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_PAYEE_NAMES",
    "MAX_PROMPT_CHARS",
    "TranscriptionError",
    "configured",
    "payee_names_prompt",
    "transcribe_audio",
]
