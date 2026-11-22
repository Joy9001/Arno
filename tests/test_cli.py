from __future__ import annotations

import asyncio
import re

from typer.testing import CliRunner

from arno.auth.setup import setup_required
from arno.cli import app as cli_app
from arno.engine import hackerrank
from tests.fixtures.passkeys import complete_setup
from tests.software_authenticator import SoftwareAuthenticator

TOKEN_RE = re.compile(r"/setup#t=([A-Za-z0-9_\-]+)")


def test_backtest_refuses_to_publish_real_ledger(monkeypatch):
    def unexpected_settings():
        raise AssertionError("must reject publishing before opening the ledger")

    monkeypatch.setattr("arno.cli._settings", unexpected_settings)
    result = CliRunner().invoke(cli_app, ["backtest", "--source", "db", "--publish"])
    assert result.exit_code == 2
    assert "only available for synthetic holdout personas" in result.output


def test_hackerrank_only_writes_when_requested_and_available(tmp_path, monkeypatch):
    readme = tmp_path / "README.md"
    readme.write_text("existing results", encoding="utf-8")
    monkeypatch.setattr("arno.cli.REPO_ROOT", tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli_app, ["hackerrank", "--dataset", str(tmp_path / "missing"), "--publish"]
    )
    assert result.exit_code == 3
    assert readme.read_text(encoding="utf-8") == "existing results"

    monkeypatch.setattr(hackerrank, "score", lambda dataset: {"available": True})
    monkeypatch.setattr(hackerrank, "format_report", lambda result: "local results")

    def publish(result, path):
        path.write_text("updated", encoding="utf-8")

    monkeypatch.setattr(hackerrank, "publish_report", publish)
    assert runner.invoke(cli_app, ["hackerrank"]).exit_code == 0
    assert readme.read_text(encoding="utf-8") == "existing results"
    assert runner.invoke(cli_app, ["hackerrank", "--publish"]).exit_code == 0
    assert readme.read_text(encoding="utf-8") == "updated"


async def test_passkeys_reset_reopens_setup_with_a_fresh_enrollment_link(
    client, setup_token, settings, db
):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert complete_setup(client, setup_token, authenticator).status_code == 200

    result = await asyncio.to_thread(CliRunner().invoke, cli_app, ["passkeys", "reset"])

    assert result.exit_code == 0, result.output
    assert "Deleted 1 passkey(s) and revoked 1 session(s)." in result.output
    assert "/setup#t=" in result.output
    assert "Setup token (paste fallback):" in result.output

    assert await setup_required(db)
    assert await db.fetchval("SELECT count(*) FROM users") == 1
    assert await db.fetchval("SELECT count(*) FROM webauthn_credentials") == 0
    assert await db.fetchval("SELECT count(*) FROM sessions WHERE revoked_at IS NULL") == 0

    match = TOKEN_RE.search(result.output)
    assert match is not None
    token = match.group(1)
    replacement = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert complete_setup(client, token, replacement).status_code == 200


def test_passkey_list_is_empty_before_setup(client):
    result = CliRunner().invoke(cli_app, ["passkeys", "list"])

    assert result.exit_code == 0
    assert "No passkeys are registered." in result.output


def test_setup_alias_prints_an_enrollment_link(client):
    result = CliRunner().invoke(cli_app, ["setup"])

    assert result.exit_code == 0, result.output
    assert "First-run setup is open" in result.output
    assert "/setup#t=" in result.output
    assert "Setup token (paste fallback):" in result.output


def test_migrate_reports_already_up_to_date(client):
    result = CliRunner().invoke(cli_app, ["migrate"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "already up to date"


async def test_setup_token_command_refuses_when_setup_is_complete(client, setup_token, settings):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert complete_setup(client, setup_token, authenticator).status_code == 200

    result = await asyncio.to_thread(CliRunner().invoke, cli_app, ["setup-token"])

    assert result.exit_code == 0
    assert "use Settings" in result.output


def test_cli_requires_a_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    result = CliRunner().invoke(cli_app, ["migrate"])

    assert result.exit_code == 2
    assert "DATABASE_URL is not set" in result.output
