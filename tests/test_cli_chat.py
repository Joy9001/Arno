"""The CLI chat path and the export command."""

from __future__ import annotations

from pydantic_ai.models.test import TestModel
from typer.testing import CliRunner

from fin_agent.agent.builder import build_agent
from fin_agent.cli import app as cli_app


def test_cli_chat_runs_one_turn_and_saves_it(monkeypatch, demo_ledger):
    model = TestModel(call_tools=[], custom_output_text="Hello from the CLI.")
    monkeypatch.setattr("fin_agent.cli.build_agent", lambda: build_agent(model=model))
    runner = CliRunner()
    result = runner.invoke(cli_app, ["chat", "--message", "hi there"])
    assert result.exit_code == 0, result.output
    assert "Hello from the CLI." in result.output
    row = demo_ledger.execute(
        "SELECT count(*) AS n FROM messages WHERE role = 'assistant'"
    ).fetchone()
    assert row["n"] >= 1


def test_cli_export_writes_a_json_file(tmp_path, demo_ledger):
    runner = CliRunner()
    out = tmp_path / "export.json"
    result = runner.invoke(cli_app, ["export", "--format", "json", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "accounts" in out.read_text(encoding="utf-8")
