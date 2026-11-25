"""The model-usage log and the one shared daily spend cap.

Every paid path writes one `llm_usage` row per model response; the cap is the sum of today's rows
(India time) against `DAILY_LIMIT_USD`. A read or write failure propagates, so a database that
cannot be reached never lets inference run uncapped. The check runs before a request, so the cap
is a runaway brake: a response that crosses it still lands in the log.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Hooks
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.usage import RequestUsage, RunUsage

from arno.db.conn import Connection, connect

logger = logging.getLogger("arno.usage")

DAILY_LIMIT_USD = Decimal("20")
INDIA = ZoneInfo("Asia/Kolkata")


class DailyBudgetExceeded(RuntimeError):
    """Today's model spend reached the daily limit. The message is safe to show the user."""


COST_STOP_ERRORS = (DailyBudgetExceeded, UsageLimitExceeded)


async def check_daily_budget(conn: Connection, limit_usd: Decimal = DAILY_LIMIT_USD) -> None:
    midnight = datetime.now(INDIA).replace(hour=0, minute=0, second=0, microsecond=0)
    row = await conn.fetchrow_required(
        "SELECT coalesce(sum(usd), 0) AS spent FROM llm_usage WHERE created_at >= $1", midnight
    )
    if row["spent"] >= limit_usd:
        raise DailyBudgetExceeded(
            f"Paused at the daily spend limit (${limit_usd}). It resets at midnight India time."
        )


async def record_usage(
    conn: Connection,
    role: str,
    model: str,
    usage: RequestUsage | RunUsage,
    usd: Decimal | float | None,
) -> None:
    if usd is None:
        # shortcut: an unpriced model counts as $0 toward the cap since genai-prices cannot price
        # it; upgrade to a per-model price override if a paid model ever lacks a catalog entry.
        logger.warning("no price for %s (%s); recording $0", model, role)
        usd = 0
    await conn.execute(
        "INSERT INTO llm_usage (role, model, input_tokens, output_tokens, usd)"
        " VALUES ($1, $2, $3, $4, $5)",
        role,
        model,
        usage.input_tokens,
        usage.output_tokens,
        Decimal(str(usd)),
    )


def usage_hooks(role: str) -> Hooks:
    async def check(ctx: RunContext, request_context: ModelRequestContext) -> ModelRequestContext:
        async with connect() as conn:
            await check_daily_budget(conn)
        return request_context

    async def record(
        ctx: RunContext, *, request_context: ModelRequestContext, response: ModelResponse
    ) -> ModelResponse:
        async with connect() as conn:
            await record_usage(
                conn, role, response.model_name or "unknown", response.usage, response.usage.cost
            )
        return response

    return Hooks(before_model_request=check, after_model_request=record)
