"""The model-facing write contract: typed movement kinds and setup data."""

from __future__ import annotations

import json
from datetime import date

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from fin_agent.agent.builder import build_agent
from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.toolset import update_setup
from fin_agent.agent.write_models import AccountSetup


async def _deps(conn, thread_id: str, message_id: str) -> AgentDeps:
    await conn.execute(
        "INSERT INTO conversations (id, title) VALUES ($1, 'write contract test')",
        thread_id,
    )
    row = await conn.fetchrow(
        """
        INSERT INTO messages (conversation_id, client_id, role, content, body_text)
        VALUES ($1, $2, 'user', '{}'::jsonb, 'test') RETURNING id
        """,
        thread_id,
        message_id,
    )
    return AgentDeps(conversation_id=thread_id, message_id=str(row["id"]))


def _tool_return(messages, name: str) -> dict | None:
    for message in messages:
        for part in getattr(message, "parts", []):
            if getattr(part, "tool_name", None) != name:
                continue
            content = getattr(part, "content", None)
            if content is None:
                continue
            if isinstance(content, dict):
                return content
            if isinstance(content, str):
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                    continue
    return None


async def _run(script, prompt: str, deps: AgentDeps):
    agent = build_agent()
    with agent.override(model=FunctionModel(script)):
        return await agent.run(prompt, deps=deps, message_history=[])


def _one_tool_script(tool_name: str, args: dict, answer: str = "Done."):
    def script(messages, info: AgentInfo) -> ModelResponse:
        if _tool_return(messages, tool_name) is None:
            return ModelResponse(parts=[ToolCallPart(tool_name, args)])
        return ModelResponse(parts=[TextPart(answer)])

    return script


def _expense_script(args: dict, answer: str = "Logged."):
    return _one_tool_script("record_movement", args, answer)


def test_card_create_requirements_are_in_the_model_visible_contract():
    schema = AccountSetup.model_json_schema()
    properties = schema["properties"]

    assert "card" in schema["description"]
    for field in ("credit_limit_rupees", "statement_day", "due_day"):
        assert "required on add" in properties[field]["description"].lower()
    assert "optional" in properties["last4"]["description"].lower()
    assert "omit it if unknown" in properties["opening_balance_rupees"]["description"].lower()

    tool_guidance = update_setup.__doc__ or ""
    assert "required" in tool_guidance
    assert "leave the card pending" in tool_guidance
    assert "update changes only the fields" in tool_guidance


async def test_expense_uses_amount_rupees_and_payee(demo_ledger):
    deps = await _deps(demo_ledger, "cnv_wr_expense", "msg_wr_expense")
    script = _expense_script(
        {
            "movement": {
                "kind": "expense",
                "amount_rupees": 450,
                "payee": "Swiggy",
                "category": "Food & Dining",
                "date": "2026-10-31",
            }
        }
    )
    result = await _run(script, "I spent ₹450 on Swiggy yesterday.", deps)
    assert result.output == "Logged."
    row = await demo_ledger.fetchrow(
        """
        SELECT m.id, m.kind, m.date, t.amount::bigint AS amount_paise,
               p.display_name AS payee, c.name AS category
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
        LEFT JOIN transfer_labels l ON l.transfer_id = t.id
        LEFT JOIN payees p ON p.id = l.payee_id
        LEFT JOIN categories c ON c.id = l.category_id
        WHERE m.kind = 'expense' AND ta.kind = 'spending'
        ORDER BY m.created_at DESC LIMIT 1
        """
    )
    assert row["kind"] == "expense"
    assert row["amount_paise"] == 45000
    assert row["date"] == date(2026, 10, 31)
    assert row["payee"] == "Swiggy"
    assert row["category"] == "Food & Dining"


async def test_expense_without_a_payee_is_a_row_with_no_payee(demo_ledger):
    """The schema must allow what the prompt asks for: no shop named, no payee invented."""
    deps = await _deps(demo_ledger, "cnv_wr_no_payee", "msg_wr_no_payee")
    script = _expense_script(
        {
            "movement": {
                "kind": "expense",
                "amount_rupees": 250,
                "description": "Hardware store",
                "category_guess": "Shopping",
            }
        }
    )
    result = await _run(script, "Spent ₹250 at a hardware store.", deps)
    assert result.output == "Logged."
    tool_return = _tool_return(result.all_messages(), "record_movement")
    assert tool_return is not None and tool_return.get("movement_id"), tool_return
    row = await demo_ledger.fetchrow(
        """
        SELECT t.amount::bigint AS amount_paise, p.display_name AS payee
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        LEFT JOIN transfer_labels l ON l.transfer_id = t.id
        LEFT JOIN payees p ON p.id = l.payee_id
        WHERE m.kind = 'expense' ORDER BY m.created_at DESC LIMIT 1
        """
    )
    assert row["amount_paise"] == 25_000
    assert row["payee"] is None


async def test_hinglish_expense_uses_the_same_contract(demo_ledger):
    deps = await _deps(demo_ledger, "cnv_wr_hinglish", "msg_wr_hinglish")
    script = _expense_script(
        {
            "movement": {
                "kind": "expense",
                "amount_rupees": 150,
                "payee": "Auto bhaiya",
                "category": "Transport",
            }
        }
    )
    await _run(script, "Auto bhaiya ko 150 diye abhi.", deps)
    row = await demo_ledger.fetchrow(
        """
        SELECT t.amount::bigint AS amount_paise, p.display_name AS payee
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        LEFT JOIN transfer_labels l ON l.transfer_id = t.id
        LEFT JOIN payees p ON p.id = l.payee_id
        WHERE m.kind = 'expense' ORDER BY m.created_at DESC LIMIT 1
        """
    )
    assert row["amount_paise"] == 15000
    assert row["payee"] == "Auto bhaiya"


async def test_correction_takes_amount_rupees(demo_ledger):
    deps = await _deps(demo_ledger, "cnv_wr_correction", "msg_wr_correction")
    state: dict[str, str] = {}

    def script(messages, info: AgentInfo) -> ModelResponse:
        written = _tool_return(messages, "record_movement")
        corrected = _tool_return(messages, "correct_movement")
        if written is None:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "record_movement",
                        {
                            "movement": {
                                "kind": "expense",
                                "amount_rupees": 450,
                                "payee": "Swiggy",
                                "category": "Food & Dining",
                            }
                        },
                    )
                ]
            )
        state["id"] = str(written["movement_id"])
        if corrected is None:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "correct_movement",
                        {"movement_id": state["id"], "changes": {"amount_rupees": 480}},
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Corrected.")])

    result = await _run(script, "Actually it was ₹480. Fix that.", deps)
    assert result.output == "Corrected."
    row = await demo_ledger.fetchrow(
        """
        SELECT t.amount::bigint AS amount_paise
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        WHERE m.kind = 'expense'
        ORDER BY m.created_at DESC LIMIT 1
        """
    )
    assert row["amount_paise"] == 48000
    reversal = await demo_ledger.fetchval("SELECT count(*) FROM movements WHERE kind = 'reversal'")
    assert reversal >= 1


async def test_split_takes_people_and_shares(demo_ledger):
    deps = await _deps(demo_ledger, "cnv_wr_split", "msg_wr_split")
    script = _expense_script(
        {
            "movement": {
                "kind": "split",
                "total_rupees": 1200,
                "my_share_rupees": 400,
                "people": [
                    {"name": "Ravi", "amount_rupees": 400},
                    {"name": "Amit", "amount_rupees": 400},
                ],
                "payee": "Dinner",
                "category": "Food & Dining",
            }
        }
    )
    await _run(script, "Split a ₹1,200 dinner with Ravi and Amit equally.", deps)
    row = await demo_ledger.fetchrow(
        """
        SELECT m.kind, sum(t.amount)::bigint AS total
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        WHERE m.kind = 'split'
        GROUP BY m.id, m.kind
        ORDER BY max(m.created_at) DESC LIMIT 1
        """
    )
    assert row["kind"] == "split"
    assert row["total"] == 120000
    people = await demo_ledger.fetchval(
        "SELECT count(*) FROM payees WHERE display_name IN ('Ravi', 'Amit')"
    )
    assert people == 2


async def test_preference_remembers_text(demo_ledger):
    deps = await _deps(demo_ledger, "cnv_wr_pref", "msg_wr_pref")
    script = _one_tool_script(
        "preference", {"action": "remember", "text": "Mom's monthly transfer is not discretionary"}
    )
    await _run(script, "Remember that Mom's monthly transfer isn't discretionary.", deps)
    row = await demo_ledger.fetchrow(
        "SELECT body, active FROM preferences ORDER BY created_at DESC LIMIT 1"
    )
    assert row["body"] == "Mom's monthly transfer is not discretionary"
    assert row["active"] is True


async def test_floor_uses_settings_data(demo_ledger):
    deps = await _deps(demo_ledger, "cnv_wr_floor", "msg_wr_floor")
    script = _one_tool_script(
        "update_setup",
        {"entity": "settings", "action": "update", "data": {"floor_rupees": 30000}},
    )
    await _run(script, "Set my floor to ₹30,000.", deps)
    row = await demo_ledger.fetchrow("SELECT floor_paise FROM settings WHERE id = 1")
    assert row["floor_paise"] == 3_000_000


async def test_schedule_uses_schedule_data(demo_ledger):
    deps = await _deps(demo_ledger, "cnv_wr_schedule", "msg_wr_schedule")
    script = _one_tool_script(
        "update_setup",
        {
            "entity": "schedule",
            "action": "add",
            "data": {
                "name": "Rent contract test",
                "kind": "obligation",
                "subtype": "rent",
                "amount_rupees": 30000,
                "rule": "FREQ=MONTHLY;BYMONTHDAY=5",
                "starts": "2026-11-05",
            },
        },
    )
    await _run(script, "My rent is now ₹30,000 a month from the 5th.", deps)
    row = await demo_ledger.fetchrow(
        "SELECT name, amount_paise FROM schedules WHERE name = 'Rent contract test' AND active"
    )
    assert row["name"] == "Rent contract test"
    assert row["amount_paise"] == 3_000_000


async def test_card_transfer_uses_from_and_to_accounts(demo_ledger):
    deps = await _deps(demo_ledger, "cnv_wr_transfer", "msg_wr_transfer")
    script = _expense_script(
        {
            "movement": {
                "kind": "transfer",
                "amount_rupees": 5000,
                "from_account": "HDFC Savings",
                "to_account": "Slice card",
            }
        }
    )
    await _run(script, "I paid ₹5,000 to my Slice card from HDFC.", deps)
    row = await demo_ledger.fetchrow(
        """
        SELECT fa.name AS from_name, ta.name AS to_name, t.amount::bigint AS amount_paise
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
        WHERE m.kind = 'transfer' ORDER BY m.created_at DESC LIMIT 1
        """
    )
    assert row["from_name"] == "HDFC Savings"
    assert row["to_name"] == "Slice card"
    assert row["amount_paise"] == 500_000


async def test_schedule_payment_tool_matches_existing_money_and_returns_undo(ledger):
    from fin_agent.ledger.accounts import create_account
    from fin_agent.ledger.common import today
    from fin_agent.ledger.movements import record_expense
    from fin_agent.ledger.schedules import create_schedule

    bank = await create_account(ledger, kind="bank", name="Match bank")
    schedule = await create_schedule(
        ledger,
        kind="obligation",
        name="Rent matching",
        amount_paise=100_000,
        rule="FREQ=MONTHLY",
        starts=today(),
        account_id=bank["id"],
    )
    movement = await record_expense(
        ledger,
        account_id=bank["id"],
        amount_paise=100_000,
        description="Rent receipt",
    )
    deps = await _deps(ledger, "cnv_wr_match", "msg_wr_match")
    script = _one_tool_script(
        "schedule_payment",
        {
            "schedule": schedule["id"],
            "due_date": today().isoformat(),
            "movement_id": movement["id"],
            "amount_rupees": "600.00",
        },
    )
    result = await _run(script, "Match this recorded payment to rent.", deps)
    payload = _tool_return(result.all_messages(), "schedule_payment")
    assert payload is not None
    assert payload["occurrence"]["remaining_paise"] == 40_000
    assert payload["undo_id"]
    assert await ledger.fetchval("SELECT count(*) FROM schedule_payments") == 1
    assert await ledger.fetchval(
        "SELECT outside_text_at IS NOT NULL FROM conversations WHERE id=$1", deps.conversation_id
    )
