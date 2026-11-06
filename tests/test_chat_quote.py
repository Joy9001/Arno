"""Quotes are validated snapshots, forwarded as context, and kept on the user message."""

from __future__ import annotations

from typing import Any

import pytest
from psycopg.types.json import Jsonb
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo

from fin_agent.agent.history import (
    create_thread,
    record_user_message,
    save_records,
)
from fin_agent.agent.quote import MAX_QUOTE_CHARS
from tests.agent_helpers import streaming_model
from tests.import_fixtures import sbi_csv


def _thread(db, thread_id: str) -> None:
    create_thread(db, title="quote", thread_id=thread_id)
    db.commit()


def _seed_message(db, thread_id: str, *, role: str, text: str, client_id: str) -> str:
    row = db.execute(
        """
        INSERT INTO messages (conversation_id, client_id, role, content, body_text)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
        """,
        (
            thread_id,
            client_id,
            role,
            Jsonb({"id": client_id, "role": role, "parts": [{"type": "text", "text": text}]}),
            text,
        ),
    ).fetchone()
    db.commit()
    return str(row["id"])


def _body(
    message_id: str,
    *,
    text: str | None,
    quote: dict[str, str] | None = None,
    parts: list[dict[str, Any]] | None = None,
    thread: str = "thread_quote",
) -> dict[str, Any]:
    message_parts = list(parts or [])
    if text is not None:
        message_parts.insert(0, {"type": "text", "text": text})
    message: dict[str, Any] = {"id": message_id, "role": "user", "parts": message_parts}
    if quote is not None:
        message["metadata"] = {"custom": {"quote": quote}}
    return {
        "id": thread,
        "trigger": "submit-message",
        "messageId": message_id,
        "messages": [message],
    }


def _capture(seen: list[list[Any]], reply: str = "The fee is explained."):
    def script(messages, info: AgentInfo) -> ModelResponse:
        del info
        seen.append(messages)
        return ModelResponse(parts=[TextPart(reply)])

    return streaming_model(script)


def _user_text(messages: list[Any]) -> str:
    return "\n".join(
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    )


def _system_text(messages: list[Any]) -> str:
    return "\n".join(
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, SystemPromptPart)
    )


def _stored_user(db, client_id: str) -> dict[str, Any]:
    return db.execute("SELECT content FROM messages WHERE client_id = %s", (client_id,)).fetchone()[
        "content"
    ]


def test_a_quote_reaches_the_model_and_survives_reload(authed_client, demo_ledger):
    _thread(demo_ledger, "thread_quote")
    source_id = _seed_message(
        demo_ledger,
        "thread_quote",
        role="assistant",
        text="The fee was 1.8% of the amount.",
        client_id="source-quote",
    )
    quote_text = "The fee was 1.8%"
    seen: list[list[Any]] = []
    body = _body(
        "client_quote",
        text="Why was that charged?",
        quote={"text": quote_text, "messageId": source_id},
    )

    with authed_client.app.state.agent.override(model=_capture(seen)):
        response = authed_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    assert f"> {quote_text}" in _user_text(seen[0])
    assert "Why was that charged?" in _user_text(seen[0])
    assert quote_text not in _system_text(seen[0])

    thread = authed_client.get("/api/threads/thread_quote/messages").json()
    user = next(message for message in thread["messages"] if message["role"] == "user")
    assert user["parts"] == [{"type": "text", "text": "Why was that charged?"}]
    assert user["metadata"]["custom"]["quote"] == {
        "text": quote_text,
        "messageId": source_id,
    }


def test_a_quote_can_name_the_live_assistant_client_id(authed_client, demo_ledger):
    _thread(demo_ledger, "thread_quote")
    row_id = _seed_message(
        demo_ledger,
        "thread_quote",
        role="assistant",
        text="A live reply.",
        client_id="live-assistant-id",
    )
    seen: list[list[Any]] = []
    body = _body(
        "client_live_id",
        text="What did you mean?",
        quote={"text": "A live reply.", "messageId": "live-assistant-id"},
    )

    with authed_client.app.state.agent.override(model=_capture(seen)):
        response = authed_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    assert row_id != "live-assistant-id"
    assert "> A live reply." in _user_text(seen[0])
    stored = _stored_user(demo_ledger, "client_live_id")
    assert stored["metadata"]["custom"]["quote"]["messageId"] == "live-assistant-id"


def test_a_just_streamed_reply_can_be_quoted_by_its_streamed_id(authed_client, demo_ledger):
    """The browser only knows the id from the stream's start chunk until it reloads."""
    import json

    _thread(demo_ledger, "thread_quote")
    seen: list[list[Any]] = []
    with authed_client.app.state.agent.override(model=_capture(seen, "Your floor is unset.")):
        first = authed_client.post("/api/chat", json=_body("client_first", text="Floor?"))
    assert first.status_code == 200, first.text
    start = next(
        json.loads(line[len("data: ") :])
        for line in first.text.splitlines()
        if line.startswith("data: {") and '"type":"start"' in line.replace(" ", "")
    )
    streamed_id = start["messageId"]

    body = _body(
        "client_second",
        text="Why?",
        quote={"text": "Your floor is unset.", "messageId": streamed_id},
    )
    with authed_client.app.state.agent.override(model=_capture(seen)):
        second = authed_client.post("/api/chat", json=body)

    assert second.status_code == 200, second.text
    assert "> Your floor is unset." in _user_text(seen[-1])


def test_a_quote_source_must_be_a_text_message_in_the_same_thread(authed_client, demo_ledger):
    _thread(demo_ledger, "thread_quote_a")
    _thread(demo_ledger, "thread_quote_b")
    source_id = _seed_message(
        demo_ledger,
        "thread_quote_a",
        role="assistant",
        text="Only in the first thread.",
        client_id="source-other-thread",
    )

    cross = authed_client.post(
        "/api/chat",
        json=_body(
            "client_cross",
            text="Explain this.",
            quote={"text": "Only in the first thread.", "messageId": source_id},
            thread="thread_quote_b",
        ),
    )
    assert cross.status_code == 422
    assert "no longer available" in cross.json()["detail"]

    missing = authed_client.post(
        "/api/chat",
        json=_body(
            "client_missing",
            text="Explain this.",
            quote={"text": "Gone.", "messageId": "msg_missing"},
        ),
    )
    assert missing.status_code == 422
    assert "no longer available" in missing.json()["detail"]
    assert (
        demo_ledger.execute(
            "SELECT count(*) AS n FROM messages "
            "WHERE client_id IN ('client_cross', 'client_missing')"
        ).fetchone()["n"]
        == 0
    )


def test_oversized_and_malformed_quotes_are_refused(authed_client, demo_ledger):
    _thread(demo_ledger, "thread_quote")
    oversized = authed_client.post(
        "/api/chat",
        json=_body(
            "client_oversized",
            text="Explain this.",
            quote={"text": "x" * (MAX_QUOTE_CHARS + 1), "messageId": "msg_any"},
        ),
    )
    assert oversized.status_code == 422
    assert "longer than" in oversized.json()["detail"]

    malformed = authed_client.post(
        "/api/chat",
        json=_body(
            "client_malformed",
            text="Explain this.",
            quote={"text": 42, "messageId": "msg_any"},  # type: ignore[dict-item]
        ),
    )
    assert malformed.status_code == 422
    assert "not valid" in malformed.json()["detail"]


def test_a_quote_is_redacted_before_it_is_stored_and_shown_to_the_model(authed_client, demo_ledger):
    _thread(demo_ledger, "thread_quote")
    source_id = _seed_message(
        demo_ledger,
        "thread_quote",
        role="assistant",
        text="Card 4111 1111 1111 1111 was charged.",
        client_id="source-secret",
    )
    seen: list[list[Any]] = []
    body = _body(
        "client_secret",
        text="What was that?",
        quote={"text": "Card 4111 1111 1111 1111 was charged.", "messageId": source_id},
    )

    with authed_client.app.state.agent.override(model=_capture(seen)):
        response = authed_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    assert "4111" not in _user_text(seen[0])
    assert "4111" not in str(seen[0])
    assert "[redacted:credit_card]" in _user_text(seen[0])
    stored = _stored_user(demo_ledger, "client_secret")
    quote = stored["metadata"]["custom"]["quote"]
    assert "4111" not in quote["text"]
    assert "[redacted:credit_card]" in quote["text"]
    model_history = demo_ledger.execute(
        "SELECT model_history FROM conversations WHERE id = 'thread_quote'"
    ).fetchone()["model_history"]
    assert "4111" not in str(model_history)


def test_a_second_turn_keeps_the_quote_in_history_without_duplicating_it(
    authed_client, demo_ledger
):
    _thread(demo_ledger, "thread_quote")
    source_id = _seed_message(
        demo_ledger,
        "thread_quote",
        role="assistant",
        text="The fee was 1.8% of the amount.",
        client_id="source-history",
    )
    quote_text = "The fee was 1.8%"
    seen: list[list[Any]] = []
    model = _capture(seen)

    with authed_client.app.state.agent.override(model=model):
        first = authed_client.post(
            "/api/chat",
            json=_body(
                "client_quote_first",
                text="Why?",
                quote={"text": quote_text, "messageId": source_id},
            ),
        )
        assert first.status_code == 200, first.text
        second = authed_client.post(
            "/api/chat",
            json=_body("client_quote_second", text="And later?"),
        )

    assert second.status_code == 200, second.text
    assert str(seen[1]).count(quote_text) == 1
    second_user = _stored_user(demo_ledger, "client_quote_second")
    assert "custom" not in (second_user.get("metadata") or {})


def test_a_quote_and_a_statement_keep_both_references(authed_client, demo_ledger):
    _thread(demo_ledger, "thread_quote")
    source_id = _seed_message(
        demo_ledger,
        "thread_quote",
        role="assistant",
        text="The statement covers October.",
        client_id="source-statement",
    )
    statement = authed_client.post(
        "/api/attachments",
        files={"file": ("sbi.csv", sbi_csv(), "text/csv")},
        data={"purpose": "statement"},
    )
    assert statement.status_code == 201, statement.text
    statement_ref = statement.json()
    seen: list[list[Any]] = []
    body = _body(
        "client_quote_statement",
        text="Use this statement.",
        quote={"text": "The statement covers October.", "messageId": source_id},
        parts=[
            {
                "type": "data-statement",
                "data": {
                    "attachment_id": statement_ref["id"],
                    "filename": statement_ref["filename"],
                },
            }
        ],
    )

    with authed_client.app.state.agent.override(model=_capture(seen)):
        response = authed_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    assert "The statement covers October." in _user_text(seen[0])
    thread = authed_client.get("/api/threads/thread_quote/messages").json()
    user = next(message for message in thread["messages"] if message["role"] == "user")
    assert [part["type"] for part in user["parts"]] == ["text", "data-statement"]
    assert user["metadata"]["custom"]["quote"]["text"] == "The statement covers October."
    assert (
        demo_ledger.execute(
            "SELECT count(*) AS n FROM imports WHERE attachment_id = %s",
            (statement_ref["id"],),
        ).fetchone()["n"]
        == 1
    )


def test_a_legacy_turn_without_a_quote_is_unchanged_and_keeps_the_stream_headers(
    authed_client, demo_ledger
):
    _thread(demo_ledger, "thread_quote")
    body = _body("client_plain", text="Hello")
    with authed_client.app.state.agent.override(model=_capture([])):
        response = authed_client.post("/api/chat", json=body)
        replay = authed_client.post("/api/chat", json=body)

    assert response.status_code == 200, response.text
    assert response.headers["x-resumable-stream-id"] == "thread_quote"
    assert response.headers["x-thread-id"] == "thread_quote"
    assert replay.headers["x-resumable-stream-id"] == "thread_quote"
    stored = _stored_user(demo_ledger, "client_plain")
    assert stored["parts"] == [{"type": "text", "text": "Hello"}]
    assert "custom" not in (stored.get("metadata") or {})


@pytest.mark.parametrize("has_client_id", [True, False])
@pytest.mark.parametrize("question", ["What does that mean?", ""])
def test_save_records_keeps_the_quote_metadata_and_the_typed_question(
    demo_ledger, has_client_id, question
):
    thread = create_thread(demo_ledger, title="quote-save", thread_id="thread_quote_save")
    demo_ledger.commit()
    quote = {"text": "Quoted line", "messageId": "msg_source"}
    record_user_message(
        demo_ledger,
        thread_id=thread["id"],
        client_id="client_save",
        text=question,
        quote=quote,
    )
    request = ModelRequest(parts=[UserPromptPart(f"> Quoted line\n\n{question}")])
    from pydantic_ai.ui._utils import set_ui_message_id

    if has_client_id:
        set_ui_message_id(request, "client_save")
    turn = [
        request,
        ModelResponse(parts=[TextPart("It means the fee.")]),
    ]

    save_records(
        demo_ledger,
        thread_id=thread["id"],
        run_id="run_save",
        ui_message_id="client_save",
        history=[],
        turn_messages=turn,
        ending="complete",
    )

    stored = _stored_user(demo_ledger, "client_save")
    assert stored["parts"] == ([{"type": "text", "text": question}] if question else [])
    assert stored["metadata"]["custom"]["quote"] == quote
