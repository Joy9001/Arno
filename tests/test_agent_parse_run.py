"""The parse run: Code Mode over the host functions, and a submitted script rerun fresh."""

from __future__ import annotations

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from fin_agent.agent.parse_run import run_parse
from fin_agent.importers.layout import detect_fingerprint
from fin_agent.importers.monty import ScriptContext
from fin_agent.importers.readers import Doc
from tests.import_fixtures import SBI_PARSER, sbi_csv


def _model(script: str) -> FunctionModel:
    code = f"result = await submit_parser(code={script!r})\nprint(result)"
    calls = {"n": 0}

    def respond(messages, info: AgentInfo) -> ModelResponse:
        del messages, info
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": code})])
        return ModelResponse(parts=[TextPart("Submitted the parser.")])

    return FunctionModel(respond)


async def test_a_parse_run_submits_a_script_and_keeps_rows_on_the_host():
    doc = Doc(sbi_csv())
    context = ScriptContext(
        period=("2026-04-01", "2026-04-30"),
        ledger_opening_paise=24567890,
        expected_fingerprint=detect_fingerprint(doc),
        allow_vision=True,
    )
    result = await run_parse(
        doc,
        context,
        account_name="SBI savings",
        model=_model(SBI_PARSER),
    )
    assert result.verified, result
    assert result.script and "doc_cells" in result.script
    assert len(result.accounts) == 1
    assert len(result.accounts[0]["rows"]) == 3
    assert result.checks[0]["status"] == "VERIFIED"


async def test_a_parse_run_reports_not_a_statement():
    doc = Doc(b"Salary slip\nNet pay 1,00,000\n")
    context = ScriptContext(allow_vision=False)
    result = await run_parse(
        doc,
        context,
        account_name=None,
        model=_model("{'not_a_statement': 'payslip'}"),
    )
    assert result.status == "not_a_statement"
    assert result.kind == "payslip"
