"""The parser subagent: read, replay or repair, gate, review, and resume."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_harness.step_persistence import PostgresStepStore
from pydantic_ai_harness.subagents import DelegationReports, DelegationTasks, SubAgents

from fin_agent.agent.builder import build_agent
from fin_agent.agent.capabilities import MODEL_SETTINGS
from fin_agent.agent.cards import cards_for_transcript, cards_from_messages
from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.parser import ImportReport, ParseReport, build_parser_agent, open_imports
from fin_agent.importers.commit import commit_import
from fin_agent.importers.passwords import PASSWORDS
from fin_agent.importers.pipeline import create_import, import_payload
from fin_agent.importers.readers import DocError
from fin_agent.ledger.accounts import create_account
from fin_agent.ledger.audit import new_id
from tests.agent_helpers import streaming_model
from tests.import_fixtures import SBI_PARSER, UNION_PARSER, sbi_csv, union_tsv


async def _att(conn, payload: bytes, name: str = "statement.csv") -> str:
    import hashlib

    attachment_id = await new_id(conn, "att")
    await conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'statement', $2, 'text/csv', $3, $4, $5)
        """,
        attachment_id,
        name,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    return attachment_id


async def _sbi_account(conn) -> dict:
    return await create_account(
        conn,
        kind="bank",
        name="SBI savings",
        institution="SBI",
        last4="6789",
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )


async def _union_account(conn) -> dict:
    return await create_account(
        conn,
        kind="bank",
        name="Union Bank",
        institution="Union Bank of India",
        last4="4321",
        opening_balance_paise=10000000,
        opening_balance_as_of=date(2026, 3, 31),
    )


def _code(code: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart("run_code", {"code": code})])


def _output(report: ParseReport):
    def respond(info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, report.model_dump())])

    return respond


def _sequence(*steps: Any) -> FunctionModel:
    calls = {"n": 0}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages
        index = min(calls["n"], len(steps) - 1)
        calls["n"] += 1
        step = steps[index]
        return step(info) if callable(step) else step

    return FunctionModel(respond)


def _streaming(*steps: Any):
    """A scripted model that also streams, as a subagent run requires."""
    calls = {"n": 0}

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages
        index = min(calls["n"], len(steps) - 1)
        calls["n"] += 1
        step = steps[index]
        return step(info) if callable(step) else step

    return streaming_model(script)


def _returns(messages: list[ModelMessage]) -> str:
    chunks: list[str] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                chunks.append(str(part.content))
    return "\n".join(chunks)


async def _run(agent, deps: AgentDeps, prompt: str = "Import the statement."):
    return await agent.run(prompt, deps=deps)


async def test_a_statement_parses_to_review_and_keeps_rows_on_the_host(ledger):
    account = await _sbi_account(ledger)
    attachment_id = await _att(ledger, sbi_csv())
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    import_id = row["id"]

    steps = [
        _code("print(await open_imports())"),
        _code(f"print(await saved_parser(import_id={import_id!r}))"),
        _code(f"print(await submit_parser(import_ids=[{import_id!r}], code={SBI_PARSER!r}))"),
        _output(
            ParseReport(
                status="ready",
                imports=[ImportReport(import_id=import_id, status="review", rows=3)],
            )
        ),
    ]
    agent = build_parser_agent(model=_sequence(*steps))
    result = await _run(
        agent,
        AgentDeps(conversation_id="conv_parse", statement_attachments=(attachment_id,)),
    )

    assert result.output.status == "ready", result.output
    detail = await import_payload(ledger, import_id)
    assert detail["status"] == "review", detail
    assert detail["check"] == "VERIFIED"
    assert detail["counts"]["new"] == 3
    assert len(detail["lines"]) == 3
    assert "parser_submitted" in _returns(result.all_messages())
    assert await ledger.fetchval("SELECT count(*) FROM jobs WHERE kind = 'import_statement'") == 0


async def test_a_second_statement_replays_the_saved_parser_cleanly(ledger):
    account = await _sbi_account(ledger)
    first_attachment = await _att(ledger, sbi_csv(), "april.csv")
    first = await create_import(ledger, attachment_id=first_attachment, account_id=account["id"])
    agent = build_parser_agent(
        model=_sequence(
            _code(f"print(await submit_parser(import_ids=[{first['id']!r}], code={SBI_PARSER!r}))"),
            _output(
                ParseReport(
                    status="ready",
                    imports=[ImportReport(import_id=first["id"], status="review", rows=3)],
                )
            ),
        )
    )
    await _run(agent, AgentDeps(conversation_id="conv_replay"))
    await commit_import(ledger, first["id"])

    second_attachment = await _att(
        ledger,
        sbi_csv().replace(b"SWIGGY BANGALORE", b"SWIGGY BANGALORE TWO"),
        "may.csv",
    )
    second = await create_import(ledger, attachment_id=second_attachment, account_id=account["id"])
    agent = build_parser_agent(
        model=_sequence(
            _code(f"print(await saved_parser(import_id={second['id']!r}))"),
            _code(
                f"print(await submit_parser(import_ids=[{second['id']!r}], code={SBI_PARSER!r}))"
            ),
            _output(
                ParseReport(
                    status="ready",
                    imports=[ImportReport(import_id=second["id"], status="review", rows=3)],
                )
            ),
        )
    )
    result = await _run(agent, AgentDeps(conversation_id="conv_replay"))

    assert result.output.status == "ready"
    detail = await import_payload(ledger, second["id"])
    assert detail["status"] == "review", detail
    assert detail["check"] == "VERIFIED"
    text = _returns(result.all_messages())
    assert "version" in text
    assert "checked" in text
    assert "passed" in text


async def test_a_gate_failure_is_repaired_and_resubmitted(ledger):
    account = await _sbi_account(ledger)
    attachment_id = await _att(ledger, sbi_csv())
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    broken = SBI_PARSER.replace("'closing': closing", "'closing': 1")
    agent = build_parser_agent(
        model=_sequence(
            _code(f"print(await submit_parser(import_ids=[{row['id']!r}], code={broken!r}))"),
            _code(f"print(await submit_parser(import_ids=[{row['id']!r}], code={SBI_PARSER!r}))"),
            _output(
                ParseReport(
                    status="ready",
                    imports=[ImportReport(import_id=row["id"], status="review", rows=3)],
                )
            ),
        )
    )
    result = await _run(agent, AgentDeps(conversation_id="conv_repair"))

    assert result.output.status == "ready"
    text = _returns(result.all_messages())
    assert "'status': 'failed'" in text
    assert "'status': 'parser_submitted'" in text
    detail = await import_payload(ledger, row["id"])
    assert detail["status"] == "review", detail
    assert len(detail["lines"]) == 3


async def test_one_task_can_carry_two_banks(ledger):
    sbi = await _sbi_account(ledger)
    union = await _union_account(ledger)
    sbi_attachment = await _att(ledger, sbi_csv(), "sbi.csv")
    union_attachment = await _att(ledger, union_tsv(), "union.tsv")
    sbi_import = await create_import(ledger, attachment_id=sbi_attachment, account_id=sbi["id"])
    union_import = await create_import(
        ledger, attachment_id=union_attachment, account_id=union["id"]
    )
    agent = build_parser_agent(
        model=_sequence(
            _code("print(await open_imports())"),
            _code(
                f"print(await submit_parser(import_ids=[{sbi_import['id']!r}], "
                f"code={SBI_PARSER!r}))"
            ),
            _code(
                f"print(await submit_parser(import_ids=[{union_import['id']!r}], "
                f"code={UNION_PARSER!r}))"
            ),
            _output(
                ParseReport(
                    status="ready",
                    imports=[
                        ImportReport(import_id=sbi_import["id"], status="review", rows=3),
                        ImportReport(import_id=union_import["id"], status="review", rows=3),
                    ],
                )
            ),
        )
    )
    result = await _run(
        agent,
        AgentDeps(
            conversation_id="conv_two_banks",
            statement_attachments=(sbi_attachment, union_attachment),
        ),
    )

    assert result.output.status == "ready"
    for import_row in (sbi_import, union_import):
        detail = await import_payload(ledger, import_row["id"])
        assert detail["status"] == "review", detail
        assert len(detail["lines"]) == 3


async def test_own_account_suggestions_land_on_the_lines(ledger):
    account = await _sbi_account(ledger)
    other = await create_account(
        ledger,
        kind="bank",
        name="Axis savings",
        institution="Axis",
        last4="1111",
        opening_balance_paise=100000,
        opening_balance_as_of=date(2026, 3, 31),
    )
    attachment_id = await _att(ledger, sbi_csv())
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    agent = build_parser_agent(
        model=_sequence(
            _code(f"print(await submit_parser(import_ids=[{row['id']!r}], code={SBI_PARSER!r}))"),
            _code(
                "print(await suggest_transfers("
                f"import_id={row['id']!r}, suggestions=[{{'row': 2, "
                f"'account_id': {other['id']!r}}}]))"
            ),
            _output(
                ParseReport(
                    status="ready",
                    imports=[ImportReport(import_id=row["id"], status="review", rows=3)],
                )
            ),
        )
    )
    result = await _run(agent, AgentDeps(conversation_id="conv_suggest"))

    assert result.output.status == "ready"
    lines = await ledger.fetch(
        "SELECT match_detail FROM statement_lines WHERE import_id = $1 ORDER BY date, id",
        row["id"],
    )
    assert lines[2]["match_detail"]["suggested_transfer_account_id"] == other["id"]
    assert all("suggested_category" not in line["match_detail"] for line in lines)
    assert other["id"] in _returns(result.all_messages())


async def test_a_restart_resumes_a_cancelled_parser_from_the_step_store(ledger, tmp_path):
    account = await _sbi_account(ledger)
    attachment_id = await _att(ledger, sbi_csv())
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    import_id = row["id"]

    parser = build_parser_agent(
        model=_streaming(
            _code("print(await open_imports())"),
            _code(f"print(await wait_for_password(import_id={import_id!r}))"),
            _output(
                ParseReport(
                    status="ready",
                    imports=[ImportReport(import_id=import_id, status="review", rows=3)],
                )
            ),
        )
    )
    parent = build_agent(
        model=_sequence(
            ModelResponse(
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
            ),
            ModelResponse(parts=[TextPart("Started.")]),
        ),
        parser_agent=parser,
    )
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    try:
        store = PostgresStepStore(pool)
        first_owner = DelegationTasks(directory=tmp_path, step_store=store)
        async with first_owner.opened():
            with first_owner.bind():
                await parent.run(
                    "Import the statement.",
                    deps=AgentDeps(
                        conversation_id="conv_resume", statement_attachments=(attachment_id,)
                    ),
                    conversation_id="conv_resume",
                )
            task_id = next(iter(first_owner.records))
            record = first_owner.records[task_id]
            # The accept-time record, as a process that died before the final save would leave it.
            accept_json = (tmp_path / f"{task_id}.json").read_text(encoding="utf-8")
            history = ""
            for _ in range(200):
                snapshot = await store.latest_snapshot(
                    run_id=record.run_id, include_interrupted=True
                )
                history = str(snapshot.messages) if snapshot else ""
                if import_id in history and "open_imports" in history:
                    break
                await asyncio.sleep(0.05)
            assert import_id in history

        (tmp_path / f"{task_id}.json").write_text(accept_json, encoding="utf-8")
        second_owner = DelegationTasks(directory=tmp_path, step_store=store)
        async with second_owner.opened():
            restored = second_owner.records[task_id]
            assert restored.status == "finished"
            assert restored.outcome == "cancelled"
            assert "interrupted" in restored.output
            restored_history = str(second_owner.history(task_id))
            assert import_id in restored_history
            parent2 = build_agent(
                model=_sequence(
                    ModelResponse(
                        parts=[
                            ToolCallPart(
                                "delegate_task",
                                {
                                    "agent_name": "parser",
                                    "task": "Continue the import.",
                                    "resume": task_id,
                                },
                            )
                        ]
                    ),
                    ModelResponse(parts=[TextPart("Resumed.")]),
                ),
                parser_agent=build_parser_agent(
                    model=_streaming(
                        _output(
                            ParseReport(
                                status="ready",
                                imports=[
                                    ImportReport(import_id=import_id, status="review", rows=3)
                                ],
                            )
                        )
                    )
                ),
            )
            with second_owner.bind():
                await parent2.run(
                    "Continue.",
                    deps=AgentDeps(conversation_id="conv_resume"),
                    conversation_id="conv_resume",
                )
            assert second_owner.records[task_id].status == "finished"
            assert second_owner.records[task_id].outcome == "ok"
    finally:
        await pool.close()


async def test_the_password_never_touches_history_the_task_record_or_the_plan(
    ledger, monkeypatch, tmp_path
):
    import fin_agent.agent.parser as parser_module

    secret = "ARJU0101-unique-secret"
    real_open = parser_module.open_documents
    unlocked = {"done": False}

    def fake_open(data: bytes, password: str | None = None):
        if password == secret:
            unlocked["done"] = True
            return real_open(data, password=None)
        if unlocked["done"]:
            return real_open(data, password=None)
        raise DocError("password_required")

    monkeypatch.setattr(parser_module, "open_documents", fake_open)

    account = await _sbi_account(ledger)
    attachment_id = await _att(ledger, sbi_csv())
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    import_id = row["id"]

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
    try:
        parser = build_parser_agent(
            model=_streaming(
                ModelResponse(
                    parts=[
                        ToolCallPart("write_plan", {"items": [{"content": "Read the statement"}]})
                    ]
                ),
                _code("print(await open_imports())"),
                _code(f"print(await wait_for_password(import_id={import_id!r}))"),
                _code(
                    f"print(await submit_parser(import_ids=[{import_id!r}], code={SBI_PARSER!r}))"
                ),
                _output(
                    ParseReport(
                        status="ready",
                        imports=[ImportReport(import_id=import_id, status="review", rows=3)],
                    )
                ),
            ),
            plan_pool=pool,
        )
        parent = build_agent(
            model=_sequence(
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            "delegate_task",
                            {
                                "agent_name": "parser",
                                "task": "Import the statement files from this turn.",
                            },
                        )
                    ]
                ),
                ModelResponse(parts=[TextPart("Done.")]),
            ),
            parser_agent=parser,
        )
        store = PostgresStepStore(pool)
        owner = DelegationTasks(directory=tmp_path, step_store=store)
        async with owner.opened():

            async def inject() -> None:
                for _ in range(200):
                    status = await ledger.fetchval(
                        "SELECT status FROM imports WHERE id = $1", import_id
                    )
                    if status == "needs_password":
                        PASSWORDS.put(import_id, secret)
                        return
                    await asyncio.sleep(0.05)

            injector = asyncio.create_task(inject())
            with owner.bind():
                result = await parent.run(
                    "Import the statement.",
                    deps=AgentDeps(
                        conversation_id="conv_password", statement_attachments=(attachment_id,)
                    ),
                    conversation_id="conv_password",
                )
            await injector
            task_id = next(iter(owner.records))
            child_history = str(owner.history(task_id))
            plan = await ledger.fetch("SELECT content FROM plan_items")
    finally:
        await pool.close()
        PASSWORDS.clear()

    assert secret not in str(result.all_messages())
    assert secret not in child_history
    assert secret not in json.dumps([dict(item) for item in plan])
    for path in Path(tmp_path).glob("*.json"):
        assert secret not in path.read_text(encoding="utf-8")
    assert (await import_payload(ledger, import_id))["status"] == "review"


async def test_open_imports_uses_the_turn_attachments_with_no_argument(ledger):
    from types import SimpleNamespace

    attachment_id = await _att(ledger, sbi_csv())
    deps = AgentDeps(conversation_id="conv_open", statement_attachments=(attachment_id,))

    opened = await open_imports(SimpleNamespace(deps=deps))

    assert len(opened) == 1
    import_id = opened[0]["import_id"]
    assert import_id
    assert opened[0]["locked"] is False
    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == "parsing"

    empty = await open_imports(SimpleNamespace(deps=AgentDeps(conversation_id="conv_open")))
    assert [item["import_id"] for item in empty] == [None]
    assert "no statement attachments" in empty[0]["reason"]


async def test_a_background_parser_opens_the_turns_attachment_without_an_id_in_the_task(
    ledger, tmp_path
):
    account = await _sbi_account(ledger)
    attachment_id = await _att(ledger, sbi_csv())
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    import_id = row["id"]

    parser = build_parser_agent(
        model=_streaming(
            _code("print(await open_imports())"),
            _output(
                ParseReport(
                    status="ready",
                    imports=[ImportReport(import_id=import_id, status="review", rows=3)],
                )
            ),
        )
    )
    parent = build_agent(
        model=_sequence(
            ModelResponse(
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
            ),
            ModelResponse(parts=[TextPart("Started.")]),
        ),
        parser_agent=parser,
    )
    owner = DelegationTasks(directory=tmp_path)
    async with owner.opened():
        with owner.bind():
            await parent.run(
                "Import it.",
                deps=AgentDeps(
                    conversation_id="conv_open_bg", statement_attachments=(attachment_id,)
                ),
                conversation_id="conv_open_bg",
            )
        task_id = next(iter(owner.records))
        for _ in range(200):
            if owner.records[task_id].status == "finished":
                break
            await asyncio.sleep(0.05)
        history = str(owner.history(task_id))

    assert import_id in history
    assert await ledger.fetchval("SELECT task_id FROM imports WHERE id = $1", import_id) == task_id


async def test_open_imports_links_the_task_and_writes_then_clears_progress(ledger, tmp_path):
    account = await _sbi_account(ledger)
    attachment_id = await _att(ledger, sbi_csv())
    row = await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    import_id = row["id"]

    parser = build_parser_agent(
        model=_streaming(
            _code("print(await open_imports())"),
            _code(f"print(await doc_words(import_id={import_id!r}, page=0))"),
            ModelResponse(
                parts=[
                    ToolCallPart("ask_parent", {"question": "Which account is this statement for?"})
                ]
            ),
            _code(f"print(await submit_parser(import_ids=[{import_id!r}], code={SBI_PARSER!r}))"),
            _output(
                ParseReport(
                    status="ready",
                    imports=[ImportReport(import_id=import_id, status="review", rows=3)],
                )
            ),
        )
    )
    parent = build_agent(
        model=_sequence(
            ModelResponse(
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
            ),
            ModelResponse(parts=[TextPart("Started.")]),
        ),
        parser_agent=parser,
    )
    owner = DelegationTasks(directory=tmp_path)
    async with owner.opened():
        with owner.bind():
            await parent.run(
                "Import the statement.",
                deps=AgentDeps(
                    conversation_id="conv_progress", statement_attachments=(attachment_id,)
                ),
                conversation_id="conv_progress",
            )
        task_id = next(iter(owner.records))
        assert task_id

        question = None
        for _ in range(200):
            question = owner.pending_question(task_id)
            if question is not None:
                break
            await asyncio.sleep(0.05)
        assert question is not None

        live = await ledger.fetchrow(
            "SELECT task_id, progress FROM imports WHERE id = $1", import_id
        )
        assert live["task_id"] == task_id
        assert live["progress"] == "reading the statement"

        assert owner.answer(task_id, question_id=question[0], answer="SBI savings")
        for _ in range(200):
            status = await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id)
            if status == "review":
                break
            await asyncio.sleep(0.05)
        assert status == "review"
        assert (
            await ledger.fetchval("SELECT progress FROM imports WHERE id = $1", import_id) is None
        )


async def _conversation(conn, thread_id: str) -> None:
    await conn.execute("INSERT INTO conversations (id, title) VALUES ($1, 'test')", thread_id)


async def test_a_report_with_parser_text_flags_the_conversation(ledger):
    await _conversation(ledger, "conv_flag_report")
    parser = build_parser_agent(
        model=_streaming(
            _output(
                ParseReport(
                    status="blocked",
                    reason="The narration says PAYEE LEAK",
                    blocking="Which account is this?",
                )
            )
        )
    )
    parent = build_agent(
        model=_streaming(
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "delegate_task",
                        {
                            "agent_name": "parser",
                            "task": "Import attachment att_x",
                            "background": True,
                        },
                    )
                ]
            ),
            ModelResponse(parts=[TextPart("Started.")]),
        ),
        parser_agent=parser,
    )
    owner_path = Path("task-flag-report")
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        owner = DelegationTasks(directory=Path(folder) / owner_path)
        async with owner.opened():
            reports = [DelegationReports(owner, conversation_id="conv_flag_report")]
            with owner.bind():
                await parent.run(
                    "Import it.",
                    deps=AgentDeps(conversation_id="conv_flag_report"),
                    conversation_id="conv_flag_report",
                    capabilities=reports,
                )
            for _ in range(200):
                if all(r.status == "finished" for r in owner.records.values()):
                    break
                await asyncio.sleep(0.05)
            parent2 = build_agent(
                model=_streaming(ModelResponse(parts=[TextPart("The import is blocked.")])),
                parser_agent=parser,
            )
            with owner.bind():
                await parent2.run(
                    "What happened?",
                    deps=AgentDeps(conversation_id="conv_flag_report"),
                    conversation_id="conv_flag_report",
                    capabilities=reports,
                )
    assert await _flagged(ledger, "conv_flag_report")


async def test_a_background_parser_receipt_becomes_an_import_task_card(ledger, tmp_path):
    del ledger
    parser = build_parser_agent(model=_streaming(_output(ParseReport(status="ready"))))
    parent = build_agent(
        model=_sequence(
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "delegate_task",
                        {
                            "agent_name": "parser",
                            "task": "Import attachment att_x",
                            "background": True,
                        },
                    )
                ]
            ),
            ModelResponse(parts=[TextPart("Started.")]),
        ),
        parser_agent=parser,
    )
    owner = DelegationTasks(directory=tmp_path)
    async with owner.opened():
        with owner.bind():
            result = await parent.run(
                "Import it.",
                deps=AgentDeps(conversation_id="conv_card"),
                conversation_id="conv_card",
            )
        task_id = next(iter(owner.records))
        cards = cards_from_messages(result.all_messages())
        rebuilt = cards_for_transcript(result.all_messages())

    found = [
        card
        for card in cards
        if isinstance(card.data, dict) and card.data.get("card") == "import-task"
    ]
    assert [card.data["task_id"] for card in found] == [task_id]
    assert any(
        isinstance(card.data, dict) and card.data.get("task_id") == task_id for card in rebuilt
    )


async def _flagged(conn, thread_id: str) -> bool:
    return await conn.fetchval(
        "SELECT outside_text_at IS NOT NULL FROM conversations WHERE id = $1", thread_id
    )


async def test_a_parser_question_flags_the_conversation(ledger):
    await _conversation(ledger, "conv_flag_question")

    parser = build_parser_agent(
        model=_streaming(
            ModelResponse(
                parts=[ToolCallPart("ask_parent", {"question": "Which account is this?"})]
            ),
            _output(ParseReport(status="blocked", blocking="waiting")),
        )
    )
    parent = build_agent(
        model=_streaming(
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "delegate_task",
                        {
                            "agent_name": "parser",
                            "task": "Import attachment att_x",
                            "background": True,
                        },
                    )
                ]
            ),
            ModelResponse(parts=[TextPart("Started.")]),
        ),
        parser_agent=parser,
    )
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        owner = DelegationTasks(directory=Path(folder))
        async with owner.opened():
            reports = [DelegationReports(owner, conversation_id="conv_flag_question")]
            with owner.bind():
                await parent.run(
                    "Import it.",
                    deps=AgentDeps(conversation_id="conv_flag_question"),
                    conversation_id="conv_flag_question",
                    capabilities=reports,
                )
            for _ in range(200):
                if any(r.pending_question for r in owner.records.values()):
                    break
                await asyncio.sleep(0.05)
            parent2 = build_agent(
                model=_streaming(ModelResponse(parts=[TextPart("I need the account.")])),
                parser_agent=parser,
            )
            with owner.bind():
                await parent2.run(
                    "What happened?",
                    deps=AgentDeps(conversation_id="conv_flag_question"),
                    conversation_id="conv_flag_question",
                    capabilities=reports,
                )
    assert await _flagged(ledger, "conv_flag_question")


async def test_a_wrong_password_asks_again_and_is_not_handed_back(ledger, monkeypatch):
    from types import SimpleNamespace

    import fin_agent.agent.parser as parser_module

    secret = "right-secret"
    real_open = parser_module.open_documents

    def fake_open(data: bytes, password: str | None = None):
        if password == secret:
            return real_open(data, password=None)
        raise DocError("wrong_password" if password else "password_required")

    monkeypatch.setattr(parser_module, "open_documents", fake_open)
    monkeypatch.setattr(parser_module, "PASSWORD_WAIT_SECONDS", 10.0)

    account = await _sbi_account(ledger)
    attachment_id = await _att(ledger, sbi_csv())
    import_id = (
        await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    )["id"]

    async def type_passwords() -> None:
        PASSWORDS.put(import_id, "wrong-secret")
        for _ in range(200):
            reason = await ledger.fetchval("SELECT reason FROM imports WHERE id = $1", import_id)
            if reason and "didn't open" in reason:
                PASSWORDS.put(import_id, secret)
                return
            await asyncio.sleep(0.05)

    typist = asyncio.create_task(type_passwords())
    try:
        result = await parser_module.wait_for_password(
            SimpleNamespace(deps=AgentDeps(conversation_id="conv_pw")), import_id
        )
        await typist
    finally:
        PASSWORDS.clear()

    assert result == {"status": "unlocked"}
    assert PASSWORDS.get(import_id) is None
    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == "parsing"


def test_the_password_wait_outlasts_the_toolset_timeout():
    import fin_agent.agent.parser as parser_module

    found = []

    def visit(toolset):
        tools = getattr(toolset, "tools", None)
        if isinstance(tools, dict) and "wait_for_password" in tools:
            found.append(tools["wait_for_password"])
        for name in ("wrapped", "toolsets"):
            inner = getattr(toolset, name, None)
            for item in inner if isinstance(inner, (list, tuple)) else [inner]:
                if item is not None:
                    visit(item)

    for toolset in build_parser_agent(model="test").toolsets:
        visit(toolset)
    assert found, "wait_for_password is not on the parser"
    assert found[0].timeout is not None
    assert found[0].timeout > parser_module.PASSWORD_WAIT_SECONDS


def test_the_parser_catches_a_stall_and_its_subagent_has_no_timeout():
    parser = build_parser_agent(model="test")
    assert parser.model_settings == MODEL_SETTINGS

    parent = build_agent(model="test", parser_agent=parser)
    subagents = next(
        cap for cap in parent._root_capability.capabilities if isinstance(cap, SubAgents)
    )
    # Usage limits still bound how many requests the parser makes; wall-clock time does not.
    parser_delegate = next(item for item in subagents.agents if item.resolved_name == "parser")
    assert parser_delegate.timeout_seconds is None
