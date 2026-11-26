"""`present`'s return tells the model the reply is still the answer, tested without a model.

`ans_groceries_count` answered in prose without the total and the count, which stayed in the card.
"""

from __future__ import annotations

from arno.agent.presentation import ResultRegistry, build_answer


def test_present_tells_the_model_the_reply_is_still_the_answer():
    registry = ResultRegistry()
    registration = registry.register_read(
        "spending",
        {
            "period": {"from": "2026-09-01", "to": "2026-09-30"},
            "groups": [
                {"key": "Groceries", "amount": {"paise": 425_000, "text": "₹4,250"}, "count": 2}
            ],
            "total": {"paise": 425_000, "text": "₹4,250"},
            "coverage": {"complete": True, "gaps": []},
        },
        context={"group_by": "none"},
    )
    result_id = registration.primary_id
    assert result_id is not None

    card, return_value = build_answer(
        registry,
        {
            "_type": "Stack",
            "children": [
                {"_type": "Summary", "result_id": result_id},
                {"_type": "Table", "result_id": result_id},
            ],
        },
    )

    # The model-facing return names the prose that is still owed.
    assert return_value["shown"] is True
    assert "Your reply is the answer" in return_value["reply"]
    # The card the web app reads is unchanged by it.
    assert set(card) == {"card", "version", "display", "results"}
    assert card["card"] == "answer"
    assert [section["_type"] for section in card["display"]["children"]] == ["Summary", "Table"]
    assert card["results"][0]["summary"]["facts"][0] == {
        "label": "Total",
        "value": {"type": "money", "paise": 425_000, "text": "₹4,250"},
    }
