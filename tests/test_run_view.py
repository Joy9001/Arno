"""The parser run view, the import's terminal status, and the chat agent's paused-task hint."""

from __future__ import annotations

from types import SimpleNamespace

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)

from fin_agent.agent.builder import _instructions
from fin_agent.agent.deps import AgentDeps
from fin_agent.api.run_view import build_run
from fin_agent.importers.task_status import PAUSED_REASON, reconcile, settle_imports
from fin_agent.ledger.audit import new_id


def _event(seq, kind, name="", call="", *, run="run_a", error=None, at=None):
    return {
        "run_id": run,
        "seq": seq,
        "kind": kind,
        "tool_name": name,
        "tool_call_id": call,
        "timestamp": at or f"2026-11-08T10:00:{seq:02d}+00:00",
        "error": error,
    }


def _script(call: str, code: str, *, thinking: str | None = None) -> ModelResponse:
    parts = [ThinkingPart(content=thinking)] if thinking else []
    return ModelResponse(parts=[*parts, ToolCallPart("run_code", {"code": code}, call)])


def _result(call: str, content: str) -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart("run_code", content, call)])


def _run_fixture():
    events = [
        _event(1, "run_started"),
        _event(2, "tool_call_started", "run_code", "c1"),
        _event(3, "tool_call_started", "open_imports", "c1__1"),
        _event(4, "tool_call_completed", "open_imports", "c1__1"),
        _event(5, "tool_call_completed", "run_code", "c1"),
        _event(6, "tool_call_started", "run_code", "c2"),
        _event(7, "tool_call_started", "doc_words_range", "c2__1"),
        _event(
            8,
            "tool_call_failed",
            "doc_words_range",
            "c2__1",
            error="DocError('bad_range: too many')",
        ),
        _event(9, "tool_call_completed", "run_code", "c2"),
        _event(10, "tool_call_started", "run_code", "c3"),
        *[_event(11 + i, "tool_call_started", "doc_words", f"c3__{i}") for i in range(3)],
        _event(14, "tool_call_completed", "run_code", "c3"),
        _event(15, "tool_call_started", "run_code", "c4"),
        _event(16, "tool_call_started", "submit_parser", "c4__1"),
        _event(17, "tool_call_completed", "submit_parser", "c4__1"),
        _event(18, "tool_call_completed", "run_code", "c4"),
        _event(19, "tool_call_started", "run_code", "c5"),
        _event(20, "run_failed", error="UsageLimitExceeded('tool_calls_limit of 120')"),
    ]
    messages = [
        _script("c1", "# Open this turn's files\nimports = await open_imports()"),
        _result("c1", "[{'import_id': 'imp_1'}]"),
        _script("c2", "pages = await doc_words_range(first=0, last=73)"),
        _result("c2", "Runtime error:\nDocError"),
        _script(
            "c3",
            "# Read every page\nfor p in range(3): await doc_words(page=p)",
            thinking="Pages one by one",
        ),
        _result("c3", "3"),
        _script("c4", "# Submit the parser\nawait submit_parser(import_ids=['imp_1'], code=CODE)"),
        _result("c4", "Runtime error:\nTraceback...\nNameError: name 'files' is not defined"),
        _script("c5", "# Try again\nawait submit_parser(import_ids=['imp_1'], code=CODE)"),
    ]
    return events, messages


def test_the_run_view_titles_steps_from_host_calls_and_marks_failures():
    events, messages = _run_fixture()
    run = build_run(events=events, messages=messages, running=False, outcome="budget", limit=120)

    titles = [step["title"] for step in run["steps"]]
    assert titles == [
        "Opened the statement files",
        "Read a range of pages",
        "Read 3 pages",
        "Submitted the parser · attempt 1",
        "Submitted the parser · attempt 2",
    ]
    statuses = [step["status"] for step in run["steps"]]
    assert statuses == ["ok", "failed", "ok", "failed", "cut_off"]
    assert run["steps"][1]["error"] == "too many"
    assert run["steps"][3]["error"] == "NameError: name 'files' is not defined"
    assert run["steps"][0]["purpose"] == "Open this turn's files"
    assert run["steps"][1]["purpose"] is None
    assert run["steps"][2]["reasoning"] == "Pages one by one"
    assert {"name": "doc_words", "count": 3, "failed": False} in run["steps"][2]["calls"]
    assert run["stop"] == "budget"
    assert run["used"] == 11


def test_stages_follow_the_host_calls_not_the_models_plan():
    events, messages = _run_fixture()
    run = build_run(events=events, messages=messages, running=False, outcome="budget", limit=120)

    by_key = {stage["key"]: stage for stage in run["stages"]}
    assert by_key["open"]["status"] == "done"
    assert by_key["read"]["status"] == "done"
    assert by_key["submit"]["status"] == "stopped"
    assert by_key["moves"]["status"] == "not_reached"
    assert by_key["submit"]["steps"] == ["c4", "c5"]


def test_a_live_run_shows_its_last_step_running_and_later_stages_to_do():
    events, messages = _run_fixture()
    run = build_run(
        events=events[:-1], messages=messages[:-1], running=True, outcome=None, limit=120
    )

    assert run["steps"][-1]["status"] == "running"
    assert run["stop"] is None
    assert {stage["key"]: stage["status"] for stage in run["stages"]}["report"] == "todo"


def test_a_continued_task_shows_one_segment_per_run():
    events, messages = _run_fixture()
    more = [
        _event(30, "run_started", run="run_b"),
        _event(31, "tool_call_started", "run_code", "d1", run="run_b"),
        _event(32, "tool_call_completed", "run_code", "d1", run="run_b"),
    ]
    run = build_run(events=events + more, messages=messages, running=True, outcome=None, limit=120)

    assert [segment["run_id"] for segment in run["segments"]] == ["run_a", "run_b"]
    assert run["segments"][0]["stop"] == "budget"
    assert run["used"] == 1
    assert run["steps"][-1]["segment"] == 1


async def _parsing_import(conn, task_id: str, *, reason: str | None = None) -> str:
    import_id = await new_id(conn, "imp")
    await conn.execute(
        """
        INSERT INTO imports (id, file_hash, status, task_id, reason)
        VALUES ($1, $2, 'parsing', $3, $4)
        """,
        import_id,
        f"hash-{import_id}",
        task_id,
        reason,
    )
    return import_id


async def _status(conn, import_id: str) -> tuple[str, str | None]:
    row = await conn.fetchrow("SELECT status, reason FROM imports WHERE id = $1", import_id)
    return row["status"], row["reason"]


async def test_a_budget_stop_pauses_the_import_instead_of_failing_it(db):
    import_id = await _parsing_import(db, "task_budget")

    changed = await settle_imports(db, task_id="task_budget", outcome="budget", output="")

    assert changed == 1
    assert await _status(db, import_id) == ("paused", PAUSED_REASON)


async def test_an_error_fails_the_import_with_a_plain_reason(db):
    import_id = await _parsing_import(db, "task_err")

    await settle_imports(db, task_id="task_err", outcome="failed", output="Boom\nValueError: bad")

    assert await _status(db, import_id) == (
        "failed",
        "The parser stopped with an error: ValueError: bad",
    )


async def test_a_finished_parse_keeps_the_last_check_reason(db):
    import_id = await _parsing_import(db, "task_ok", reason="balance chain breaks at row 12")

    await settle_imports(db, task_id="task_ok", outcome="ok", output="")

    assert await _status(db, import_id) == ("failed", "balance chain breaks at row 12")


async def test_startup_settles_imports_whose_task_is_gone_and_skips_running_ones(db):
    gone = await _parsing_import(db, "task_gone")
    running = await _parsing_import(db, "task_live")
    records = {"task_live": SimpleNamespace(status="running", outcome=None, output="")}

    await reconcile(db, records)

    assert (await _status(db, gone))[0] == "failed"
    assert (await _status(db, running))[0] == "parsing"


async def test_the_chat_agent_is_told_which_parser_task_it_can_continue():
    deps = AgentDeps(conversation_id="thr_1", paused_parser_tasks=("task_9",))
    text = await _instructions(SimpleNamespace(deps=deps))

    assert 'resume="<task_id>"' in text
    assert "`task_9`" in text

    plain = await _instructions(SimpleNamespace(deps=AgentDeps(conversation_id="thr_1")))
    assert "Paused statement import" not in plain
