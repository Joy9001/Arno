"""The parser task API: its card payload, the account answer, and the layout versions."""

from __future__ import annotations

import asyncio
import hashlib
import json
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
    layout_rollback,
    layouts_list,
    task_answer,
    task_detail,
    task_payload,
)
from arno.ledger.accounts import create_account
from arno.ledger.audit import new_id


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
    await conn.execute(
        "UPDATE attachments SET import_id = $1 WHERE id = $2", import_id, attachment_id
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
    await ledger.execute(
        "UPDATE statement_layouts SET active_version_id = $1 WHERE id = $2", second, layout_id
    )

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
        ("post", "/api/imports/imp_x/commit"),
        ("delete", "/api/attachments/att_x"),
        ("get", "/api/tasks/abc"),
        ("post", "/api/tasks/abc/answer"),
        ("get", "/api/layouts"),
        ("post", "/api/layouts/lay_x/rollback"),
    ):
        assert getattr(client, method)(path).status_code == 401, path
