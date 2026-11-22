from __future__ import annotations

import csv
import io
import json
import zipfile

import pytest

from arno.ledger.export import _csv_value, export_csv_zip, export_json, table_names

SECRET_TABLES = frozenset(
    {
        "model_credentials",
        "users",
        "sessions",
        "webauthn_credentials",
        "webauthn_challenges",
        "setup_tokens",
        "vapid_keys",
        "push_subscriptions",
    }
)


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_neutralizes_formula_prefixes_only_for_text(prefix):
    assert _csv_value(prefix + "123") == "'" + prefix + "123"
    assert _csv_value(-123) == -123


async def test_export_never_carries_credential_tables_or_secrets(ledger):
    await ledger.execute("INSERT INTO users (webauthn_user_handle) VALUES (decode('00', 'hex'))")
    await ledger.execute(
        "INSERT INTO vapid_keys (public_key, private_key, subject) "
        "VALUES ('public', 'PRIVATE-SECRET-KEY', 'mailto:demo@example.com')"
    )

    names = set(await table_names(ledger))
    assert names & SECRET_TABLES == set()
    assert {"movements", "audit_log"} <= names

    payload = await export_json(ledger)
    assert set(payload["tables"]) & SECRET_TABLES == set()
    assert payload["money_unit"] == "paise"
    assert "PRIVATE-SECRET-KEY" not in json.dumps(payload)


async def test_csv_export_neutralizes_text_without_changing_json(ledger):
    await ledger.execute(
        "INSERT INTO preferences (body, created_by) VALUES ('=HYPERLINK(x)', 'user')"
    )
    payload = await export_json(ledger)
    assert any(row["body"].startswith("=HYPERLINK") for row in payload["tables"]["preferences"])
    with zipfile.ZipFile(io.BytesIO(await export_csv_zip(ledger))) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("preferences.csv").decode())))
    assert any(row["body"].startswith("'=HYPERLINK") for row in rows)
