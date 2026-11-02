"""Passwords live here, in memory, for the length of one import job.

The API puts a password here from the import card. It is never written to the database, put in
a job payload, sent to the model, or logged. A crash loses it, which is why the card asks again.
"""

from __future__ import annotations

import threading
import time
from typing import Any


class PasswordVault:
    def __init__(self, ttl_seconds: int = 900) -> None:
        self._values: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self.ttl_seconds = ttl_seconds

    def put(self, import_id: str, password: str) -> None:
        with self._lock:
            self._values[import_id] = (password, time.monotonic() + self.ttl_seconds)

    def get(self, import_id: str) -> str | None:
        with self._lock:
            item = self._values.get(import_id)
            if item is None:
                return None
            password, expires = item
            if expires < time.monotonic():
                self._values.pop(import_id, None)
                return None
            return password

    def pop(self, import_id: str) -> str | None:
        with self._lock:
            item = self._values.pop(import_id, None)
            return item[0] if item else None

    def clear(self) -> None:
        with self._lock:
            self._values.clear()

    def __repr__(self) -> str:
        return "PasswordVault(<redacted>)"

    def __bool__(self) -> bool:
        return True


PASSWORDS = PasswordVault()


def describe_vault(vault: Any = None) -> dict[str, Any]:
    vault = vault or PASSWORDS
    return {"ttl_seconds": vault.ttl_seconds, "stored": "in memory only"}


__all__ = ["PASSWORDS", "PasswordVault", "describe_vault"]
