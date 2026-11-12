"""The `STT_*` role: one OpenAI-compatible transcription request, metered as a direct call.

The endpoint is the same for `speaches` on the `local-stt` Compose profile and a cloud API:
`POST {STT_BASE_URL}/audio/transcriptions`. The optional `prompt` carries the user's payee names,
because those are the words a Hinglish note is most likely to mis-hear. A cloud endpoint receives
that prompt too, which is why the caller can turn it off.

Speech is not an `Agent` run, so it uses the shared direct-call budget helper: the shared day is
checked before the request, the call is journaled and traced once, and its reported usage is
priced through the one pricing function. A paid endpoint whose model cannot be priced is refused
before the request, and a response with no usable usage is recorded unknown and stops.
"""

# TODO(L7): no live STT endpoint or Hinglish audio corpus was available. Tests prove the
# request shape with a loopback stub; real cloud-versus-local Hinglish WER and the payee-name
# prompt's effect remain unmeasured. See plan.md section 6.

from __future__ import annotations

import math
import os
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic_ai.messages import ModelResponse
from pydantic_ai.usage import RequestUsage
from pydantic_ai_harness.spend import SpendLimitExceeded

from arno.agent.metering import metered_call
from arno.agent.models import role_config
from arno.agent.pricing import (
    LOCAL_BILLING_MODES,
    REGISTRY_PRICE_REVISION,
    UNKNOWN_SOURCE,
    CostObservation,
    observe_cost,
    validate_pricing,
)
from arno.agent.spend import (
    SpendStoreError,
    ensure_shared_day_available,
    post_direct_call,
    spend_store_scope,
)
from arno.db.conn import Connection
from arno.tools.mark import clean_outside

DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_PROMPT_CHARS = 500
MAX_PAYEE_NAMES = 80
STT_ROLE = "stt"


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


def _preflight(config: Any) -> None:
    """Refuse a paid request whose model has no verified price before it is sent."""
    if config.billing_mode in LOCAL_BILLING_MODES:
        return
    try:
        validate_pricing(
            provider=config.provider,
            model=config.model,
            provider_url=config.base_url,
            billing_mode=config.billing_mode,
        )
    except ValueError as exc:
        raise TranscriptionError(
            "Speech-to-text pricing is not configured for this endpoint; "
            "set STT_BILLING_MODE=local for a local endpoint, or reported when the "
            "endpoint returns its own charge."
        ) from exc


def _amount(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except InvalidOperation, ValueError:
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def _usage(payload: Any) -> RequestUsage | None:
    """The provider's reported billable units, or `None` when it reported none.

    Whisper-style endpoints report `{"type": "duration", "seconds": n}`; token-billed
    transcription reports input/output tokens and an audio-token detail. Nothing is inferred from
    the file, which would be an invented duration.
    """
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    fields: dict[str, Any] = {}
    seconds = usage.get("seconds")
    if isinstance(seconds, (int, float, Decimal)) and not isinstance(seconds, bool):
        number = float(seconds)
        if math.isfinite(number) and number >= 0:
            fields["audio_seconds"] = number
    for name in ("input_tokens", "output_tokens"):
        value = usage.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            fields[name] = value
    details = usage.get("input_token_details")
    if isinstance(details, dict):
        audio = details.get("audio_tokens")
        if isinstance(audio, int) and not isinstance(audio, bool) and audio >= 0:
            fields["input_audio_tokens"] = audio
    audio_tokens = usage.get("input_audio_tokens")
    if isinstance(audio_tokens, int) and not isinstance(audio_tokens, bool) and audio_tokens >= 0:
        fields["input_audio_tokens"] = audio_tokens
    cost = _amount(usage.get("cost"))
    if cost is not None:
        fields["cost"] = cost
    return RequestUsage(**fields) if fields else None


def _unknown() -> CostObservation:
    return CostObservation(
        usd=None,
        source=UNKNOWN_SOURCE,
        complete=False,
        price_revision=REGISTRY_PRICE_REVISION,
    )


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
    _preflight(config)
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

    async with spend_store_scope() as store:
        try:
            await ensure_shared_day_available(store)
        except SpendLimitExceeded as exc:
            raise TranscriptionError(
                "Speech-to-text is paused at the shared daily spend ceiling."
            ) from exc
        except SpendStoreError as exc:
            raise TranscriptionError(
                "Speech-to-text is paused because the spend ledger is unavailable."
            ) from exc

        try:
            return await _call_endpoint(
                store,
                config=config,
                url=url,
                fields=fields,
                headers=headers,
                data=data,
                filename=filename,
                content_type=content_type,
                timeout=timeout,
            )
        except TranscriptionError:
            raise
        except SpendStoreError as exc:
            raise TranscriptionError(
                "Speech-to-text spent the request but the spend ledger could not be written."
            ) from exc
        except Exception as exc:
            # The journal intent is written before the request, so a failure here is a cost
            # record that did not reach Postgres. Stop safely; a retry is the user's choice.
            raise TranscriptionError(
                f"Speech-to-text could not be recorded ({type(exc).__name__})."
            ) from exc


async def _call_endpoint(
    store: Any,
    *,
    config: Any,
    url: str,
    fields: dict[str, str],
    headers: dict[str, str],
    data: bytes,
    filename: str,
    content_type: str,
    timeout: float,
) -> str:
    async with metered_call(
        config.provider,
        config.model,
        config.billing_mode,
        role=STT_ROLE,
        workload="capture",
    ) as attempt:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    url,
                    data=fields,
                    files={"file": (filename, data, content_type or "application/octet-stream")},
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise TranscriptionError(
                f"Speech-to-text request failed ({type(exc).__name__})."
            ) from exc

        if response.status_code >= 400:
            raise TranscriptionError(f"Speech-to-text returned HTTP {response.status_code}.")

        payload: Any = None
        try:
            payload = response.json(parse_float=Decimal)
        except ValueError:
            text = response.text
        else:
            text = str(payload.get("text") or "") if isinstance(payload, dict) else ""
        cleaned = " ".join(text.split())

        usage = _usage(payload)
        observation: CostObservation
        if usage is None and config.billing_mode not in LOCAL_BILLING_MODES:
            observation = _unknown()
        else:
            model_response = ModelResponse(
                parts=[],
                usage=usage or RequestUsage(),
                model_name=config.model,
                provider_name=config.provider,
                provider_url=config.base_url,
                provider_details=(
                    {"provider_usage": {"cost": usage.cost}}
                    if usage is not None and usage.cost is not None
                    else None
                ),
            )
            observation = observe_cost(
                model_response,
                provider=config.provider,
                billing_mode=config.billing_mode,
            )

        try:
            await post_direct_call(store, observation, token=attempt.attempt_id)
        finally:
            await attempt.finish(observation)

        if not cleaned:
            raise TranscriptionError("The recording produced no text.")
        if usage is None and config.billing_mode not in LOCAL_BILLING_MODES:
            raise TranscriptionError(
                "The endpoint reported no usage; the transcription cost is unknown."
            )
        if observation.usd is None or not observation.complete:
            raise TranscriptionError(
                "Speech-to-text usage could not be priced; the cost is unknown."
            )
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
