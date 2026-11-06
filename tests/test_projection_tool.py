"""The DB adapter, the project tool card, and GET /api/projection."""

from __future__ import annotations

from datetime import UTC, date, datetime

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.toolsets import FunctionToolset

from fin_agent.agent import toolset as agent_tools
from fin_agent.agent.builder import build_agent
from fin_agent.agent.cards import cards_from_messages
from fin_agent.agent.deps import AgentDeps
from fin_agent.ledger.common import today as ledger_today
from fin_agent.tools import projection as projection_tools


def test_eval_grader_accepts_the_project_and_affordability_checks(demo_ledger):
    from evals.graders import grade_answers
    from evals.models import EvalOutput

    base = projection_tools.project(demo_ledger)
    reply = (
        f"{base['floor_probability'] * 100:.0f}% above the floor; the lowest median point is "
        f"₹{base['median_low']['paise'] / 100:,.0f}; a balance is 92 days old."
    )
    checks = grade_answers(
        demo_ledger,
        EvalOutput(reply=reply, started_at=datetime.now(UTC).isoformat()),
        {"check": "projection"},
    )
    assert next(check for check in checks if check.name == "floor_probability").passed
    assert next(check for check in checks if check.name == "median_low").passed
    assert next(check for check in checks if check.name == "data_quality_note").passed

    purchase = projection_tools.project(
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
    checks = grade_answers(
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


def test_project_from_the_ledger_has_a_curve_quality_and_source_ids(demo_ledger):
    result = projection_tools.project(demo_ledger, as_of=date(2026, 11, 1), runs=100)
    assert result["horizon_days"] >= 90
    assert len(result["curve"]) == result["horizon_days"] + 1
    assert 0.0 <= result["floor_probability"] <= 1.0
    assert result["source_ids"]["accounts"]
    assert result["source_ids"]["schedules"]
    assert result["source_ids"]["goals"]
    assert result["data_quality"]["balance_ages"]
    assert result["optimistic"] is not None

    compact = projection_tools.summary(result)
    assert "curve" not in compact
    assert compact["source_ids"] == result["source_ids"]
    assert compact["data_quality"]["gaps"]

    card = projection_tools.card(result)
    assert card["card"] == "projection"
    assert len(card["curve"]) == len(result["curve"])
    assert card["sources"]
    assert card["source_ids"]["accounts"]


def test_an_unknown_balance_with_activity_is_not_known_in_the_projection(ledger):
    from fin_agent.ledger.accounts import create_account
    from fin_agent.ledger.movements import record_expense

    account = create_account(ledger, kind="bank", name="SBI Savings")
    record_expense(ledger, account_id=account["id"], amount_paise=25_000, description="Lunch")

    result = projection_tools.project(ledger, as_of=ledger_today(), runs=20)

    age = next(row for row in result["data_quality"]["balance_ages"] if row["id"] == account["id"])
    assert age["known"] is False
    assert age["as_of"] is None
    assert age["balance_paise"] == -25_000
    assert any("SBI Savings has no stated balance" in gap for gap in result["data_quality"]["gaps"])
    assert any("recorded net change" in gap for gap in result["data_quality"]["gaps"])
    assert "no liquid account has a known balance" in result["data_quality"]["setup_gaps"]
    source = next(item for item in result["sources"] if item["id"] == account["id"])
    assert source["detail"] == "liquid · balance unknown"


def test_a_project_tool_call_on_an_unknown_balance_has_no_verdict(ledger):
    from fin_agent.ledger.accounts import create_account

    create_account(ledger, kind="bank", name="SBI Savings")

    result = projection_tools.project(
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
    assert "no liquid account has a known balance" in result["data_quality"]["setup_gaps"]
    assert "verdict" not in projection_tools.summary(result)


def test_project_with_a_hypothetical_returns_a_verdict_and_tradeoffs(demo_ledger):
    result = projection_tools.project(
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


def test_the_project_tool_direct_call_returns_a_projection_card(demo_ledger):
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
    result = agent.run_sync("project", deps=AgentDeps(conversation_id="cnv_test"))
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
    assert part.content["source_ids"]["accounts"]
    assert "curve" not in part.content


def test_a_project_call_inside_run_code_still_emits_its_card(demo_ledger):
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
        result = agent.run_sync("project", deps=AgentDeps(conversation_id="cnv_test"))
    cards = cards_from_messages(result.all_messages())
    projection = next(card for card in cards if card.data["card"] == "projection")
    assert projection.data["source_ids"]["accounts"]
    assert projection.data["curve"]


def test_get_api_projection_returns_the_panel_payload(authed_client, demo_ledger):
    response = authed_client.get("/api/projection", params={"horizon_days": 90})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["curve"]
    assert payload["source_ids"]["accounts"]
    assert payload["data_quality"]["balance_ages"]


def test_get_api_projection_with_a_hypothetical(authed_client, demo_ledger):
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


def test_get_api_projection_defaults_and_returns_the_emi_tenure(authed_client, demo_ledger):
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


def test_get_api_projection_rejects_an_amount_without_a_date(authed_client):
    response = authed_client.get("/api/projection", params={"amount_paise": 1000})
    assert response.status_code == 422
