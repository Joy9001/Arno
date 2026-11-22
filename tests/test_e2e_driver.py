"""The driver's HTTP surface works, with no model in the loop.

The journeys themselves need a real model, so they are run by hand (see `tests/e2e/README.md`).
These tests cover the parts that break silently: the passkey ceremony, the upload, the exact body
the composer sends, and how a stream becomes tool calls, cards, and confirm cards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter
from pydantic_ai.ui.vercel_ai.request_types import RequestData

from arno.ledger.movements import (
    _effectively_reversed_ids,  # noqa: PLC2701 - the ledger's own rule
    correct_movement,
    record_expense,
)
from tests.e2e.driver import (
    Driver,
    _paise,
    _render_import,
    approval_message,
    build_parser,
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
                [
                    {
                        "tool_call_id": "call_1",
                        "tool_name": "preference",
                        "input": {"action": "remember"},
                    },
                    {"tool_call_id": "call_2", "tool_name": "update_setup", "input": {}},
                ],
                approved=True,
            )
        ],
    )
    parts = REQUEST_DATA.validate_python(approval).messages[0].parts
    assert [part.tool_call_id for part in parts] == ["call_1", "call_2"]
    assert all(part.state == "approval-responded" for part in parts)
    assert all(part.approval.approved is True for part in parts)

    denied = chat_body(
        "thread-1",
        [
            approval_message(
                [{"tool_call_id": "call_1", "tool_name": "preference", "input": {}}],
                approved=False,
            )
        ],
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


def test_the_driver_commit_sends_the_adjustment_confirmation_only_when_asked(monkeypatch):
    """The server refuses a non-zero closing adjustment unless the body confirms it."""
    calls: list[tuple[str, dict]] = []

    def post(path: str, **kwargs: object) -> dict:
        calls.append((path, dict(kwargs)))
        return {"status": "committed"}

    driver = Driver("http://testserver")
    try:
        monkeypatch.setattr(driver, "post", post)
        assert driver.commit("imp_1") == {"status": "committed"}
        driver.commit("imp_1", confirm_adjustment=True)
    finally:
        driver.close()

    assert calls == [
        ("/api/imports/imp_1/commit", {}),
        ("/api/imports/imp_1/commit", {"json": {"adjustment_confirmed": True}}),
    ]


def test_the_commit_command_takes_the_adjustment_flag():
    parser = build_parser()
    assert parser.parse_args(["commit", "imp_1"]).confirm_adjustment is False
    assert parser.parse_args(["commit", "imp_1", "--confirm-adjustment"]).confirm_adjustment is True


REVIEW = {"import_id": "imp_1", "status": "review", "lines": []}


def test_an_import_review_states_the_closing_adjustment_it_would_book():
    rendered = _render_import(
        {
            **REVIEW,
            "closing_adjustment_paise": 12000,
            "closing_adjustment_reason": (
                "The statement's closing balance is ₹120.00 above the ledger."
            ),
        }
    )
    assert "closing adjustment: ₹120.00" in rendered
    assert "above the ledger." in rendered
    assert "ready: driver commit imp_1 --confirm-adjustment" in rendered


def test_a_zero_closing_adjustment_needs_no_confirmation():
    rendered = _render_import(
        {**REVIEW, "closing_adjustment_paise": 0, "closing_adjustment_reason": None}
    )
    assert "closing adjustment: ₹0.00" in rendered
    assert "ready: driver commit imp_1" in rendered
    assert "--confirm-adjustment" not in rendered


def test_the_driver_prints_a_stated_balance_conflict_and_needs_the_flag():
    rendered = _render_import(
        {
            "import_id": "imp_1",
            "status": "review",
            "lines": [],
            "closing_adjustment_paise": 0,
            "stated_balance_paise": 500_000,
            "stated_balance_as_of": "2026-11-10",
            "stated_balance_reason": (
                "You stated ₹5,000.00 on 2026-11-10; the statement shows ₹5,050.00 on that day."
            ),
        }
    )
    assert "stated balance: ₹5,000.00 as of 2026-11-10" in rendered
    assert "the statement shows ₹5,050.00" in rendered
    assert "ready: driver commit imp_1 --confirm-adjustment" in rendered


def test_the_driver_prints_a_closing_taken_from_the_running_balance():
    rendered = _render_import(
        {
            "import_id": "imp_1",
            "status": "review",
            "lines": [],
            "closing_adjustment_paise": 0,
            "closing_source_note": "No printed closing balance was read on this statement.",
        }
    )
    assert "closing source: No printed closing balance was read" in rendered
    assert "ready: driver commit imp_1" in rendered
    assert "--confirm-adjustment" not in rendered


# `up --keep` restarts the server without seeding, and the summary agrees with the ledger -------


def test_up_keep_parses_and_refuses_to_seed():
    from tests.e2e import driver

    args = driver.build_parser().parse_args(["up", "--port", "8999", "--keep"])
    assert args.keep is True
    plain = driver.build_parser().parse_args(["up", "--port", "8999"])
    assert plain.keep is False
    with pytest.raises(SystemExit):
        driver.cmd_up(
            argparse.Namespace(port=8999, seed="demo", empty=False, keep=True, timeout=1.0)
        )


def test_up_keep_restarts_the_server_and_leaves_the_ledger_alone(monkeypatch, tmp_path):
    from tests.e2e import driver

    calls: dict[str, object] = {}

    monkeypatch.setattr(driver, "load_state", lambda: {"pid": 4321, "cookie": "c"})
    monkeypatch.setattr(driver, "stop_server", lambda pid: calls.setdefault("stopped", pid) and 1)
    monkeypatch.setattr(driver, "server_env", lambda port: {})
    monkeypatch.setattr(driver, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(driver, "_require_free_port", lambda port: None)
    monkeypatch.setattr(
        driver,
        "prepare_database",
        lambda *, empty, seed: calls.setdefault("database", (empty, seed)) and "arno_x_test",
    )
    monkeypatch.setattr(driver, "start_server", lambda port, log_path: SimpleNamespace(pid=987))
    monkeypatch.setattr(
        driver, "wait_for_health", lambda base, timeout, process: {"schema_version": "26"}
    )

    class FakeDriver:
        def __init__(self, base_url, cookie=None):
            self.cookie = cookie

        def logged_in(self):
            return True

        def session(self):
            return {"user": {"display_name": "Demo"}}

        def close(self):
            pass

    monkeypatch.setattr(driver, "Driver", FakeDriver)
    monkeypatch.setattr(driver, "save_state", lambda state: calls.setdefault("saved", state))

    assert (
        driver.cmd_up(argparse.Namespace(port=8999, seed=None, empty=False, keep=True, timeout=5.0))
        == 0
    )
    assert calls["stopped"] == 4321
    assert calls["database"] == (False, False)


async def test_the_drivers_spending_agrees_with_the_ledger_after_undo_of_undo(ledger):
    from arno.ledger.accounts import create_account
    from arno.ledger.audit import undo_group
    from arno.ledger.common import today
    from tests.invariants import check_ledger

    bank = await create_account(
        ledger, kind="bank", name="Driver bank", opening_balance_paise=10_000_000
    )
    food = await ledger.fetchval(
        "SELECT id FROM categories WHERE name = 'Food & Dining' AND active"
    )
    created = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=45_000,
        description="Test Cafe",
        category_id=food,
        date=today(),
    )
    corrected = await correct_movement(ledger, created["id"], changes={"amount_paise": 40_500})
    await undo_group(ledger, corrected["undo_id"])
    await undo_group(ledger, created["undo_id"])

    # The forest the parity rule misreads: one reversal child already restored, one still live.
    assert created["id"] in await _effectively_reversed_ids(ledger)
    summary = await asyncio.to_thread(ledger_summary)
    assert summary["spending"] == []
    violations = await check_ledger(ledger)
    assert violations == [], [violation.code for violation in violations]
