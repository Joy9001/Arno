"""The milestone 21 harness and the research `check_real.py` on synthetic fixtures."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from arno.importers.spike import check_real_command, run_synthetic
from tests.import_fixtures import SBI_PARSER, sbi_csv


def test_synthetic_spike_runs_three_banks():
    runs = run_synthetic()
    assert {run.bank for run in runs} == {"sbi", "union", "slice"}
    assert all(run.status == "VERIFIED" for run in runs)
    assert all(run.rows >= 3 for run in runs)


def test_check_real_agrees_with_the_synthetic_sbi_parser():
    folder = Path(tempfile.mkdtemp())
    lines = [line for line in sbi_csv().decode("utf-8").splitlines() if "," in line]
    statement = folder / "sbi.csv"
    statement.write_text("\n".join(lines) + "\n", encoding="utf-8")
    parser = folder / "parser.py"
    parser.write_text(SBI_PARSER.replace("await ", ""), encoding="utf-8")
    output = check_real_command(
        statement, parser, opening=24567890, period=("2026-04-01", "2026-04-30")
    )
    assert "PASS" in output


def test_no_real_folder_means_no_model_call(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from arno.importers.spike import run_real; from pathlib import Path; "
            "print(run_real(Path('private/statements-does-not-exist')))",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "No folder" in result.stdout
