"""Composed answers: bounded snapshots, strict views, and the saved answer card."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from fin_agent.agent.builder import build_agent
from fin_agent.agent.cards import cards_from_messages
from fin_agent.agent.deps import AgentDeps
from fin_agent.agent.history import create_thread, ensure_history, record_user_message, save_records
from fin_agent.agent.presentation import (
    PresentationError,
    ResultRegistry,
    TableColumn,
    TableInput,
    build_answer,
)
from fin_agent.agent.turns import encode_saved_turn


def _spending(registry: ResultRegistry, *, gaps: list[str] | None = None):
    return registry.register_read(
        "spending",
        {
            "period": {"from": "2026-09-01", "to": "2026-09-30"},
            "groups": [
                {"key": "Food", "amount": {"paise": 45_000, "text": "₹450"}, "count": 2},
                {"key": "Transport", "amount": {"paise": 12_000, "text": "₹120"}, "count": 1},
            ],
            "total": {"paise": 57_000, "text": "₹570"},
            "coverage": {"complete": not gaps, "gaps": gaps or []},
        },
        context={"group_by": "category"},
    )


def _movements(registry: ResultRegistry):
    return registry.register_read(
        "movements",
        {
            "rows": [
                {
                    "id": "mov_1",
                    "date": "2026-09-02",
                    "description": "Lunch",
                    "payee": None,
                    "category": "Food",
                    "amount_paise": 45_000,
                }
            ],
            "total_count": 1,
            "next_cursor": None,
            "coverage": {"complete": True, "gaps": []},
        },
        context={"period": {"label": "2026-09"}},
    )


def test_a_composed_stack_can_show_summary_table_and_chart_from_one_result():
    registry = ResultRegistry()
    result = _spending(registry)
    result_id = result.primary_id

    card, return_value = build_answer(
        registry,
        {
            "_type": "Stack",
            "children": [
                {"_type": "Summary", "result_id": result_id},
                {"_type": "Table", "result_id": result_id},
                {"_type": "Chart", "result_id": result_id},
            ],
        },
    )

    assert card["card"] == "answer"
    assert card["version"] == 1
    assert [section["_type"] for section in card["display"]["children"]] == [
        "Summary",
        "Table",
        "Chart",
    ]
    assert len(card["results"]) == 1
    assert card["results"][0]["kind"] == "spending"
    assert return_value["sections"] == 3


def test_only_referenced_snapshots_travel_and_returned_snapshots_are_copies():
    registry = ResultRegistry()
    spending = _spending(registry)
    _movements(registry)
    card, _ = build_answer(
        registry,
        {"_type": "Summary", "result_id": spending.primary_id},
    )

    assert [result["kind"] for result in card["results"]] == ["spending"]
    card["results"][0]["title"] = "tampered"
    assert registry.get(spending.primary_id).snapshot["title"] != "tampered"


def test_an_unknown_id_or_wrong_view_is_a_retry_with_the_supported_views():
    registry = ResultRegistry()
    _spending(registry)
    movements = _movements(registry)

    with pytest.raises(PresentationError, match="Unknown or stale result id"):
        build_answer(registry, {"_type": "Summary", "result_id": "res_999"})
    with pytest.raises(PresentationError, match="It offers Summary, Table"):
        build_answer(registry, {"_type": "Chart", "result_id": movements.primary_id})


def test_a_previous_turns_id_cannot_resolve_to_a_new_result():
    previous = _spending(ResultRegistry()).primary_id
    current = ResultRegistry()
    latest = _spending(current).primary_id
    assert previous != latest
    with pytest.raises(PresentationError, match="Unknown or stale result id"):
        build_answer(current, {"_type": "Summary", "result_id": previous})


def test_the_tree_is_bounded_and_rejects_unknown_props_or_containers_with_ids():
    registry = ResultRegistry()
    spending = _spending(registry)
    result_id = spending.primary_id

    with pytest.raises(PresentationError, match="not valid"):
        build_answer(
            registry,
            {"_type": "Summary", "result_id": result_id, "value": 450},
        )
    with pytest.raises(PresentationError, match="at most 3 containers"):
        build_answer(
            registry,
            {
                "_type": "Stack",
                "children": [
                    {
                        "_type": "Stack",
                        "children": [
                            {
                                "_type": "Stack",
                                "children": [
                                    {
                                        "_type": "Stack",
                                        "children": [{"_type": "Summary", "result_id": result_id}],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )
    with pytest.raises(PresentationError, match="at most 12 nodes"):
        build_answer(
            registry,
            {
                "_type": "Stack",
                "children": [{"_type": "Summary", "result_id": result_id} for _ in range(12)],
            },
        )


def test_a_comparison_aligns_leaves_and_never_computes_a_difference():
    registry = ResultRegistry()
    first = _spending(registry)
    second = _spending(registry)

    card, _ = build_answer(
        registry,
        {
            "_type": "Comparison",
            "children": [
                {"_type": "Summary", "result_id": first.primary_id},
                {"_type": "Summary", "result_id": second.primary_id},
            ],
        },
    )

    assert [result["result_id"] for result in card["results"]] == [
        first.primary_id,
        second.primary_id,
    ]
    assert "difference" not in json.dumps(card["display"])


def test_unknown_money_and_coverage_gaps_stay_unknown_not_zero():
    registry = ResultRegistry()
    result = registry.register_read(
        "spending",
        {
            "period": {"from": "2026-07-01", "to": "2026-07-31"},
            "groups": [],
            "total": None,
            "coverage": {"complete": False, "gaps": ["2026-07"]},
        },
        context={"group_by": "category"},
    )
    snapshot = registry.get(result.primary_id).snapshot

    assert snapshot["summary"]["facts"][0]["value"] == {"type": "unknown"}
    assert "No statement covers 2026-07." in snapshot["notes"]
    assert snapshot["empty"] is True


def test_context_accounts_expose_an_unknown_balance_and_separate_results():
    registry = ResultRegistry()
    registration = registry.register_read(
        "context",
        {
            "today": "2026-11-06",
            "accounts": [
                {
                    "id": "acc_1",
                    "name": "Empty bank",
                    "kind": "bank",
                    "balance": None,
                    "as_of": None,
                    "covered": {"from": None, "to": None, "gaps": ["2026-10"]},
                }
            ],
            "liquid_total": None,
            "upcoming": [],
            "goals": [],
            "owed": [],
            "warnings": ["Empty bank has no stated balance"],
            "source_ids": {"accounts": ["acc_1"], "schedules": [], "goals": [], "payees": []},
        },
    )

    kinds = [result.kind for result in registration.results]
    assert kinds == ["accounts", "upcoming", "goals", "owed"]
    accounts = registration.results[0].snapshot
    assert accounts["table"]["rows"][0][2] == {"type": "unknown"}
    assert accounts["source_ids"] == {"accounts": ["acc_1"]}
    assert accounts["notes"] == ["Empty bank has no stated balance"]


def test_show_table_is_bounded_and_rejects_non_integer_money():
    with pytest.raises(ValidationError):
        TableInput(
            title="Too many",
            columns=[TableColumn(label="Amount", type="money")],
            rows=[[1] for _ in range(51)],
        )

    registry = ResultRegistry()
    with pytest.raises(PresentationError, match="integer paise"):
        registry.register_table(
            TableInput(
                title="Bad money",
                columns=[TableColumn(label="Amount", type="money")],
                rows=[["450"]],
            )
        )


def test_computed_table_columns_have_stable_unique_keys_and_keep_source_evidence():
    registry = ResultRegistry()
    source = _spending(registry, gaps=["2026-09"])
    source_id = source.primary_id
    table = registry.register_table(
        TableInput(
            title="Comparison",
            columns=[TableColumn(label="Cost", type="money"), TableColumn(label="Cost")],
            rows=[[57_000, None]],
            source_result_ids=[source_id],
        )
    )
    snapshot = table.snapshot
    assert [column["key"] for column in snapshot["table"]["columns"]] == ["0", "1"]
    assert snapshot["coverage"] == {"complete": False, "gaps": ["2026-09"]}
    assert snapshot["notes"] == ["No statement covers 2026-09."]
    assert snapshot["sources"][0]["period"] == {"from": "2026-09-01", "to": "2026-09-30"}
    derived = registry.register_table(
        TableInput(
            title="Derived again",
            columns=[TableColumn(label="Cost", type="money")],
            rows=[[57_000]],
            source_result_ids=[table.result_id],
        )
    )
    assert derived.snapshot["coverage"] == snapshot["coverage"]
    assert [item["id"] for item in derived.snapshot["sources"]] == [table.result_id, source_id]


def test_a_computed_table_keeps_ledger_ids_assumptions_and_unknown_coverage():
    registry = ResultRegistry()
    source = registry.add(
        kind="query",
        title="Source",
        views=["Summary"],
        snapshot={
            "source_ids": {"movements": ["mov_1"]},
            "assumptions": ["Rate assumed"],
            "coverage": {"complete": False, "gaps": []},
            "sql": "SELECT amount_paise",
        },
    )
    table = registry.register_table(
        TableInput(
            title="Computed",
            columns=[TableColumn(label="Cost", type="money")],
            rows=[[None]],
            source_result_ids=[source.result_id],
        )
    )
    assert table.snapshot["source_ids"] == {"movements": ["mov_1"]}
    assert table.snapshot["assumptions"] == ["Rate assumed"]
    assert table.snapshot["coverage"]["complete"] is False
    assert table.snapshot["sources"][0]["sql"] == "SELECT amount_paise"
    assert table.snapshot["table"]["rows"][0][0] == {"type": "unknown"}


def test_a_table_cannot_claim_complete_coverage_for_an_unverified_source():
    registry = ResultRegistry()
    complete = _spending(registry)
    unverified = registry.add(kind="query", title="Unverified source", views=[], snapshot={})
    table = registry.register_table(
        TableInput(
            title="Mixed sources",
            columns=[TableColumn(label="Amount", type="money")],
            rows=[[57_000]],
            source_result_ids=[complete.primary_id, unverified.result_id],
        )
    )
    assert table.snapshot["coverage"] == {"complete": False, "gaps": []}


@pytest.mark.parametrize("floor_set", [False, True])
def test_a_projection_summary_requires_a_floor_before_showing_its_probability(floor_set):
    registry = ResultRegistry()
    result = registry.register_read(
        "project",
        {},
        context={
            "card": {
                "floor_set": floor_set,
                "floor": {"paise": 100_000},
                "floor_probability": 0.965,
                "horizon_days": 90,
                "median_low": {"amount": {"paise": 300_000}},
                "data_quality": {},
            }
        },
    )
    facts = {
        fact["label"]: fact["value"] for fact in result.results[0].snapshot["summary"]["facts"]
    }
    assert facts["Floor probability"] == (
        {"type": "text", "text": "97%"} if floor_set else {"type": "unknown"}
    )


def test_source_preview_notes_do_not_describe_the_derived_tables_rows():
    registry = ResultRegistry()
    source = registry.register_read(
        "query",
        {
            "columns": ["amount_paise"],
            "rows": [[100] for _ in range(60)],
            "row_count": 60,
            "truncated": False,
            "sql": "SELECT amount_paise",
        },
    )
    result = registry.register_table(
        TableInput(
            title="Two-row comparison",
            columns=[TableColumn(label="Amount", type="money")],
            rows=[[100], [100]],
            source_result_ids=[source.primary_id],
        )
    )
    assert result.snapshot["table"]["shown_rows"] == 2
    assert result.snapshot["notes"] == [
        "Source preview (Query result): Showing the first 50 of 60 rows."
    ]
    assert result.snapshot["sources"][0]["notes"] == ["Showing the first 50 of 60 rows."]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_computed_tables_reject_non_finite_numbers(value):
    with pytest.raises(PresentationError, match="finite"):
        ResultRegistry().register_table(
            TableInput(
                title="Invalid",
                columns=[TableColumn(label="Count", type="number")],
                rows=[[value]],
            )
        )


@pytest.mark.parametrize("computed", [False, True])
async def test_a_run_code_present_card_is_saved_and_replayed(demo_ledger, computed):
    code = (
        "spending = await summarize_spending(group_by='category', period='2026-10')\n"
        "result_id = spending['ui_results'][0]['id']\n"
    )
    if computed:
        code += (
            "table = await show_table(title='Computed spending', "
            "columns=[{'label': 'Amount', 'type': 'money'}], "
            "rows=[[spending['total']['paise']]], source_result_ids=[result_id])\n"
            "result_id = table['ui_results'][0]['id']\n"
        )
    code += (
        "await present(display={'_type': 'Stack', 'children': ["
        "{'_type': 'Summary', 'result_id': result_id},"
        "{'_type': 'Table', 'result_id': result_id}]})\n"
    )

    def script(messages, info: AgentInfo) -> ModelResponse:
        del info
        if not any(
            getattr(part, "tool_name", None) == "run_code"
            for message in messages
            for part in getattr(message, "parts", [])
        ):
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": code})])
        return ModelResponse(parts=[TextPart("Done.")])

    agent = build_agent()
    deps = AgentDeps(conversation_id="cnv_test")
    with agent.override(model=FunctionModel(script)):
        result = await agent.run("show me", deps=deps)

    cards = cards_from_messages(result.all_messages())
    answer = next((card for card in cards if card.data.get("card") == "answer"), None)
    assert answer is not None, [
        str(getattr(part, "content", ""))[-4000:]
        for message in result.all_messages()
        for part in getattr(message, "parts", [])
        if getattr(part, "tool_name", None) == "run_code"
    ]
    assert answer.data["results"][0]["kind"] == ("table" if computed else "spending")
    assert answer.data["results"][0]["table"]["rows"]

    thread = await create_thread(demo_ledger, title="composed")
    history, _ = await ensure_history(demo_ledger, thread)
    await record_user_message(
        demo_ledger, thread_id=thread["id"], client_id="client-composed", text="show me"
    )
    saved = await save_records(
        demo_ledger,
        thread_id=thread["id"],
        run_id="run_composed",
        ui_message_id="client-composed",
        history=history,
        turn_messages=result.all_messages(),
        ending="complete",
    )
    row = await demo_ledger.fetchrow(
        "SELECT content FROM messages WHERE id = $1",
        saved["messages"][-1],
    )
    stored = row["content"]
    stored_answer = next(
        part
        for part in stored["parts"]
        if part.get("type") == "data-card" and part.get("data", {}).get("card") == "answer"
    )
    assert (
        stored_answer["data"]["results"][0]["result_id"] == answer.data["results"][0]["result_id"]
    )

    replay = [
        json.loads(line[6:]) for line in encode_saved_turn(stored) if line.startswith("data: ")
    ]
    replayed = next(
        chunk
        for chunk in replay
        if chunk.get("type") == "data-card" and chunk["data"].get("card") == "answer"
    )
    assert replayed["data"]["results"] == stored_answer["data"]["results"]
