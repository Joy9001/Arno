"""Unit and integration tests for the native multimodal image harness and batch writes."""

from __future__ import annotations

import base64
import json
from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent
from pydantic_ai.capabilities import Instrumentation
from pydantic_ai.messages import (
    BinaryContent,
    CachePoint,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestContext, ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage

from arno.agent import history as history_mod
from arno.agent.deps import AgentDeps
from arno.agent.images import ImageAttachments, with_photos
from arno.ledger.audit import new_id
from arno.tools import write as write_tools
from tests.agent_helpers import run_context

# Minimal valid 1x1 PNG (67 bytes), no Pillow required.
VALID_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


async def _insert_photo(conn: Any, data: bytes = VALID_PNG, *, filename: str = "shot.png") -> str:
    att_id = await new_id(conn, "att")
    await conn.execute(
        """
        INSERT INTO attachments (id, kind, filename, content_type, size_bytes, sha256, data)
        VALUES ($1, 'photo', $2, 'image/png', $3, $4, $5)
        """,
        att_id,
        filename,
        len(data),
        f"sha_{att_id}",
        data,
    )
    return att_id


async def test_with_photos_cache_point_and_no_image_tools(demo_ledger):
    att_id = await _insert_photo(demo_ledger, filename="card.png")
    run_input = SubmitMessage(
        id="cnv_guard",
        trigger="submit-message",
        messages=[
            UIMessage(
                id="msg_guard",
                role="user",
                parts=[TextUIPart(text="Paid 4111 1111 1111 1111 to Swiggy")],
            )
        ],
    )
    updated = with_photos(run_input, [{"attachment_id": att_id, "filename": "card.png"}])
    assert len(updated.messages[0].parts) == 1
    assert isinstance(updated.messages[0].parts[0], TextUIPart)

    seen_prompts: list[list[Any]] = []
    seen_tools: list[str] = []

    def script(messages: list[Any], info: AgentInfo) -> ModelResponse:
        seen_tools.extend(tool.name for tool in info.function_tools)
        for msg in messages:
            for part in getattr(msg, "parts", []):
                if isinstance(part, UserPromptPart):
                    content = part.content if isinstance(part.content, list) else [part.content]
                    seen_prompts.append(content)
        return ModelResponse(parts=[TextPart("Noted.")])

    cap = ImageAttachments()
    assert cap.get_toolset() is None

    agent: Agent[AgentDeps, str] = Agent(
        FunctionModel(script),
        deps_type=AgentDeps,
        capabilities=[cap],
    )
    deps = AgentDeps(conversation_id="cnv_guard", image_attachments=(att_id,))
    await agent.run(updated.messages[0].parts[0].text, deps=deps)

    assert seen_tools == []
    assert deps.saw_images is True
    assert len(seen_prompts) == 1
    items = seen_prompts[0]
    joined_text = "\n".join(item for item in items if isinstance(item, str))
    assert "Swiggy" in joined_text
    bins = [item for item in items if isinstance(item, BinaryContent)]
    assert len(bins) == 1
    assert bins[0].data == VALID_PNG
    assert bins[0].media_type == "image/png"

    # Also verify CachePoint preservation when before_model_request hydrates a user message.
    req = ModelRequest(
        parts=[
            UserPromptPart(content=[f"Look [Attached image: {att_id} (card.png)]", CachePoint()])
        ]
    )
    run_cap = await cap.for_run(run_context(deps))
    hydrated_ctx = await run_cap.before_model_request(
        run_context(deps),
        ModelRequestContext(
            model=FunctionModel(script),
            messages=[req],
            model_settings=None,
            model_request_parameters=ModelRequestParameters(),
        ),
    )
    out_req = hydrated_ctx.messages[0]
    assert isinstance(out_req, ModelRequest)
    out_part = out_req.parts[0]
    assert isinstance(out_part, UserPromptPart)
    out_content = out_part.content
    assert isinstance(out_content, list)
    assert any(isinstance(item, BinaryContent) and item.data == VALID_PNG for item in out_content)
    assert isinstance(out_content[-1], CachePoint)


async def test_history_dump_strips_binary_and_hydrates_all_uncompacted_turns(demo_ledger):
    raw_old = VALID_PNG + b"old"
    raw_mid = VALID_PNG + b"mid"
    raw_new = VALID_PNG + b"new"
    att_old = await _insert_photo(demo_ledger, raw_old, filename="old.png")
    att_mid = await _insert_photo(demo_ledger, raw_mid, filename="mid.png")
    att_new = await _insert_photo(demo_ledger, raw_new, filename="new.png")

    msgs: list[Any] = [
        ModelRequest(parts=[UserPromptPart(f"Turn 1 [Attached image: {att_old} (old.png)]")]),
        ModelResponse(parts=[TextPart("Ack 1")]),
        ModelRequest(parts=[UserPromptPart(f"Turn 2 [Attached image: {att_mid} (mid.png)]")]),
        ModelResponse(parts=[TextPart("Ack 2")]),
        ModelRequest(parts=[UserPromptPart(f"Turn 3 [Attached image: {att_new} (new.png)]")]),
    ]

    def script(messages: list[Any], info: AgentInfo) -> ModelResponse:
        del info
        hydrated: list[tuple[int, bytes]] = []
        for msg_idx, msg in enumerate(messages):
            for part in getattr(msg, "parts", []):
                if isinstance(part, UserPromptPart) and isinstance(part.content, list):
                    for item in part.content:
                        if isinstance(item, BinaryContent):
                            hydrated.append((msg_idx, item.data))
        # Every uncompacted image turn (indices 0, 2, 4) hydrates raw without an artificial cap.
        assert hydrated == [(0, raw_old), (2, raw_mid), (4, raw_new)]
        return ModelResponse(parts=[TextPart("Checked all three turns.")])

    deps = AgentDeps(conversation_id="cnv_cap")
    agent: Agent[AgentDeps, str] = Agent(
        FunctionModel(script),
        deps_type=AgentDeps,
        capabilities=[ImageAttachments()],
    )
    await agent.run(
        message_history=msgs[:-1],
        user_prompt=msgs[-1].parts[0].content,
        deps=deps,
    )
    assert deps.saw_images is True

    # _dump strips BinaryContent from both UserPromptPart and ToolReturnPart.
    dirty = [
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=[
                        "Prompt",
                        BinaryContent(data=b"secret-img-bytes", media_type="image/png"),
                    ]
                ),
                ToolReturnPart(
                    tool_name="custom_tool",
                    content=[
                        "Data",
                        BinaryContent(data=b"secret-crop-bytes", media_type="image/png"),
                    ],
                    tool_call_id="call_img",
                ),
            ]
        )
    ]
    dumped = history_mod._dump(dirty)
    serialized = json.dumps(dumped)
    assert "secret-img-bytes" not in serialized
    assert "secret-crop-bytes" not in serialized
    assert serialized.count("[Image omitted from stored history]") == 2


async def test_observability_excludes_binary_content_from_otel_spans(demo_ledger, monkeypatch):
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    att_id = await _insert_photo(demo_ledger, VALID_PNG, filename="trace.png")

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    def script(messages: list[Any], info: AgentInfo) -> ModelResponse:
        del info
        has_img = any(
            isinstance(item, BinaryContent) and item.data == VALID_PNG
            for msg in messages
            for part in getattr(msg, "parts", [])
            if isinstance(part, UserPromptPart) and isinstance(part.content, list)
            for item in part.content
        )
        assert has_img
        return ModelResponse(parts=[TextPart("Traced text without image bytes.")])

    agent: Agent[AgentDeps, str] = Agent(
        FunctionModel(script),
        deps_type=AgentDeps,
        capabilities=[
            ImageAttachments(),
            Instrumentation(
                settings=InstrumentationSettings(
                    tracer_provider=provider,
                    include_content=True,
                    include_binary_content=False,
                )
            ),
        ],
    )
    await agent.run(
        f"Check [Attached image: {att_id} (trace.png)]",
        deps=AgentDeps(conversation_id="cnv_otel", image_attachments=(att_id,)),
    )

    spans = exporter.get_finished_spans()
    assert len(spans) > 0
    blob = json.dumps(
        [
            {
                "name": s.name,
                "attrs": dict(s.attributes or {}),
                "events": [dict(e.attributes or {}) for e in s.events],
            }
            for s in spans
        ],
        default=str,
    )
    assert "Traced text without image bytes." in blob
    raw_b64_prefix = base64.b64encode(VALID_PNG).decode("ascii")[:24]
    assert raw_b64_prefix not in blob


async def test_record_movements_mixed_batch_duplicates_undo_and_history(
    session_client, demo_ledger
):
    att_id = await _insert_photo(demo_ledger, VALID_PNG, filename="mix.png")

    # Record a prior movement so duplicate detection catches matching prior ledger rows.
    first = await write_tools.record_movement(
        demo_ledger,
        {
            "kind": "expense",
            "amount_rupees": 500,
            "payee": "Swiggy",
            "upi_ref": "999900001111",
        },
        message_id=None,
        conversation_id="cnv_mix",
    )
    assert first["status"] == "recorded"

    dup = await write_tools.record_movements(
        demo_ledger,
        [
            {"kind": "expense", "amount_rupees": 500, "payee": "Swiggy", "upi_ref": "999900001111"},
            {"kind": "expense", "amount_rupees": 150, "payee": "Fresh Chai"},
        ],
        message_id=None,
        conversation_id="cnv_mix",
    )
    assert dup["status"] == "possible_duplicate"
    assert dup["duplicates"][0]["index"] == 0
    assert (
        await demo_ledger.fetchval("SELECT count(*) FROM payees WHERE display_name = 'Fresh Chai'")
        == 0
    )

    # A different UPI reference is not flagged as a duplicate for single or batch writes.
    distinct_upi = await write_tools.record_movement(
        demo_ledger,
        {
            "kind": "expense",
            "amount_rupees": 500,
            "payee": "Swiggy",
            "upi_ref": "888877776666",
        },
        message_id=None,
        conversation_id="cnv_mix",
    )
    assert distinct_upi["status"] == "recorded"

    # Undoing a recent movement clears it from duplicate checks.
    undone_distinct = session_client.post(f"/api/undo/{distinct_upi['undo_id']}")
    assert undone_distinct.status_code == 200, undone_distinct.text
    relogged = await write_tools.record_movement(
        demo_ledger,
        {
            "kind": "expense",
            "amount_rupees": 500,
            "payee": "Swiggy",
            "upi_ref": "888877776666",
        },
        message_id=None,
        conversation_id="cnv_mix",
    )
    assert relogged["status"] == "recorded"

    before_payees = await demo_ledger.fetchval("SELECT count(*) FROM payees WHERE active")
    accounts = await demo_ledger.fetch(
        "SELECT name FROM accounts WHERE active AND kind IN ('bank', 'cash') ORDER BY name LIMIT 2"
    )
    acct_from = str(accounts[0]["name"])
    acct_to = str(accounts[1]["name"])

    batch = await write_tools.record_movements(
        demo_ledger,
        [
            {"kind": "expense", "amount_rupees": 20, "payee": "Tea Stall"},
            {"kind": "expense", "amount_rupees": 20, "payee": "Tea Stall"},
            {"kind": "refund", "amount_rupees": 1200, "payee": "Amazon"},
            {
                "kind": "split",
                "total_rupees": 1000,
                "my_share_rupees": 500,
                "payee": "Split Dinner",
                "people": [{"name": "Asha", "amount_rupees": 500}],
            },
            {
                "kind": "transfer",
                "amount_rupees": 5000,
                "from_account": acct_from,
                "to_account": acct_to,
            },
        ],
        message_id=None,
        conversation_id="cnv_mix",
        attachment_id=att_id,
        capture_kind="photo",
        capture_ref=att_id,
        tool_call_id="call_mix_1",
    )
    assert batch["status"] == "recorded"
    assert batch["count"] == 5
    # Outflow: 20 + 20 + 1000 = 1040 rupees; Inflow: 1200 rupees (transfers excluded).
    assert batch["total_outflow"] == {"paise": 104_000, "text": "₹1,040"}
    assert batch["total_inflow"] == {"paise": 120_000, "text": "₹1,200"}
    card = batch["card"]
    assert card["card"] == "movements_batch"
    assert "attachment_ids" not in card
    assert len(card["items"]) == 5

    assert (
        await demo_ledger.fetchval(
            "SELECT count(*) FROM captures WHERE kind = 'photo' AND ref = $1", att_id
        )
        == 5
    )

    page = session_client.get("/api/history").json()
    entry = next(item for item in page["items"] if item["undo_id"] == batch["undo_id"])
    assert entry["summary"].startswith("Logged 5 movements · ")

    undone = session_client.post(f"/api/undo/{batch['undo_id']}")
    assert undone.status_code == 200, undone.text
    assert await demo_ledger.fetchval("SELECT count(*) FROM payees WHERE active") == before_payees
    assert (
        await demo_ledger.fetchval(
            "SELECT count(*) FROM movements WHERE id = ANY($1::text[])",
            [item["movement_id"] for item in card["items"]],
        )
        == 0
    )
