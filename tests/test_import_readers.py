"""The host's file handling: kinds, locks, ZIPs, and PDF pages."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from arno.importers.readers import (
    DocError,
    is_locked,
    pdf_pages,
    preview_text,
    render_pages,
    sniff,
    unlock,
    unwrap_zip,
)
from tests.import_fixtures import minimal_pdf, minimal_xlsx, sbi_csv

LOCKED = (Path(__file__).parent / "fixtures" / "locked-1234.pdf").read_bytes()


def _zip(*names: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, "Date\n")
    return buffer.getvalue()


def test_the_kind_comes_from_the_bytes_not_the_name():
    assert sniff(minimal_pdf(["x"])) == "pdf"
    assert sniff(b"\x89PNG\r\n\x1a\nrest") == "image"
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image"
    assert sniff(sbi_csv()) == "text"
    assert sniff(minimal_xlsx([["a"]])) == "xlsx"
    assert sniff(_zip("a.csv")) == "zip"
    with pytest.raises(DocError):
        sniff(b"")


def test_a_locked_pdf_opens_only_with_its_password_and_the_copy_needs_none():
    assert is_locked(LOCKED)
    with pytest.raises(DocError) as wrong:
        unlock(LOCKED, "0000")
    assert wrong.value.code == "wrong_password"

    opened = unlock(LOCKED, "1234")

    assert not is_locked(opened)
    assert pdf_pages(opened)[0] >= 1


def test_an_unlocked_file_is_not_locked_and_unlocking_it_changes_nothing():
    data = minimal_pdf(["Date Narration"])
    assert not is_locked(data)
    assert unlock(sbi_csv(), "ignored") == sbi_csv()


def test_pages_are_counted_with_their_text_layer_and_rendered_to_png():
    data = minimal_pdf(["Date Narration Debit Credit Balance", "01/04/2026 TEA 20.00"])

    assert pdf_pages(data) == (1, 1)
    [page] = render_pages(data)
    assert page.startswith(b"\x89PNG\r\n\x1a\n")
    assert "Narration" in preview_text(data, "pdf")


def test_a_preview_reads_the_first_lines_of_text_and_spreadsheets():
    assert "Statement Period: 01/04/2026" in preview_text(sbi_csv(), "text")
    assert "Opening" in preview_text(minimal_xlsx([["Opening", 5], ["Date", "Amount"]]), "xlsx")
    assert preview_text(b"\x89PNG", "image") == ""


def test_a_zip_gives_up_its_one_file_and_refuses_many_or_too_many():
    assert unwrap_zip(_zip("only.csv")) == b"Date\n"
    with pytest.raises(DocError) as many:
        unwrap_zip(_zip("a.csv", "b.csv"))
    assert many.value.code == "zip_many"
    with pytest.raises(DocError) as too_many:
        unwrap_zip(_zip(*[f"part{index}.csv" for index in range(51)]))
    assert too_many.value.code == "zip_too_big"
    with pytest.raises(DocError) as escaped:
        unwrap_zip(_zip("../evil.csv"))
    assert escaped.value.code == "zip_path"
