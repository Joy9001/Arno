from __future__ import annotations

import csv
import io
import zipfile

import pytest

from arno.ledger.export import _csv_value, export_csv_zip, export_json, table_names


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_neutralizes_formula_prefixes_only_for_text(prefix):
    assert _csv_value(prefix + "123") == "'" + prefix + "123"
    assert _csv_value(-123) == -123


async def test_export_carries_every_table_in_paise(ledger):
    names = set(await table_names(ledger))
    assert {"movements", "undo_log"} <= names

    payload = await export_json(ledger)
    assert set(payload["tables"]) == names
    assert payload["money_unit"] == "paise"


async def test_csv_export_neutralizes_text_without_changing_json(ledger):
    await ledger.execute(
        "INSERT INTO preferences (body, created_by) VALUES ('=HYPERLINK(x)', 'user')"
    )
    payload = await export_json(ledger)
    assert any(row["body"].startswith("=HYPERLINK") for row in payload["tables"]["preferences"])
    with zipfile.ZipFile(io.BytesIO(await export_csv_zip(ledger))) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read("preferences.csv").decode())))
    assert any(row["body"].startswith("'=HYPERLINK") for row in rows)
