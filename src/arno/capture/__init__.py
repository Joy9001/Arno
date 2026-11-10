"""Capture: voice notes and photo extractions.

Both are input preprocessing, not tools. Speech-to-text returns text that takes the normal chat
path. A photo extraction is an isolated, tool-less VISION call into typed fields, and the server
logs it at once and marks the thread's history.
"""

from __future__ import annotations

from arno.capture.photo import PaymentPhoto, extract_payment_photo
from arno.capture.transcribe import (
    TranscriptionError,
    payee_names_prompt,
    transcribe_audio,
)

__all__ = [
    "PaymentPhoto",
    "TranscriptionError",
    "extract_payment_photo",
    "payee_names_prompt",
    "transcribe_audio",
]
