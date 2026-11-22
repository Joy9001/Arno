"""Financial context, projections, and read tools agree on ledger data."""

from __future__ import annotations

from datetime import date, timedelta

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from arno.agent.builder import build_agent
from arno.agent.capabilities import SANDBOX_HELPERS
from arno.agent.deps import AgentDeps
from arno.engine.money import money
from arno.engine.projection import CardAccount, statement_close
from arno.ledger.accounts import create_account, set_statement_balance
from arno.ledger.common import today as ledger_today
from arno.ledger.movements import record_expense
from arno.ledger.schedules import create_schedule
from arno.tools import projection as projection_tools
from arno.tools import read as read_tools


def _context_account(context: dict, account_id: str) -> dict:
    return next(item for item in context["accounts"] if item["id"] == account_id)


def _balance_age(inputs, account_id: str) -> dict:
    return next(row for row in inputs.data_quality["balance_ages"] if row["id"] == account_id)


def _answered(messages: list, tool_name: str) -> bool:
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == tool_name
        for message in messages
        for part in getattr(message, "parts", ())
    )


def test_statement_close_clamps_in_february_and_rolls_after_close():
    card = CardAccount("card", "Credit Card", 31, 10, 0, 100_000)

    assert statement_close(card, date(2026, 2, 28)) == date(2026, 2, 28)
    assert statement_close(card, date(2026, 3, 1)) == date(2026, 3, 31)


# --- The age of a balance, in the context and in the projection ---------------------------------


async def test_the_projection_ages_a_balance_from_the_same_evidence_as_the_context(ledger):
    today = ledger_today()
    stated = today - timedelta(days=100)
    account = await create_account(
        ledger,
        kind="bank",
        name="Aged bank",
        opening_balance_paise=10_000_000,
        opening_balance_as_of=stated,
    )
    await record_expense(
        ledger,
        account_id=account["id"],
        amount_paise=1_000,
        description="Chai",
        date=today - timedelta(days=2),
    )

    context = await read_tools.get_financial_context(ledger)
    context_row = _context_account(context, account["id"])
    assert context_row["age_days"] == 2

    inputs = await projection_tools.load_inputs(ledger)
    row = _balance_age(inputs, account["id"])
    assert row["as_of"] == (today - timedelta(days=2)).isoformat()
    assert row["age_days"] == context_row["age_days"]
    liquid = next(item for item in inputs.liquid if item.id == account["id"])
    assert liquid.balance_age_days == 2
    assert not any(
        "Aged bank balance is 101 days old" in gap for gap in inputs.data_quality["gaps"]
    )


# --- Available credit, in the payload and in the engine -----------------------------------------


async def test_the_engine_counts_a_credit_balance_the_way_the_context_does(ledger):
    today = ledger_today()
    card = await create_account(
        ledger,
        kind="card",
        name="Credit card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )
    await set_statement_balance(ledger, card["id"], balance_paise=50_000, as_of=today)

    context = await read_tools.get_financial_context(ledger)
    assert _context_account(context, card["id"])["available_credit"] == money(10_050_000)

    inputs = await projection_tools.load_inputs(ledger)
    engine_card = next(item for item in inputs.cards if item.id == card["id"])
    assert engine_card.outstanding_paise == 0
    assert engine_card.credit_paise == 50_000
    assert engine_card.available_credit_paise == 10_050_000

    # A debt still clamps at zero once it passes the limit, and an unknown card claims nothing.
    await set_statement_balance(ledger, card["id"], balance_paise=-10_100_000, as_of=today)
    inputs = await projection_tools.load_inputs(ledger)
    engine_card = next(item for item in inputs.cards if item.id == card["id"])
    assert engine_card.available_credit_paise == 0


# --- Why an amount is null ----------------------------------------------------------------------


async def test_an_unknown_card_bill_keeps_its_reason_in_the_context(ledger):
    today = ledger_today()
    card = await create_account(
        ledger,
        kind="card",
        name="Slice card",
        credit_limit_paise=10_000_000,
        statement_day=5,
        due_day=25,
    )
    # No statement balance: the ledger shows a debt, but the amount due is unknown.
    await record_expense(
        ledger,
        account_id=card["id"],
        amount_paise=194_700,
        description="Shoes",
        date=today - timedelta(days=31),
    )
    bank = (
        await create_account(ledger, kind="bank", name="Bank", opening_balance_paise=5_000_000)
    )["id"]
    await create_schedule(
        ledger,
        kind="obligation",
        subtype="rent",
        name="Flat rent",
        amount_paise=2_800_000,
        account_id=bank,
        rule="FREQ=MONTHLY;BYMONTHDAY=5",
        starts=date(2025, 1, 5),
        holiday_adjust="none",
    )

    context = await read_tools.get_financial_context(ledger)
    bill = next(event for event in context["upcoming"] if event["kind"] == "card_due")
    assert bill["amount"] is None
    assert "no statement balance" in bill["note"]
    rent = next(event for event in context["upcoming"] if event["name"] == "Flat rent")
    assert rent["amount"] is not None
    assert rent["note"] is None


# --- A script can print the rupee form ----------------------------------------------------------


def test_the_rupee_helper_is_a_sandbox_helper():
    from arno.agent.toolset import rupees

    assert rupees(12_345_678) == "₹1,23,456.78"
    assert rupees(-33) == "-₹0.33"
    assert rupees(300_000) == "₹3,000"
    assert "rupees" in SANDBOX_HELPERS


async def test_a_script_prints_rupees_with_the_helper(demo_ledger):
    def script(messages: list, info: AgentInfo) -> ModelResponse:
        del info
        if not _answered(messages, "run_code"):
            return ModelResponse(
                parts=[ToolCallPart("run_code", {"code": "print(rupees(12345678))"})]
            )
        return ModelResponse(parts=[TextPart("It is ₹1,23,456.78.")])

    agent = build_agent()
    with agent.override(model=FunctionModel(script)):
        result = await agent.run("What is this?", deps=AgentDeps(conversation_id="cnv_rupees"))

    printed = [
        str(part.content)
        for message in result.all_messages()
        for part in getattr(message, "parts", ())
        if isinstance(part, ToolReturnPart) and part.tool_name == "run_code"
    ]
    assert printed, "the script must have run"
    assert "₹1,23,456.78" in printed[0]


async def test_the_helper_list_matches_the_read_toolset():
    from pydantic_ai.models.test import TestModel
    from pydantic_ai.usage import RunUsage

    from arno.agent.toolset import build_read_toolset

    ctx = RunContext(
        deps=AgentDeps(conversation_id="cnv_helpers3"), model=TestModel(), usage=RunUsage()
    )
    names = sorted(await build_read_toolset().get_tools(ctx))
    assert names == sorted(SANDBOX_HELPERS)
