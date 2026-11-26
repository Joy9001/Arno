"""The DB adapter, the project tool card, and GET /api/projection."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from arno.agent import toolset as agent_tools
from arno.agent.deps import AgentDeps
from arno.db.conn import quote_ident
from arno.engine.inputs import CategoryRow, LedgerSnapshot, build_inputs
from arno.engine.projection import CardAccount, SpendRow
from arno.ledger.common import today as ledger_today
from arno.tools import projection as projection_tools


@pytest.fixture
def small_projection_runs(monkeypatch):
    """Shape and wiring tests use a fixed small ensemble, not the 16,384-run production default."""
    real_project = projection_tools.project
    real_with_inputs = projection_tools.project_with_inputs

    async def project(*args, **kwargs):
        kwargs["runs"] = 50
        return await real_project(*args, **kwargs)

    async def project_with_inputs(*args, **kwargs):
        kwargs["runs"] = 50
        return await real_with_inputs(*args, **kwargs)

    monkeypatch.setattr(projection_tools, "project", project)
    monkeypatch.setattr(projection_tools, "project_with_inputs", project_with_inputs)


async def test_snapshot_reads_in_one_read_only_repeatable_read_transaction(ledger, monkeypatch):
    read_settings = projection_tools.read_settings
    calls: list[int] = []

    async def check_isolation(conn):
        calls.append(1)
        assert await conn.fetchval("SHOW transaction_isolation") == "repeatable read"
        assert await conn.fetchval("SHOW transaction_read_only") == "on"
        return await read_settings(conn)

    monkeypatch.setattr(projection_tools, "read_settings", check_isolation)
    await projection_tools.load_snapshot(ledger)
    assert calls, "load_snapshot must fetch settings inside the read-only transaction"
    assert not ledger.is_in_transaction()


async def test_a_card_with_no_bill_does_not_fail_the_obligation_gate(ledger):
    from arno.ledger.accounts import create_account

    await create_account(ledger, kind="bank", name="Overdrawn bank", opening_balance_paise=-1_000)
    await create_account(
        ledger,
        kind="card",
        name="Unused card",
        statement_day=5,
        due_day=25,
        credit_limit_paise=20_000,
        opening_balance_paise=0,
    )
    result = await projection_tools.project(ledger, runs=1)
    assert result["floor_probability"] == 0
    assert result["obligations_met"]


def test_inputs_censor_today_and_share_history_across_cards():
    first = date(2026, 11, 1)
    second = first + timedelta(days=1)
    as_of = second + timedelta(days=1)
    inputs = build_inputs(
        LedgerSnapshot(
            as_of=as_of,
            floor_paise=0,
            history_start=first,
            cards=tuple(CardAccount(name, name, 5, 25, 0, 50_000) for name in ("a", "b")),
            categories=(CategoryRow("food", "Food", True),),
            spend=(
                SpendRow(first, "food", "a", 200),
                SpendRow(first, "food", "b", 100),
                SpendRow(second, "food", None, 100),
                SpendRow(as_of, "food", "b", 100_000),
            ),
        )
    )
    category = inputs.categories[0]
    assert category.history == ((first, 300), (second, 100))
    assert category.baseline_per_day_paise == 200
    assert category.card_shares == (("a", 0.5), ("b", 0.25))
    assert category.card_share == 0.75
    assert all(row.date < as_of for row in inputs.spend)


async def test_yearly_commitment_occurs_on_its_actual_date_with_a_goal_funding_plan(ledger):
    from arno.ledger.accounts import create_account
    from arno.ledger.goals import create_goal
    from arno.ledger.schedules import create_schedule

    bank = await create_account(
        ledger,
        kind="bank",
        name="Bills",
        opening_balance_paise=500_000,
        opening_balance_as_of=date(2026, 1, 1),
    )
    bill = await create_schedule(
        ledger,
        kind="obligation",
        name="Insurance",
        account_id=bank["id"],
        amount_paise=120_000,
        rule="FREQ=YEARLY;BYMONTH=6;BYMONTHDAY=1",
        starts=date(2026, 6, 1),
    )
    contribution = await create_schedule(
        ledger,
        kind="goal_contribution",
        name="Insurance reserve monthly",
        account_id=bank["id"],
        amount_paise=24_000,
        rule="FREQ=MONTHLY",
        starts=date(2026, 1, 1),
    )
    goal = await create_goal(
        ledger,
        name="Insurance reserve",
        target_paise=120_000,
        contribution_schedule_id=contribution["id"],
    )
    inputs = await projection_tools.load_inputs(ledger, as_of=date(2026, 1, 1), horizon_days=365)
    events = [event for event in inputs.events if event.name == "Insurance"]
    assert len(events) == 1
    assert events[0].date == date(2026, 6, 1)
    assert events[0].amount_paise == -120_000

    result = await projection_tools.project(
        ledger, as_of=date(2026, 1, 1), horizon_days=365, runs=10
    )
    row = next(goal_row for goal_row in result["goals"] if goal_row["id"] == goal["id"])
    assert row["target_paise"] == 120_000
    assert row["completes_on"] == "2026-06-01"
    assert row["reached"] is True
    assert date.fromisoformat(row["completes_on"]) <= events[0].date
    assert {bill["id"], contribution["id"]} <= set(result["source_ids"]["schedules"])
    assert goal["id"] in result["source_ids"]["goals"]


async def test_goal_reserves_are_excluded_from_liquid_cash_and_contributed_once(ledger):
    from arno.ledger.accounts import create_account
    from arno.ledger.goals import create_goal
    from arno.ledger.movements import record_transfer
    from arno.ledger.schedules import create_schedule

    today = ledger_today()
    bank = await create_account(
        ledger,
        kind="bank",
        name="Reserve bank",
        opening_balance_paise=1_000_000,
        opening_balance_as_of=today,
    )
    schedule = await create_schedule(
        ledger,
        kind="goal_contribution",
        name="Reserve monthly",
        rule="FREQ=MONTHLY",
        starts=today,
        amount_paise=50_000,
        account_id=bank["id"],
    )
    goal = await create_goal(
        ledger, name="Reserve", target_paise=500_000, contribution_schedule_id=schedule["id"]
    )
    await record_transfer(
        ledger,
        from_account_id=bank["id"],
        to_account_id=goal["account_id"],
        amount_paise=250_000,
        date=today,
    )
    inputs = await projection_tools.load_inputs(ledger, as_of=today, horizon_days=90)
    assert inputs.starting_balance_paise == 750_000
    assert sum(account.balance_paise for account in inputs.liquid) == 750_000
    assert inputs.goals[0].saved_paise == 250_000
    assert all(account.id != goal["account_id"] for account in inputs.liquid)
    events = [event for event in inputs.events if event.goal_id == goal["id"]]
    assert events
    assert all(event.amount_paise == -50_000 for event in events)

    result = await projection_tools.project(ledger, as_of=today, horizon_days=90, runs=10)
    assert result["curve"][0]["median_paise"] == 750_000
    goal_row = next(row for row in result["goals"] if row["id"] == goal["id"])
    assert goal_row["saved_paise"] == 250_000


async def test_purchase_projection_labels_follow_corrected_goal_contribution(ledger):
    from arno.ledger.accounts import create_account
    from arno.ledger.goals import create_goal
    from arno.ledger.movements import correct_movement, record_transfer

    today = ledger_today()
    bank = await create_account(
        ledger,
        kind="bank",
        name="Main bank",
        opening_balance_paise=1_000_000,
        opening_balance_as_of=today,
    )
    goal = await create_goal(ledger, name="Travel reserve", target_paise=500_000)
    movement = await record_transfer(
        ledger,
        from_account_id=bank["id"],
        to_account_id=goal["account_id"],
        amount_paise=100_000,
        date=today,
    )

    async def forecast():
        return await projection_tools.project(
            ledger,
            as_of=today,
            horizon_days=90,
            runs=10,
            hypothetical={
                "amount_paise": 10_000,
                "date": today.isoformat(),
                "method": "account",
                "account": "Main bank",
            },
        )

    before = await forecast()
    await correct_movement(ledger, movement["id"], changes={"amount_paise": 200_000})
    after = await forecast()
    for result, cash, saved in ((before, 900_000, 100_000), (after, 800_000, 200_000)):
        assert result["as_of"] == today.isoformat()
        assert (
            result["end"]
            == (
                date.fromisoformat(result["as_of"]) + timedelta(days=result["horizon_days"])
            ).isoformat()
        )
        assert result["hypothetical"]["account"] == "Main bank"
        assert result["hypothetical"]["date"] == today.isoformat()
        account = next(
            row for row in result["data_quality"]["balance_ages"] if row["id"] == bank["id"]
        )
        assert account["account"] == "Main bank"
        assert account["balance_paise"] == cash
        row = next(row for row in result["goals"] if row["id"] == goal["id"])
        assert row["name"] == "Travel reserve"
        assert row["saved_paise"] == saved
        summary = projection_tools.summary(result)
        card = projection_tools.card(result)
        assert (summary["as_of"], summary["horizon_days"]) == (
            result["as_of"],
            result["horizon_days"],
        )
        assert (card["as_of"], card["end"]) == (result["as_of"], result["end"])
        assert (
            summary["floor_probability"] == card["floor_probability"] == result["floor_probability"]
        )


@pytest.mark.usefixtures("small_projection_runs")
async def test_eval_grader_accepts_the_project_and_affordability_checks(demo_ledger):
    from evals.graders import grade_answers
    from evals.models import EvalOutput

    base = await projection_tools.project(demo_ledger)
    reply = (
        f"{base['floor_probability'] * 100:.0f}% above the floor; the lowest median point is "
        f"₹{base['median_low']['paise'] / 100:,.0f}; a balance is 92 days old."
    )
    checks = await grade_answers(
        demo_ledger,
        EvalOutput(reply=reply, started_at=datetime.now(UTC).isoformat()),
        {"check": "projection"},
    )
    assert next(check for check in checks if check.name == "floor_probability").passed
    assert next(check for check in checks if check.name == "median_low").passed
    assert next(check for check in checks if check.name == "data_quality_note").passed

    purchase = await projection_tools.project(
        demo_ledger,
        hypothetical={
            "amount_paise": 20_000_000,
            "date": ledger_today().isoformat(),
            "method": "account",
        },
    )
    word = {
        "yes": "yes",
        "yes-but": "yes, but",
        "no": "no, this is not affordable",
    }[purchase["verdict"]]
    affordability_reply = (
        f"{word}; {purchase['floor_probability'] * 100:.0f}% of futures stay above the floor."
    )
    checks = await grade_answers(
        demo_ledger,
        EvalOutput(reply=affordability_reply, started_at=datetime.now(UTC).isoformat()),
        {
            "check": "affordability",
            "amount_paise": 20_000_000,
            "date": "today",
            "method": "account",
        },
    )
    assert next(check for check in checks if check.name == "verdict").passed
    assert next(check for check in checks if check.name == "affordability_floor_probability").passed

    safe = base["safe_to_spend"]
    assert safe is not None
    safe_reply = (
        f"You can spend ₹{safe['amount_paise'] / 100:,.2f} today "
        f"(binding on {safe['binding_date']})."
    )
    safe_checks = await grade_answers(
        demo_ledger,
        EvalOutput(reply=safe_reply, started_at=datetime.now(UTC).isoformat()),
        {"check": "safe_to_spend"},
    )
    assert all(check.passed for check in safe_checks), safe_checks
    bad_safe = await grade_answers(
        demo_ledger,
        EvalOutput(reply="You can spend ₹1 today.", started_at=datetime.now(UTC).isoformat()),
        {"check": "safe_to_spend"},
    )
    assert any(not check.passed for check in bad_safe)


async def test_project_from_the_ledger_has_a_curve_quality_and_source_ids(demo_ledger):
    result = await projection_tools.project(demo_ledger, as_of=date(2026, 11, 1), runs=100)
    assert result["horizon_days"] >= 90
    assert len(result["curve"]) == result["horizon_days"] + 1
    assert 0.0 <= result["floor_probability"] <= 1.0
    assert result["source_ids"]["accounts"]
    assert result["source_ids"]["schedules"]
    assert result["source_ids"]["goals"]
    assert result["data_quality"]["balance_ages"]
    assert result["optimistic"] is not None
    assert result["safe_to_spend"] is not None
    assert result["safe_to_spend"]["amount_paise"] >= 0
    assert result["safe_to_spend"]["binding"] in {"floor", "obligation"}
    assert result["safe_to_spend"]["binding_date"]

    compact = projection_tools.summary(result)
    assert "curve" not in compact
    assert compact["source_ids"] == result["source_ids"]
    assert compact["data_quality"]["gaps"]
    assert compact["safe_to_spend"] is not None
    assert compact["safe_to_spend"]["amount"]["paise"] == result["safe_to_spend"]["amount_paise"]
    assert compact["safe_to_spend"]["binding"] == result["safe_to_spend"]["binding"]
    assert compact["safe_to_spend"]["binding_date"] == result["safe_to_spend"]["binding_date"]

    card = projection_tools.card(result)
    assert card["card"] == "projection"
    assert len(card["curve"]) == len(result["curve"])
    assert card["sources"]
    assert card["source_ids"]["accounts"]
    assert card["safe_to_spend"] == compact["safe_to_spend"]


async def test_an_unknown_balance_with_activity_is_not_known_in_the_projection(ledger):
    from arno.ledger.accounts import create_account
    from arno.ledger.movements import record_expense

    account = await create_account(ledger, kind="bank", name="SBI Savings")
    await record_expense(ledger, account_id=account["id"], amount_paise=25_000, description="Lunch")

    result = await projection_tools.project(ledger, as_of=ledger_today(), runs=20)

    age = next(row for row in result["data_quality"]["balance_ages"] if row["id"] == account["id"])
    assert age["known"] is False
    assert age["as_of"] is None
    assert age["balance_paise"] == -25_000
    assert any("SBI Savings has no stated balance" in gap for gap in result["data_quality"]["gaps"])
    assert any("recorded net change" in gap for gap in result["data_quality"]["gaps"])
    assert "no liquid account has a known balance" in result["data_quality"]["setup_gaps"]
    source = next(item for item in result["sources"] if item["id"] == account["id"])
    assert source["detail"] == "liquid · balance unknown"


async def test_a_project_tool_call_on_an_unknown_balance_has_no_verdict(ledger):
    from arno.ledger.accounts import create_account

    await create_account(ledger, kind="bank", name="SBI Savings")

    result = await projection_tools.project(
        ledger,
        as_of=date(2026, 11, 1),
        runs=20,
        hypothetical={
            "amount_paise": 5_000_000,
            "date": date(2026, 11, 1),
            "method": "account",
        },
    )
    assert "verdict" not in result
    assert result["tradeoffs"] == []
    assert result["safe_to_spend"] is None
    assert "no liquid account has a known balance" in result["data_quality"]["setup_gaps"]
    compact = projection_tools.summary(result)
    assert "verdict" not in compact
    assert compact["safe_to_spend"] is None
    assert projection_tools.card(result)["safe_to_spend"] is None


async def test_project_with_a_hypothetical_returns_a_verdict_and_tradeoffs(demo_ledger):
    result = await projection_tools.project(
        demo_ledger,
        as_of=date(2026, 11, 1),
        runs=100,
        hypothetical={
            "amount_paise": 120_000_000,
            "date": date(2026, 11, 1),
            "method": "account",
        },
    )
    assert result["verdict"] in {"yes", "yes-but", "no"}
    assert result["hypothetical"]["feasible"] is True
    assert isinstance(result["tradeoffs"], list)
    assert result["verdict_reason"]


async def test_the_project_tool_direct_call_returns_a_projection_card(demo_ledger):
    calls = {"n": 0}

    def script(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("project", {"horizon_days": 90})])
        return ModelResponse(parts=[TextPart("Done.")])

    model = FunctionModel(script)
    agent = Agent(
        model,
        deps_type=AgentDeps,
        toolsets=[FunctionToolset(tools=[agent_tools.project])],
    )
    result = await agent.run("project", deps=AgentDeps(conversation_id="cnv_test"))
    part = next(
        part
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "project"
    )
    assert part.metadata
    card = part.metadata[0].data
    assert card["card"] == "projection"
    assert card["source_ids"]["accounts"]
    content = part.content
    assert isinstance(content, dict)
    assert content["source_ids"]["accounts"]
    assert "curve" not in content


@pytest.mark.usefixtures("small_projection_runs")
async def test_get_api_projection_returns_the_panel_payload(session_client, demo_ledger):
    response = session_client.get("/api/projection", params={"horizon_days": 90})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["curve"]
    assert payload["source_ids"]["accounts"]
    assert payload["data_quality"]["balance_ages"]


@pytest.mark.usefixtures("small_projection_runs")
async def test_get_api_projection_with_a_hypothetical(session_client, demo_ledger):
    response = session_client.get(
        "/api/projection",
        params={
            "amount_paise": 120_000_000,
            "date": "2026-11-01",
            "method": "account",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["verdict"] in {"yes", "yes-but", "no"}
    assert "tradeoffs" in payload


@pytest.mark.usefixtures("small_projection_runs")
async def test_get_api_projection_defaults_and_returns_the_emi_tenure(session_client, demo_ledger):
    response = session_client.get(
        "/api/projection",
        params={
            "amount_paise": 120_000_000,
            "date": "2026-11-01",
            "method": "emi",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["hypothetical"]["emi_months"] == 3

    chosen = session_client.get(
        "/api/projection",
        params={
            "amount_paise": 120_000_000,
            "date": "2026-11-01",
            "method": "emi",
            "emi_months": 6,
        },
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["hypothetical"]["emi_months"] == 6


async def test_get_api_projection_rejects_an_amount_without_a_date(session_client):
    response = session_client.get("/api/projection", params={"amount_paise": 1000})
    assert response.status_code == 422


async def test_load_inputs_carries_a_cards_emi_terms(ledger):
    from arno.ledger.accounts import create_account

    card = await create_account(
        ledger,
        kind="card",
        name="Slice card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
        emi_rate_bps=1_650,
        emi_fee_paise=25_000,
    )

    inputs = await projection_tools.load_inputs(ledger)

    loaded = next(item for item in inputs.cards if item.id == card["id"])
    assert loaded.emi_rate_bps == 1_650
    assert loaded.emi_fee_paise == 25_000


def test_a_hypothetical_emi_takes_the_offers_rate_fee_and_no_cost():
    purchase = projection_tools._hypothetical(
        {
            "amount_paise": 5_000_000,
            "date": "2026-11-20",
            "method": "emi",
            "emi_rate_percent": 15.5,
            "emi_fee_paise": 20_000,
            "no_cost": True,
        }
    )

    assert purchase is not None
    assert purchase.emi_rate_bps == 1_550
    assert purchase.emi_fee_paise == 20_000
    assert purchase.no_cost is True


def test_a_hypothetical_without_emi_terms_leaves_them_unset():
    purchase = projection_tools._hypothetical(
        {"amount_paise": 5_000_000, "date": "2026-11-20", "method": "emi"}
    )

    assert purchase is not None
    assert purchase.emi_rate_bps is None
    assert purchase.emi_fee_paise is None
    assert purchase.no_cost is False


def test_an_unknown_hypothetical_key_is_still_rejected():
    with pytest.raises(ValueError, match="emo_months"):
        projection_tools._hypothetical(
            {"amount_paise": 5_000_000, "date": "2026-11-20", "emo_months": 3}
        )


async def test_load_snapshot_excludes_schedule_paid_and_one_off_movements_from_spend(ledger):
    from arno.ledger.accounts import create_account
    from arno.ledger.movements import record_expense
    from arno.ledger.schedule_payments import link_payment
    from arno.ledger.schedules import create_schedule

    bank = await create_account(
        ledger,
        kind="bank",
        name="SBI",
        opening_balance_paise=10_000_000,
        opening_balance_as_of=date(2026, 9, 1),
    )
    food_cat = await ledger.fetchval(
        "SELECT id FROM categories WHERE name = 'Food & Dining' AND active"
    )
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        subtype="rent",
        name="Rent",
        amount_paise=2_500_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2026, 8, 5),
    )
    paid_mov = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=2_500_000,
        description="September rent",
        category_id=str(food_cat) if food_cat else None,
        date=date(2026, 9, 5),
    )
    await link_payment(
        ledger,
        schedule_id=schedule["id"],
        due_date=date(2026, 9, 5),
        movement_id=paid_mov["id"],
    )

    one_off_mov = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=1_200_000,
        description="Laptop repair",
        category_id=str(food_cat) if food_cat else None,
        date=date(2026, 9, 12),
    )
    await ledger.execute("UPDATE movements SET one_off = true WHERE id = $1", one_off_mov["id"])

    await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=45_000,
        description="Lunch",
        category_id=str(food_cat) if food_cat else None,
        date=date(2026, 9, 20),
    )

    snapshot = await projection_tools.load_snapshot(ledger, as_of=date(2026, 10, 1))
    assert [(row.date, row.amount_paise) for row in snapshot.spend] == [(date(2026, 9, 20), 45_000)]


async def test_load_snapshot_projects_uncovered_series_and_excludes_matched_spend(ledger):
    from arno.ledger.accounts import create_account
    from arno.ledger.movements import record_expense
    from arno.ledger.payees import create_payee
    from arno.tools import patterns

    bank = await create_account(
        ledger,
        kind="bank",
        name="SBI",
        opening_balance_paise=10_000_000,
        opening_balance_as_of=date(2026, 9, 1),
    )
    card = await create_account(
        ledger,
        kind="card",
        name="HDFC Card",
        credit_limit_paise=5_000_000,
        statement_day=15,
        due_day=5,
    )
    cat_row = await ledger.fetchrow_required(
        "SELECT id, reducible FROM categories WHERE active ORDER BY name LIMIT 1"
    )
    cat_id = str(cat_row["id"])
    netflix = await create_payee(ledger, display_name="Netflix", kind="merchant", outside_text=True)

    for month in (6, 7, 8, 9):
        await record_expense(
            ledger,
            account_id=card["id"],
            amount_paise=64_900,
            description="Netflix",
            payee_id=netflix["id"],
            category_id=cat_id,
            date=date(2026, month, 1),
        )
    await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=15_000,
        description="Coffee",
        category_id=cat_id,
        date=date(2026, 9, 18),
    )

    as_of = date(2026, 10, 1)
    await patterns.detect(ledger, as_of=as_of)
    snapshot = await projection_tools.load_snapshot(ledger, as_of=as_of)

    assert [(event.date, event.amount_paise) for event in snapshot.series] == [
        (date(2026, 11, 1), -64_900),
        (date(2026, 12, 1), -64_900),
    ]
    assert {(event.kind, event.card_id, event.name) for event in snapshot.series} == {
        ("series", card["id"], "Netflix")
    }
    assert [(row.date, row.amount_paise) for row in snapshot.spend] == [(date(2026, 9, 18), 15_000)]

    early = await projection_tools.load_snapshot(ledger, as_of=date(2026, 9, 25), known_then=True)
    assert early.series == ()


async def test_project_changes_resolves_names(ledger):
    from arno.ledger.accounts import create_account
    from arno.ledger.goals import create_goal
    from arno.ledger.movements import record_expense
    from arno.ledger.payees import create_payee
    from arno.ledger.schedules import create_schedule
    from arno.tools import patterns

    as_of = date(2026, 10, 1)
    bank = await create_account(
        ledger,
        kind="bank",
        name="SBI",
        opening_balance_paise=10_000_000,
        opening_balance_as_of=date(2026, 9, 1),
    )
    await create_schedule(
        ledger,
        kind="income_fixed",
        name="Salary",
        amount_paise=8_000_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=1",
        starts=date(2026, 9, 1),
    )
    await create_schedule(
        ledger,
        kind="obligation",
        subtype="rent",
        name="Rent",
        amount_paise=2_500_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2026, 9, 5),
    )
    outside_sched = await create_schedule(
        ledger,
        kind="obligation",
        subtype="subscription",
        name="Outside Club",
        amount_paise=100_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=10",
        starts=date(2026, 9, 10),
    )
    await ledger.execute(
        "UPDATE schedules SET outside_text = true WHERE id = $1", outside_sched["id"]
    )
    await create_goal(ledger, name="Goa trip", target_paise=5_000_000)

    # Also create a detected recurring charge with outside_text=True so pause_schedule matches it.
    cat_id = str(
        await ledger.fetchval("SELECT id FROM categories WHERE active ORDER BY name LIMIT 1")
    )
    netflix = await create_payee(ledger, display_name="Netflix", kind="merchant", outside_text=True)
    for month in (6, 7, 8, 9):
        await record_expense(
            ledger,
            account_id=bank["id"],
            amount_paise=64_900,
            description="Netflix",
            payee_id=netflix["id"],
            category_id=cat_id,
            date=date(2026, month, 1),
        )
    await patterns.detect(ledger, as_of=as_of)

    # Case-insensitive exact resolution across schedule, series, and goal.
    result = await projection_tools.project(
        ledger,
        as_of=as_of,
        runs=20,
        changes=[
            {"kind": "stop_income", "schedule": "salary", "start": "2026-10-15"},
            {
                "kind": "change_amount",
                "schedule": "RENT",
                "amount_paise": 3_000_000,
                "start": "2026-11-01",
            },
            {"kind": "pause_schedule", "schedule": "netflix", "start": "2026-10-15"},
            {
                "kind": "add_recurring",
                "name": "Extra Goa SIP",
                "amount_paise": 500_000,
                "direction": "out",
                "start": "2026-11-05",
                "goal": "goa TRIP",
            },
        ],
    )
    scenario = result.get("scenario")
    assert scenario is not None
    labels = [item["label"] for item in scenario["changes"]]
    assert any("Netflix" in label for label in labels)

    # Unknown schedule lists active schedules and detected series.
    with pytest.raises(ValueError, match="unknown schedule") as missing_exc:
        await projection_tools.project(
            ledger,
            as_of=as_of,
            runs=20,
            changes=[{"kind": "pause_schedule", "schedule": "Gym", "start": "2026-10-15"}],
        )
    msg = str(missing_exc.value)
    assert "Outside Club" in msg
    assert "Netflix" in msg
    assert "Rent" in msg

    # Unknown goal lists active goals.
    with pytest.raises(ValueError, match="Goa trip"):
        await projection_tools.project(
            ledger,
            as_of=as_of,
            runs=20,
            changes=[
                {
                    "kind": "add_recurring",
                    "name": "SIP",
                    "amount_paise": 100_000,
                    "direction": "out",
                    "start": "2026-11-01",
                    "goal": "Europe",
                }
            ],
        )

    # Ambiguous schedules fail and list the matches.
    await create_schedule(
        ledger,
        kind="obligation",
        subtype="other",
        name="SIP",
        amount_paise=200_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=1",
        starts=date(2026, 9, 1),
    )
    await create_schedule(
        ledger,
        kind="obligation",
        subtype="other",
        name="sip",
        amount_paise=300_000,
        account_id=bank["id"],
        rule="FREQ=MONTHLY;BYMONTHDAY=15",
        starts=date(2026, 9, 15),
    )
    with pytest.raises(ValueError, match="multiple schedules match"):
        await projection_tools.project(
            ledger,
            as_of=as_of,
            runs=20,
            changes=[{"kind": "pause_schedule", "schedule": "SIP", "start": "2026-10-15"}],
        )


async def test_project_changes_summary_card_horizon_and_writes_nothing(demo_ledger):
    tables = [
        str(row["table_name"])
        for row in await demo_ledger.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        )
    ]
    before_counts = {
        table: int(await demo_ledger.fetchval(f"SELECT count(*) FROM {quote_ident(table)}"))
        for table in tables
    }

    as_of = date(2026, 11, 1)
    # Start 60 days after as_of -> horizon must reach at least 60 + 90 = 150 days.
    result = await projection_tools.project(
        demo_ledger,
        as_of=as_of,
        runs=20,
        changes=[
            {"kind": "stop_income", "start": "2026-12-31"},
            {
                "kind": "change_amount",
                "schedule": "Rent",
                "amount_paise": 3_500_000,
                "start": "2026-11-15",
            },
        ],
    )
    assert result["horizon_days"] >= 150
    assert any(
        "assumes the listed scenario changes" in gap for gap in result["data_quality"]["gaps"]
    )

    compact = projection_tools.summary(result)
    ui_card = projection_tools.card(result)
    for payload in (compact, ui_card):
        scen = payload.get("scenario")
        assert scen is not None
        assert len(scen["changes"]) == 2
        assert all(
            set(item) == {"kind", "label", "occurrences", "amount"}
            and set(item["amount"]) == {"paise", "text"}
            for item in scen["changes"]
        )
        assert isinstance(scen["assumptions"], list) and scen["assumptions"]
        assert isinstance(scen["holds"], bool)
        assert isinstance(scen["reason"], str) and scen["reason"]
        assert "runway_days" in scen
        baseline = scen["baseline"]
        assert set(baseline) == {
            "floor_probability",
            "median_low",
            "first_floor_crossing",
            "safe_to_spend",
        }

    # Combined changes + hypothetical returns verdict and omits holds from scenario.
    combined = await projection_tools.project(
        demo_ledger,
        as_of=as_of,
        runs=20,
        hypothetical={"amount_paise": 5_000_000, "date": "2026-11-10", "method": "account"},
        changes=[
            {
                "kind": "change_amount",
                "schedule": "Rent",
                "amount_paise": 3_500_000,
                "start": "2026-11-01",
            }
        ],
    )
    assert combined["verdict"] in {"yes", "yes-but", "no"}
    assert "holds" not in projection_tools.summary(combined)["scenario"]
    assert "holds" not in projection_tools.card(combined)["scenario"]

    after_counts = {
        table: int(await demo_ledger.fetchval(f"SELECT count(*) FROM {quote_ident(table)}"))
        for table in tables
    }
    assert after_counts == before_counts


@pytest.mark.usefixtures("small_projection_runs")
async def test_get_api_projection_with_changes_and_validation(session_client, demo_ledger):
    start = ledger_today().isoformat()
    ok = session_client.get(
        "/api/projection",
        params={"changes": json.dumps([{"kind": "stop_income", "start": start}])},
    )
    assert ok.status_code == 200, ok.text
    payload = ok.json()
    assert payload["scenario"] is not None
    assert payload["scenario"]["changes"][0]["kind"] == "stop_income"

    bad_json = session_client.get("/api/projection", params={"changes": "{bad"})
    assert bad_json.status_code == 422

    not_list = session_client.get(
        "/api/projection",
        params={"changes": json.dumps({"kind": "stop_income", "start": start})},
    )
    assert not_list.status_code == 422

    unknown_sched = session_client.get(
        "/api/projection",
        params={
            "changes": json.dumps([{"kind": "pause_schedule", "schedule": "Gym", "start": start}])
        },
    )
    assert unknown_sched.status_code == 422
    assert "unknown schedule" in unknown_sched.json()["detail"].lower()

    empty_every = session_client.get(
        "/api/projection",
        params={
            "changes": json.dumps(
                [
                    {
                        "kind": "add_recurring",
                        "name": "Club",
                        "amount_paise": 100_000,
                        "direction": "out",
                        "start": start,
                        "every": "",
                    }
                ]
            )
        },
    )
    assert empty_every.status_code == 422
    assert "every" in empty_every.json()["detail"].lower()


async def test_project_changes_loads_schedule_two_years_out(demo_ledger):
    from datetime import timedelta

    as_of = date(2026, 11, 1)
    far_start = as_of + timedelta(days=700)
    far_until = as_of + timedelta(days=760)
    result = await projection_tools.project(
        demo_ledger,
        as_of=as_of,
        runs=20,
        changes=[
            {
                "kind": "change_amount",
                "schedule": "Rent",
                "amount_paise": 3_500_000,
                "start": far_start.isoformat(),
                "until": far_until.isoformat(),
            }
        ],
    )
    assert result["horizon_days"] >= 760
    assert result["scenario"]["changes"][0]["occurrences"] >= 2


async def test_agent_project_turns_validation_errors_into_model_retry(demo_ledger):
    from arno.ledger.schedules import create_schedule

    bank = await demo_ledger.fetchrow_required(
        "SELECT id FROM accounts WHERE kind = 'bank' AND active ORDER BY name LIMIT 1"
    )
    outside_sched = await create_schedule(
        demo_ledger,
        kind="obligation",
        subtype="subscription",
        name="Outside Stream",
        amount_paise=50_000,
        account_id=str(bank["id"]),
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2026, 9, 5),
    )
    await demo_ledger.execute(
        "UPDATE schedules SET outside_text = true WHERE id = $1", outside_sched["id"]
    )
    await demo_ledger.execute(
        "INSERT INTO conversations (id, title) VALUES ('cnv_retry', 'Retry check') "
        "ON CONFLICT DO NOTHING"
    )

    seen_retry: list[str] = []
    calls = {"n": 0}

    def script(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "project",
                        {
                            "changes": [
                                {
                                    "kind": "pause_schedule",
                                    "schedule": "Gym",
                                    "start": ledger_today().isoformat(),
                                }
                            ]
                        },
                    )
                ]
            )
        if calls["n"] == 2:
            for msg in messages:
                for part in getattr(msg, "parts", ()):
                    if getattr(part, "part_kind", None) in {"retry-prompt", "tool-return"}:
                        seen_retry.append(str(part.content))
            return ModelResponse(parts=[ToolCallPart("project", {"changes": []})])
        for msg in messages:
            for part in getattr(msg, "parts", ()):
                if getattr(part, "part_kind", None) in {"retry-prompt", "tool-return"}:
                    seen_retry.append(str(part.content))
        return ModelResponse(parts=[TextPart("Which schedule did you mean?")])

    agent = Agent(
        FunctionModel(script),
        deps_type=AgentDeps,
        retries=2,
        toolsets=[FunctionToolset(tools=[agent_tools.project])],
    )
    result = await agent.run("Pause my Gym schedule", deps=AgentDeps(conversation_id="cnv_retry"))
    assert result.output == "Which schedule did you mean?"
    assert any("unknown schedule" in msg.lower() for msg in seen_retry)
    assert any("at least 1 item" in msg.lower() for msg in seen_retry)


async def test_the_project_tool_echoes_the_request_it_was_asked(demo_ledger):
    today = ledger_today()
    arguments = {
        "horizon_days": 120,
        "hypothetical": {
            "amount_paise": 5_000_000,
            "date": today.isoformat(),
            "method": "card",
            "account": "Slice card",
        },
        "changes": [{"kind": "stop_income", "start": (today + timedelta(days=3)).isoformat()}],
    }

    def script(messages, info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("project", arguments)])
        return ModelResponse(parts=[TextPart("Done.")])

    agent = Agent(
        FunctionModel(script),
        deps_type=AgentDeps,
        toolsets=[FunctionToolset(tools=[agent_tools.project])],
    )
    result = await agent.run("project", deps=AgentDeps(conversation_id="cnv_test"))
    card = next(
        part.metadata[0].data
        for message in result.all_messages()
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "project"
    )
    assert card["request"] == arguments


@pytest.mark.usefixtures("small_projection_runs")
async def test_cut_scenario_is_validated_by_the_projection_endpoint(session_client, demo_ledger):
    invalid = session_client.get("/api/projection", params={"cut_percent": 51})
    assert invalid.status_code == 422
    cut = session_client.get("/api/projection", params={"cut_percent": 25})
    assert cut.status_code == 200


def test_precut_affordability_never_proposes_a_second_cut():
    from arno.engine.affordability import Hypothetical, afford
    from arno.engine.projection import (
        LiquidAccount,
        ProjectionInput,
        SpendCategory,
        reduce_categories,
    )

    today = date(2026, 1, 1)
    category = SpendCategory(
        id="cat",
        name="Food",
        baseline_per_day_paise=30_000,
        reducible=True,
        history=tuple((today - timedelta(days=i + 1), 30_000) for i in range(90)),
    )
    inputs = ProjectionInput(
        as_of=today,
        floor_paise=3_000_000,
        liquid=(LiquidAccount(id="acc", name="Bank", balance_paise=5_000_000),),
        categories=(category,),
        ask_horizon_days=90,
    )
    reduced, _ = reduce_categories(inputs, 0.25)
    result = afford(
        reduced,
        Hypothetical(amount_paise=1_550_000, date=today),
        runs=50,
        allow_cut_tradeoffs=False,
    )
    assert all("cut" not in trade["kind"] for trade in result["tradeoffs"])
    assert reduced.categories[0].reduce_ratio == 0.25
