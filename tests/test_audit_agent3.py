"""Wave 3, agent3: the prompt map's new rules and the code halves they need.

Each fix is checked where it lives: a sandbox error that names its helpers, a composed answer
that replaces an earlier one, a scheduled answer that carries the number note, a declined
approval that tells the model to stop, and a transcript that keeps the answer rather than the
attempt it abandoned.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from evals.graders import grade_writes
from evals.models import EvalOutput
from fin_agent.agent.builder import build_agent
from fin_agent.agent.capabilities import SANDBOX_HELPERS, SANDBOX_MODULES, sandbox_help
from fin_agent.agent.cards import cards_from_messages
from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.history import save_records
from fin_agent.agent.instructions import render_instructions
from fin_agent.agent.presentation import PresentationError, keep_last_answer
from fin_agent.agent.toolset import show_chart
from fin_agent.db.conn import connect
from fin_agent.jobs.questions import run_scheduled_question
from tests.agent_helpers import streaming_model

# --- A failed script says what it could have used ---------------------------------------------


async def test_a_script_error_names_its_helpers_and_modules(demo_ledger):
    """e1 F10 / e3 F14: a tool name or a missing module no longer wastes a turn."""

    def script(messages, info: AgentInfo) -> ModelResponse:
        del info
        if not _answered(messages, "run_code"):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {"code": "update_setup(entity='settings', action='update')"},
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("asked")])

    agent = build_agent()
    with agent.override(model=FunctionModel(script)):
        result = await agent.run("set my floor", deps=AgentDeps(conversation_id="cnv_hint"))

    retry = [
        str(part.content)
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if getattr(part, "tool_name", None) == "run_code"
        and getattr(part, "part_kind", "") == "tool-return"
    ]
    assert retry, "the script must have run"
    assert "unresolved-reference" in retry[0]
    assert "query_movements" in retry[0], retry[0]
    assert "never a write tool" in retry[0]
    assert "json, datetime, math" in retry[0]


def test_the_sandbox_hint_lists_the_modules_it_really_has():
    assert "calendar" not in SANDBOX_MODULES
    assert "statistics" not in SANDBOX_MODULES
    assert {"json", "datetime", "math", "decimal"} <= set(SANDBOX_MODULES)


async def test_the_helper_list_matches_the_read_toolset():
    from fin_agent.agent.toolset import build_read_toolset

    ctx = RunContext(deps=AgentDeps(conversation_id="cnv_helpers"), model=None, usage=None)
    names = sorted(await build_read_toolset().get_tools(ctx))
    assert names == sorted(SANDBOX_HELPERS)


def test_the_sandbox_hint_names_the_read_helpers_only():
    help_text = sandbox_help()
    assert "summarize_spending" in help_text
    assert "record_movement" not in help_text
    assert "update_setup" not in help_text


# --- One composed answer per reply -------------------------------------------------------------


def test_a_second_composed_answer_replaces_the_first():
    cards = [
        {"type": "data-card", "data": {"card": "spending", "total": 1}},
        {"type": "data-card", "data": {"card": "answer", "results": ["first"]}},
        {"type": "data-card", "data": {"card": "answer", "results": ["second"]}},
    ]
    kept = keep_last_answer(cards)
    assert [card["data"].get("results") for card in kept if card["data"]["card"] == "answer"] == [
        ["second"]
    ]
    assert any(card["data"]["card"] == "spending" for card in kept)


async def test_one_answer_card_reaches_the_transcript(demo_ledger):
    """e1 F6: two `present` calls in one reply used to render two answer cards."""

    def script(messages, info: AgentInfo) -> ModelResponse:
        del info
        if not _answered(messages, "run_code"):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "spending = await summarize_spending(group_by='category', "
                                "period='2026-10')\n"
                                "rid = spending['ui_results'][0]['id']\n"
                                "await present(display={'_type': 'Summary', 'result_id': rid})\n"
                                "await present(display={'_type': 'Table', 'result_id': rid})\n"
                            )
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Here it is.")])

    agent = build_agent()
    with agent.override(model=FunctionModel(script)):
        result = await agent.run("october", deps=AgentDeps(conversation_id="cnv_one_answer"))
    turn_messages = result.all_messages()

    cards = cards_from_messages(turn_messages)
    answers = [card for card in keep_last_answer(cards) if card.data.get("card") == "answer"]
    assert len(answers) == 1
    assert answers[0].data["display"]["_type"] == "Table"

    # The saved transcript shows one composed answer too, so a reload cannot bring the first back.
    thread = await _thread(demo_ledger, "thread-one-answer")
    await save_records(
        demo_ledger,
        thread_id=str(thread),
        run_id="run_one_answer",
        ui_message_id="client-one-answer",
        history=[],
        turn_messages=turn_messages,
        ending="complete",
        reply_id="msg_one_answer",
    )
    row = await demo_ledger.fetchrow(
        "SELECT content FROM messages WHERE client_id = $1", "msg_one_answer"
    )
    saved = [
        part
        for part in row["content"]["parts"]
        if part.get("type") == "data-card" and part["data"].get("card") == "answer"
    ]
    assert len(saved) == 1
    assert saved[0]["data"]["display"]["_type"] == "Table"


def test_the_answer_tree_error_shows_one_valid_answer(demo_ledger):
    """e6 F8: the error names a shape the caller can copy."""
    from fin_agent.agent.presentation import ResultRegistry, build_answer

    with pytest.raises(PresentationError) as excinfo:
        build_answer(ResultRegistry(), {"_type": "Stack", "children": [{"result_id": "res_1"}]})
    assert '"result_id": "res_1"' in str(excinfo.value)


async def test_the_chart_series_error_shows_one_valid_series(demo_ledger):
    """e6 F8: a series with no points gets a copyable example."""
    with pytest.raises(Exception) as excinfo:
        await show_chart(
            _run_ctx(demo_ledger),
            type="bar",
            title="Rent",
            series=[{"name": "Rent", "value": 2_800_000}],
        )
    assert '"points"' in str(excinfo.value)


# --- A scheduled answer carries the number note -----------------------------------------------


async def test_a_scheduled_answer_carries_the_untraced_note(demo_ledger):
    """e4 F4: a job answer is prose too, so it gets the same note a chat turn carries."""
    question = await _question(demo_ledger)

    def script(messages, info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[TextPart("You spent ₹11,000 on food.")])

    agent = build_agent(read_only=True)
    with agent.override(model=streaming_model(script)):
        result = await run_scheduled_question(
            demo_ledger, question_id=str(question), period="2026-11-10", agent=agent
        )

    body = await demo_ledger.fetchval(
        "SELECT body FROM notifications WHERE id = $1", result["notification_id"]
    )
    assert "You spent ₹11,000 on food." in body
    assert "Not verified against a ledger row: ₹11,000" in body


async def test_a_traceable_scheduled_answer_gets_no_note(demo_ledger):
    question = await _question(demo_ledger, "sq_noteless")

    def script(messages, info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[TextPart("Nothing was spent last week.")])

    agent = build_agent(read_only=True)
    with agent.override(model=streaming_model(script)):
        result = await run_scheduled_question(
            demo_ledger, question_id=str(question), period="2026-11-10", agent=agent
        )

    body = await demo_ledger.fetchval(
        "SELECT body FROM notifications WHERE id = $1", result["notification_id"]
    )
    assert "Not verified" not in body


# --- A declined approval ends the attempt -----------------------------------------------------


async def test_a_declined_approval_reaches_the_model_as_a_stop(authed_client, demo_ledger):
    """e6 F5: the tool result the model sees says the request is closed."""
    thread_id = "thread-denied-3"
    async with connect() as conn:
        await conn.execute(
            "INSERT INTO conversations (id, title, outside_text_at) VALUES ($1, 'flagged', now())",
            thread_id,
        )
    first = {
        "id": thread_id,
        "trigger": "submit-message",
        "messages": [_user("client-denied-3", "remember this")],
    }
    with authed_client.app.state.agent.override(model=streaming_model(_preference_script())):
        assert authed_client.post("/api/chat", json=first).status_code == 200

    seen: list[str] = []

    def script(messages, info: AgentInfo) -> ModelResponse:
        del info
        for message in messages:
            for part in getattr(message, "parts", []):
                if getattr(part, "part_kind", "") == "tool-return":
                    seen.append(str(getattr(part, "content", "")))
        return ModelResponse(parts=[TextPart("Okay.")])

    denial = {
        "id": thread_id,
        "trigger": "submit-message",
        "messages": [
            *first["messages"],
            {
                "id": "reply-denied-3",
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
    with authed_client.app.state.agent.override(model=streaming_model(script)):
        assert authed_client.post("/api/chat", json=denial).status_code == 200

    assert await demo_ledger.fetchval("SELECT count(*) FROM preferences") == 0
    assert any(
        "declined this change" in text and "don't offer it again" in text for text in seen
    ), seen


# --- The transcript keeps the answer, not the attempt -----------------------------------------


async def test_a_retried_response_text_stays_out_of_the_transcript(demo_ledger):
    """e1 F5: two versions of one answer used to be stored back to back."""
    thread = await _thread(demo_ledger, "thread-retried")
    first_call = ToolCallPart("run_code", {"code": "print(1)"}, "call_retried_1")
    draft = ModelResponse(parts=[TextPart("Nothing rent-related shows for October."), first_call])
    retry = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="run_code",
                content="Runtime error:\nboom",
                tool_call_id="call_retried_1",
                outcome="retried",
            )
        ]
    )
    answer = ModelResponse(parts=[TextPart("No rent went out of SBI Salary in October.")])
    await save_records(
        demo_ledger,
        thread_id=str(thread),
        run_id="run_retried",
        ui_message_id="client-retried",
        history=[],
        turn_messages=[draft, retry, answer],
        ending="complete",
        reply_id="msg_retried",
    )

    row = await demo_ledger.fetchrow(
        "SELECT body_text, content FROM messages WHERE client_id = $1", "msg_retried"
    )
    assert row["body_text"] == "No rent went out of SBI Salary in October."
    texts = [part["text"] for part in row["content"]["parts"] if part.get("type") == "text"]
    assert texts == ["No rent went out of SBI Salary in October."]


async def test_a_response_whose_tool_call_succeeded_keeps_its_text(demo_ledger):
    """The sentence before a tool that worked is real context, not a draft."""
    thread = await _thread(demo_ledger, "thread-kept")
    call = ToolCallPart("run_code", {"code": "print(1)"}, "call_kept_1")
    first = ModelResponse(parts=[TextPart("Let me check October."), call])
    answered = ModelRequest(parts=[_return("run_code", "call_kept_1", {"output": "1\n"})])
    answer = ModelResponse(parts=[TextPart("You spent nothing in October.")])
    await save_records(
        demo_ledger,
        thread_id=str(thread),
        run_id="run_kept",
        ui_message_id="client-kept",
        history=[],
        turn_messages=[first, answered, answer],
        ending="complete",
        reply_id="msg_kept",
    )

    body = await demo_ledger.fetchval(
        "SELECT body_text FROM messages WHERE client_id = $1", "msg_kept"
    )
    assert body == "Let me check October.\n\nYou spent nothing in October."


# --- The prompt map's fixed rules -------------------------------------------------------------


def test_the_prompt_carries_each_wave_three_rule():
    prompt = render_instructions(["Groceries"])
    for rule in (
        "is a request and not a report",
        "a schedule's amount is never the amount received",
        "ask which one; never widen it",
        "net figures after a refund included",
        "say in one sentence what will change",
        "don't offer it again unless they ask",
        "Never write an example amount",
        "before stating a current balance or",
        "Print rupees(paise) beside any figure",
        "The month-end clamp is automatic",
        "not marked as paid",
        "needs its allocation",
        "revert that setup too",
        "the card and the reply name it",
    ):
        assert rule in prompt, rule


def test_each_new_rule_has_an_eval_case():
    from evals.cases import build_cases

    names = {case.name for case in build_cases()}
    assert {
        "nm_own_transfer",
        "wr_income_no_amount",
        "wr_account_ambiguous",
        "wr_default_account_named",
        "wr_split_refund_asks",
        "wr_forget_reverts_setup",
        "ans_food_difference",
    } <= names


async def test_the_no_write_grader_rejects_a_movement(demo_ledger):
    output = EvalOutput(
        reply="Logged ₹500 for chai.",
        started_at=datetime.now(UTC).isoformat(),
    )
    results = await grade_writes(demo_ledger, output, {"check": "no_write", "must_say": ["which"]})
    by_name = {result.name: result for result in results}
    assert "no_write" in by_name
    assert by_name["no_write"].passed
    assert not by_name["must_say"].passed


# --- Helpers ----------------------------------------------------------------------------------


def _answered(messages: list[Any], name: str) -> bool:
    for message in messages:
        for part in getattr(message, "parts", []):
            if getattr(part, "part_kind", "") in {"tool-return", "retry-prompt"} and (
                getattr(part, "tool_name", None) == name
            ):
                return True
    return False


def _return(name: str, call_id: str, content: Any) -> Any:
    from pydantic_ai.messages import ToolReturnPart

    return ToolReturnPart(tool_name=name, content=content, tool_call_id=call_id)


def _user(message_id: str, text: str) -> dict[str, Any]:
    return {"id": message_id, "role": "user", "parts": [{"type": "text", "text": text}]}


def _preference_script(text: str = "Never warn about rent"):
    def script(messages, info: AgentInfo) -> ModelResponse:
        del info
        if not _answered(messages, "preference"):
            return ModelResponse(
                parts=[
                    ToolCallPart("preference", {"action": "remember", "text": text}, "call_pref_1")
                ]
            )
        return ModelResponse(parts=[TextPart("Remembered.")])

    return script


def _run_ctx(conn: Any) -> Any:
    from types import SimpleNamespace

    from fin_agent.agent.presentation import ResultRegistry

    return SimpleNamespace(
        deps=SimpleNamespace(
            presentation=ResultRegistry(),
            conversation_id="cnv_chart",
            flagged=False,
        )
    )


async def _question(conn: Any, question_id: str = "sq_number_note") -> str:
    await conn.execute(
        """
        INSERT INTO scheduled_questions (id, prompt, rule, active, spend_limit_cents)
        VALUES ($1, 'What did I spend on food last week?', 'FREQ=WEEKLY;BYDAY=MO', true, 100)
        ON CONFLICT (id) DO NOTHING
        """,
        question_id,
    )
    return question_id


async def _thread(conn: Any, thread_id: str) -> str:
    await conn.execute(
        """
        INSERT INTO conversations (id, title) VALUES ($1, 'retried')
        ON CONFLICT (id) DO NOTHING
        """,
        thread_id,
    )
    return thread_id
