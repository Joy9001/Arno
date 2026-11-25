"""Multimodal image attachment capability.

User messages store lightweight `[Attached image: att_... (filename)]` markers in persistent
`model_history` so Postgres never bloats with base64 blobs. Right before each model call,
`ImageAttachments.before_model_request` hydrates attached images in uncompacted history into
request-only `BinaryContent` parts, after `InputGuardrail` and `TieredCompaction` have run and
without mutating persistent history.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, override

from pydantic_ai import BinaryContent, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai_harness.compaction import is_pinned

from arno.agent.deps import AgentDeps
from arno.db.conn import connect

IMAGE_MARKER_RE = re.compile(r"\[Attached image:\s*(att_[A-Za-z0-9_-]+)(?:\s+\(([^)]*)\))?\]")


def _safe_filename(name: str) -> str:
    cleaned = " ".join(str(name or "").split())
    for ch in ("(", ")", "[", "]"):
        cleaned = cleaned.replace(ch, "_")
    return cleaned[:120] or "image"


def format_image_markers(photos: Sequence[Mapping[str, str]]) -> str:
    """Format compact, text-safe markers for attached images on a user turn."""
    lines = [
        f"[Attached image: {photo['attachment_id']} "
        f"({_safe_filename(photo.get('filename', 'image'))})]"
        for photo in photos
    ]
    return "\n".join(lines)


def with_photos(
    run_input: Any,
    photos: Sequence[Mapping[str, str]],
    *,
    fallback_text: str | None = None,
) -> Any:
    """Attach image markers onto the last user message as a single `TextUIPart`.

    Combining existing `TextUIPart` texts with the image markers into one `TextUIPart` guarantees
    `VercelAIAdapter.load_messages` produces a `str` `UserPromptPart.content` so `InputGuardrail`
    can redact user-typed secrets without raising on a list prompt.
    """
    from pydantic_ai.ui.vercel_ai.request_types import TextUIPart

    if not photos:
        return run_input
    markers = format_image_markers(photos)
    messages = list(run_input.messages)
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role != "user":
            continue
        text_chunks: list[str] = []
        other_parts: list[Any] = []
        insert_at: int | None = None
        for part in message.parts:
            if isinstance(part, TextUIPart):
                if insert_at is None:
                    insert_at = len(other_parts)
                if part.text:
                    text_chunks.append(part.text)
            else:
                other_parts.append(part)
        if not text_chunks and fallback_text and fallback_text.strip():
            text_chunks.append(fallback_text.strip())
        combined = "\n\n".join([*text_chunks, markers]) if text_chunks else markers
        merged_part = TextUIPart(text=combined)
        pos = 0 if insert_at is None else insert_at
        new_parts = [*other_parts[:pos], merged_part, *other_parts[pos:]]
        messages[index] = message.model_copy(update={"parts": new_parts})
        return run_input.model_copy(update={"messages": messages})
    return run_input


def extract_image_refs(
    messages: Sequence[ModelMessage],
) -> list[tuple[int, int, list[str]]]:
    """Return `(msg_idx, part_idx, [attachment_id, ...])` for image markers."""
    found: list[tuple[int, int, list[str]]] = []
    for msg_idx, message in enumerate(messages):
        if not isinstance(message, ModelRequest):
            continue
        for part_idx, part in enumerate(message.parts):
            if not isinstance(part, UserPromptPart) or is_pinned(part):
                continue
            texts = (
                [part.content]
                if isinstance(part.content, str)
                else [item for item in part.content if isinstance(item, str)]
            )
            ids: list[str] = []
            seen: set[str] = set()
            for text in texts:
                for match in IMAGE_MARKER_RE.finditer(text):
                    attachment_id = match.group(1)
                    if attachment_id not in seen:
                        seen.add(attachment_id)
                        ids.append(attachment_id)
            if ids:
                found.append((msg_idx, part_idx, ids))
    return found


class ImageAttachments(AbstractCapability[AgentDeps]):
    """Hydrate attached image markers into raw `BinaryContent` on outgoing model requests."""

    def __init__(self) -> None:
        self._cache: dict[str, BinaryContent | None] = {}

    @override
    async def for_run(self, ctx: RunContext[AgentDeps]) -> ImageAttachments:
        del ctx
        return ImageAttachments()

    @override
    async def before_model_request(
        self, ctx: RunContext[AgentDeps], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        all_refs = extract_image_refs(request_context.messages)
        if not all_refs:
            return request_context

        missing = list(
            dict.fromkeys(
                att_id for _m, _p, ids in all_refs for att_id in ids if att_id not in self._cache
            )
        )
        if missing:
            async with connect() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, content_type, data
                    FROM attachments
                    WHERE id = ANY($1::text[]) AND kind = 'photo'
                      AND data IS NOT NULL AND purged_at IS NULL
                    """,
                    missing,
                )
            for att_id in missing:
                self._cache[att_id] = None
            for row in rows:
                if row["data"] is not None:
                    self._cache[str(row["id"])] = BinaryContent(
                        data=bytes(row["data"]),
                        media_type=str(row["content_type"] or "image/png"),
                    )

        new_messages = list(request_context.messages)
        hydrated_any = False
        for msg_idx, part_idx, ids in all_refs:
            blobs = [blob for att_id in ids if (blob := self._cache.get(att_id)) is not None]
            if not blobs:
                continue
            message = new_messages[msg_idx]
            if not isinstance(message, ModelRequest):
                continue
            part = message.parts[part_idx]
            if not isinstance(part, UserPromptPart):
                continue
            existing = [part.content] if isinstance(part.content, str) else list(part.content)
            text_items = [item for item in existing if isinstance(item, str)]
            trailing_items = [
                item for item in existing if not isinstance(item, (str, BinaryContent))
            ]
            new_parts = list(message.parts)
            new_parts[part_idx] = replace(part, content=[*text_items, *blobs, *trailing_items])
            new_messages[msg_idx] = replace(message, parts=new_parts)
            hydrated_any = True

        if not hydrated_any:
            return request_context
        if ctx.deps is not None:
            ctx.deps.saw_images = True
        return replace(request_context, messages=new_messages)


__all__ = [
    "IMAGE_MARKER_RE",
    "ImageAttachments",
    "extract_image_refs",
    "format_image_markers",
    "with_photos",
]
