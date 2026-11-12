"""Replay cost metadata through the standard exporter after native tracing had time to flush."""

from __future__ import annotations

from typing import Any

from arno.agent.cost_journal import replay_costs


async def handle_cost_replay(payload: dict[str, Any]) -> dict[str, int]:
    del payload
    return await replay_costs()
