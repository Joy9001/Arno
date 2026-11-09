"""The driver's HTTP surface works, with no model in the loop.

The journeys themselves need a real model, so they are run by hand (see `tests/e2e/README.md`).
These tests cover the parts that break silently: the passkey ceremony, the upload, the exact body
the composer sends, and how a stream becomes tool calls, cards, and confirm cards.
"""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter
from pydantic_ai.ui.vercel_ai.request_types import RequestData

from tests.e2e.driver import (
    Driver,
    _paise,
    approval_message,
    chat_body,
    issue_setup_token,
    ledger_summary,
    parse_stream,
    render_turn,
    turn_summary,
)
from tests.import_fixtures import sbi_csv


def _sse(*events: dict) -> list[str]:
    """A canned UI message stream, as `iter_lines` would hand it over."""
    lines = [f"data: {json.dumps(event, ensure_ascii=False)}" for event in events]
    return [*lines, "", "data: [DONE]", ""]


TURN = _sse(
    {"type": "start"},
    {"type": "start-step"},
    {
        "type": "tool-input-available",
        "toolCallId": "call_1",
        "toolName": "run_code",
        "input": {"code": "1"},
    },
    {"type": "tool-output-available", "toolCallId": "call_1", "output": {"status": "ok"}},
    {"type": "text-delta", "id": "t1", "delta": "Chai was "},
    {"type": "text-delta", "id": "t1", "delta": "₹50."},
    {"type": "data-card", "data": {"card": "movement", "undo_id": "und_1", "title": "Recorded"}},
    {
        "type": "tool-input-available",
        "toolCallId": "call_2",
        "toolName": "preference",
        "input": {"action": "remember"},
    },
    {"type": "tool-approval-request", "approvalId": "call_2", "toolCallId": "call_2"},
    {"type": "data-number-flags", "data": {"amounts": ["₹50"]}},
    {"type": "finish"},
)

REQUEST_DATA = TypeAdapter(RequestData)


def test_the_driver_enrolls_uploads_and_reads_the_ledger(client):
    driver = Driver("http://testserver", http=client)
    user = driver.enroll(issue_setup_token())

    assert user["user"]["display_name"]
    assert driver.logged_in()
    assert driver.auth_state()["setup_required"] is False

    uploaded = driver.upload("sbi.csv", sbi_csv(), "text/csv")
    assert uploaded["kind"] == "statement"
    assert uploaded["detected_kind"] == "text"
    # An upload alone imports nothing: the turn's statement refs start the parser.
    assert driver.imports()["items"] == []

    summary = ledger_summary()
    assert summary["open_imports"] == []
    assert summary["spending"] == []


def test_the_chat_body_and_the_approval_part_match_the_server_schema():
    body = chat_body(
        "thread-1",
        [
            {
                "id": "msg-1",
                "role": "user",
                "parts": [
                    {"type": "text", "text": "Import this"},
                    {
                        "type": "data-statement",
                        "data": {
                            "attachment_id": "att_1",
                            "filename": "sbi.csv",
                            "detected_kind": "text",
                        },
                    },
                ],
            }
        ],
    )
    parsed = REQUEST_DATA.validate_python(body)
    assert parsed.id == "thread-1"
    assert body["messageId"] == "msg-1"

    approval = chat_body(
        "thread-1",
        [
            approval_message(
                "call_1", approved=True, tool_name="preference", tool_input={"action": "remember"}
            )
        ],
    )
    part = REQUEST_DATA.validate_python(approval).messages[0].parts[0]
    assert part.state == "approval-responded"
    assert part.tool_call_id == "call_1"
    assert part.approval.approved is True

    denied = chat_body(
        "thread-1",
        [approval_message("call_1", approved=False, tool_name="preference", tool_input={})],
    )
    assert REQUEST_DATA.validate_python(denied).messages[0].parts[0].approval.approved is False


def test_a_stream_becomes_tool_calls_cards_confirm_cards_and_flags():
    turn = turn_summary("th_1", parse_stream(TURN))

    assert turn["text"] == "Chai was ₹50."
    assert turn["tool_calls"][0]["result"] == {"status": "ok"}
    assert turn["undo_ids"] == ["und_1"]
    assert turn["number_flags"] == ["₹50"]
    assert turn["approvals"] == [
        {
            "tool_call_id": "call_2",
            "approval_id": "call_2",
            "tool_name": "preference",
            "input": {"action": "remember"},
        }
    ]

    rendered = render_turn(turn)
    assert "undo: driver undo und_1" in rendered
    assert "approve: driver approve call_2 yes" in rendered


def test_rupees_become_integer_paise():
    assert _paise("2,45,678.90") == 24567890
    assert _paise("₹0.01") == 1
    assert _paise("0") == 0
    assert _paise("-2,450.00") == -245000
    with pytest.raises(SystemExit):
        _paise("1.005")
