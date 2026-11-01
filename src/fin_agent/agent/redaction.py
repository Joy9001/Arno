"""Redaction shared by the input guardrail and storage.

The guardrail protects what the model sees; this also protects what the server stores, because the
browser shows what was typed until a reload while the server must hold the redacted text.
"""

from __future__ import annotations

from pydantic_ai_harness.guardrails.detectors import personal_data, secret_data

_DETECTORS = [
    secret_data(),
    personal_data(
        only=["credit_card"],
        extra={
            "pan": r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
            "aadhaar": r"\b[2-9]\d{3}[ -]\d{4}[ -]\d{4}\b",
        },
    ),
]


def redact_text(text: str) -> str:
    for detector in _DETECTORS:
        result = detector(text)
        if getattr(result, "action", None) == "replace":
            text = str(result.replacement)
    return text


__all__ = ["redact_text"]
