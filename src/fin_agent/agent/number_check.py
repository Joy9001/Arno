"""The post-reply number check, ported from the October 31 probe.

Every money amount and percentage in a reply is compared with what the model was given: tool
returns and arguments, the snapshot, and the user's messages. It understands rounding, lakh,
crore, percentages, and values shown without their sign. An untraced number gets a note, and the
trace counts it. The check is deliberately permissive about labels: a real number used wrongly is
an eval's job, not this check's.
"""

from __future__ import annotations

import re
from typing import Any

SCALE = {
    "k": 1e3,
    "lakh": 1e5,
    "lakhs": 1e5,
    "lac": 1e5,
    "lacs": 1e5,
    "l": 1e5,
    "crore": 1e7,
    "crores": 1e7,
    "cr": 1e7,
}
NUM = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
MONEY = re.compile(
    rf"(?:₹|\bRs\.?\s?|\bINR\s?)\s?({NUM})\s?(k|lakhs?|lacs?|crores?|cr|l)?\b"
    rf"|\b({NUM})\s?(lakhs?|lacs?|crores?|cr)\b"
    rf"|\b({NUM})\s?(?:rupees?|rs\b|rupaye)",
    re.I,
)
PERCENT = re.compile(rf"\b({NUM})\s?(?:%|percent\b|per cent\b)", re.I)
BARE = re.compile(rf"(?<![\w.,/-])({NUM})(?![\w/-]|[.,]\d)")
MONTHS = (
    "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|"
    "june|july|august|september|october|november|december"
)
DATE = re.compile(
    rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b"
    rf"|\b\d{{4}}-\d{{2}}\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})\b(?:\s+\d{{4}})?"
    rf"|\b(?:{MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+\d{{4}})?"
    rf"|\b(?:{MONTHS})\s+\d{{4}}\b"
    rf"|\b\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?\b",
    re.I,
)
LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.M)
TIME = re.compile(r"\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?(?:am|pm)\b", re.I)


def _value(text: str) -> float:
    return float(text.replace(",", ""))


def _unit(text: str, scale: float = 1.0) -> float:
    """How far apart two values can be and still read the same at the shown precision."""
    decimals = len(text.split(".")[1]) if "." in text else 0
    if decimals == 0 and scale == 1.0 and "," not in text:
        trailing = len(text) - len(text.rstrip("0"))
        if trailing >= 2:
            return 10.0**trailing
    return scale * 10.0 ** (-decimals)


def extract(text: str) -> list[tuple[str, float, float, str]]:
    """Return `(kind, value, unit, shown)` for each number, leaving out dates, times, and lists."""
    # Models often use a narrow no-break space before a percent sign.
    text = text.replace("\u202f", " ").replace("\u00a0", " ").replace("\u2009", " ")
    blanked = DATE.sub(lambda m: " " * len(m.group()), text)
    blanked = TIME.sub(lambda m: " " * len(m.group()), blanked)
    blanked = LIST_MARKER.sub(lambda m: " " * len(m.group()), blanked)
    found: list[tuple[str, float, float, str]] = []

    def take(pattern: re.Pattern[str], handler: Any) -> list[tuple[str, float, float, str]]:
        nonlocal blanked
        out = [handler(match) for match in pattern.finditer(blanked)]
        blanked = pattern.sub(lambda m: " " * len(m.group()), blanked)
        return out

    def money(match: re.Match[str]) -> tuple[str, float, float, str]:
        raw = match.group(1) or match.group(3) or match.group(5)
        scale_word = (match.group(2) or match.group(4) or "").lower()
        scale = SCALE.get(scale_word, 1.0)
        return ("money", _value(raw) * scale, _unit(raw, scale), match.group().strip())

    found += take(MONEY, money)
    found += take(
        PERCENT,
        lambda m: ("percent", _value(m.group(1)), _unit(m.group(1)), m.group().strip()),
    )
    found += take(BARE, lambda m: ("bare", _value(m.group(1)), _unit(m.group(1)), m.group(1)))
    return found


def sources(returns: Any, user_texts: tuple[str, ...] = ()) -> set[float]:
    """Every value the model was given: tool returns, tool arguments, and the user's messages."""
    values: set[float] = set()

    def walk(obj: Any, key: str = "") -> None:
        if isinstance(obj, bool):
            if "complete" in key.lower():
                values.add(0.0 if not obj else 100.0)
            return
        if isinstance(obj, (int, float)):
            values.add(float(obj))
            if "paise" in key:
                values.add(obj / 100)
        elif isinstance(obj, str):
            for _kind, value, _unit_value, _shown in extract(obj):
                values.add(value)
        elif isinstance(obj, dict):
            if isinstance(obj.get("columns"), list) and isinstance(obj.get("rows"), list):
                for row in obj["rows"]:
                    for column, cell in zip(obj["columns"], row, strict=False):
                        walk(cell, str(column))
            for key_name, value in obj.items():
                if key_name != "rows":
                    walk(value, str(key_name))
        elif isinstance(obj, (list, tuple)):
            for value in obj:
                walk(value, key)

    walk(returns)
    for text in user_texts:
        walk(text)
    return values


def traced(kind: str, value: float, unit: float, known: set[float]) -> bool:
    for candidate in known:
        if abs(candidate - value) <= unit / 2 + 1e-9:
            return True
        if kind == "percent" and abs(candidate * 100 - value) <= unit / 2 + 1e-9:
            return True
        if candidate < 0 and abs(-candidate - value) <= unit / 2 + 1e-9:
            return True
    return False


def check(
    reply: str,
    returns: Any,
    user_texts: tuple[str, ...] = (),
    kinds: tuple[str, ...] = ("money", "percent", "bare"),
) -> list[str]:
    known = sources(returns, user_texts)
    return [
        shown
        for kind, value, unit, shown in extract(reply)
        if kind in kinds and not traced(kind, value, unit, known)
    ]


def _text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        pieces: list[str] = []
        for item in value:
            content = getattr(item, "content", None)
            if isinstance(content, str):
                pieces.append(content)
        return "\n".join(pieces)
    return ""


def check_messages(reply: str, messages: list[Any]) -> list[str]:
    """Run the check over a turn's model messages: returns, arguments, and user text."""
    import json

    from pydantic_ai.messages import (
        ModelRequest,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    returns: list[Any] = []
    texts: list[str] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart):
                    returns.append(part.content)
                elif isinstance(part, UserPromptPart):
                    text = _text_content(part.content)
                    if not text:
                        continue
                    stripped = text.strip()
                    if stripped.startswith(("{", "[")):
                        try:
                            returns.append(json.loads(stripped))
                            continue
                        except json.JSONDecodeError:
                            pass
                    texts.append(text)
                elif isinstance(part, ToolCallPart):
                    returns.append(part.args_as_dict())
    return check(reply, returns, tuple(texts))


__all__ = ["check", "check_messages", "extract", "sources", "traced"]
