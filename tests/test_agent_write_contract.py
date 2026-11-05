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


def _deps(conn, thread_id: str, message_id: str) -> AgentDeps:
    conn.execute(
        "INSERT INTO conversations (id, title) VALUES (%s, 'write contract test')",
        (thread_id,),
    )
    row = conn.execute(
        """
        INSERT INTO messages (conversation_id, client_id, role, content, body_text)
        VALUES (%s, %s, 'user', '{}'::jsonb, 'test') RETURNING id
        """,
        (thread_id, message_id),
    ).fetchone()
    conn.commit()
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


def _run(script, prompt: str, deps: AgentDeps):
    agent = build_agent()
    with agent.override(model=FunctionModel(script)):
        return agent.run_sync(prompt, deps=deps, message_history=[])


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


def test_expense_uses_amount_rupees_and_payee(demo_ledger):
    deps = _deps(demo_ledger, "cnv_wr_expense", "msg_wr_expense")
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
    result = _run(script, "I spent ₹450 on Swiggy yesterday.", deps)
    assert result.output == "Logged."
    row = demo_ledger.execute(
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
    ).fetchone()
    assert row["kind"] == "expense"
    assert row["amount_paise"] == 45000
    assert row["date"] == date(2026, 10, 31)
    assert row["payee"] == "Swiggy"
    assert row["category"] == "Food & Dining"


def test_hinglish_expense_uses_the_same_contract(demo_ledger):
    deps = _deps(demo_ledger, "cnv_wr_hinglish", "msg_wr_hinglish")
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
    _run(script, "Auto bhaiya ko 150 diye abhi.", deps)
    row = demo_ledger.execute(
        """
        SELECT t.amount::bigint AS amount_paise, p.display_name AS payee
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        LEFT JOIN transfer_labels l ON l.transfer_id = t.id
        LEFT JOIN payees p ON p.id = l.payee_id
        WHERE m.kind = 'expense' ORDER BY m.created_at DESC LIMIT 1
        """
    ).fetchone()
    assert row["amount_paise"] == 15000
    assert row["payee"] == "Auto bhaiya"


def test_correction_takes_amount_rupees(demo_ledger):
    deps = _deps(demo_ledger, "cnv_wr_correction", "msg_wr_correction")
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

    result = _run(script, "Actually it was ₹480. Fix that.", deps)
    assert result.output == "Corrected."
    row = demo_ledger.execute(
        """
        SELECT t.amount::bigint AS amount_paise
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        WHERE m.kind = 'expense'
        ORDER BY m.created_at DESC LIMIT 1
        """
    ).fetchone()
    assert row["amount_paise"] == 48000
    reversal = demo_ledger.execute(
        "SELECT count(*) AS n FROM movements WHERE kind = 'reversal'"
    ).fetchone()
    assert reversal["n"] >= 1


def test_split_takes_people_and_shares(demo_ledger):
    deps = _deps(demo_ledger, "cnv_wr_split", "msg_wr_split")
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
    _run(script, "Split a ₹1,200 dinner with Ravi and Amit equally.", deps)
    row = demo_ledger.execute(
        """
        SELECT m.kind, sum(t.amount)::bigint AS total
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        WHERE m.kind = 'split'
        GROUP BY m.id, m.kind
        ORDER BY max(m.created_at) DESC LIMIT 1
        """
    ).fetchone()
    assert row["kind"] == "split"
    assert row["total"] == 120000
    people = demo_ledger.execute(
        "SELECT count(*) AS n FROM payees WHERE display_name IN ('Ravi', 'Amit')"
    ).fetchone()
    assert people["n"] == 2


def test_preference_remembers_text(demo_ledger):
    deps = _deps(demo_ledger, "cnv_wr_pref", "msg_wr_pref")
    script = _one_tool_script(
        "preference", {"action": "remember", "text": "Mom's monthly transfer is not discretionary"}
    )
    _run(script, "Remember that Mom's monthly transfer isn't discretionary.", deps)
    row = demo_ledger.execute(
        "SELECT body, active FROM preferences ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert row["body"] == "Mom's monthly transfer is not discretionary"
    assert row["active"] is True


def test_floor_uses_settings_data(demo_ledger):
    deps = _deps(demo_ledger, "cnv_wr_floor", "msg_wr_floor")
    script = _one_tool_script(
        "update_setup",
        {"entity": "settings", "action": "update", "data": {"floor_rupees": 30000}},
    )
    _run(script, "Set my floor to ₹30,000.", deps)
    row = demo_ledger.execute("SELECT floor_paise FROM settings WHERE id = 1").fetchone()
    assert row["floor_paise"] == 3_000_000


def test_schedule_uses_schedule_data(demo_ledger):
    deps = _deps(demo_ledger, "cnv_wr_schedule", "msg_wr_schedule")
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
    _run(script, "My rent is now ₹30,000 a month from the 5th.", deps)
    row = demo_ledger.execute(
        "SELECT name, amount_paise FROM schedules WHERE name = 'Rent contract test' AND active"
    ).fetchone()
    assert row["name"] == "Rent contract test"
    assert row["amount_paise"] == 3_000_000


def test_card_transfer_uses_from_and_to_accounts(demo_ledger):
    deps = _deps(demo_ledger, "cnv_wr_transfer", "msg_wr_transfer")
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
    _run(script, "I paid ₹5,000 to my Slice card from HDFC.", deps)
    row = demo_ledger.execute(
        """
        SELECT fa.name AS from_name, ta.name AS to_name, t.amount::bigint AS amount_paise
        FROM movements m
        JOIN pgledger_transfers t ON t.metadata ->> 'movement_id' = m.id
        JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
        JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
        WHERE m.kind = 'transfer' ORDER BY m.created_at DESC LIMIT 1
        """
    ).fetchone()
    assert row["from_name"] == "HDFC Savings"
    assert row["to_name"] == "Slice card"
    assert row["amount_paise"] == 500_000
