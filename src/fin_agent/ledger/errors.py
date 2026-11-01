"""Errors the ledger raises. The API turns each into an RFC 9457 problem response."""

from __future__ import annotations

from typing import Any


class LedgerError(Exception):
    """A rule the caller broke, with a safe message for the client."""

    status = 400
    title = "Bad request"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidLedgerError(LedgerError):
    status = 400
    title = "Bad request"


class ForbiddenLedgerError(LedgerError):
    status = 403
    title = "Forbidden"


class NotFoundLedgerError(LedgerError):
    status = 404
    title = "Not found"


class ConflictLedgerError(LedgerError):
    status = 409
    title = "Conflict"


class UnavailableLedgerError(LedgerError):
    status = 503
    title = "Service unavailable"
