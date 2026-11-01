"""A prototype of rule 1's output check: does every number in a reply trace back to something it was given?"""
import json
import re

SCALE = {"k": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5, "l": 1e5, "crore": 1e7, "crores": 1e7, "cr": 1e7}
NUM = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
MONEY = re.compile(rf"(?:₹|\bRs\.?\s?|\bINR\s?)\s?({NUM})\s?(k|lakhs?|lacs?|crores?|cr|l)?\b|\b({NUM})\s?(lakhs?|lacs?|crores?|cr)\b|\b({NUM})\s?(?:rupees?|rs\b|rupaye)", re.I)
PERCENT = re.compile(rf"\b({NUM})\s?(?:%|percent\b|per cent\b)", re.I)
BARE = re.compile(rf"(?<![\w.,/-])({NUM})(?![\w/-]|[.,]\d)")
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december"
DATE = re.compile(rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTHS})\b(?:\s+\d{{4}})?|\b(?:{MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+\d{{4}})?|\b(?:{MONTHS})\s+\d{{4}}\b|\b\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?\b", re.I)
LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.M)
TIME = re.compile(r"\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?(?:am|pm)\b", re.I)


def _value(text):
    return float(text.replace(",", ""))


def _unit(text, scale=1.0):
    """How far apart two values can be and still read the same at the precision shown."""
    decimals = len(text.split(".")[1]) if "." in text else 0
    if decimals == 0 and scale == 1.0 and "," not in text:
        trailing = len(text) - len(text.rstrip("0"))
        if trailing >= 2:  # "12000" or "₹5000" may well be rounded
            return 10.0**trailing
    return scale * 10.0 ** (-decimals)


def extract(text):
    """Returns (kind, value, unit, shown) for each number, leaving out dates, times, and list markers."""
    blanked = DATE.sub(lambda m: " " * len(m.group()), text)
    blanked = TIME.sub(lambda m: " " * len(m.group()), blanked)
    blanked = LIST_MARKER.sub(lambda m: " " * len(m.group()), blanked)
    found = []

    def take(pattern, handler):
        nonlocal blanked
        out = []
        for m in pattern.finditer(blanked):
            out.append(handler(m))
        blanked = pattern.sub(lambda m: " " * len(m.group()), blanked)
        return out

    def money(m):
        raw = m.group(1) or m.group(3) or m.group(5)
        scale_word = (m.group(2) or m.group(4) or "").lower()
        scale = SCALE.get(scale_word, 1.0)
        return ("money", _value(raw) * scale, _unit(raw, scale), m.group().strip())

    found += take(MONEY, money)
    found += take(PERCENT, lambda m: ("percent", _value(m.group(1)), _unit(m.group(1)), m.group().strip()))
    found += take(BARE, lambda m: ("bare", _value(m.group(1)), _unit(m.group(1)), m.group(1)))
    return found


def sources(returns, user_texts=()):
    """Every value the model was given: tool returns, tool arguments, and your own messages."""
    values = set()

    def walk(obj, key=""):
        if isinstance(obj, bool):
            return
        if isinstance(obj, (int, float)):
            values.add(float(obj))
            if "paise" in key:
                values.add(obj / 100)
        elif isinstance(obj, str):
            for _, v, _, _ in extract(obj):
                values.add(v)
        elif isinstance(obj, dict):
            if isinstance(obj.get("columns"), list) and isinstance(obj.get("rows"), list):  # a run_query return
                for row in obj["rows"]:
                    for col, cell in zip(obj["columns"], row):
                        walk(cell, str(col))
            for k, v in obj.items():
                if k != "rows":
                    walk(v, k)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                walk(v, key)

    walk(returns)
    for t in user_texts:
        walk(t)
    return values


def traced(kind, value, unit, known):
    for s in known:
        if abs(s - value) <= unit / 2 + 1e-9:
            return True
        if kind == "percent" and abs(s * 100 - value) <= unit / 2 + 1e-9:  # 0.12 shown as 12%
            return True
        if s < 0 and abs(-s - value) <= unit / 2 + 1e-9:  # a debit of -450 shown as ₹450
            return True
    return False


def check(reply, returns, user_texts=(), kinds=("money", "percent", "bare")):
    known = sources(returns, user_texts)
    return [shown for kind, value, unit, shown in extract(reply) if kind in kinds and not traced(kind, value, unit, known)]
