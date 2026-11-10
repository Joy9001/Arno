"""The prompt contracts behind two eval slips, tested without a model.

`ans_groceries_count` answered in prose without the total and the count, which stayed in the card;
`lt_floor_decision` recalled a decision after a summary but dropped the floor's figure. Each slip
came from what the app told the model at that moment: `present`'s return said the card was shown,
and the summarizer was told to leave amounts out.
"""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_harness.compaction import (
    FallbackCompaction,
    SummarizingCompaction,
    TieredCompaction,
    compact_now,
)

from arno.agent.capabilities import build_capabilities
from arno.agent.instructions import render_instructions
from arno.agent.presentation import ResultRegistry, build_answer

DECISION = "We agreed to keep the floor at ₹25,000 and not add a new subscription this month."


def _production_summarizer(model: Model) -> SummarizingCompaction[None]:
    """The summarizer the production capability list actually builds."""
    capabilities = build_capabilities(primary=model, spend_store=None, context_window=1_000)
    tiered = next(item for item in capabilities if isinstance(item, TieredCompaction))
    fallback = next(item for item in tiered.tiers if isinstance(item, FallbackCompaction))
    return next(item for item in fallback.fallback_chain if isinstance(item, SummarizingCompaction))


def _decision_thread() -> list[ModelMessage]:
    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(f"Long-term planning note. {DECISION}")]),
        ModelResponse(parts=[TextPart("Noted. I'll keep that decision.")]),
    ]
    for index in range(12):
        history += [
            ModelRequest(parts=[UserPromptPart(f"Filler turn {index} " + "x" * 200)]),
            ModelResponse(parts=[TextPart("Noted; nothing new to decide.")]),
        ]
    return history


async def test_the_summarizer_is_told_to_keep_the_figures_a_decision_rests_on():
    prompts: list[str] = []

    def summarizer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for message in messages:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    prompts.append(str(part.content))
        return ModelResponse(parts=[TextPart("SUMMARY")])

    model = FunctionModel(summarizer)
    summarizer_capability = _production_summarizer(model)
    history = _decision_thread()

    compacted = await compact_now(summarizer_capability, history, model=model)

    assert len(compacted) < len(history)
    prompt = "\n".join(prompts)
    # The guideline, and the transcript it applies to.
    assert "figures a decision rests on" in prompt
    assert DECISION in prompt
    # The old wording dropped every amount, which is how the floor lost its figure.
    assert "Leave out amounts" not in prompt


def test_the_instructions_put_the_asked_figures_in_the_reply_not_only_in_the_card():
    text = render_instructions(["Groceries"])
    answer_format = text.split("Answer format\n", 1)[1].split("\nWrites\n", 1)[0]

    assert "Do not restate a presented table in prose" not in answer_format
    assert "The reply is the answer" in answer_format
    assert "in your first sentence" in answer_format


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
