"""Layout fingerprints: the normalized column headers of one statement layout."""

from __future__ import annotations

import re
from typing import Any

from fin_agent.importers.readers import Doc

_AMOUNT_WORDS = ("amount", "debit", "credit", "withdrawal", "deposit", "balance", "dr", "cr")
_DATE_WORDS = ("date", "txn date", "value date", "transaction date")
_DESCRIPTION_WORDS = (
    "description",
    "narration",
    "particular",
    "details",
    "transaction details",
    "remarks",
)


def normalize_header(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def normalize_headers(values: Any) -> str:
    if isinstance(values, str):
        values = [values]
    parts = [normalize_header(value) for value in values or []]
    return "|".join(part for part in parts if part)


def _looks_like_header(row: list[Any]) -> bool:
    text = " ".join(normalize_header(cell) for cell in row)
    has_date = any(word in text for word in _DATE_WORDS) or "date" in text
    has_amount = any(word in text for word in _AMOUNT_WORDS)
    has_description = any(word in text for word in _DESCRIPTION_WORDS)
    filled = sum(1 for cell in row if str(cell or "").strip())
    return has_date and has_amount and (has_description or filled >= 3)


def detect_header_row(rows: list[list[Any]]) -> list[Any] | None:
    for row in rows[:30]:
        if row and _looks_like_header(row):
            return row
    return None


def detect_fingerprint(doc: Doc) -> str | None:
    """The header fingerprint from the document itself, before any parser runs."""
    return _detect_fingerprint(doc)


def _detect_fingerprint(doc: Doc) -> str | None:
    if doc.kind in ("xlsx", "xls", "xlsb", "ods", "docx", "html", "rtf"):
        try:
            for index in range(max(1, len(doc.info().get("sheets", [])))):
                row = detect_header_row(doc.cells(index))
                if row:
                    return normalize_headers(row)
        except Exception:  # noqa: BLE001 - a reader's cell view is optional
            pass
        return None
    if doc.kind == "pdf":
        try:
            from fin_agent.importers.layout_detect import text_lines

            for page in range(min(doc.page_count(), 3)):
                for line in text_lines(doc.words(page)):
                    if _looks_like_header([word["text"] for word in line]):
                        return normalize_headers([word["text"] for word in line])
        except Exception:  # noqa: BLE001 - detection is best effort
            return None
        return None
    if doc.kind in ("text", "ofx", "mt940", "json", "camt", "xml"):
        try:
            rows = doc.cells(0)
        except Exception:  # noqa: BLE001
            return None
        row = detect_header_row(rows)
        if row:
            return normalize_headers(row)
        # OFX, MT940, JSON, and CAMT have no column header row. The first stable line names the
        # format, so the saved parser can still replay without a model call.
        for candidate in rows[:5]:
            text = " ".join(str(cell) for cell in candidate if cell is not None).strip()
            if text:
                return f"{doc.kind}|{normalize_headers(text)[:120]}"
    return None


__all__ = [
    "detect_fingerprint",
    "detect_header_row",
    "normalize_header",
    "normalize_headers",
]
