from __future__ import annotations

from typer.testing import CliRunner

from fin_agent.auth.setup import setup_required
from fin_agent.cli import app as cli_app
from tests.software_authenticator import SoftwareAuthenticator
from tests.test_auth import _complete_setup


def test_passkeys_reset_reopens_setup_with_a_fresh_token(client, setup_token, settings, db):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200

    result = CliRunner().invoke(cli_app, ["passkeys", "reset"])

    assert result.exit_code == 0, result.output
    assert "Deleted 1 passkey(s) and revoked 1 session(s)." in result.output
    assert "setup token" in result.output

    assert setup_required(db)
    assert db.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 1
    assert db.execute("SELECT count(*) AS n FROM webauthn_credentials").fetchone()["n"] == 0
    assert (
        db.execute("SELECT count(*) AS n FROM sessions WHERE revoked_at IS NULL").fetchone()["n"]
        == 0
    )

    token = result.output.strip().rsplit(": ", 1)[-1]
    replacement = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, token, replacement).status_code == 200


def test_passkey_list_is_empty_before_setup(client):
    result = CliRunner().invoke(cli_app, ["passkeys", "list"])

    assert result.exit_code == 0
    assert "No passkeys are registered." in result.output


def test_setup_token_alias_issues_a_token(client):
    result = CliRunner().invoke(cli_app, ["setup"])

    assert result.exit_code == 0, result.output
    assert "First-run setup token" in result.output


def test_migrate_reports_already_up_to_date(client):
    result = CliRunner().invoke(cli_app, ["migrate"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "already up to date"


def test_setup_token_command_refuses_when_setup_is_complete(client, setup_token, settings):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200

    result = CliRunner().invoke(cli_app, ["setup-token"])

    assert result.exit_code == 0
    assert "nothing to do" in result.output


def test_cli_requires_a_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    result = CliRunner().invoke(cli_app, ["migrate"])

    assert result.exit_code == 2
    assert "DATABASE_URL is not set" in result.output
