"""The CLI chat path and the export command."""

from __future__ import annotations

import asyncio

from pydantic_ai.models.test import TestModel
from typer.testing import CliRunner

from arno.agent.builder import build_agent
from arno.cli import app as cli_app


class _SpyStore:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


async def test_cli_chat_runs_one_turn_and_saves_it(monkeypatch, demo_ledger):
    model = TestModel(call_tools=[], custom_output_text="Hello from the CLI.")
    store = _SpyStore()
    monkeypatch.setattr("arno.agent.spend.SharedSpendStore", lambda: store)
    monkeypatch.setattr("arno.cli.build_agent", lambda **_kwargs: build_agent(model=model))
    runner = CliRunner()
    result = await asyncio.to_thread(runner.invoke, cli_app, ["chat", "--message", "hi there"])
    assert result.exit_code == 0, result.output
    assert "Hello from the CLI." in result.output
    assert store.closed
    served = await demo_ledger.fetchval("SELECT count(*) FROM messages WHERE role = 'assistant'")
    assert served >= 1


async def test_cli_export_writes_a_json_file(tmp_path, demo_ledger):
    runner = CliRunner()
    out = tmp_path / "export.json"
    result = await asyncio.to_thread(
        runner.invoke, cli_app, ["export", "--format", "json", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "accounts" in out.read_text(encoding="utf-8")
