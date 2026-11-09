"""Redaction shared by the input guardrail and storage.

The guardrail protects what the model sees; this also protects what the server stores, because the
browser shows what was typed until a reload while the server must hold the redacted text.

A card, PAN, Aadhaar, or API key has a shape. An OTP, PIN, or password does not: six digits are also
an amount, a date, or a count. So the last rule anchors on the word that names the secret and never
on the value alone: `pin 4321` is a PIN, `4321 rupees` and `pin code 560001` are not.
"""

from __future__ import annotations

import re

from pydantic_ai_harness.guardrails import GuardrailResult
from pydantic_ai_harness.guardrails.detectors import TextDetector, personal_data, secret_data

_PLACEHOLDER = "[redacted:{kind}]"

# The word that names the secret, and the kind to report. Longest forms first: "net banking
# password" must win over "password", and "upi pin" over "pin".
_NAMED = (
    r"(?:(?P<password>net\s*banking\s+password|netbanking\s+password|login\s+password"
    r"|one[ -]time\s+password|verification\s+code|passcode|password|passwd|pwd|पासवर्ड)"
    r"|(?P<otp>otp|ओटीपी)"
    # "PIN number 4321" is a card PIN; a postal PIN code has six digits, so only four count.
    r"|(?P<pin>upi\s+pin|m?pin(?:\s+(?:number|no\.?)(?=\s*\d{4}(?!\d)))?|पिन)"
    r"|(?P<cvv>cvv|cvc))"
)

# A value is three to eight digits (a CVV, a PIN, or an OTP), optionally written with spaces as a
# phone shows it ("4 8 2 9 1 3"). It is not a longer run (a UTR or card already has its own rule), a
# date, or an amount followed by a currency word.
_DIGITS = (
    r"\d(?: ?\d){2,7}(?![-\d])"
    r"(?!\s*(?:₹|rs\b|inr\b|rupees?\b|rupaye\b|paise\b|paisa\b|lakh\b|crore\b))"
)

# A password has no length or shape, so it only counts after an explicit is/hai/colon/equals.
_TOKEN = r"[A-Za-z0-9@#$%^&*!_+=.~/-]{3,64}"
_SEPARATOR = r"(?:hain\b|hai\b|is\b|was\b|है|:|=|-)"

_TYPED_SECRET = re.compile(
    rf"(?<!\w){_NAMED}(?!\w)(?!\s*(?:code|postal)\b)"
    rf"(?:\s*{_SEPARATOR}?\s*(?P<digits>{_DIGITS})"
    rf"|\s*{_SEPARATOR}\s*(?P<token>{_TOKEN}))",
    re.IGNORECASE,
)


def _kind(match: re.Match[str]) -> str:
    for kind in ("otp", "pin", "cvv"):
        if match.group(kind):
            return kind
    return "password"


def _looks_like_a_password(token: str) -> bool:
    """A real one carries a digit or a symbol; "my password is strong" is prose about it."""
    return any(not character.isalnum() for character in token) or any(
        character.isdigit() for character in token
    )


def _replace_typed_secret(match: re.Match[str]) -> str:
    token = match.group("token")
    if token is not None and not _looks_like_a_password(token):
        return match.group()
    return _PLACEHOLDER.replace("{kind}", _kind(match))


def keyword_secret_data(text: object) -> GuardrailResult:
    """Redact a secret written next to the word that names it.

    The words alone are not a secret ("I forgot my password"), and neither is an amount, a date,
    or an Indian postal "PIN code", so those pass through unchanged.
    """
    if not isinstance(text, str):
        return GuardrailResult.allow()
    cleaned = _TYPED_SECRET.sub(_replace_typed_secret, text)
    return GuardrailResult.replace(cleaned) if cleaned != text else GuardrailResult.allow()


_DETECTORS: list[TextDetector] = [
    secret_data(),
    personal_data(
        only=["credit_card"],
        extra={
            "pan": r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
            "aadhaar": r"\b[2-9]\d{3}[ -]\d{4}[ -]\d{4}\b",
        },
    ),
    keyword_secret_data,
]


def redaction_detectors() -> list[TextDetector]:
    """The whole policy in the order it applies, for storage and for the input guardrail."""
    return list(_DETECTORS)


def redact_text(text: str) -> str:
    for detector in _DETECTORS:
        result = detector(text)
        if result.action == "replace":
            text = str(result.replacement)
    return text


__all__ = ["keyword_secret_data", "redact_text", "redaction_detectors"]
