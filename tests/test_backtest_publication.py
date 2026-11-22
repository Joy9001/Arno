"""Backtests publish only measurements grounded in a known ledger history."""

from datetime import date

import pytest

from arno.engine import backtest
from arno.ledger.accounts import create_account
from arno.ledger.movements import record_income
from arno.tools.projection import load_inputs


async def test_a_backfilled_account_is_known_before_its_row_existed(ledger):
    created = await create_account(ledger, kind="bank", name="SBI savings", institution="SBI")
    await record_income(
        ledger,
        account_id=created["id"],
        amount_paise=500_000,
        description="SALARY",
        date=date(2026, 3, 5),
    )
    as_of = date(2026, 4, 1)

    inputs = await load_inputs(ledger, as_of=as_of, known_then=True)

    assert [row.id for row in inputs.liquid] == [created["id"]]
    assert "no liquid account" not in inputs.data_quality["setup_gaps"]


_README = "intro\n<!-- backtest:start -->\nold\n<!-- backtest:end -->\noutro\n"


def _score(group: str = "ledger") -> backtest.Score:
    return backtest.Score(
        group=group,
        as_of=date(2026, 1, 1),
        forecaster="flat",
        target="end",
        horizon=7,
        crps=100.0,
        pinball=(10.0, 50.0, 10.0),
        covered=True,
        width=0,
        pit=0.5,
        breach_probability=None,
        breached=None,
    )


def test_the_backtest_says_when_it_could_not_measure_an_origin(tmp_path):
    # Every origin was dropped for missing setup: nothing was measured.
    result = backtest.summarize([], source="real ledger", dropped=2, names=["flat"])

    report = backtest.format_report(result)
    assert "not a measurement" in report.lower()
    assert "2 dropped" in report
    assert "must not be published" in report

    readme = tmp_path / "README.md"
    readme.write_text(_README, encoding="utf-8")
    with pytest.raises(ValueError, match="not a measurement"):
        backtest.publish_report(result, readme)
    assert readme.read_text(encoding="utf-8") == _README


def test_a_measured_backtest_still_publishes_and_counts_what_it_dropped(tmp_path):
    result = backtest.summarize([_score()], source="real ledger", dropped=1, names=["flat"])
    readme = tmp_path / "README.md"
    readme.write_text(_README, encoding="utf-8")

    backtest.publish_report(result, readme)

    text = readme.read_text(encoding="utf-8")
    assert "Source: real ledger. 1 weekly origins scored, 1 dropped" in text
