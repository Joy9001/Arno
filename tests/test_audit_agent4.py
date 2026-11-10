"""Wave 4, agent4: secrets in Hindi word order, two confirm cards, wordless cards, and cards.

Each fix is checked where it lives: the redaction table, the approval endpoint, the saved turn,
the transcript join, the carried upload's instructions, and the cards a script emits.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from arno.agent.builder import build_agent
from arno.agent.cards import cards_from_messages
from arno.agent.deps import AgentDeps
from arno.agent.history import join_text, merge_text_parts, save_records
from arno.agent.instructions import render_instructions
from arno.agent.presentation import keep_last_answer
from arno.agent.redaction import redact_text
from arno.agent.toolset import build_read_toolset
from arno.agent.turns import (
    approval_sentence,
    approval_text,
    encode_saved_turn,
    pending_approvals,
)
from arno.db.conn import connect
from tests.agent_helpers import streaming_model
from tests.import_fixtures import sbi_csv

# --- 1. A password in Hindi or Hinglish word order -------------------------------------------


@pytest.mark.parametrize(
    ("text", "secret", "kind"),
    [
        ("mera password Abc@1234 hai", "Abc@1234", "password"),
        ("password mera Abc@1234 hai", "Abc@1234", "password"),
        ("mera net banking password Xy#9 hai", "Xy#9", "password"),
        ("मेरा पासवर्ड Kaveri#2026 है", "Kaveri#2026", "password"),
        (
            "मेरा ATM PIN 4821 है और मेरा net banking password Kaveri#2026 है - याद रखना।",
            "Kaveri#2026",
            "password",
        ),
        ("my password hunter2", "hunter2", "password"),
    ],
)
def test_a_password_after_its_name_is_redacted(text: str, secret: str, kind: str):
    redacted = redact_text(text)
    assert secret not in redacted
    assert f"[redacted:{kind}]" in redacted


@pytest.mark.parametrize(
    "text",
    [
        "mera password strong hai",
        "mera password mera hai",
        "my password manager is BitWarden and it costs 300",
        "password reset link bhejo 3 din se",
        # A post-position value must carry a letter, so a date or a UTR stays prose.
        "pin 2026-11-10",
        "pin 123456789012",
    ],
)
def test_prose_about_a_password_is_left_alone(text: str):
    assert redact_text(text) == text


def test_a_card_number_that_fails_luhn_is_redacted_whole():
    """r3 R3: the Aadhaar pattern used to claim the middle twelve digits of a card."""
    redacted = redact_text("mera card number 4532 9876 5432 1098 hai")
    assert "4532" not in redacted
    assert "1098" not in redacted
    assert "[redacted:card_number]" in redacted
    assert "aadhaar" not in redacted


def test_an_aadhaar_reports_its_own_kind_and_a_long_run_is_not_one():
    assert redact_text("aadhaar 2345 6789 0123") == "aadhaar [redacted:aadhaar]"
    assert redact_text("account 123456789012") == "account 123456789012"


# --- 2. A turn with two confirm cards --------------------------------------------------------


def _user(message_id: str, text: str) -> dict[str, Any]:
    return {"id": message_id, "role": "user", "parts": [{"type": "text", "text": text}]}


def _approval_part(tool_call_id: str, *, approved: bool, tool: str, args: Any) -> dict[str, Any]:
    return {
        "type": f"tool-{tool}",
        "toolCallId": tool_call_id,
        "state": "approval-responded",
        "input": args,
        "approval": {"id": tool_call_id, "approved": approved},
    }


async def _flagged_thread(conn: Any, thread_id: str) -> None:
    await conn.execute(
        "INSERT INTO conversations (id, title, outside_text_at) VALUES ($1, 'flagged', now())"
        " ON CONFLICT (id) DO NOTHING",
        thread_id,
    )


def _two_cards() -> Any:
    """A setup sentence that proposes two lasting writes, as J02.03 did."""

    def script(messages, info: AgentInfo) -> ModelResponse:
        del info
        if not _answered(messages, "preference"):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "preference",
                        {"action": "remember", "text": "Never warn about rent", "kind": "rule"},
                        "call_rule_one",
                    ),
                    ToolCallPart(
                        "preference",
                        {
                            "action": "remember",
                            "text": "Use Hinglish with me",
                            "kind": "preference",
                        },
                        "call_rule_two",
                    ),
                ]
            )
        return ModelResponse(parts=[TextPart("Both saved.")])

    return streaming_model(script)


async def test_a_partial_answer_to_two_cards_applies_nothing_and_keeps_both(authed_client):
    """r1 N2: answering one of two cards ended the turn and lost the other. Answering both is
    the only submission the model can resume, so a partial one is refused before anything runs."""
    thread_id = "thread_two_cards_partial"
    async with connect() as conn:
        await _flagged_thread(conn, thread_id)
    first = {"id": thread_id, "trigger": "submit-message", "messages": [_user("c-1", "set up")]}
    with authed_client.app.state.agent.override(model=_two_cards()):
        assert authed_client.post("/api/chat", json=first).status_code == 200

    partial = {
        "id": thread_id,
        "trigger": "submit-message",
        "messages": [
            first["messages"][0],
            {
                "id": "reply-1",
                "role": "assistant",
                "parts": [
                    _approval_part("call_rule_one", approved=True, tool="preference", args={})
                ],
            },
        ],
    }
    with authed_client.app.state.agent.override(model=_two_cards()):
        refused = authed_client.post("/api/chat", json=partial)

    assert refused.status_code == 409, refused.text
    assert "call_rule_two" in refused.json()["detail"]
    async with connect() as conn:
        assert await conn.fetchval("SELECT count(*) FROM preferences") == 0
    thread = authed_client.get(f"/api/threads/{thread_id}/messages").json()
    pending = [
        part["toolCallId"]
        for message in thread["messages"]
        for part in message.get("parts", [])
        if part.get("state") == "approval-requested"
    ]
    assert sorted(pending) == ["call_rule_one", "call_rule_two"], pending


async def test_answering_both_cards_applies_each_once(authed_client):
    thread_id = "thread_two_cards_both"
    async with connect() as conn:
        await _flagged_thread(conn, thread_id)
    first = {"id": thread_id, "trigger": "submit-message", "messages": [_user("c-2", "set up")]}
    with authed_client.app.state.agent.override(model=_two_cards()):
        assert authed_client.post("/api/chat", json=first).status_code == 200

    both = {
        "id": thread_id,
        "trigger": "submit-message",
        "messages": [
            first["messages"][0],
            {
                "id": "reply-2",
                "role": "assistant",
                "parts": [
                    _approval_part("call_rule_one", approved=True, tool="preference", args={}),
                    _approval_part("call_rule_two", approved=True, tool="preference", args={}),
                ],
            },
        ],
    }
    with authed_client.app.state.agent.override(model=_two_cards()):
        assert authed_client.post("/api/chat", json=both).status_code == 200
        async with connect() as conn:
            assert await conn.fetchval("SELECT count(*) FROM preferences") == 2
        # The same submission again is a duplicate tap: it replays and writes nothing.
        assert authed_client.post("/api/chat", json=both).status_code == 200
    async with connect() as conn:
        assert await conn.fetchval("SELECT count(*) FROM preferences") == 2


async def test_a_stale_card_id_is_refused_with_words(authed_client):
    thread_id = "thread_two_cards_stale"
    async with connect() as conn:
        await _flagged_thread(conn, thread_id)
    first = {"id": thread_id, "trigger": "submit-message", "messages": [_user("c-3", "set up")]}
    with authed_client.app.state.agent.override(model=_two_cards()):
        assert authed_client.post("/api/chat", json=first).status_code == 200
    stale = {
        "id": thread_id,
        "trigger": "submit-message",
        "messages": [
            first["messages"][0],
            {
                "id": "reply-3",
                "role": "assistant",
                "parts": [
                    _approval_part("call_rule_one", approved=True, tool="preference", args={}),
                    _approval_part("call_rule_two", approved=True, tool="preference", args={}),
                    _approval_part("call_gone", approved=True, tool="preference", args={}),
                ],
            },
        ],
    }
    with authed_client.app.state.agent.override(model=_two_cards()):
        refused = authed_client.post("/api/chat", json=stale)
    assert refused.status_code == 409, refused.text
    assert "call_gone" in refused.json()["detail"]


def test_pending_approvals_reads_the_last_card_turn():
    response = ModelResponse(
        parts=[
            ToolCallPart("preference", {"action": "remember", "text": "a"}, "call_a"),
            ToolCallPart("preference", {"action": "remember", "text": "b"}, "call_b"),
        ]
    )
    waiting = pending_approvals([response], {"call_a"})
    assert [call.tool_call_id for call in waiting] == ["call_b"]


# --- 3. A confirm card always arrives with a sentence ----------------------------------------


def _floor_sentence_script(text: str | None):
    def script(messages, info: AgentInfo) -> ModelResponse:
        del info
        if not _answered(messages, "update_setup"):
            parts: list[Any] = []
            if text:
                parts.append(TextPart(text))
            parts.append(
                ToolCallPart(
                    "update_setup",
                    {"entity": "settings", "action": "update", "data": {"floor_rupees": 30000}},
                    "call_floor_1",
                )
            )
            return ModelResponse(parts=parts)
        return ModelResponse(parts=[TextPart("Saved.")])

    return streaming_model(script)


async def test_a_wordless_confirm_card_gets_one_plain_sentence(authed_client):
    """r1 F-4 / r3 R4: the model kept writing no text, so the host writes it from the arguments."""
    thread_id = "thread_wordless_card"
    async with connect() as conn:
        await _flagged_thread(conn, thread_id)
    body = {
        "id": thread_id,
        "trigger": "submit-message",
        "messages": [_user("c-4", "My floor is 30000.")],
    }
    with authed_client.app.state.agent.override(model=_floor_sentence_script(None)):
        response = authed_client.post("/api/chat", json=body)
    assert response.status_code == 200
    assert "Approve setting your floor to ₹30,000." in response.text

    thread = authed_client.get(f"/api/threads/{thread_id}/messages").json()
    texts = [
        part["text"]
        for message in thread["messages"]
        for part in message.get("parts", [])
        if part.get("type") == "text"
    ]
    assert "Approve setting your floor to ₹30,000." in texts, texts


async def test_a_sentence_the_model_wrote_is_not_replaced(authed_client):
    thread_id = "thread_written_card"
    async with connect() as conn:
        await _flagged_thread(conn, thread_id)
    body = {
        "id": thread_id,
        "trigger": "submit-message",
        "messages": [_user("c-5", "My floor is 30000.")],
    }
    with authed_client.app.state.agent.override(
        model=_floor_sentence_script("This sets your floor; approve it.")
    ):
        assert authed_client.post("/api/chat", json=body).status_code == 200
    thread = authed_client.get(f"/api/threads/{thread_id}/messages").json()
    texts = [
        part["text"]
        for message in thread["messages"]
        for part in message.get("parts", [])
        if part.get("type") == "text"
    ]
    assert "This sets your floor; approve it." in texts
    assert not [text for text in texts if text.startswith("Approve setting your floor")]


@pytest.mark.parametrize(
    ("tool_name", "arguments", "sentence"),
    [
        (
            "preference",
            {"action": "remember", "text": "never categorize Uber as travel.", "kind": "rule"},
            "Approve saving this rule: never categorize Uber as travel.",
        ),
        (
            "preference",
            {"action": "forget", "text": "rent is essential"},
            "Approve forgetting this: rent is essential.",
        ),
        (
            "update_setup",
            {"entity": "settings", "action": "update", "data": {"floor_rupees": 30000}},
            "Approve setting your floor to ₹30,000.",
        ),
        (
            "update_setup",
            {
                "entity": "balance",
                "action": "update",
                "data": {"account": "Slice", "balance_rupees": -500},
            },
            "Approve setting the Slice balance to -₹500.",
        ),
        (
            "update_setup",
            {"entity": "account", "action": "add", "data": {"name": "HDFC Salary"}},
            "Approve adding the account HDFC Salary.",
        ),
    ],
)
def test_a_confirm_sentence_comes_from_the_arguments(
    tool_name: str, arguments: dict[str, Any], sentence: str
):
    assert approval_sentence(tool_name, arguments) == sentence


def test_a_model_that_asked_a_question_keeps_its_words():
    call = ToolCallPart("preference", {"action": "remember", "text": "x"}, "call_x")
    from pydantic_ai.messages import DeferredToolRequests

    requests = DeferredToolRequests(approvals=[call])
    assert approval_text(requests, ModelResponse(parts=[TextPart("Shall I save this?")])) is None
    assert approval_text(requests, ModelResponse(parts=[])) == "Approve saving this memory: x."
    assert approval_text("plain reply", ModelResponse(parts=[])) is None


# --- 4. Two text parts are two paragraphs -----------------------------------------------------


def test_two_text_parts_in_a_reply_are_a_paragraph_apart():
    assert join_text(["Done.", "Next."]) == "Done.\n\nNext."
    merged = merge_text_parts(
        [
            {"type": "text", "text": "Done."},
            {"type": "text", "text": "Next."},
            {"type": "data-card", "data": {"card": "answer"}},
        ]
    )
    assert [part["type"] for part in merged] == ["text", "data-card"]
    assert merged[0]["text"] == "Done.\n\nNext."


async def test_the_saved_reply_and_replay_separate_text_parts(demo_ledger):
    await demo_ledger.execute(
        "INSERT INTO conversations (id, title) VALUES ('thread_two_texts', 'x')"
        " ON CONFLICT (id) DO NOTHING"
    )
    await save_records(
        demo_ledger,
        thread_id="thread_two_texts",
        run_id="run_two_texts",
        ui_message_id="client-two-texts",
        history=[],
        turn_messages=[
            ModelResponse(parts=[TextPart("The import is read."), TextPart("Next up.")])
        ],
        ending="complete",
        reply_id="msg_two_texts",
    )
    row = await demo_ledger.fetchrow(
        "SELECT body_text, content FROM messages WHERE client_id = 'msg_two_texts'"
    )
    assert row["body_text"] == "The import is read.\n\nNext up."
    assert row["content"]["parts"][0]["text"] == "The import is read.\n\nNext up."
    chunks = encode_saved_turn(row["content"])
    deltas = [chunk for chunk in chunks if '"type":"text-delta"' in chunk]
    assert len(deltas) == 1, deltas


# --- 5. The retried upload carries its name ---------------------------------------------------


async def test_the_retry_turn_names_the_file_it_carries(authed_client):
    """r3 R1: the retry turn had the file in deps but the block never named it."""
    from arno.agent.turns import UNREAD_STATEMENT_NOTE_PREFIX

    upload = authed_client.post(
        "/api/attachments",
        files={"file": ("sbi.csv", sbi_csv(), "text/csv")},
        data={"purpose": "statement"},
    )
    assert upload.status_code == 201, upload.text
    attachment = upload.json()

    def body(message_id: str, attachment_id: str | None, text: str) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if attachment_id:
            parts.append(
                {
                    "type": "data-statement",
                    "data": {"attachment_id": attachment_id, "filename": "sbi.csv"},
                }
            )
        return {
            "id": "thread_retry_name",
            "trigger": "submit-message",
            "messageId": message_id,
            "messages": [{"id": message_id, "role": "user", "parts": parts}],
        }

    seen: list[str] = []

    def script(messages, info: AgentInfo) -> ModelResponse:
        seen.append(str(info.instructions))
        return ModelResponse(parts=[TextPart("No statement came through.")])

    model = streaming_model(script)
    with authed_client.app.state.agent.override(model=model):
        first = authed_client.post(
            "/api/chat", json=body("msg_retry_1", attachment["id"], "Import")
        )
        second = authed_client.post(
            "/api/chat", json=body("msg_retry_2", None, "Sorry - read the statement I attached")
        )
    assert first.status_code == 200 and second.status_code == 200
    assert "sbi.csv" in seen[1], seen[1]
    thread = authed_client.get("/api/threads/thread_retry_name/messages").json()
    notes = [
        str(part["data"]["note"])
        for message in thread["messages"]
        for part in message.get("parts", [])
        if part.get("type") == "data-ending"
    ]
    assert any(UNREAD_STATEMENT_NOTE_PREFIX in note and "sbi.csv" in note for note in notes), notes


# --- 6 and 8. The rules and the tool descriptions ---------------------------------------------


def test_the_prompt_carries_the_new_rules():
    prompt = render_instructions(["Groceries"])
    for rule in (
        "never name a day of the week yourself",
        "Before you state an import's status, rows, or counts, read it",
        "inventing a placeholder like",
    ):
        assert rule in prompt, rule


def test_each_new_rule_has_an_eval_case():
    from evals.cases import build_cases

    names = {case.name for case in build_cases()}
    assert {"ans_next_dates_weekday", "hn_import_state", "wr_no_invented_payee"} <= names


async def test_the_tool_descriptions_name_their_shapes():
    from pydantic_ai import RunContext
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    from arno.agent.toolset import build_write_toolset

    ctx = RunContext(
        deps=AgentDeps(conversation_id="cnv_desc"), model=TestModel(), usage=RunUsage()
    )
    reads = await build_read_toolset().get_tools(ctx)
    writes = await build_write_toolset().get_tools(ctx)
    assert "never a list" in str(reads["summarize_spending"].tool_def.description)
    assert "credit is -500" in str(writes["update_setup"].tool_def.description)
    assert "Unknown shop" in str(writes["record_movement"].tool_def.description)


# --- 7. One card per answer, not one per calculation ------------------------------------------


def test_a_script_that_read_the_same_data_twice_shows_one_card():
    first = {
        "type": "data-card",
        "data": {"card": "result", "result_id": "res_1", "snapshot": {"kind": "spending"}},
    }
    second = {
        "type": "data-card",
        "data": {"card": "result", "result_id": "res_2", "snapshot": {"kind": "spending"}},
    }
    other = {
        "type": "data-card",
        "data": {"card": "result", "result_id": "res_3", "snapshot": {"kind": "context"}},
    }
    kept = keep_last_answer([first, second, other])
    assert [card["data"]["snapshot"]["kind"] for card in kept] == ["spending", "context"]


async def test_a_script_that_calls_amortize_emits_no_schedule_card(demo_ledger):
    def script(messages, info: AgentInfo) -> ModelResponse:
        del info
        if not _answered(messages, "run_code"):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "base = await amortize(loan='Home loan')\n"
                                "for months in (120, 180, 240):\n"
                                "    await amortize(loan='Home loan', "
                                "prepay={'amount_paise': 5000000})\n"
                                "await present(display={'_type': 'Summary', "
                                "'result_id': base['ui_results'][0]['id']})\n"
                            )
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Here it is.")])

    agent = build_agent()
    with agent.override(model=FunctionModel(script)):
        result = await agent.run("prepay", deps=AgentDeps(conversation_id="cnv_cards"))

    cards = cards_from_messages(result.all_messages())
    kinds = [card.data.get("card") for card in cards if isinstance(card.data, dict)]
    assert "amortize" not in kinds, kinds
    assert kinds.count("answer") == 1, kinds


def _answered(messages: list[Any], name: str) -> bool:
    for message in messages:
        for part in getattr(message, "parts", []):
            if getattr(part, "part_kind", "") in {"tool-return", "retry-prompt"} and (
                getattr(part, "tool_name", None) == name
            ):
                return True
    return False
