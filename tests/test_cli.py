from __future__ import annotations

import asyncio

from typer.testing import CliRunner

from arno.cli import app as cli_app


def test_migrate_reports_already_up_to_date(client):
    result = CliRunner().invoke(cli_app, ["migrate"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "already up to date"


def test_cli_requires_a_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    result = CliRunner().invoke(cli_app, ["migrate"])

    assert result.exit_code == 2
    assert "DATABASE_URL is not set" in result.output


async def test_cli_export_writes_a_json_file(tmp_path, demo_ledger):
    runner = CliRunner()
    out = tmp_path / "export.json"
    result = await asyncio.to_thread(
        runner.invoke, cli_app, ["export", "--format", "json", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "accounts" in out.read_text(encoding="utf-8")
