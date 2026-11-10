"""A quote is a validated snapshot of text selected from a stored message.

The browser sends `message.metadata.custom.quote = {text, messageId}`. The text is plain
rendered text, so it is never matched against Markdown and never treated as an instruction.
"""

from __future__ import annotations

from typing import Any

from arno.agent.redaction import redact_text
from arno.db.conn import Connection

MAX_QUOTE_CHARS = 4000
MAX_QUOTE_MESSAGE_ID_CHARS = 100
SOURCE_ROLES = frozenset({"user", "assistant"})


class QuoteError(ValueError):
    """A quote that cannot be used without guessing or crossing the thread."""


def _quote_from_metadata(metadata: Any) -> dict[str, str] | None:
    if not isinstance(metadata, dict):
        return None
    custom = metadata.get("custom")
    if not isinstance(custom, dict):
        return None
    raw = custom.get("quote")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise QuoteError("The quote is not valid. Select the text again.")
    text = raw.get("text")
    message_id = raw.get("messageId")
    if not isinstance(text, str) or not isinstance(message_id, str):
        raise QuoteError("The quote is not valid. Select the text again.")
    if not text.strip():
        raise QuoteError("The quote is empty. Select the text again.")
    if len(text) > MAX_QUOTE_CHARS:
        raise QuoteError(f"The quote is longer than {MAX_QUOTE_CHARS} characters.")
    if not message_id or len(message_id) > MAX_QUOTE_MESSAGE_ID_CHARS:
        raise QuoteError("The quoted message reference is not valid.")
    return {"text": text, "messageId": message_id}


def quote_from_messages(messages: list[dict[str, Any]]) -> dict[str, str] | None:
    """The quote on the newest user turn, if the request carries one."""
    for message in reversed(messages):
        if message.get("role") == "user":
            return _quote_from_metadata(message.get("metadata"))
    return None


def _source_has_text(content: Any) -> bool:
    if not isinstance(content, dict):
        return False
    return any(
        isinstance(part, dict)
        and part.get("type") == "text"
        and isinstance(part.get("text"), str)
        and part["text"].strip()
        for part in content.get("parts") or []
    )


async def resolve_quote(conn: Connection, thread_id: str, quote: dict[str, str]) -> dict[str, str]:
    """Bind a snapshot to a real text message in the same thread, then redact it."""
    message_id = quote["messageId"]
    row = await conn.fetchrow(
        """
        SELECT role, content FROM messages
        WHERE conversation_id = $1 AND (id = $2 OR client_id = $2)
        ORDER BY (id = $2) DESC
        LIMIT 1
        """,
        thread_id,
        message_id,
    )
    if row is None or str(row["role"]) not in SOURCE_ROLES or not _source_has_text(row["content"]):
        raise QuoteError("The quoted message is no longer available. Select the text again.")
    safe = redact_text(quote["text"]).strip()
    if not safe:
        raise QuoteError("The quote is empty. Select the text again.")
    if len(safe) > MAX_QUOTE_CHARS:
        raise QuoteError(f"The quote is longer than {MAX_QUOTE_CHARS} characters.")
    return {"text": safe, "messageId": message_id}


def quote_block(quote: dict[str, str]) -> str:
    """The markdown blockquote the model sees, matching the official client helper."""
    return "\n".join(f"> {line}" for line in quote["text"].splitlines()) + "\n\n"


def with_quote(run_input: Any, quote: dict[str, str]) -> Any:
    """Prepend the quote as a text part of the new user turn.

    `run_input` is the adapter's public request model, so the injected part is what the
    adapter loads into model messages. The browser's own message is never changed.
    """
    from pydantic_ai.ui.vercel_ai.request_types import TextUIPart

    messages = list(run_input.messages)
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role != "user":
            continue
        parts = [TextUIPart(text=quote_block(quote)), *message.parts]
        metadata = dict(message.metadata or {})
        custom = dict(metadata.get("custom") or {})
        custom["quote"] = dict(quote)
        metadata["custom"] = custom
        messages[index] = message.model_copy(update={"parts": parts, "metadata": metadata})
        return run_input.model_copy(update={"messages": messages})
    return run_input


__all__ = [
    "MAX_QUOTE_CHARS",
    "QuoteError",
    "quote_block",
    "quote_from_messages",
    "resolve_quote",
    "with_quote",
]
