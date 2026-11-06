"""A chart card shows the run_code script that produced it, not a model-written copy."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

from fin_agent.agent.builder import build_agent
from fin_agent.agent.cards import cards_for_transcript, cards_from_messages
from fin_agent.agent.deps import AgentDeps


def test_a_run_code_chart_carries_its_script_and_calculation_time(demo_ledger):
    code = (
        'result = await show_chart(type="bar", title="Spend", as_of="2026-10-31", '
        'series=[{"name": "Food", "points": [{"x": "Oct", "y": 45000}]}])\n'
        "print(result)\n"
    )

    def script(messages, info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": code})])
        return ModelResponse(parts=[TextPart("Here is the chart.")])

    agent = build_agent()
    with agent.override(model=FunctionModel(script)):
        result = agent.run_sync("show me", deps=AgentDeps(conversation_id="cnv_test"))

    for cards in (
        cards_from_messages(result.all_messages()),
        cards_for_transcript(result.all_messages()),
    ):
        chart = next(card for card in cards if card.data["card"] == "chart")
        assert chart.data["script"] == code
        assert chart.data["computed_at"]
        assert chart.data["as_of"] == "2026-10-31"


def test_a_direct_chart_without_a_script_says_it_is_unavailable():
    part = ToolReturnPart(
        tool_name="show_chart",
        content={"shown": True},
        tool_call_id="call_direct",
        metadata=[
            DataChunk(
                type="data-card",
                data={"card": "chart", "title": "Direct", "series": [], "as_of": "2026-11-01"},
            )
        ],
        timestamp=datetime(2026, 11, 1, 9, 30, tzinfo=UTC),
    )

    cards = cards_for_transcript([ModelRequest(parts=[part])])

    chart = next(card for card in cards if card.data["card"] == "chart")
    assert "script" not in chart.data
    assert chart.data["computed_at"] == "2026-11-01T09:30:00+00:00"
    assert chart.data["as_of"] == "2026-11-01"


def _chart_return(tool_call_id: str, timestamp: datetime) -> ToolReturnPart:
    return ToolReturnPart(
        tool_name="run_code",
        content={"shown": True},
        tool_call_id=tool_call_id,
        metadata={
            "tool_returns": {
                "shown": ToolReturnPart(
                    tool_name="show_chart",
                    content={"shown": True},
                    metadata=[
                        DataChunk(
                            type="data-card",
                            data={
                                "card": "chart",
                                "title": "Spend",
                                "series": [],
                                "as_of": "2026-11-01",
                            },
                        )
                    ],
                )
            }
        },
        timestamp=timestamp,
    )


def test_run_code_args_stored_as_a_json_string_keep_the_script_source():
    code = 'result = await show_chart(type="bar", title="Spend", as_of="2026-11-01", series=[])\n'
    call = ToolCallPart("run_code", json.dumps({"code": code}), tool_call_id="call_json_string")

    cards = cards_for_transcript(
        [
            ModelResponse(parts=[call]),
            ModelRequest(
                parts=[_chart_return("call_json_string", datetime(2026, 11, 1, tzinfo=UTC))]
            ),
        ]
    )

    chart = next(card for card in cards if card.data["card"] == "chart")
    assert chart.data["script"] == code


def test_malformed_legacy_run_code_args_do_not_break_the_transcript():
    call = ToolCallPart("run_code", '{"code": ', tool_call_id="call_malformed")

    cards = cards_for_transcript(
        [
            ModelResponse(parts=[call]),
            ModelRequest(
                parts=[_chart_return("call_malformed", datetime(2026, 11, 1, tzinfo=UTC))]
            ),
        ]
    )

    chart = next(card for card in cards if card.data["card"] == "chart")
    assert "script" not in chart.data
