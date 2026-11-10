"""Wave 4 import fixes: the hand-off guarantee, the answered account, the parser's own state,
and one definition of a reversal in the driver's SQL.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace
from typing import cast

from fastapi import Request
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai_harness.subagents import DelegationTasks, SubAgents

from arno.agent.deps import AgentDeps
from arno.agent.parser import ParseReport, build_parser_agent, import_state, submit_parser_code
from arno.agent.turns import UNREAD_STATEMENT_NOTE_PREFIX
from arno.api.tasks import AnswerIn, task_answer
from arno.importers.pipeline import _resolve_parsed_account, create_import, import_payload
from arno.importers.readers import Doc
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id, undo_group
from arno.ledger.common import today
from arno.ledger.movements import (
    _effectively_reversed_ids,  # noqa: PLC2701 - the ledger's own rule
    correct_movement,
    record_expense,
)
from tests.agent_helpers import streaming_model
from tests.e2e.driver import ledger_summary
from tests.import_fixtures import SBI_PARSER, sbi_csv
from tests.invariants import check_ledger
from tests.test_agent_parser import _code, _output, _returns, _sequence

# --- 1. the upload hand-off ---------------------------------------------------------------


def _upload(client) -> dict:
    response = client.post(
        "/api/attachments",
        files={"file": ("sbi.csv", sbi_csv(), "text/csv")},
        data={"purpose": "statement"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _body(message_id: str, attachment_id: str | None, text: str | None = None) -> dict:
    parts: list[dict] = []
    if text is not None:
        parts.append({"type": "text", "text": text})
    if attachment_id is not None:
        parts.append(
            {
                "type": "data-statement",
                "data": {"attachment_id": attachment_id, "filename": "sbi.csv"},
            }
        )
    return {
        "id": "thread_unread",
        "trigger": "submit-message",
        "messageId": message_id,
        "messages": [{"id": message_id, "role": "user", "parts": parts}],
    }


def _ignoring_model(seen: list[str | None]):
    def script(messages, info: AgentInfo) -> ModelResponse:
        seen.append(info.instructions)
        return ModelResponse(parts=[TextPart("No statement came through.")])

    return streaming_model(script)


def _notes(thread: dict) -> list[str]:
    return [
        str(part["data"]["note"])
        for message in thread["messages"]
        for part in message.get("parts", [])
        if part.get("type") == "data-ending"
    ]


async def test_a_turn_that_ignores_the_statement_says_the_file_was_not_read(authed_client, ledger):
    attachment = _upload(authed_client)
    seen: list[str | None] = []

    with authed_client.app.state.agent.override(model=_ignoring_model(seen)):
        response = authed_client.post(
            "/api/chat", json=_body("msg_ignored", attachment["id"], "Import this statement")
        )

    assert response.status_code == 200, response.text
    thread = authed_client.get("/api/threads/thread_unread/messages").json()
    notes = _notes(thread)
    assert any(UNREAD_STATEMENT_NOTE_PREFIX in note for note in notes), notes
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0
    # The model still saw the file: the note is the host's, after the turn.
    assert "1 statement file" in str(seen)


async def test_the_next_turn_carries_the_unread_statement_again(authed_client, ledger):
    attachment = _upload(authed_client)
    seen: list[str | None] = []

    with authed_client.app.state.agent.override(model=_ignoring_model(seen)):
        first = authed_client.post(
            "/api/chat", json=_body("msg_unread_1", attachment["id"], "Import this statement")
        )
        # "try again" has no attachment of its own: the host keeps the unread file with the thread.
        second = authed_client.post("/api/chat", json=_body("msg_unread_2", None, "try again"))

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert "1 statement file" in str(seen[0])
    assert "1 statement file" in str(seen[1])
    thread = authed_client.get("/api/threads/thread_unread/messages").json()
    assert sum(UNREAD_STATEMENT_NOTE_PREFIX in note for note in _notes(thread)) == 2
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 0
    # The retry's own message carries no file part; it is handed over, not re-attached.
    user = [message for message in thread["messages"] if message["role"] == "user"][-1]
    assert not any(part["type"] == "data-statement" for part in user["parts"])


def _parser_agent(app):
    capability = next(
        cap for cap in app.state.agent.root_capability.capabilities if isinstance(cap, SubAgents)
    )
    return next(sub.agent for sub in capability.agents if sub.resolved_name == "parser")


def _delegating_model(seen: list[str | None] | None = None):
    calls = {"n": 0}

    def script(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if seen is not None:
            seen.append(info.instructions)
        if calls["n"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "delegate_task",
                        {
                            "agent_name": "parser",
                            "task": "Import the statement files from this turn.",
                            "background": True,
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Handed it to the parser.")])

    return streaming_model(script)


def _reporting_parser_model():
    def script(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(ParseReport(status="ready", blocking=None).model_dump_json())]
        )

    return streaming_model(script)


async def test_a_turn_that_delegates_gets_no_unread_note(authed_client, ledger):
    attachment = _upload(authed_client)
    tasks = authed_client.app.state.tasks
    before = frozenset(tasks.records)
    # No server-started turn for the parser's report: this test reads the same thread again.
    authed_client.app.state.turns.waker = None

    with (
        authed_client.app.state.agent.override(model=_delegating_model()),
        _parser_agent(authed_client.app).override(model=_reporting_parser_model()),
    ):
        response = authed_client.post(
            "/api/chat", json=_body("msg_delegated", attachment["id"], "Import this statement")
        )
        assert response.status_code == 200, response.text
        for _ in range(300):
            started = [tasks.records[key] for key in tasks.records if key not in before]
            if started and all(record.status == "finished" for record in started):
                break
            await asyncio.sleep(0.01)

    thread = authed_client.get("/api/threads/thread_unread/messages").json()
    assert not [note for note in _notes(thread) if UNREAD_STATEMENT_NOTE_PREFIX in note]
    # One upload is still one task, however the file reached the parser.
    assert len([key for key in tasks.records if key not in before]) == 1


async def test_a_parse_in_the_history_does_not_excuse_a_later_upload(authed_client, ledger):
    tasks = authed_client.app.state.tasks
    before = frozenset(tasks.records)
    authed_client.app.state.turns.waker = None
    first_file = _upload(authed_client)

    with (
        authed_client.app.state.agent.override(model=_delegating_model()),
        _parser_agent(authed_client.app).override(model=_reporting_parser_model()),
    ):
        first = authed_client.post(
            "/api/chat", json=_body("msg_first_file", first_file["id"], "Import this statement")
        )
        assert first.status_code == 200, first.text
        for _ in range(300):
            started = [tasks.records[key] for key in tasks.records if key not in before]
            if started and all(record.status == "finished" for record in started):
                break
            await asyncio.sleep(0.01)

    second_file = _upload(authed_client)
    for _ in range(400):
        if authed_client.app.state.turns.get("thread_unread") is None:
            break
        await asyncio.sleep(0.02)
    with authed_client.app.state.agent.override(model=_ignoring_model([])):
        second = authed_client.post(
            "/api/chat", json=_body("msg_second_file", second_file["id"], "Import this one too")
        )

    assert second.status_code == 200, second.text
    thread = authed_client.get("/api/threads/thread_unread/messages").json()
    notes = [note for note in _notes(thread) if UNREAD_STATEMENT_NOTE_PREFIX in note]
    # The first file's parser call is in the history; the second file is still unread.
    assert len(notes) == 1, notes


# --- 2. the answered account --------------------------------------------------------------


async def _attachment(conn, payload: bytes = sbi_csv()) -> str:
    attachment_id = await new_id(conn, "att")
    await conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'statement', 'sbi.csv', 'text/csv', $2, $3, $4)
        """,
        attachment_id,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    return attachment_id


def _request(owner: DelegationTasks) -> Request:
    # A Request double: the handler reads only `app.state.tasks`.
    return cast(Request, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(tasks=owner))))


async def test_answering_with_another_account_files_the_import_against_it(ledger):
    sbi = await create_account(
        ledger, kind="bank", name="SBI Salary", institution="SBI", last4="6789"
    )
    hdfc = await create_account(
        ledger, kind="bank", name="HDFC Savings", institution="HDFC", last4="4821"
    )
    import_id = (await create_import(ledger, attachment_id=await _attachment(ledger)))["id"]
    assert await ledger.fetchval("SELECT account_id FROM imports WHERE id = $1", import_id) is None

    owner = DelegationTasks()
    task_id = "9" * 32
    async with owner.opened():
        owner.records[task_id] = _task_record(task_id)
        await ledger.execute("UPDATE imports SET task_id = $1 WHERE id = $2", task_id, import_id)
        question = asyncio.create_task(
            owner.ask(task_id, question_id="q1", question="Which account is this statement for?")
        )
        for _ in range(200):
            if owner.pending_question(task_id) is not None:
                break
            await asyncio.sleep(0.01)
        try:
            await task_answer(
                task_id,
                AnswerIn(question_id="q1", account_id=hdfc["id"]),
                _request(owner),
                ledger,
            )
        finally:
            if not question.done():
                question.cancel()

    assert (
        await ledger.fetchval("SELECT account_id FROM imports WHERE id = $1", import_id)
        == hdfc["id"]
    )
    # The script still names SBI; the answer wins, so the rows land on HDFC.
    result = await submit_parser_code(ledger, import_ids=[import_id], code=SBI_PARSER)
    assert result["status"] == "parser_submitted", result
    payload = await import_payload(ledger, import_id)
    assert payload["status"] == "review"
    assert payload["account"]["id"] == hdfc["id"]
    assert payload["account"]["id"] != sbi["id"]


def _task_record(task_id: str):
    from pydantic_ai_harness.subagents import DelegationTask

    return DelegationTask(
        id=task_id,
        agent_name="parser",
        prompt="Import the statement.",
        conversation_id="conv_answered",
    )


async def test_a_parsed_name_cannot_overrule_the_chosen_account(ledger):
    hdfc = await create_account(ledger, kind="bank", name="HDFC Savings", institution="HDFC")
    resolved = await _resolve_parsed_account(
        ledger,
        parsed_account={"name": "SBI savings", "institution": "SBI"},
        current=hdfc,
        doc=cast(Doc, None),  # never read: `current` short-circuits the call
    )
    assert resolved is not None
    assert resolved["id"] == hdfc["id"]


# --- 4. the parser reads its own import's state -------------------------------------------


async def test_import_state_reports_status_counts_and_earlier_imports(ledger):
    attachment_id = await _attachment(ledger)
    earlier = await create_import(ledger, attachment_id=attachment_id)
    await ledger.execute(
        "UPDATE imports SET status = 'rejected', rejected_at = now() WHERE id = $1", earlier["id"]
    )
    current = await create_import(ledger, attachment_id=await _attachment(ledger))
    await ledger.execute(
        """
        INSERT INTO statement_lines
            (id, import_id, date, description_raw, amount_paise, debit_paise, credit_paise,
             content_hash)
        VALUES ($1, $2, '2026-04-01', 'UPI/DR/123456789012/SWIGGY BANGALORE/ramesh@okaxis',
                45000, 45000, 0, 'h1'),
               ($3, $2, '2026-04-05', 'NEFT CR/ACME PAYROLL/SALARY APRIL', 10000000, 0, 10000000,
                'h2')
        """,
        await new_id(ledger, "stl"),
        current["id"],
        await new_id(ledger, "stl"),
    )

    state = await import_state(cast(RunContext[AgentDeps], None), current["id"])  # deletes ctx

    assert state["status"] == "parsing"
    assert state["lines"] == 2
    assert state["debits"] == 1
    assert state["credits"] == 1
    assert state["earlier_imports"] == [
        {
            "import_id": earlier["id"],
            "status": "rejected",
            "created_at": state["earlier_imports"][0]["created_at"],
        }
    ]
    # Host facts only: a narration never comes back through this tool.
    assert "SWIGGY" not in json.dumps(state)


async def test_the_parser_script_can_read_import_state(ledger):
    import_id = (
        await create_import(ledger, attachment_id=await _attachment(ledger), account_id=None)
    )["id"]
    code = (
        "state = await import_state(import_id=" + repr(import_id) + ")\n"
        "print(state['status'], state['lines'])"
    )
    agent = build_parser_agent(_sequence(_code(code), _output(ParseReport(status="ready"))))
    result = await agent.run("Import.", deps=AgentDeps(conversation_id="conv_state"))
    assert result.output.status == "ready"
    assert "parsing 0" in _returns(result.all_messages())


# --- 5. one definition of a reversed movement in the driver's SQL -------------------------


async def test_the_drivers_spending_agrees_with_the_ledger_after_undo_of_undo(ledger):
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
