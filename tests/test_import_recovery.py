"""Recoverable ingestion: replays, password states, failed refreshes, textless files, retries."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date
from pathlib import Path

import pytest
from pydantic_ai import CancellationToken
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai_harness.subagents import DelegationTask

from arno.agent.deps import AgentDeps
from arno.agent.history import create_thread
from arno.agent.parser import ImportReport, ParseReport, open_imports, submit_parser_code
from arno.agent.turns import ChatTurn
from arno.importers.commit import commit_import
from arno.importers.passwords import PASSWORDS, PasswordVault
from arno.importers.pipeline import create_import, import_payload
from arno.importers.readers import Doc, DocError
from arno.importers.task_status import reconcile as reconcile_imports
from arno.importers.vision import VISION_UNAVAILABLE, transcribe_page
from arno.jobs.runner import enqueue_job, recover_interrupted_jobs
from arno.ledger.accounts import create_account, set_statement_balance
from arno.ledger.audit import new_id
from arno.ledger.errors import InvalidLedgerError
from tests.agent_helpers import (
    parser_agent,
    run_context,
    step_code,
    step_output,
    streaming_model,
    streaming_steps,
)
from tests.import_fixtures import SBI_PARSER, minimal_pdf, sbi_csv

LOCKED_PDF = Path(__file__).parent / "fixtures" / "locked-1234.pdf"


async def _attachment(
    conn,
    payload: bytes,
    *,
    name: str = "sbi.csv",
    content_type: str = "text/csv",
) -> str:
    attachment_id = await new_id(conn, "att")
    await conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'statement', $2, $3, $4, $5, $6)
        """,
        attachment_id,
        name,
        content_type,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        payload,
    )
    return attachment_id


async def _account(conn) -> dict:
    return await create_account(
        conn,
        kind="bank",
        name="SBI savings",
        institution="SBI",
        last4="6789",
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )


def _deps(attachment_id: str) -> AgentDeps:
    return AgentDeps(conversation_id="conv_recovery", statement_attachments=(attachment_id,))


async def _upload(client, payload: bytes, name: str = "sbi.csv") -> dict:
    response = client.post(
        "/api/attachments",
        files={"file": (name, payload, "application/pdf" if name.endswith(".pdf") else "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- C19 box 1: the same bytes never book twice -------------------------------------------


async def test_replaying_the_same_statement_bytes_books_one_import_and_one_set_of_movements(
    session_client, ledger
):
    account = await _account(ledger)
    first_upload = await _upload(session_client, sbi_csv())
    second_upload = await _upload(session_client, sbi_csv())
    assert first_upload["sha256"] == second_upload["sha256"]

    first = await create_import(ledger, attachment_id=first_upload["id"], account_id=account["id"])
    replay = await create_import(
        ledger, attachment_id=second_upload["id"], account_id=account["id"]
    )
    assert replay["id"] == first["id"]
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 1

    result = await submit_parser_code(ledger, import_ids=[first["id"]], code=SBI_PARSER)
    assert result["status"] == "parser_submitted", result
    await commit_import(ledger, first["id"])
    booked = await ledger.fetchval("SELECT count(*) FROM movements")

    again = await create_import(
        ledger,
        attachment_id=(await _upload(session_client, sbi_csv()))["id"],
        account_id=account["id"],
    )
    assert again["id"] == first["id"]
    assert again["status"] == "committed"
    assert await ledger.fetchval("SELECT count(*) FROM imports") == 1
    assert await ledger.fetchval("SELECT count(*) FROM movements") == booked


# --- C19 box 2: password states persist and explain themselves ----------------------------


async def test_a_statement_that_needs_a_password_shows_why_and_survives_a_restart(
    session_client, ledger
):
    account = await _account(ledger)
    attachment_id = await _attachment(
        ledger, LOCKED_PDF.read_bytes(), name="statement.pdf", content_type="application/pdf"
    )
    import_id = (
        await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    )["id"]

    opened = await open_imports(run_context(_deps(attachment_id)))
    assert opened[0]["import_id"] == import_id
    assert opened[0]["locked"] is True

    detail = session_client.get(f"/api/imports/{import_id}").json()
    assert detail["status"] == "needs_password"
    assert "password" in (detail["reason"] or "").lower()

    job_id = await enqueue_job(
        ledger,
        key=f"categorize_import:{import_id}",
        kind="categorize_import",
        payload={"import_id": import_id},
    )
    await ledger.execute("UPDATE jobs SET status = 'running' WHERE id = $1", job_id)
    # A boot: the interrupted job is requeued and a stale import pass is settled.
    assert await recover_interrupted_jobs(ledger) == 1
    assert await reconcile_imports(ledger, {}) == 0
    assert await ledger.fetchval("SELECT status FROM jobs WHERE id = $1", job_id) == "pending"

    after_restart = session_client.get(f"/api/imports/{import_id}").json()
    assert after_restart["status"] == "needs_password"
    assert after_restart["reason"] == detail["reason"]


async def test_a_wrong_password_leaves_an_explained_needs_password_state(
    session_client, ledger, monkeypatch
):
    import arno.agent.parser as parser_module

    account = await _account(ledger)
    attachment_id = await _attachment(
        ledger, LOCKED_PDF.read_bytes(), name="statement.pdf", content_type="application/pdf"
    )
    import_id = (
        await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    )["id"]
    await open_imports(run_context(_deps(attachment_id)))
    monkeypatch.setattr(parser_module, "PASSWORD_WAIT_SECONDS", 0.05)

    PASSWORDS.put(import_id, "9999")
    result = await parser_module.wait_for_password(run_context(_deps(attachment_id)), import_id)

    assert result == {"status": "timed_out"}
    payload = await import_payload(ledger, import_id)
    assert payload["status"] == "needs_password"
    assert "didn't open" in payload["reason"]
    shown = session_client.get(f"/api/imports/{import_id}").json()
    assert shown["status"] == "needs_password"
    assert shown["reason"] == payload["reason"]


# --- C19 box 3: a failed refresh keeps the old date and reconciles nothing ----------------


async def test_a_failed_refresh_keeps_the_old_statement_date_visible_and_reconciles_nothing(
    session_client, ledger
):
    account = await _account(ledger)
    await set_statement_balance(
        ledger, account["id"], balance_paise=30_000_000, as_of=date(2026, 5, 31)
    )
    attachment_id = await _attachment(ledger, sbi_csv())
    import_id = (
        await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    )["id"]

    broken = SBI_PARSER.replace("'closing': closing", "'closing': 1")
    booked = await ledger.fetchval("SELECT count(*) FROM movements")
    result = await submit_parser_code(ledger, import_ids=[import_id], code=broken)
    assert result["status"] == "failed", result
    assert result["reason"] == "DISCREPANCY"
    with pytest.raises(InvalidLedgerError):
        await commit_import(ledger, import_id)

    row = await ledger.fetchrow(
        "SELECT statement_balance_paise, statement_balance_as_of FROM accounts WHERE id = $1",
        account["id"],
    )
    assert row["statement_balance_paise"] == 30_000_000
    assert row["statement_balance_as_of"] == date(2026, 5, 31)
    assert await ledger.fetchval("SELECT count(*) FROM movements") == booked
    assert (
        await ledger.fetchval(
            "SELECT count(*) FROM statement_lines WHERE import_id = $1", import_id
        )
        == 0
    )

    shown = session_client.get("/api/accounts").json()["accounts"]
    [account_payload] = [item for item in shown if item["id"] == account["id"]]
    assert account_payload["statement_balance_as_of"] == "2026-05-31"
    assert account_payload["statement_balance_paise"] == 30_000_000
    assert account_payload["statement_age_days"] is not None


# --- C04 box 1: a textless document has an explicit recovery state ------------------------


async def test_a_textless_statement_ends_in_a_visible_vision_recovery_state(session_client, ledger):
    with pytest.raises(DocError) as caught:
        Doc(sbi_csv()).words(0)
    assert caught.value.code == "no_words"

    note = await transcribe_page(Doc(minimal_pdf([])))
    assert note["rows"] == []
    assert note["note"] == VISION_UNAVAILABLE
    assert "vision is not configured" in note["note"]

    account = await _account(ledger)
    attachment_id = await _attachment(
        ledger, minimal_pdf([]), name="scanned.pdf", content_type="application/pdf"
    )
    import_id = (
        await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    )["id"]
    opened = await open_imports(run_context(_deps(attachment_id)))
    assert opened[0]["import_id"] == import_id
    assert opened[0]["vision_required"] is True

    payload = session_client.get(f"/api/imports/{import_id}").json()
    assert payload["status"] == "needs_input"
    assert "vision is not configured" in payload["reason"]
    assert payload["can_retry_parse"] is False


# --- C04 box 3: the password never reaches a log, and a restart asks again ----------------


async def test_the_statement_password_never_reaches_a_log_and_a_restart_asks_again(
    session_client, ledger, monkeypatch, caplog
):
    import arno.agent.parser as parser_module
    import arno.importers.pipeline as pipeline_module

    caplog.set_level(logging.DEBUG)
    caplog.clear()
    secret = "1234"  # the synthetic fixture's password; it must never reach a log
    account = await _account(ledger)
    attachment_id = await _attachment(
        ledger, LOCKED_PDF.read_bytes(), name="statement.pdf", content_type="application/pdf"
    )
    import_id = (
        await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    )["id"]
    await open_imports(run_context(_deps(attachment_id)))

    session_client.post(f"/api/imports/{import_id}/password", json={"password": secret})
    # The same in-memory vault: the route writes what the waiting parser reads.
    assert PASSWORDS.get(import_id) == secret
    unlocked = await parser_module.wait_for_password(run_context(_deps(attachment_id)), import_id)

    assert unlocked["status"] == "failed"
    assert unlocked["vision_required"] is True
    row = await ledger.fetchrow("SELECT * FROM imports WHERE id = $1", import_id)
    assert secret not in str(dict(row))
    assert (
        await ledger.fetchval(
            "SELECT count(*) FROM jobs WHERE payload::text LIKE $1", f"%{secret}%"
        )
        == 0
    )
    assert secret not in caplog.text

    # A restart builds a fresh vault, so a still-locked import asks for the password again.
    # Put the original locked bytes back, as a restart before the unlock would have left them.
    await ledger.execute(
        "UPDATE attachments SET data = $1, unlocked_at = NULL WHERE id = $2",
        LOCKED_PDF.read_bytes(),
        attachment_id,
    )
    await ledger.execute(
        "UPDATE imports SET status = 'needs_password', reason = 'password required' WHERE id = $1",
        import_id,
    )
    restarted = PasswordVault()
    monkeypatch.setattr(parser_module, "PASSWORDS", restarted)
    monkeypatch.setattr(pipeline_module, "PASSWORDS", restarted)
    monkeypatch.setattr(parser_module, "PASSWORD_WAIT_SECONDS", 0.05)
    assert restarted.get(import_id) is None
    again = await parser_module.wait_for_password(run_context(_deps(attachment_id)), import_id)
    assert again == {"status": "timed_out"}
    payload = session_client.get(f"/api/imports/{import_id}").json()
    assert payload["status"] == "needs_password"
    assert "password" in (payload["reason"] or "").lower()


# --- C18 box 3, backend: a failed parse has a retry ---------------------------------------


def _delegating_model():
    calls = {"n": 0}

    def script(messages, info: AgentInfo) -> ModelResponse:
        del messages, info
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "delegate_task",
                        {
                            "agent_name": "parser",
                            "task": "Import the statement files.",
                            "background": True,
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Retrying the import.")])

    return streaming_model(script)


async def test_a_failed_parse_can_be_retried_as_a_new_parser_turn(session_client, ledger):
    account = await _account(ledger)
    attachment_id = await _attachment(ledger, sbi_csv())
    import_id = (
        await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    )["id"]
    await ledger.execute(
        "UPDATE imports SET status = 'failed', reason = 'The parser stopped.' WHERE id = $1",
        import_id,
    )
    detail = session_client.get(f"/api/imports/{import_id}").json()
    assert detail["can_retry_parse"] is True

    app = session_client.app
    app.state.turns.waker = None
    tasks = app.state.tasks
    before = frozenset(tasks.records)
    parser = parser_agent(app)
    started = asyncio.Event()

    def mark(step):
        def script(info):
            started.set()
            return step(info) if callable(step) else step

        return script

    sequence = streaming_steps(
        mark(step_code("print(await open_imports())")),
        mark(
            step_code(
                f"print(await submit_parser(import_ids=[{import_id!r}], code={SBI_PARSER!r}))"
            )
        ),
        mark(
            step_output(
                ParseReport(
                    status="ready",
                    imports=[ImportReport(import_id=import_id, status="review", rows=3)],
                )
            )
        ),
    )
    with app.state.agent.override(model=_delegating_model()), parser.override(model=sequence):
        response = session_client.post(f"/api/imports/{import_id}/retry")
        assert response.status_code == 200, response.text
        assert response.json()["thread_id"]
        # The parser's own worker finishing is what settles the import.
        await asyncio.wait_for(started.wait(), timeout=10)
        [task_id] = [key for key in tasks.records if key not in before]
        await asyncio.wait_for(tasks._workers[task_id], timeout=10)

    final = session_client.get(f"/api/imports/{import_id}").json()
    assert final["status"] == "review", final
    assert len(final["lines"]) == 3
    assert final["can_retry_parse"] is False


async def test_retrying_an_import_that_is_not_failed_is_refused(session_client, ledger):
    account = await _account(ledger)
    attachment_id = await _attachment(ledger, sbi_csv())
    import_id = (
        await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    )["id"]
    response = session_client.post(f"/api/imports/{import_id}/retry")
    assert response.status_code == 409
    assert session_client.post("/api/imports/imp_missing/retry").status_code == 404


async def test_a_retry_while_a_reply_is_running_refuses_and_leaves_the_run_marker(
    session_client, ledger
):
    account = await _account(ledger)
    attachment_id = await _attachment(ledger, sbi_csv())
    import_id = (
        await create_import(ledger, attachment_id=attachment_id, account_id=account["id"])
    )["id"]
    await ledger.execute(
        "UPDATE imports SET status = 'failed', reason = 'The parser stopped.' WHERE id = $1",
        import_id,
    )
    app = session_client.app
    thread = await create_thread(ledger, title="Retry the import", thread_id="thread_retry_busy")
    task_id = "d" * 32
    app.state.tasks.records[task_id] = DelegationTask(
        id=task_id,
        agent_name="parser",
        prompt="Import the statement.",
        conversation_id=str(thread["id"]),
    )
    await ledger.execute("UPDATE imports SET task_id = $1 WHERE id = $2", task_id, import_id)
    await ledger.execute(
        "UPDATE conversations SET active_run_id = 'run_live', run_started_at = now() WHERE id = $1",
        thread["id"],
    )
    # The live turn is in memory, as a reply the user is watching would be.
    app.state.turns.turns[str(thread["id"])] = ChatTurn(
        thread_id=str(thread["id"]),
        message_id="msg_live",
        run_id="run_live",
        history=[],
        deps=AgentDeps(conversation_id=str(thread["id"])),
        token=CancellationToken(),
    )

    try:
        response = session_client.post(f"/api/imports/{import_id}/retry")

        assert response.status_code == 409
        assert "already running" in response.json()["detail"]
        marker = await ledger.fetchrow(
            "SELECT active_run_id, run_started_at FROM conversations WHERE id = $1",
            thread["id"],
        )
        assert marker["active_run_id"] == "run_live"
        assert marker["run_started_at"] is not None
    finally:
        # Nothing will ever finish this fake turn; left in place, the app's shutdown drain waits
        # its full timeout for it on teardown.
        app.state.turns.turns.pop(str(thread["id"]), None)
