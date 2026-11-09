"""Audit regressions 2: secrets typed in chat, the CLI turn save, and eval test isolation."""

from __future__ import annotations

import os
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai_harness.compaction import SlidingWindowCompaction

from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.history import create_thread, title_for
from fin_agent.agent.redaction import redact_text
from fin_agent.cli import _run_cli_turn
from fin_agent.db.conn import conninfo_to_dict

# --- A secret typed next to the word that names it --------------------------------------------


@pytest.mark.parametrize(
    ("text", "secret", "kind"),
    [
        ("otp hai 482913", "482913", "otp"),
        ("OTP is 4 8 2 9 1 3", "482913", "otp"),
        ("my otp 482913 please", "482913", "otp"),
        ("otp is 482913", "482913", "otp"),
        ("mera pin 4321 hai", "4321", "pin"),
        ("UPI PIN 4321", "4321", "pin"),
        ("PIN: 4321", "4321", "pin"),
        ("Mera MPIN 1234 hai", "1234", "pin"),
        ("my CVV is 123", "123", "cvv"),
        ("password: Abc@1234", "Abc@1234", "password"),
        ("my statement password is hunter2", "hunter2", "password"),
        ("my net banking password is passw0rd", "passw0rd", "password"),
        ("login password = Hunter2!", "Hunter2!", "password"),
        ("passwd: 98zxcv", "98zxcv", "password"),
        ("pwd 4821", "4821", "password"),
        ("OTP-482913", "482913", "otp"),
    ],
)
def test_a_secret_typed_next_to_its_name_is_redacted(text: str, secret: str, kind: str):
    redacted = redact_text(text)
    assert secret.replace(" ", "") not in redacted.replace(" ", "")
    assert f"[redacted:{kind}]" in redacted


@pytest.mark.parametrize(
    "text",
    [
        "I forgot my password",
        "what is my pin?",
        "my password is strong",
        "the OTP expires in ten minutes",
        "my upi pin is set correctly",
        "otp based login",
        "pin code 560001",
        "my pincode is 560001",
        "PIN code 560001",
        "PIN number 560001",
        "spent ₹4,321 on food",
        "4321 rupees",
        "paid 5000 for rent",
        "otp 4321 rupees",
        "otp 4,321",
        "pin 2026-11-10",
        "IPL match on 2026-11-10",
        "UTR 123456789012",
        "pin 123456789012",
        "upi ramesh@okaxis",
    ],
)
def test_amounts_dates_postal_codes_and_the_bare_words_are_left_alone(text: str):
    assert redact_text(text) == text


def test_the_whole_message_is_redacted_once_and_stays_readable():
    text = "mera pin 4321 hai and the otp 482913 came by SMS"
    redacted = redact_text(text)
    assert redacted == "mera [redacted:pin] hai and the [redacted:otp] came by SMS"
    assert redact_text(redacted) == redacted


async def test_a_pin_in_the_first_message_is_redacted_in_the_body_and_the_title(
    authed_client, demo_ledger
):
    model = TestModel(call_tools=[], custom_output_text="Noted.")
    body = {
        "id": "thread-secret-1",
        "trigger": "submit-message",
        "messages": [
            {
                "id": "client-secret-1",
                "role": "user",
                "parts": [{"type": "text", "text": "mera pin 4321 hai"}],
            }
        ],
    }
    with authed_client.app.state.agent.override(model=model):
        response = authed_client.post("/api/chat", json=body)
    assert response.status_code == 200
    thread = authed_client.get(f"/api/threads/{response.headers['x-thread-id']}/messages").json()
    stored = thread["messages"][0]["parts"][0]["text"]
    assert "4321" not in stored
    assert "[redacted:pin]" in stored
    assert "4321" not in thread["thread"]["title"]
    assert title_for("password: Abc@1234").startswith("[redacted:password]")


# --- The CLI saves the same turn the API does, even after compaction ---------------------------


def _an_old_exchange(index: int) -> list[Any]:
    return [
        ModelRequest(parts=[UserPromptPart(content=f"question {index}")]),
        ModelResponse(parts=[TextPart(f"answer {index}")]),
    ]


async def test_a_cli_turn_saves_its_reply_after_compaction_shortened_the_history(demo_ledger):
    history = [message for index in range(3) for message in _an_old_exchange(index)]

    def script(messages: list[Any], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("Saved after compaction.")])

    agent = Agent(
        FunctionModel(script),
        deps_type=AgentDeps,
        capabilities=[
            SlidingWindowCompaction(
                max_messages=3, keep_messages=2, preserve_first_user_message=False
            )
        ],
    )
    thread = await create_thread(demo_ledger, title="terminal chat")
    await _run_cli_turn(demo_ledger, agent, thread, history, "and now?")

    assistant = await demo_ledger.fetchrow(
        "SELECT content FROM messages WHERE conversation_id = $1 AND role = 'assistant'",
        thread["id"],
    )
    assert assistant is not None, "the reply was never saved"
    assert "Saved after compaction." in str(assistant["content"])
    assert (
        await demo_ledger.fetchval(
            "SELECT body_text FROM messages WHERE conversation_id = $1 AND role = 'user'",
            thread["id"],
        )
        == "and now?"
    )


# --- Eval test isolation -----------------------------------------------------------------------


def test_the_eval_test_template_is_named_for_this_run_s_database():
    import tests.test_evals as eval_tests
    from evals.lifecycle import DEFAULT_TEMPLATE

    database = str(conninfo_to_dict(os.environ["DATABASE_URL"])["dbname"])
    assert eval_tests.TEMPLATE.startswith(database)
    assert eval_tests.PREFIX.startswith(database)
    # `fin-agent eval` keeps its documented default; only the test's names are per-run.
    assert DEFAULT_TEMPLATE == "fin_agent_eval_template"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("pin number 4321", "4321"),
        ("PIN no. 4321", "4321"),
        ("मेरा पिन 4321 है", "4321"),
        ("ओटीपी 482913", "482913"),
        ("पासवर्ड है Abc@123", "Abc@123"),
    ],
)
def test_a_card_pin_number_and_devanagari_keywords_are_redacted(text: str, secret: str):
    assert secret not in redact_text(text)


def test_a_six_digit_pin_number_is_a_postal_code_and_stays():
    assert redact_text("pin number 560001") == "pin number 560001"


def test_the_model_sees_a_named_pin_redacted_like_storage_does():
    """The input guardrail uses the same detectors as `redact_text`, so a PIN never reaches the
    model or the stored model history."""
    from fin_agent.agent.builder import build_agent

    seen: list[str] = []

    def script(messages: list[Any], info: AgentInfo) -> ModelResponse:
        for message in messages:
            for part in getattr(message, "parts", []):
                content = getattr(part, "content", "")
                if isinstance(content, str):
                    seen.append(content)
        return ModelResponse(parts=[TextPart("ok")])

    agent = build_agent()
    with agent.override(model=FunctionModel(script)):
        result = agent.run_sync("mera pin 4321 hai, otp is 482913")
    stored = str(result.all_messages())
    assert not any("4321" in text or "482913" in text for text in seen)
    assert "4321" not in stored and "482913" not in stored
