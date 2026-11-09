"""Audit regressions: the agent loop, its safety rules, and the chat API."""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from fin_agent.agent.number_check import check
from fin_agent.db.conn import connect
from tests.agent_helpers import streaming_model

# --- Approval cards through the real chat endpoint -------------------------------------------


def _tool_answered(messages: list[Any], name: str) -> bool:
    for message in messages:
        for part in getattr(message, "parts", []):
            if getattr(part, "part_kind", "") in {"tool-return", "retry-prompt"} and (
                part.tool_name == name
            ):
                return True
    return False


async def _flagged_thread(thread_id: str) -> None:
    async with connect() as conn:
        await conn.execute(
            "INSERT INTO conversations (id, title, outside_text_at) VALUES ($1, 'flagged', now())",
            thread_id,
        )


def _preference_script(text: str = "Never warn about rent", *, seen: list[str] | None = None):
    def script(messages, info: AgentInfo) -> ModelResponse:
        if seen is not None:
            for message in messages:
                for part in getattr(message, "parts", []):
                    if getattr(part, "part_kind", "") == "user-prompt":
                        seen.append(str(part.content))
        if not _tool_answered(messages, "preference"):
            return ModelResponse(
                parts=[
                    ToolCallPart("preference", {"action": "remember", "text": text}, "call_pref_1")
                ]
            )
        return ModelResponse(parts=[TextPart("Remembered.")])

    return script


def _user(message_id: str, text: str) -> dict[str, Any]:
    return {"id": message_id, "role": "user", "parts": [{"type": "text", "text": text}]}


def _approval_part(*, settled: bool) -> dict[str, Any]:
    """The tool part a browser holds: a fresh tap, or settled with the result already in."""
    if settled:
        return {
            "type": "tool-preference",
            "toolCallId": "call_pref_1",
            "state": "output-available",
            "input": {"action": "remember", "text": "Never warn about rent"},
            "output": {"status": "remembered"},
            "approval": {"id": "call_pref_1", "approved": True},
        }
    return {
        "type": "tool-preference",
        "toolCallId": "call_pref_1",
        "state": "approval-responded",
        "input": {"action": "remember", "text": "Never warn about rent"},
        "approval": {"id": "call_pref_1", "approved": True},
    }


async def _first_turn(authed_client, thread_id: str, client_id: str) -> dict[str, Any]:
    await _flagged_thread(thread_id)
    body = {
        "id": thread_id,
        "trigger": "submit-message",
        "messages": [_user(client_id, "remember this")],
    }
    with authed_client.app.state.agent.override(model=streaming_model(_preference_script())):
        response = authed_client.post("/api/chat", json=body)
    assert response.status_code == 200, response.text
    thread = authed_client.get(f"/api/threads/{thread_id}/messages").json()
    part = thread["messages"][-1]["parts"][0]
    assert part["state"] == "approval-requested"
    return body


def _approval_body(thread_id: str, first: dict[str, Any], *, settled: bool = False) -> dict:
    return {
        "id": thread_id,
        "trigger": "submit-message",
        "messages": [*first["messages"], _assistant_part(settled)],
    }


def _assistant_part(settled: bool) -> dict[str, Any]:
    return {"id": "reply-1", "role": "assistant", "parts": [_approval_part(settled=settled)]}


async def test_a_message_after_an_approval_is_kept_and_answered(authed_client, demo_ledger):
    thread_id = "thread-approval-followup"
    first = await _first_turn(authed_client, thread_id, "client-approve-1")
    seen: list[str] = []
    model = streaming_model(_preference_script(seen=seen))
    with authed_client.app.state.agent.override(model=model):
        approved = authed_client.post("/api/chat", json=_approval_body(thread_id, first))
        assert approved.status_code == 200, approved.text
        # The browser now holds the settled part with its approval, as the AI SDK keeps it.
        follow_up = {
            "id": thread_id,
            "trigger": "submit-message",
            "messages": [
                *first["messages"],
                _assistant_part(True),
                _user("client-after-1", "thanks"),
            ],
        }
        answer = authed_client.post("/api/chat", json=follow_up)
        assert answer.status_code == 200, answer.text

    thread = authed_client.get(f"/api/threads/{thread_id}/messages").json()
    user_texts = [
        part.get("text")
        for message in thread["messages"]
        if message["role"] == "user"
        for part in message["parts"]
        if part.get("type") == "text"
    ]
    # The follow-up used to be dropped: it was mistaken for another approval submission.
    assert user_texts == ["remember this", "thanks"]
    assert any("thanks" in prompt for prompt in seen)


async def test_a_run_query_in_the_same_turn_makes_setup_ask_first(authed_client, demo_ledger):
    thread_id = "thread-gate-setup"
    code = "r = await run_query(sql='SELECT 1 AS n')\nr"
    setup = {
        "entity": "account",
        "action": "add",
        "data": {"name": "Gate savings", "kind": "bank"},
    }

    def script(messages, info: AgentInfo) -> ModelResponse:
        if not _tool_answered(messages, "run_code"):
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": code}, "call_query_1")])
        if not _tool_answered(messages, "update_setup"):
            return ModelResponse(parts=[ToolCallPart("update_setup", setup, "call_setup_1")])
        return ModelResponse(parts=[TextPart("Saved.")])

    body = {
        "id": thread_id,
        "trigger": "submit-message",
        "messages": [_user("client-gate-1", "add an account")],
    }
    with authed_client.app.state.agent.override(model=streaming_model(script)):
        first = authed_client.post("/api/chat", json=body)
        assert first.status_code == 200
        thread = authed_client.get(f"/api/threads/{thread_id}/messages").json()
        parts = thread["messages"][-1]["parts"]
        part = next(p for p in parts if p.get("type") == "tool-update_setup")
        # The raw text a query read in this same turn makes the lasting write ask first.
        assert part["state"] == "approval-requested"
        assert thread["thread"]["flagged"] is True
        assert (
            await demo_ledger.fetchval("SELECT count(*) FROM accounts WHERE name = 'Gate savings'")
            == 0
        )

        approval = {
            "id": thread_id,
            "trigger": "submit-message",
            "messages": [
                *body["messages"],
                {
                    "id": "reply-gate",
                    "role": "assistant",
                    "parts": [
                        {
                            "type": "tool-update_setup",
                            "toolCallId": "call_setup_1",
                            "state": "approval-responded",
                            # A client cannot swap the change it is approving: the server
                            # executes the call it stored, not this copy.
                            "input": {**setup, "data": {"name": "Client injected", "kind": "bank"}},
                            "approval": {"id": "call_setup_1", "approved": True},
                        }
                    ],
                },
            ],
        }
        second = authed_client.post("/api/chat", json=approval)
        assert second.status_code == 200
        assert "Saved." in second.text

    row = await demo_ledger.fetchrow("SELECT kind FROM accounts WHERE name = 'Gate savings'")
    assert row is not None and row["kind"] == "bank"
    assert (
        await demo_ledger.fetchval("SELECT count(*) FROM accounts WHERE name = 'Client injected'")
        == 0
    )


async def test_a_failed_mark_cleanup_does_not_turn_a_saved_setup_into_a_failure(
    monkeypatch, demo_ledger
):
    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage

    from fin_agent.agent import toolset
    from fin_agent.agent.deps import AgentDeps
    from fin_agent.agent.write_models import AccountSetup

    await _flagged_thread("thread-mark-cleanup")

    async def broken(names):
        raise RuntimeError("the connection dropped after the write")

    monkeypatch.setattr(toolset, "_clear_confirmed_marks", broken)
    deps = AgentDeps(conversation_id="thread-mark-cleanup", flagged=True)
    ctx = RunContext(deps=deps, model=None, usage=RunUsage(), tool_call_approved=True)

    result = await toolset.update_setup(
        ctx, entity="account", action="add", data=AccountSetup(name="Mark bank")
    )
    # The write committed; telling the model it failed would invite a duplicate retry.
    assert result.return_value["status"] == "saved"
    assert await demo_ledger.fetchval("SELECT count(*) FROM accounts WHERE name = 'Mark bank'") == 1


async def test_a_duplicate_approval_post_replays_and_never_writes_twice(authed_client, demo_ledger):
    thread_id = "thread-approval-twice"
    first = await _first_turn(authed_client, thread_id, "client-approve-2")
    body = _approval_body(thread_id, first)
    with authed_client.app.state.agent.override(model=streaming_model(_preference_script())):
        first_answer = authed_client.post("/api/chat", json=body)
        assert first_answer.status_code == 200
        assert "Remembered." in first_answer.text
        duplicate = authed_client.post("/api/chat", json=body)
        assert duplicate.status_code == 200
        assert "Remembered." in duplicate.text

    assert await demo_ledger.fetchval("SELECT count(*) FROM preferences") == 1
    thread = authed_client.get(f"/api/threads/{thread_id}/messages").json()
    notes = [
        part["data"]["note"]
        for message in thread["messages"]
        for part in message["parts"]
        if part.get("type") == "data-ending" and isinstance(part.get("data"), dict)
    ]
    assert not any("Couldn't answer" in note for note in notes)


async def test_a_denied_approval_writes_nothing_and_the_thread_keeps_working(
    authed_client, demo_ledger
):
    thread_id = "thread-approval-deny"
    first = await _first_turn(authed_client, thread_id, "client-deny-1")
    denial = {
        "id": thread_id,
        "trigger": "submit-message",
        "messages": [
            *first["messages"],
            {
                "id": "reply-1",
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool-preference",
                        "toolCallId": "call_pref_1",
                        "state": "approval-responded",
                        "input": {"action": "remember", "text": "Never warn about rent"},
                        "approval": {"id": "call_pref_1", "approved": False},
                    }
                ],
            },
        ],
    }
    with authed_client.app.state.agent.override(model=streaming_model(_preference_script())):
        denied = authed_client.post("/api/chat", json=denial)
        assert denied.status_code == 200, denied.text

    assert await demo_ledger.fetchval("SELECT count(*) FROM preferences") == 0

    # The browser holds the settled denied part; the next message must still be stored.
    follow_up = {
        "id": thread_id,
        "trigger": "submit-message",
        "messages": [
            *first["messages"],
            {
                "id": "reply-1",
                "role": "assistant",
                "parts": [
                    {
                        "type": "tool-preference",
                        "toolCallId": "call_pref_1",
                        "state": "output-denied",
                        "input": {"action": "remember", "text": "Never warn about rent"},
                        "approval": {"id": "call_pref_1", "approved": False},
                    }
                ],
            },
            _user("client-after-deny", "ok then"),
        ],
    }
    with authed_client.app.state.agent.override(model=streaming_model(_preference_script())):
        answer = authed_client.post("/api/chat", json=follow_up)
        assert answer.status_code == 200, answer.text

    thread = authed_client.get(f"/api/threads/{thread_id}/messages").json()
    user_texts = [
        part.get("text")
        for message in thread["messages"]
        if message["role"] == "user"
        for part in message["parts"]
        if part.get("type") == "text"
    ]
    assert user_texts == ["remember this", "ok then"]
    assert await demo_ledger.fetchval("SELECT count(*) FROM preferences") == 0


# --- Turn bookkeeping ------------------------------------------------------------------------


async def test_the_staleness_reminder_counts_writes_since_the_snapshot(demo_ledger):
    from types import SimpleNamespace

    from pydantic_ai import RunContext
    from pydantic_ai.usage import RunUsage

    from fin_agent.agent.capabilities import date_and_changes
    from fin_agent.agent.deps import AgentDeps
    from fin_agent.agent.history import create_thread, ensure_history

    thread = await create_thread(demo_ledger, title="stale", thread_id="thread-stale")
    await ensure_history(demo_ledger, thread)
    deps = AgentDeps(conversation_id="thread-stale")
    ctx = RunContext(deps=deps, model=None, usage=RunUsage())

    fresh = await date_and_changes(ctx)
    assert "Nothing has changed since your snapshot" in fresh

    await demo_ledger.execute(
        "INSERT INTO audit_log (actor, group_id, table_name, row_id) "
        "VALUES ('user', 'und_stale', 'settings', '1')"
    )
    stale = await date_and_changes(ctx)
    assert "1 write" in stale
    assert "re-read before stating a balance" in stale

    plain = await date_and_changes(
        RunContext(deps=SimpleNamespace(conversation_id=None), model=None, usage=RunUsage())
    )
    assert plain.startswith("Today is ")


async def test_a_second_turn_on_one_thread_is_refused():
    from types import SimpleNamespace

    import pytest

    from fin_agent.agent.turns import TurnBusy, TurnManager

    manager = TurnManager(SimpleNamespace())
    manager.turns["thread-busy"] = SimpleNamespace(done=SimpleNamespace(is_set=lambda: False))
    with pytest.raises(TurnBusy):
        await manager.start(
            adapter=None,
            thread_id="thread-busy",
            message_id="msg-busy",
            history=[],
            deps=None,
        )


# --- Write conversion and card data ----------------------------------------------------------


async def test_rupees_become_paise_exactly_once(demo_ledger):
    from decimal import Decimal

    from fin_agent.tools import write as write_tools

    result = await write_tools.record_movement(
        demo_ledger,
        {
            "kind": "expense",
            "amount_rupees": Decimal("450.155"),
            "payee": "Swiggy",
            "category": "Food & Dining",
        },
        message_id=None,
        conversation_id=None,
    )
    paise = await demo_ledger.fetchval(
        """
        SELECT t.amount::bigint FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        WHERE m.id = $1
        """,
        result["movement_id"],
    )
    # Half up, once: 450.155 rupees is 45016 paise, and the card says the same.
    assert paise == 45_016
    assert result["card"]["amount"]["paise"] == 45_016
    assert result["summary"].startswith("₹450.16")


async def test_a_script_cannot_call_a_write_tool(demo_ledger):
    from fin_agent.agent.builder import build_agent
    from fin_agent.agent.deps import AgentDeps

    before = await demo_ledger.fetchval("SELECT count(*) FROM movements")
    code = (
        "r = await record_movement(movement={'kind': 'expense', 'amount_rupees': 10, "
        "'payee': 'Ghost payee'})\nr"
    )

    def script(messages, info: AgentInfo) -> ModelResponse:
        if _tool_answered(messages, "run_code"):
            return ModelResponse(parts=[TextPart("I can't log that.")])
        return ModelResponse(parts=[ToolCallPart("run_code", {"code": code}, "call_write_1")])

    agent = build_agent()
    with agent.override(model=FunctionModel(script)):
        result = await agent.run("log it", deps=AgentDeps(conversation_id="cnv_script_write"))

    feedback = " ".join(
        str(part.content)
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if getattr(part, "part_kind", "") == "tool-return"
    )
    assert "record_movement" in feedback
    assert await demo_ledger.fetchval("SELECT count(*) FROM movements") == before
    assert (
        await demo_ledger.fetchval("SELECT count(*) FROM payees WHERE display_name = 'Ghost payee'")
        == 0
    )


async def test_a_fractional_chart_value_is_a_retry_not_a_truncated_number(demo_ledger):
    from fin_agent.agent.builder import build_agent
    from fin_agent.agent.cards import cards_from_messages
    from fin_agent.agent.deps import AgentDeps

    code = (
        "r = await show_chart(type='bar', title='Share', unit='percent', "
        "series=[{'name': 'Food', 'points': [{'x': 'Oct', 'y': 45.6}]}])\nr"
    )

    def script(messages, info: AgentInfo) -> ModelResponse:
        if _tool_answered(messages, "run_code"):
            return ModelResponse(parts=[TextPart("Could not chart that.")])
        return ModelResponse(parts=[ToolCallPart("run_code", {"code": code}, "call_chart_1")])

    agent = build_agent()
    with agent.override(model=FunctionModel(script)):
        result = await agent.run("chart it", deps=AgentDeps(conversation_id="cnv_chart_float"))

    feedback = [
        str(part.content)
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if getattr(part, "part_kind", "") in {"retry-prompt", "tool-return"}
    ]
    assert any("whole numbers" in text for text in feedback)
    cards = cards_from_messages(result.all_messages())
    assert not [card for card in cards if card.data.get("card") == "chart"]


# --- Redaction ------------------------------------------------------------------------------


def test_redaction_covers_card_formats_and_leaves_amounts_upi_and_utr():
    from fin_agent.agent.redaction import redact_text

    for card in ("4111-1111-1111-1111", "4111 1111 1111 1111", "5500000000000004"):
        redacted = redact_text(f"card {card} please")
        assert "[redacted:credit_card]" in redacted, card
        assert card not in redacted.replace("[redacted:credit_card]", "")

    kept = redact_text("spent ₹1,23,456 on 2026-10-09 to ramesh@okaxis, UTR 123456789012")
    assert "₹1,23,456" in kept
    assert "2026-10-09" in kept
    assert "ramesh@okaxis" in kept
    assert "123456789012" in kept


async def test_a_card_number_in_the_first_message_stays_out_of_the_thread_title(
    authed_client, demo_ledger
):
    from pydantic_ai.models.test import TestModel

    body = {
        "id": "thread-title-redact",
        "trigger": "submit-message",
        "messages": [
            {
                "id": "client-title-redact",
                "role": "user",
                "parts": [{"type": "text", "text": "my card is 4111 1111 1111 1111"}],
            }
        ],
    }
    with authed_client.app.state.agent.override(
        model=TestModel(call_tools=[], custom_output_text="Noted.")
    ):
        response = authed_client.post("/api/chat", json=body)
    assert response.status_code == 200
    threads = authed_client.get("/api/threads").json()["threads"]
    title = next(thread["title"] for thread in threads if thread["id"] == "thread-title-redact")
    assert "4111" not in title
    assert "[redacted:credit_card]" in title


# --- The number check, in both directions ----------------------------------------------------


def test_a_short_scale_amount_without_a_rupee_sign_is_checked():
    # "12k" is how a model writes an amount; it used to be invisible to the check.
    assert check("Roughly 12k this month.", {"total": {"paise": 1_000_000}}) == ["12k"]
    assert check("Roughly 12k this month.", {"total": {"paise": 1_234_500}}) == []


def test_a_thousand_word_is_one_amount_not_a_bare_count():
    savings = {"monthly": {"paise": 3_000_000}}
    assert check("Rent is 30 thousand a month.", savings) == []
    assert check("Rent is 30 thousand a month.", {"monthly": {"paise": 1_000_000}}) == [
        "30 thousand"
    ]


def test_a_grouped_amount_without_a_rupee_sign_is_an_amount():
    # Indian grouping is how this product writes money; a bare integer stays a count.
    assert check("The EMI is 31,500 a month.", {"emi": {"paise": 3_150_000}}) == []
    assert check("The EMI is 31,500 a month.", {}) == ["31,500"]
    assert check("The EMI is 31,500 a month.", {}, kinds=("money", "percent")) == ["31,500"]
    assert check("I found 3 transactions.", {}, kinds=("money", "percent")) == []


def test_a_negative_amount_without_a_rupee_sign_is_checked():
    assert check("Net worth is -18,04,797.", [], scripted=[{"output": "-1804797.0"}]) == []
    assert check("Net worth is -18,04,797.", {}) == ["-18,04,797"]


def test_a_percentage_after_top_is_still_checked():
    assert check("Top 10% of your spending was food.", {}) == ["10%"]
    assert check("Your top 3 categories are rent, food, and transport.", {}) == []


def test_scales_dates_ranges_and_cards_do_not_become_new_false_positives():
    assert check("2 l of water a day", {}) == ["2"]
    assert check("The dates are 2026-10-01 to 2026-10-31.", {}) == []
    assert check("The 10-20 band is where you land.", {"band": "10-20"}) == []
    assert check("Ledger stores last 4 digits, 16-digit numbers are not kept.", {}) == []
