from __future__ import annotations

import asyncio
import re

from typer.testing import CliRunner

from arno.auth.setup import setup_required
from arno.cli import app as cli_app
from tests.software_authenticator import SoftwareAuthenticator
from tests.test_auth import _complete_setup

TOKEN_RE = re.compile(r"/setup#t=([A-Za-z0-9_\-]+)")


async def test_passkeys_reset_reopens_setup_with_a_fresh_enrollment_link(
    client, setup_token, settings, db
):
    authenticator = SoftwareAuthenticator(settings.rp_id, settings.rp_origin)
    assert _complete_setup(client, setup_token, authenticator).status_code == 200

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
    assert _complete_setup(client, token, replacement).status_code == 200


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
    assert _complete_setup(client, setup_token, authenticator).status_code == 200

    result = await asyncio.to_thread(CliRunner().invoke, cli_app, ["setup-token"])

    assert result.exit_code == 0
    assert "use Settings" in result.output


def test_cli_requires_a_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    result = CliRunner().invoke(cli_app, ["migrate"])

    assert result.exit_code == 2
    assert "DATABASE_URL is not set" in result.output
