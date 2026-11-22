"""The parser task API: its card payload, the account answer, and the layout versions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from types import SimpleNamespace
from typing import cast

import asyncpg
import pytest
from fastapi import HTTPException, Request
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai_harness.planning import PlanItem, PostgresPlanStore, TaskStatus
from pydantic_ai_harness.subagents import DelegationTask, DelegationTasks

from arno.agent.parser import ImportReport, ParseReport
from arno.api.tasks import (
    AnswerIn,
    RollbackIn,
    TaskOwner,
    layout_rollback,
    layouts_list,
    task_answer,
    task_detail,
    task_payload,
)
from arno.importers.pipeline import _resolve_parsed_account, import_payload
from arno.importers.task_status import NOT_A_STATEMENT_REASON, settle_imports
from arno.importers.task_status import reconcile as reconcile_task_imports
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id
from tests.fixtures import import_helpers as helpers


async def _attachment(conn, payload: bytes = b"x", name: str = "sbi.csv") -> str:
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


async def _import(conn, *, task_id: str, status: str = "parsing", progress=None, counts=None):
    attachment_id = await _attachment(conn)
    import_id = await new_id(conn, "imp")
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, attachment_id, task_id, progress, counts)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        """,
        import_id,
        await new_id(conn, "hash"),
        status,
        attachment_id,
        task_id,
        progress,
        json.dumps(counts or {"new": 0}),
    )
    return import_id


def _request(owner: DelegationTasks) -> Request:
    # A Request double: the handlers read only `app.state.tasks`.
    return cast(Request, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(tasks=owner))))


def _record(task_id: str, **fields) -> DelegationTask:
    return DelegationTask(
        id=task_id,
        agent_name="parser",
        prompt="Import the statement.",
        conversation_id="conv_tasks",
        **fields,
    )


async def test_task_payload_for_a_running_and_a_finished_task(ledger):
    owner = DelegationTasks()
    async with owner.opened():
        running_id = "a" * 32
        owner.records[running_id] = _record(running_id, started_at=1000.0)
        import_id = await _import(
            ledger,
            task_id=running_id,
            progress="reading page 1 of 1",
            counts={"new": 2},
        )

        payload = await task_payload(ledger, owner, running_id)
        assert payload["state"] == "running"
        assert payload["outcome"] is None
        assert payload["report"] is None
        assert payload["question"] is None
        assert payload["started_at"] == 1000.0
        assert payload["imports"] == [
            {
                "import_id": import_id,
                "filename": "sbi.csv",
                "status": "parsing",
                "reason": None,
                "progress": "reading page 1 of 1",
                "counts": {"new": 2},
            }
        ]

        finished_id = "b" * 32
        owner.records[finished_id] = _record(
            finished_id,
            status="finished",
            outcome="ok",
            started_at=1000.0,
            finished_at=1012.5,
            output=ParseReport(
                status="ready",
                imports=[
                    ImportReport(
                        import_id=import_id,
                        status="review",
                        rows=3,
                        debits=2,
                        credits=1,
                    )
                ],
            ).model_dump_json(),
        )
        await _import(ledger, task_id=finished_id, status="review")

        finished = await task_payload(ledger, owner, finished_id)
        assert finished["state"] == "finished"
        assert finished["outcome"] == "ok"
        assert finished["finished_at"] == 1012.5
        assert finished["report"]["status"] == "ready"
        assert finished["report"]["imports"][0]["rows"] == 3
        assert len(finished["imports"]) == 1


async def test_a_stopped_task_reports_no_stale_progress_and_keeps_the_import_resumable(ledger):
    owner = DelegationTasks()
    async with owner.opened():
        running_id = "3" * 32
        owner.records[running_id] = _record(running_id, started_at=1000.0)
        await _import(ledger, task_id=running_id, progress="reading pages 47 to 74 of 74")

        finished_id = "4" * 32
        owner.records[finished_id] = _record(
            finished_id,
            status="finished",
            outcome="cancelled",
            started_at=1000.0,
            finished_at=1005.0,
        )
        await _import(ledger, task_id=finished_id, progress="reading pages 47 to 74 of 74")

        running = await task_payload(ledger, owner, running_id)
        assert running["imports"][0]["progress"] == "reading pages 47 to 74 of 74"

        stopped = await task_payload(ledger, owner, finished_id)
        assert stopped["imports"][0]["progress"] is None
        # Still resumable: a stopped parser task never fails its imports.
        assert stopped["imports"][0]["status"] == "parsing"


async def test_a_running_task_transcript_follows_the_child_history(ledger):
    owner = DelegationTasks()
    async with owner.opened():
        task_id = "5" * 32
        record = _record(task_id, started_at=1000.0)
        owner.records[task_id] = record
        record.messages = [ModelRequest(parts=[UserPromptPart("Import the statement.")])]

        first = await task_payload(ledger, owner, task_id)
        assert first["state"] == "running"
        assert [message["role"] for message in first["messages"]] == ["user"]

        record.messages = [
            *record.messages,
            ModelResponse(parts=[TextPart("Reading page 1.")]),
        ]
        second = await task_payload(ledger, owner, task_id)
        assert [message["role"] for message in second["messages"]] == ["user", "assistant"]
        assert second["messages"][1]["content"] == [{"type": "text", "text": "Reading page 1."}]


async def test_task_payload_reads_the_plan_from_the_postgres_store(ledger):
    owner = DelegationTasks()
    async with owner.opened():
        task_id = "c" * 32
        owner.records[task_id] = _record(task_id)
        pool = await asyncpg.create_pool(__import__("os").environ["DATABASE_URL"], min_size=1)
        try:
            store = PostgresPlanStore(pool, session=task_id)
            await store.set_items(
                [
                    PlanItem(content="Read the statement", status=TaskStatus.completed),
                    PlanItem(
                        content="Repair the parser",
                        status=TaskStatus.in_progress,
                        active_form="Repairing the parser",
                    ),
                ]
            )
        finally:
            await pool.close()

        payload = await task_payload(ledger, owner, task_id)
        assert [item["content"] for item in payload["plan"]] == [
            "Read the statement",
            "Repair the parser",
        ]
        assert payload["plan"][1]["status"] == "in_progress"


async def test_unknown_or_non_parser_task_is_404(ledger):
    owner = DelegationTasks()
    async with owner.opened():
        other = "d" * 32
        owner.records[other] = DelegationTask(
            id=other,
            agent_name="categorizer",
            prompt="",
            conversation_id="conv_tasks",
        )
        for task_id in ("e" * 32, other):
            with pytest.raises(HTTPException) as caught:
                await task_detail(task_id, _request(owner), ledger)
            assert caught.value.status_code == 404


async def test_answer_uses_the_account_row_and_only_active_bank_card_wallet(ledger):
    bank = await create_account(
        ledger, kind="bank", name="SBI savings", institution="SBI", last4="6789"
    )
    card = await create_account(
        ledger,
        kind="card",
        name="Slice card",
        institution="Slice",
        credit_limit_paise=100000,
        statement_day=1,
        due_day=10,
    )
    cash = await create_account(ledger, kind="cash", name="Cash")
    inactive = await create_account(ledger, kind="bank", name="Old bank", institution="Old")
    await ledger.execute("UPDATE accounts SET active = false WHERE id = $1", inactive["id"])

    owner = DelegationTasks()
    async with owner.opened():
        task_id = "f" * 32
        owner.records[task_id] = _record(task_id, pending_question="Which account is this?")
        question = asyncio.create_task(
            owner.ask(
                task_id,
                question_id="q1",
                question="Which account is this statement for?",
            )
        )
        try:
            for _ in range(200):
                if owner.pending_question(task_id) is not None:
                    break
                await asyncio.sleep(0.01)
            assert owner.pending_question(task_id) == (
                "q1",
                "Which account is this statement for?",
            )

            for account in (inactive,):
                with pytest.raises(HTTPException) as caught:
                    await task_answer(
                        task_id,
                        AnswerIn(question_id="q1", account_id=account["id"]),
                        _request(owner),
                        ledger,
                    )
                assert caught.value.status_code == 422

            with pytest.raises(HTTPException) as caught:
                await task_answer(
                    task_id,
                    AnswerIn(question_id="wrong", account_id=bank["id"]),
                    _request(owner),
                    ledger,
                )
            assert caught.value.status_code == 409

            response = await task_answer(
                task_id,
                AnswerIn(question_id="q1", account_id=bank["id"]),
                _request(owner),
                ledger,
            )
            assert response["question"] is None
            answer = await question

            # Cash is a first-class account, so a cash statement's question can be answered.
            cash_question = asyncio.create_task(
                owner.ask(
                    task_id,
                    question_id="q2",
                    question="Which account is this statement for?",
                )
            )
            for _ in range(200):
                if owner.pending_question(task_id) is not None:
                    break
                await asyncio.sleep(0.01)
            await task_answer(
                task_id,
                AnswerIn(question_id="q2", account_id=cash["id"]),
                _request(owner),
                ledger,
            )
            cash_answer = await cash_question
        finally:
            if not question.done():
                question.cancel()

    assert answer == "The statement is for the account SBI savings (SBI), id " + bank["id"] + "."
    assert cash_answer == "The statement is for the account Cash (cash), id " + cash["id"] + "."
    assert card["id"] != bank["id"]


async def test_answer_is_409_when_the_task_is_not_waiting(ledger):
    bank = await create_account(
        ledger, kind="bank", name="SBI savings", institution="SBI", last4="6789"
    )
    owner = DelegationTasks()
    async with owner.opened():
        task_id = "1" * 32
        owner.records[task_id] = _record(task_id)
        with pytest.raises(HTTPException) as caught:
            await task_answer(
                task_id,
                AnswerIn(question_id="q1", account_id=bank["id"]),
                _request(owner),
                ledger,
            )
        assert caught.value.status_code == 409


async def test_layouts_list_and_rollback(ledger):
    layout_id = await new_id(ledger, "lay")
    first = await new_id(ledger, "pv")
    second = await new_id(ledger, "pv")
    await ledger.execute(
        """
        INSERT INTO statement_layouts (id, institution, kind, variants)
        VALUES ($1, 'SBI', 'bank', ARRAY['date|narration|debit']::text[])
        """,
        layout_id,
    )
    for version_id, version in ((first, 1), (second, 2)):
        await ledger.execute(
            """
            INSERT INTO parser_versions (id, layout_id, version, parser, helper_version)
            VALUES ($1, $2, $3, 'print(1)', 'h1')
            """,
            version_id,
            layout_id,
            version,
        )
    await ledger.execute("UPDATE parser_versions SET is_active = true WHERE id = $1", second)

    listed = await layouts_list(ledger)
    [layout] = listed["items"]
    assert layout["institution"] == "SBI"
    assert layout["active_version"] == 2
    assert [item["version"] for item in layout["versions"]] == [2, 1]
    assert [item["active"] for item in layout["versions"]] == [True, False]

    rolled = await layout_rollback(layout_id, RollbackIn(version=1), ledger)
    assert rolled["active_version"] == 1
    assert [item["active"] for item in rolled["versions"]] == [False, True]

    with pytest.raises(HTTPException) as caught:
        await layout_rollback(layout_id, RollbackIn(version=99), ledger)
    assert caught.value.status_code == 404


async def test_import_rows_carry_their_task_and_progress(ledger):
    """The migration's columns are wired through the payload and cleared on a status change."""
    import_id = await _import(ledger, task_id="2" * 32, progress="checking the parser")
    row = await ledger.fetchrow("SELECT task_id, progress FROM imports WHERE id = $1", import_id)
    assert row["task_id"] == "2" * 32
    assert row["progress"] == "checking the parser"

    from arno.importers.pipeline import _set_status

    await _set_status(ledger, import_id, "review")
    assert await ledger.fetchval("SELECT progress FROM imports WHERE id = $1", import_id) is None


def test_import_and_task_routes_need_a_signed_in_session(client):
    for method, path in (
        ("get", "/api/imports"),
        ("get", "/api/imports/imp_x"),
        ("post", "/api/imports/imp_x/password"),
        ("post", "/api/imports/imp_x/opening"),
        ("post", "/api/imports/imp_x/commit"),
        ("delete", "/api/attachments/att_x"),
        ("get", "/api/tasks/abc"),
        ("post", "/api/tasks/abc/answer"),
        ("get", "/api/layouts"),
        ("post", "/api/layouts/lay_x/rollback"),
    ):
        assert getattr(client, method)(path).status_code == 401, path


def _save_record(task_id: str) -> DelegationTask:
    return DelegationTask(
        id=task_id, agent_name="parser", prompt="Import the statement.", conversation_id="conv_save"
    )


async def _save_many(owner: TaskOwner, record: DelegationTask) -> None:
    for _ in range(60):
        await owner.save(record)


async def test_two_task_owners_on_one_directory_both_save_the_same_task(tmp_path):
    record = _save_record("a" * 32)
    await asyncio.gather(
        _save_many(TaskOwner(directory=tmp_path), record),
        _save_many(TaskOwner(directory=tmp_path), record),
    )
    saved = json.loads((tmp_path / f"{record.id}.json").read_text(encoding="utf-8"))
    assert saved["id"] == record.id
    assert not list(tmp_path.glob("*.tmp"))


async def test_a_task_save_survives_its_directory_being_removed(tmp_path):
    record = _save_record("b" * 32)
    owner = TaskOwner(directory=tmp_path / "tasks")
    await owner.save(record)
    shutil.rmtree(tmp_path / "tasks")

    await owner.save(record)
    assert json.loads((tmp_path / "tasks" / f"{record.id}.json").read_text())["id"] == record.id


async def test_an_unsafe_task_id_is_refused_before_it_reaches_the_filesystem(tmp_path):
    owner = TaskOwner(directory=tmp_path)
    with pytest.raises(ValueError):
        await owner.save(_save_record("../escape"))
    assert not list(tmp_path.iterdir())


def test_task_owners_on_one_directory_work_across_event_loops(tmp_path):
    record = _save_record("c" * 32)
    asyncio.run(TaskOwner(directory=tmp_path).save(record))
    asyncio.run(TaskOwner(directory=tmp_path).save(record))
    assert json.loads((tmp_path / f"{record.id}.json").read_text())["id"] == record.id


# A parsing import is never left stuck, and the answered account wins ------------------------


async def test_a_restart_fails_an_import_that_never_got_a_task(ledger):
    account = await helpers.bank(ledger)
    import_id = await new_id(ledger, "imp")
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, commit_groups)
        VALUES ($1, 'hash-orphan', 'parsing', $2, '[]'::jsonb)
        """,
        import_id,
        account["id"],
    )

    changed = await reconcile_task_imports(ledger, {})

    assert changed == 1
    row = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", import_id)
    assert row["status"] == "failed"
    assert row["reason"]


async def test_answering_with_another_account_files_the_import_against_it(ledger):
    from arno.agent.parser import submit_parser_code
    from tests.import_fixtures import SBI_PARSER, sbi_csv

    sbi = await create_account(
        ledger, kind="bank", name="SBI Salary", institution="SBI", last4="6789"
    )
    hdfc = await create_account(
        ledger, kind="bank", name="HDFC Savings", institution="HDFC", last4="4821"
    )
    import_id = await helpers.upload(ledger, sbi_csv())
    assert await ledger.fetchval("SELECT account_id FROM imports WHERE id = $1", import_id) is None

    owner = DelegationTasks()
    task_id = "9" * 32
    async with owner.opened():
        owner.records[task_id] = _record(task_id)
        await ledger.execute("UPDATE imports SET task_id = $1 WHERE id = $2", task_id, import_id)
        question = asyncio.create_task(
            owner.ask(task_id, question_id="q1", question="Which account is this statement for?")
        )
        # A question ends the wait; the child keeps running, parked on the answer.
        await asyncio.wait_for(owner.wait_children(None), timeout=10)
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


async def test_a_parsed_name_cannot_overrule_the_chosen_account(ledger):
    from arno.importers.readers import Doc

    hdfc = await create_account(ledger, kind="bank", name="HDFC Savings", institution="HDFC")
    resolved = await _resolve_parsed_account(
        ledger,
        parsed_account={"name": "SBI savings", "institution": "SBI"},
        current=hdfc,
        doc=cast(Doc, None),  # never read: `current` short-circuits the call
    )
    assert resolved is not None
    assert resolved["id"] == hdfc["id"]


def test_the_import_card_offers_the_cash_account():
    from arno.api.tasks import ACCOUNT_KINDS

    assert "cash" in ACCOUNT_KINDS


async def test_a_parser_that_says_not_a_statement_settles_the_card_plainly(ledger):
    account = await helpers.bank(ledger)
    import_id = await new_id(ledger, "imp")
    await ledger.execute(
        """
        INSERT INTO imports (id, file_hash, status, account_id, task_id, commit_groups)
        VALUES ($1, $2, 'parsing', $3, 'task_1', '[]'::jsonb)
        """,
        import_id,
        f"hash-{import_id}",
        account["id"],
    )
    report = ParseReport(
        status="not_a_statement",
        reason="The uploaded file is an employee payslip, not a bank statement.",
    )

    changed = await settle_imports(
        ledger, task_id="task_1", outcome="ok", output=report.model_dump_json()
    )

    assert changed == 1
    row = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", import_id)
    assert row["status"] == "not_a_statement"
    assert row["reason"] == NOT_A_STATEMENT_REASON
    assert "without reading this file" not in row["reason"]
