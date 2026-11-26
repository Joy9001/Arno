"""run_python: attachments go to the sandbox by safe filename, and a dead sandbox is an answer."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from arno.tools import sandbox as sandbox_tools


async def _attachment(conn, filename: str | None, data: bytes) -> str:
    return await conn.fetchval(
        "INSERT INTO attachments (kind, filename, sha256, data) VALUES ('statement', $1, 'x', $2) "
        "RETURNING id",
        filename,
        data,
    )


def _transport(seen: list[dict], reply: dict | None = None, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.url.path == "/exec"
        return httpx.Response(
            status,
            json=reply or {"ok": True, "stdout": "42\n", "stderr": "", "result_json": {"n": 42}},
        )

    return httpx.MockTransport(handler)


async def test_files_arrive_under_safe_names_and_the_result_comes_back(ledger):
    first = await _attachment(ledger, "dir/bank.csv", b"a,b\n1,2\n")
    second = await _attachment(ledger, "script.py", b"x")
    third = await _attachment(ledger, "win\\dir.csv", b"w")
    seen: list[dict] = []
    result = await sandbox_tools.run_python(
        ledger,
        code="print(42)",
        attachment_ids=[first, second, third],
        sandbox_url="http://sandbox:8001",
        transport=_transport(seen),
    )
    assert result == {"ok": True, "stdout": "42\n", "stderr": "", "result_json": {"n": 42}}
    files = seen[0]["files"]
    assert base64.b64decode(files["dir_bank.csv"]) == b"a,b\n1,2\n"
    assert "win_dir.csv" in files
    assert f"{second}_script.py" in files and "script.py" not in files
    assert seen[0]["code"] == "print(42)"


async def test_an_unknown_attachment_is_a_retry_for_the_model(ledger):
    with pytest.raises(sandbox_tools.SandboxInputError, match="att_missing"):
        await sandbox_tools.run_python(
            ledger,
            code="1",
            attachment_ids=["att_missing"],
            sandbox_url="http://sandbox:8001",
            transport=_transport([]),
        )


async def test_output_is_capped_at_twenty_thousand_characters(ledger):
    big = {"ok": True, "stdout": "x" * 50_000, "stderr": "y" * 50_000, "result_json": None}
    result = await sandbox_tools.run_python(
        ledger,
        code="1",
        attachment_ids=[],
        sandbox_url="http://sandbox:8001",
        transport=_transport([], big),
    )
    assert len(result["stdout"]) == 20_000 and len(result["stderr"]) == 20_000


async def test_an_unreachable_sandbox_returns_an_error_the_model_can_explain(ledger):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    result = await sandbox_tools.run_python(
        ledger,
        code="1",
        attachment_ids=[],
        sandbox_url="http://sandbox:8001",
        transport=httpx.MockTransport(refuse),
    )
    assert result["ok"] is False and "unavailable" in result["stderr"]


async def test_a_refusal_from_the_sandbox_is_not_a_crash(ledger):
    result = await sandbox_tools.run_python(
        ledger,
        code="1",
        attachment_ids=[],
        sandbox_url="http://sandbox:8001",
        transport=_transport([], {"detail": "invalid filename"}, status=400),
    )
    assert result["ok"] is False and "400" in result["stderr"]
