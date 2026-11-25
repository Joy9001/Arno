"""Capture: voice notes.

Speech-to-text returns text that takes the normal chat path. Images are stored as attachments via
`/api/attachments` and attached directly to multimodal chat requests.
"""

from __future__ import annotations

from arno.capture.transcribe import (
    TranscriptionError,
    payee_names_prompt,
    transcribe_audio,
)

__all__ = [
    "TranscriptionError",
    "payee_names_prompt",
    "transcribe_audio",
]
