"""Statement extraction: routing, the vision path, the script path, and what the host does next."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from arno.agent.extractor import (
    Refused,
    handle_extract_import,
    image_agent,
    route,
    script_agent,
)
from arno.importers.checks import OPENING_MISSING
from arno.importers.extract import ExtractedAccount, ExtractedRow, ExtractedStatement, names_account
from arno.importers.pipeline import create_import
from arno.jobs import loop
from arno.ledger.accounts import create_account
from arno.tools import sandbox as sandbox_tools
from tests.fixtures import import_helpers as helpers
from tests.import_fixtures import sbi_csv, statement_from_csv

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _statement(*, closing: int = 55_000, **extra) -> ExtractedStatement:
    return ExtractedStatement(
        account=ExtractedAccount(name="SBI savings", institution="SBI"),
        closing=closing,
        period_start="2026-04-01",
        period_end="2026-04-30",
        rows=[
            ExtractedRow(
                date="2026-04-05", description="UPI/DR/5551/TEA STALL", debit=45_000, balance=55_000
            )
        ],
        **extra,
    )


def _answer(statement: ExtractedStatement):
    def respond(info) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, statement.model_dump())]
        )

    return respond


async def _bank(conn) -> dict:
    return await helpers.bank(conn)


async def _image_import(conn, account: dict | None = None) -> str:
    attachment_id = await helpers.attachment(conn, PNG, name="page.png", content_type="image/png")
    row = await create_import(
        conn, attachment_id=attachment_id, account_id=account["id"] if account else None
    )
    return row["id"]


def test_routing_sends_images_and_short_or_scanned_pdfs_to_vision_and_the_rest_to_a_script():
    assert route("image") == "image"
    assert route("pdf", pages=5, text_pages=5) == "image"
    assert route("pdf", pages=6, text_pages=6) == "script"
    assert route("pdf", pages=9, text_pages=0) == "image"
    for kind in ("xlsx", "xls", "ods", "text", "html", "ofx"):
        assert route(kind) == "script"


def test_routing_refuses_what_neither_path_can_read():
    for call in (
        lambda: route("pdf", pages=151, text_pages=151),
        lambda: route("pdf", pages=13, text_pages=0),
        lambda: route("doc"),
    ):
        with pytest.raises(Refused):
            call()


async def test_a_statement_image_is_read_in_one_vision_call(ledger):
    account = await _bank(ledger)
    import_id = await _image_import(ledger, account)
    calls: list[int] = []

    def respond(messages, info) -> ModelResponse:
        calls.append(len(messages))
        return _answer(_statement())(info)

    with image_agent.override(model=FunctionModel(respond)):
        await handle_extract_import(import_id)

    assert calls == [1]
    detail = await ledger.fetchrow("SELECT * FROM imports WHERE id = $1", import_id)
    assert detail["status"] == "review", detail["reason"]
    assert detail["check_status"] == "VERIFIED"
    assert (
        await ledger.fetchval(
            "SELECT count(*) FROM statement_lines WHERE import_id = $1", import_id
        )
        == 1
    )
    # The run is logged against the vision role.
    assert await ledger.fetchval("SELECT count(*) FROM llm_usage WHERE role = 'vision'") == 1


async def test_an_unset_vision_role_fails_the_image_path_with_what_to_set(ledger, monkeypatch):
    monkeypatch.delenv("VISION_BASE_URL", raising=False)
    import_id = await _image_import(ledger, await _bank(ledger))

    await handle_extract_import(import_id)

    row = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", import_id)
    assert row["status"] == "failed"
    assert "VISION_BASE_URL" in row["reason"]


async def test_a_failed_check_is_retried_once_with_its_reason(ledger):
    account = await _bank(ledger)
    import_id = await _image_import(ledger, account)
    prompts: list[str] = []

    def respond(messages, info) -> ModelResponse:
        prompts.append(str(messages[-1]))
        wrong = len(prompts) == 1
        return _answer(_statement(closing=99_999 if wrong else 55_000))(info)

    with image_agent.override(model=FunctionModel(respond)):
        await handle_extract_import(import_id)

    assert len(prompts) == 2
    assert "check on your reading failed" in prompts[1]
    assert "₹999.99" in prompts[1]
    assert await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == "review"


async def test_a_second_failed_check_fails_the_import_with_the_reason(ledger):
    account = await _bank(ledger)
    import_id = await _image_import(ledger, account)

    with image_agent.override(model=FunctionModel(lambda m, i: _answer(_statement(closing=1))(i))):
        await handle_extract_import(import_id)

    row = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", import_id)
    assert row["status"] == "failed"
    assert "closing" in row["reason"]
    assert await ledger.fetchval("SELECT count(*) FROM statement_lines") == 0


async def test_a_missing_opening_parks_with_the_rows_kept_and_is_not_retried(ledger):
    card = await helpers.card(ledger)
    import_id = await _image_import(ledger, card)
    calls: list[int] = []
    statement = ExtractedStatement(
        account=ExtractedAccount(name="Slice card", institution="Slice"),
        closing=-142_800,
        period_start="2026-04-01",
        period_end="2026-04-30",
        rows=[ExtractedRow(date="2026-04-01", description="BLINKIT", debit=42_800)],
    )

    def respond(messages, info) -> ModelResponse:
        calls.append(1)
        return _answer(statement)(info)

    with image_agent.override(model=FunctionModel(respond)):
        await handle_extract_import(import_id)

    assert calls == [1]
    row = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", import_id)
    assert (row["status"], row["reason"]) == ("needs_input", OPENING_MISSING)
    assert await ledger.fetchval("SELECT count(*) FROM statement_lines") == 1


async def test_a_document_that_is_not_a_statement_ends_plainly(ledger):
    import_id = await _image_import(ledger)

    with image_agent.override(
        model=FunctionModel(lambda m, i: _answer(ExtractedStatement(is_statement=False))(i))
    ):
        await handle_extract_import(import_id)

    row = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", import_id)
    assert row["status"] == "not_a_statement"
    assert "isn't a statement" in row["reason"]


async def test_a_model_error_fails_the_import_with_a_plain_reason(ledger):
    import_id = await _image_import(ledger)

    def broken(messages, info):
        raise RuntimeError("provider exploded with secret details")

    with image_agent.override(model=FunctionModel(broken)):
        await handle_extract_import(import_id)

    row = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", import_id)
    assert row["status"] == "failed"
    assert "could not read this statement" in row["reason"]
    assert "secret" not in row["reason"]


async def test_the_daily_limit_fails_the_import_before_any_model_call(ledger):
    import_id = await _image_import(ledger)
    await ledger.execute(
        "INSERT INTO llm_usage (role, model, input_tokens, output_tokens, usd)"
        " VALUES ('chat', 'm', 0, 0, 25)"
    )

    def never(messages, info):
        raise AssertionError("the limit must stop the request")

    with image_agent.override(model=FunctionModel(never)):
        await handle_extract_import(import_id)

    row = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", import_id)
    assert row["status"] == "failed"
    assert "daily spend limit" in row["reason"]


async def test_a_rejected_import_is_left_alone(ledger):
    import_id = await _image_import(ledger)
    await ledger.execute("UPDATE imports SET status = 'rejected' WHERE id = $1", import_id)

    def never(messages, info):
        raise AssertionError("a rejected import is not read")

    with image_agent.override(model=FunctionModel(never)):
        await handle_extract_import(import_id)

    assert (
        await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id) == "rejected"
    )


def _sandbox(monkeypatch, handler) -> list[str]:
    """Route the sandbox client's HTTP through `handler`, which gets each submitted script."""
    import httpx

    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        import json

        code = json.loads(request.content)["code"]
        seen.append(code)
        return httpx.Response(200, json=handler(code))

    real = sandbox_tools.run_python
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        sandbox_tools,
        "run_python",
        lambda conn, **kwargs: real(conn, **kwargs, transport=transport),
    )
    return seen


async def test_a_script_is_run_by_the_host_and_a_failed_check_sends_the_model_back(
    ledger, monkeypatch
):
    account = await create_account(
        ledger,
        kind="bank",
        name="SBI savings",
        institution="SBI",
        last4="6789",
        opening_balance_paise=24567890,
        opening_balance_as_of=date(2026, 3, 31),
    )
    data = sbi_csv()
    good = statement_from_csv(data, **helpers.PROFILES["sbi"])
    bad = good.model_copy(update={"closing": 1})
    import_id = await helpers.upload(ledger, data, account_id=account["id"])
    seen = _sandbox(
        monkeypatch,
        lambda code: {
            "ok": True,
            "stdout": "header at row 5",
            "stderr": "",
            "result_json": {"GOOD": good, "BAD": bad}[code].model_dump(mode="json")
            if code in ("GOOD", "BAD")
            else None,
        },
    )
    retries: list[str] = []

    def explore(info) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("run_python", {"code": "print(open)"})])

    def submit(script: str):
        def respond(info) -> ModelResponse:
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, {"script": script})]
            )

        return respond

    steps = [explore, submit("BAD"), submit("GOOD")]

    def spy(messages, info):
        retries.append(str(messages[-1]))
        return steps[len(retries) - 1](info)

    with script_agent.override(model=FunctionModel(spy)):
        await handle_extract_import(import_id)

    assert seen == ["print(open)", "BAD", "GOOD"]
    # The model saw the reason the checks gave, not the rows.
    assert "The checks failed" in retries[2]
    assert "closing" in retries[2]
    row = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", import_id)
    assert row["status"] == "review", row["reason"]
    assert await ledger.fetchval("SELECT count(*) FROM statement_lines") == 3
    assert await ledger.fetchval("SELECT count(*) FROM llm_usage WHERE role = 'parse'") == 3


async def test_a_script_that_never_passes_fails_the_import_with_the_last_reason(
    ledger, monkeypatch
):
    account = await _bank(ledger)
    import_id = await helpers.upload(ledger, sbi_csv(), account_id=account["id"])
    _sandbox(
        monkeypatch,
        lambda code: {"ok": False, "stdout": "", "stderr": "Traceback: boom", "result_json": None},
    )

    def submit(info) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"script": "x"})])

    with script_agent.override(model=FunctionModel(lambda m, i: submit(i))):
        await handle_extract_import(import_id)

    row = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", import_id)
    assert row["status"] == "failed"
    assert "did not pass the checks" in row["reason"]
    assert "boom" in row["reason"]


async def test_a_locked_file_goes_back_to_waiting_for_its_password(ledger):
    from pathlib import Path

    locked = (Path(__file__).parent / "fixtures" / "locked-1234.pdf").read_bytes()
    import_id = await helpers.upload(ledger, locked, name="locked.pdf")

    await handle_extract_import(import_id)

    assert (
        await ledger.fetchval("SELECT status FROM imports WHERE id = $1", import_id)
        == "needs_password"
    )


async def test_a_zip_of_one_statement_is_unwrapped_and_several_are_refused(ledger):
    import io
    import zipfile

    def archive(*names: str) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as bundle:
            for name in names:
                bundle.writestr(name, sbi_csv())
        return buffer.getvalue()

    many = await helpers.upload(ledger, archive("a.csv", "b.csv"), name="both.zip")
    await handle_extract_import(many)
    row = await ledger.fetchrow("SELECT status, reason FROM imports WHERE id = $1", many)
    assert row["status"] == "failed"
    assert "one statement at a time" in row["reason"]

    one = await helpers.upload(ledger, archive("only.csv"), name="one.zip")

    def stop(messages, info):
        raise RuntimeError("stop after the unwrap")

    with script_agent.override(model=FunctionModel(stop)):
        await handle_extract_import(one)
    stored = await ledger.fetchval(
        "SELECT a.data FROM imports i JOIN attachments a ON a.id = i.attachment_id WHERE i.id = $1",
        one,
    )
    assert bytes(stored) == sbi_csv()


async def test_boot_recovery_respawns_reading_for_imports_left_in_parsing(ledger, monkeypatch):
    waiting = await helpers.upload(ledger, sbi_csv())
    locked = await helpers.upload(ledger, sbi_csv() + b"\n", name="other.csv")
    await ledger.execute("UPDATE imports SET status = 'needs_password' WHERE id = $1", locked)
    done = await helpers.upload(ledger, sbi_csv() + b"\n\n", name="third.csv")
    await ledger.execute("UPDATE imports SET status = 'review' WHERE id = $1", done)
    spawned: list[str] = []
    monkeypatch.setattr(loop, "spawn", lambda coro, *, name: (coro.close(), spawned.append(name)))

    assert await loop.recover() == 1

    assert spawned == [f"extract-{waiting}"]


def test_a_line_names_an_account_by_its_name_or_last_four_but_not_a_bank_code_alone():
    hdfc = {"kind": "bank", "name": "HDFC Savings", "institution": "HDFC Bank", "last4": "4821"}
    assert names_account("TRANSFER TO SAVINGS 4821", hdfc, only_card=False)
    assert not names_account("UPI/DR/123/SWIGGY/HDFC", hdfc, only_card=False)
    card = {"kind": "card", "name": "Slice card", "institution": "Slice", "last4": "4242"}
    assert names_account("CC PAYMENT", card, only_card=True)
    assert not names_account("CC PAYMENT", card, only_card=False)


async def test_a_statement_line_naming_one_other_account_is_suggested_as_a_transfer(ledger):
    here = await helpers.bank(ledger, name="SBI Salary", institution="SBI", last4="7710")
    other = await helpers.bank(ledger, name="HDFC Savings", institution="HDFC", last4="4821")
    import_id = await helpers.upload(ledger, sbi_csv(), account_id=here["id"])
    await helpers.finalize(
        ledger,
        import_id,
        [helpers.row("TRANSFER TO SAVINGS 4821", debit=125_000)],
        opening=100_000,
        closing=-25_000,
    )

    detail = await ledger.fetchrow(
        "SELECT match_detail FROM statement_lines WHERE import_id = $1", import_id
    )
    assert detail["match_detail"]["suggested_transfer_account_id"] == other["id"]
