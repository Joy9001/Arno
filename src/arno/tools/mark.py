"""Outside text: markers, the 200-character cap, and the conversation flag.

The flag on `conversations.outside_text_at` is the boundary; the `<outside>` markers are only a
hint for the model. Server code sets the flag from where the data came from, and a script can
strip the markers without clearing it.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from arno.db.conn import Connection, quote_ident

MAX_OUTSIDE_CHARS = 200
OPEN = "<outside>"
CLOSE = "</outside>"
_INVISIBLE = re.compile(
    "[\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u206f\ufeff\ufff9-\ufffb]"
)


def clean_outside(text: str) -> str:
    """Strip invisible characters, our own closing marker, and cap the length."""
    text = unicodedata.normalize("NFC", text)
    text = _INVISIBLE.sub("", text)
    text = text.replace(CLOSE, "")
    text = text.replace(OPEN, "")
    text = " ".join(text.split())
    if len(text) > MAX_OUTSIDE_CHARS:
        text = text[: MAX_OUTSIDE_CHARS - 1] + "\u2026"
    return text


def strip_marks(text: str) -> str:
    """Remove `<outside>` markers without truncating, for matching stored row names."""
    return text.replace(OPEN, "").replace(CLOSE, "").strip()


def mark(text: str | None) -> str | None:
    """Wrap outside text in markers. `None` stays unknown."""
    if text is None:
        return None
    return f"{OPEN}{clean_outside(str(text))}{CLOSE}"


def has_mark(value: object) -> bool:
    return isinstance(value, str) and OPEN in value


def contains_outside(value: object) -> bool:
    """Whether plain Python data carries an `<outside>` marker or an `outside_text=True` flag."""
    if has_mark(value):
        return True
    if isinstance(value, dict):
        return any(
            (key == "outside_text" and item is True)
            or contains_outside(key)
            or contains_outside(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(contains_outside(item) for item in value)
    return False


async def flag_outside_text(conn: Connection, conversation_id: str) -> None:
    await conn.execute(
        """
        UPDATE conversations
        SET outside_text_at = coalesce(outside_text_at, now()), updated_at = now()
        WHERE id = $1
        """,
        conversation_id,
    )


async def is_conversation_flagged(conn: Connection, conversation_id: str) -> bool:
    row = await conn.fetchrow(
        "SELECT outside_text_at FROM conversations WHERE id = $1",
        conversation_id,
    )
    return bool(row and row["outside_text_at"] is not None)


async def clear_mark(conn: Connection, table: str, row_id: str) -> None:
    """A name the user confirmed on a card loses its mark."""
    if table not in {"payees", "schedules"}:
        raise ValueError(f"{table} has no outside_text column")
    await conn.execute(
        f"UPDATE {quote_ident(table)} SET outside_text = false, updated_at = now() WHERE id = $1",
        row_id,
    )


async def clear_confirmed_marks(conn: Connection, names: Sequence[str]) -> None:
    """Clear `outside_text` on payees and schedules whose names the user confirmed on a card."""
    for name in names:
        clean = strip_marks(name)
        if not clean:
            continue
        for table, column in (("payees", "display_name"), ("schedules", "name")):
            rows = await conn.fetch(
                f"SELECT id FROM {quote_ident(table)} "
                f"WHERE lower({quote_ident(column)}) = lower($1)",  # noqa: S608
                clean,
            )
            for row in rows:
                await clear_mark(conn, table, row["id"])


__all__ = [
    "MAX_OUTSIDE_CHARS",
    "clean_outside",
    "clear_confirmed_marks",
    "clear_mark",
    "contains_outside",
    "flag_outside_text",
    "has_mark",
    "is_conversation_flagged",
    "mark",
    "strip_marks",
]
