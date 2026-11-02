"""The post-reply number check, ported from the October 31 probe.

Every money amount and percentage in a reply is compared with what the model was given: tool
returns and arguments, the snapshot, and the user's messages. It understands rounding, lakh,
crore, percentages, and values shown without their sign. An untraced number gets a note, and the
trace counts it. The check is deliberately permissive about labels: a real number used wrongly is
an eval's job, not this check's.

Dates, times, list markers, labels such as "Top 3" or "Form 16", and ages such as "3 mahine
purana" are not quantities, so they are never checked. An untraced number that is the sum or
difference of two traced numbers in the same reply is classified as derived: prose arithmetic,
which the rules still forbid, kept apart from a number that has no source at all.
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
_YEAR = r"(?:19|20)\d{2}"
# A day can't start mid-number ("1483.33\noct" is not the 33rd of October), and a date never
# crosses a line break: printed script output puts a value and the next label on new lines.
_DAY = r"(?<![\d.,])\b\d{1,2}(?:st|nd|rd|th)?"
DATE = re.compile(
    rf"\b\d{{4}}-\d{{2}}-\d{{2}}(?:T\d{{2}}:\d{{2}}(?::\d{{2}})?(?:\.\d+)?Z?)?\b"
    rf"|\b\d{{4}}-\d{{2}}\b"
    rf"|{_DAY}[ \t]+(?:{MONTHS})\b(?:,?[ \t]+{_YEAR}\b)?"
    rf"|\b(?:{MONTHS})[ \t]+\d{{1,2}}(?:st|nd|rd|th)?\b(?![.,]\d)(?:,?[ \t]+{_YEAR}\b)?"
    rf"|\b(?:{MONTHS})[ \t]+{_YEAR}\b(?![.,]\d)"
    rf"|\b\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?\b"
    # Hindi and Hinglish day phrases: "16 ko" (on the 16th), "30 tareekh", "5 तारीख".
    rf"|(?<![₹\w.,])(?:[1-9]|[12]\d|3[01])[ \t]+"
    rf"(?:ko|tarikh|tareekh|taarikh|tarik|tareek|को|तारीख)(?!\w)",
    re.I,
)
# A bare year (with no amount attached) is a date, not an amount or a count. Bold markdown
# can separate it from its month ("August **2025**"), so the DATE pattern above may miss it.
# Trailing punctuation still ends a year ("calendar 2025, ..."); a rupee sign makes it money.
YEAR = re.compile(r"(?<![\d.,₹])(?<!₹ )(?:19|20)\d{2}(?!\d|[.,]\d)")
LIST_MARKER = re.compile(r"^\s*(?:[*_#>]+\s*)?\d+[.)]\s", re.M)
TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b|\b\d{1,2}\s?(?:am|pm)\b", re.I)
# Names and labels that carry a digit but claim no quantity: "Top 3", "Form 16", "section 24",
# "last 4 digits", "16-digit", and a masked card "•••• 1234".
LABEL = re.compile(
    r"\b(?:top|form|section|sec\.)\s+\d+[a-z]?\b|\b\d+[-\s]?digits?\b|[•*xX]{2,}\s?\d{4}\b",
    re.I,
)
# A minus sign in a script's printed output: "-1804797.0" is the number 1804797.
SIGNED = re.compile(rf"(?<![\w.,])-({NUM})(?![\w/]|[.,]\d)")
# A day range tied to a month ("October (1–31)", "1–31 October") is a date, not a count.
DAY_RANGE = re.compile(
    rf"\b(?:{MONTHS})\s*\(?\s*\d{{1,2}}\s*[-–—]\s*\d{{1,2}}\s*\)?"
    rf"|\b\d{{1,2}}\s*[-–—]\s*\d{{1,2}}\s*(?:{MONTHS})\b",
    re.I,
)
# A range that names only the closing day after a full date ("2026-10-01 se 31") is a date too.
SHORT_RANGE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\s*(?:se|to|tak|through|thru|[-–—])\s*\d{1,2}\b",
    re.I,
)
# An age written as a duration ("(1 din)", "3 mahine purana", "550 days old") is a date, not a
# quantity. The unit must carry an age word, so "8 months, under 1 year" is still a count.
AGE = re.compile(
    r"\(\s*\d+\s*(?:din|days?|hafte?|weeks?|mahine?|mahina|months?|saal|years?|"
    r"दिन|महीने?|महीना|हफ्ते|साल|वर्ष)\s*\)"
    r"|\b\d+\s*(?:din|days?|hafte?|weeks?|mahine?|mahina|months?|saal|years?)\s+"
    r"(?:old|purana|purane|puraana|ago)\b"
    r"|\d+\s*(?:दिन|महीने?|महीना|हफ्ते|साल|वर्ष)\s*(?:पुराना|पुराने|पहले)",
    re.I,
)


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
    blanked = DAY_RANGE.sub(lambda m: " " * len(m.group()), text)
    blanked = SHORT_RANGE.sub(lambda m: " " * len(m.group()), blanked)
    blanked = DATE.sub(lambda m: " " * len(m.group()), blanked)
    blanked = YEAR.sub(lambda m: " " * len(m.group()), blanked)
    blanked = TIME.sub(lambda m: " " * len(m.group()), blanked)
    blanked = LIST_MARKER.sub(lambda m: " " * len(m.group()), blanked)
    blanked = LABEL.sub(lambda m: " " * len(m.group()), blanked)
    blanked = AGE.sub(lambda m: " " * len(m.group()), blanked)
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


def sources(returns: Any, user_texts: tuple[str, ...] = (), scripted: Any = ()) -> set[float]:
    """Every value the model was given: tool returns, tool arguments, and the user's messages.

    `scripted` holds `run_code` returns. Scripts compute in integer paise and the reply speaks
    rupees, so each number a script printed or returned also counts at a hundredth of itself.
    """
    values: set[float] = set()

    def walk(obj: Any, key: str = "", paise: bool = False) -> None:
        here = paise or "paise" in key
        if isinstance(obj, bool):
            if "complete" in key.lower():
                values.add(0.0 if not obj else 100.0)
            return
        if isinstance(obj, (int, float)):
            values.add(float(obj))
            if here:
                values.add(obj / 100)
        elif isinstance(obj, str):
            found = [value for _kind, value, _unit_value, _shown in extract(obj)]
            found += [_value(match.group(1)) for match in SIGNED.finditer(obj)]
            for value in found:
                values.add(value)
                if here:
                    values.add(value / 100)
        elif isinstance(obj, dict):
            is_table = isinstance(obj.get("columns"), list) and isinstance(obj.get("rows"), list)
            if is_table:
                # `run_query` returns raw paise columns. Its SQL names the unit, so a value
                # summed under an alias like `spend` is still paise; add the rupee form too.
                sql = str(obj.get("sql") or "").lower()
                paise_context = paise or "paise" in sql
                for row in obj["rows"]:
                    for column, cell in zip(obj["columns"], row, strict=False):
                        walk(cell, str(column), paise_context)
            for key_name, value in obj.items():
                if not (is_table and key_name == "rows"):
                    walk(value, str(key_name), paise)
        elif isinstance(obj, (list, tuple)):
            for value in obj:
                walk(value, key, paise)

    walk(returns)
    walk(scripted, paise=True)
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


def classify(
    reply: str,
    returns: Any,
    user_texts: tuple[str, ...] = (),
    kinds: tuple[str, ...] = ("money", "percent", "bare"),
    *,
    scripted: Any = (),
) -> list[tuple[str, str]]:
    """Each untraced number in reply order, as `(shown, "untraced" | "derived")`.

    Derived means the sum or difference of two traced numbers of the same kind in this reply,
    such as "₹40,000 (₹30,000 + ₹10,000)": correct arithmetic, done in prose instead of a script.
    """
    known = sources(returns, user_texts, scripted)
    numbers = [item for item in extract(reply) if item[0] in kinds]
    # ponytail: sums and differences of two reply numbers only; products and ratios stay
    # untraced. With many small counts in one reply a coincidental match is possible, so the
    # label is for reporting and never clears a flag.
    operands: dict[str, list[float]] = {}
    flags: list[tuple[str, float, float, str]] = []
    for kind, value, unit, shown in numbers:
        if traced(kind, value, unit, known):
            operands.setdefault(kind, []).append(value)
        else:
            flags.append((kind, value, unit, shown))
    result: list[tuple[str, str]] = []
    for kind, value, unit, shown in flags:
        pool = operands.get(kind, [])
        derived = any(
            abs(abs(a + sign * b) - value) <= unit / 2 + 1e-9
            for index, a in enumerate(pool)
            for b in pool[index + 1 :]
            for sign in (1, -1)
        )
        result.append((shown, "derived" if derived else "untraced"))
    return result


def check(
    reply: str,
    returns: Any,
    user_texts: tuple[str, ...] = (),
    kinds: tuple[str, ...] = ("money", "percent", "bare"),
    *,
    scripted: Any = (),
) -> list[str]:
    """Every untraced number, derived or not: prose arithmetic still breaks the rule."""
    flags = classify(reply, returns, user_texts, kinds, scripted=scripted)
    return [shown for shown, _label in flags]


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


def classify_messages(
    reply: str,
    messages: list[Any],
    kinds: tuple[str, ...] = ("money", "percent", "bare"),
) -> list[tuple[str, str]]:
    """Classify over a turn's model messages: returns, tool arguments, and user text."""
    import json

    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    returns: list[Any] = []
    scripted: list[Any] = []
    texts: list[str] = []
    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                # A script's code is not a source: a literal typed into it would trace any
                # number. What it printed or returned is, through its ToolReturnPart.
                if isinstance(part, ToolCallPart) and part.tool_name != "run_code":
                    returns.append(part.args_as_dict())
            continue
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                if part.tool_name == "run_code":
                    scripted.append(part.content)
                else:
                    returns.append(part.content)
                # A tool called inside `run_code` keeps its call and return in the top-level
                # ToolReturnPart's metadata. The model computes from those values, so the check
                # must see them or every script-derived number looks invented.
                metadata = getattr(part, "metadata", None)
                if isinstance(metadata, dict):
                    for nested_key in ("tool_returns", "tool_calls"):
                        nested = metadata.get(nested_key)
                        if not isinstance(nested, dict):
                            continue
                        for item in nested.values():
                            if isinstance(item, ToolCallPart):
                                returns.append(item.args_as_dict())
                                continue
                            nested_content = getattr(item, "content", None)
                            if nested_content is not None:
                                returns.append(nested_content)
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
    return classify(reply, returns, tuple(texts), kinds=kinds, scripted=scripted)


def check_messages(
    reply: str,
    messages: list[Any],
    kinds: tuple[str, ...] = ("money", "percent", "bare"),
) -> list[str]:
    """Run the check over a turn's model messages: returns, arguments, and user text."""
    return [shown for shown, _label in classify_messages(reply, messages, kinds)]


__all__ = [
    "check",
    "check_messages",
    "classify",
    "classify_messages",
    "extract",
    "sources",
    "traced",
]
