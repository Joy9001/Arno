"""The spend store's asyncpg pool. asyncpg is a second driver, used only for this."""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

logger = logging.getLogger("fin_agent.spend")


async def make_spend_store(dsn: str) -> tuple[Any, Any]:
    """Return `(store, pool)`, or `(None, None)` when Postgres is unreachable at boot."""
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, command_timeout=10)
    except Exception:  # noqa: BLE001 - the app still serves read-only panels
        logger.exception("spend store pool did not start")
        return None, None
    from pydantic_ai_harness.spend import PostgresSpendStore

    return PostgresSpendStore(pool), pool


__all__ = ["make_spend_store"]
