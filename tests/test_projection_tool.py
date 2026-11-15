"""The DB adapter, the project tool card, and GET /api/projection."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from arno.agent import toolset as agent_tools
from arno.agent.builder import build_agent
from arno.agent.cards import cards_from_messages
from arno.agent.deps import AgentDeps
from arno.ledger.common import today as ledger_today
from arno.tools import projection as projection_tools


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


async def test_a_project_call_inside_run_code_still_emits_its_card(demo_ledger):
    code = (
        "result = await project(horizon_days=90)\n"
        'print(result["floor_probability"], result["source_ids"]["goals"])\n'
    )
    model = FunctionModel(
        lambda messages, info: (
            ModelResponse(parts=[ToolCallPart("run_code", {"code": code})])
            if len(messages) == 1
            else ModelResponse(parts=[TextPart("Here is the projection.")])
        )
    )
    agent = build_agent()
    with agent.override(model=model):
        result = await agent.run("project", deps=AgentDeps(conversation_id="cnv_test"))
    cards = cards_from_messages(result.all_messages())
    projection = next(card for card in cards if card.data["card"] == "projection")
    assert projection.data["source_ids"]["accounts"]
    assert projection.data["curve"]


async def test_get_api_projection_returns_the_panel_payload(authed_client, demo_ledger):
    response = authed_client.get("/api/projection", params={"horizon_days": 90})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["curve"]
    assert payload["source_ids"]["accounts"]
    assert payload["data_quality"]["balance_ages"]


async def test_get_api_projection_with_a_hypothetical(authed_client, demo_ledger):
    response = authed_client.get(
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


async def test_get_api_projection_defaults_and_returns_the_emi_tenure(authed_client, demo_ledger):
    response = authed_client.get(
        "/api/projection",
        params={
            "amount_paise": 120_000_000,
            "date": "2026-11-01",
            "method": "emi",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["hypothetical"]["emi_months"] == 3

    chosen = authed_client.get(
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


async def test_get_api_projection_rejects_an_amount_without_a_date(authed_client):
    response = authed_client.get("/api/projection", params={"amount_paise": 1000})
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
    with pytest.raises(ValueError, match="not emo_months"):
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
    assert snapshot.attributed is True
    assert [(row.date, row.amount_paise) for row in snapshot.spend] == [(date(2026, 9, 20), 45_000)]


async def test_load_snapshot_projects_uncovered_series_and_excludes_matched_spend(ledger):
    from arno.ledger.accounts import create_account
    from arno.ledger.movements import record_expense
    from arno.ledger.payees import create_payee
    from arno.tools import patterns
    from arno.tools.mark import mark

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

    assert snapshot.attributed is True
    assert len(snapshot.series) == 1
    forecast = snapshot.series[0]
    assert forecast.amounts == (64_900,)
    assert forecast.card_id == card["id"]
    assert forecast.category_id == cat_id
    assert forecast.reducible is bool(cat_row["reducible"])
    assert date(2026, 11, 1) in forecast.expected
    # The payee came from statement text, so the series carries its name marked, not bare.
    assert forecast.name == mark("Netflix")
    assert [(row.date, row.amount_paise) for row in snapshot.spend] == [(date(2026, 9, 18), 15_000)]

    early = await projection_tools.load_snapshot(ledger, as_of=date(2026, 9, 25), known_then=True)
    assert early.series == ()
