"""Identifiers at import, in plain code.

Only the last four digits of the user's own account and card numbers are kept. UPI IDs are
kept because a payee correction names them. A third-party identifier used only for matching
becomes a keyed one-way hash. Anything the product does not use is dropped.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re

_UPI = re.compile(r"\b[a-zA-Z0-9.\-_]{2,}@[a-zA-Z]{2,}\b")
_DIGITS = re.compile(r"(?<!\d)(\d{9,18})(?!\d)")
_UTR = re.compile(r"(?<!\d)(\d{12})(?!\d)")
_ACCOUNT = re.compile(r"(?<!\d)(\d{9,18})(?!\d)")


def _key() -> bytes:
    return (
        os.environ.get("FIN_AGENT_MATCH_KEY")
        or os.environ.get("AGENT_RO_PASSWORD")
        or "fin-agent-local-match-key"
    ).encode("utf-8")


def match_hash(value: str) -> str:
    normalized = re.sub(r"\s+", "", str(value)).lower()
    return hmac.new(_key(), normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def last4(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return digits[-4:] if len(digits) >= 4 else None


def find_upi_ref(text: str) -> str | None:
    match = _UTR.search(text or "")
    return match.group(1) if match else None


def find_upi_id(text: str) -> str | None:
    match = _UPI.search(text or "")
    return match.group(0) if match else None


def scrub(
    text: str | None,
    *,
    own_last4: set[str] | None = None,
    extra_hashes: dict[str, str] | None = None,
) -> tuple[str | None, dict[str, str]]:
    """Return the safe narration and the keyed hashes of identifiers it carried.

    A long number ending in one of the user's own last-four values is masked to its last four.
    A third-party account-like number is replaced by a short, stable alias and its one-way hash
    is returned so matching still works. A 12-digit UTR is kept: it is the UPI reference.
    """
    if text is None:
        return None, {}
    own = own_last4 or set()
    hashes: dict[str, str] = dict(extra_hashes or {})
    safe = str(text)

    for match in _DIGITS.finditer(safe):
        digits = match.group(1)
        tail = digits[-4:]
        before = safe[max(0, match.start() - 24) : match.start()].lower()
        if len(digits) == 12 and any(marker in before for marker in ("upi", "utr", "ref", "rrn")):
            continue  # a UPI reference or UTR, used for reconciliation
        if tail in own:
            safe = safe.replace(digits, f"XXXX{tail}")
            continue
        digest = match_hash(digits)
        hashes[digest] = digest
        safe = safe.replace(digits, f"ID-{digest[:8]}")
    return safe, hashes


def scrub_account_number(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return digits[-4:] if len(digits) >= 4 else None


def mask_pan(text: str | None) -> str | None:
    if not text:
        return text
    return re.sub(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", "XXXXX0000X", text)


__all__ = [
    "find_upi_id",
    "find_upi_ref",
    "last4",
    "mask_pan",
    "match_hash",
    "scrub",
    "scrub_account_number",
]
