"""Step 8 probe 8, no LLM: which model the summary call uses under ModelFallback, and what happens when it fails."""
import asyncio
import warnings

import pydantic_ai_harness.compaction as c
from pydantic_ai import Agent
from pydantic_ai.capabilities import ModelFallback
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import FunctionModel

warnings.filterwarnings("ignore")
calls = []


def make(name, up):
    def fn(messages, info):
        calls.append((name, "plain"))
        if not up["ok"]:
            raise ModelAPIError(name, "down")
        return ModelResponse(parts=[TextPart(f"SUMMARY from {name}")])

    async def stream(messages, info):
        calls.append((name, "stream"))
        if not up["ok"]:
            raise ModelAPIError(name, "down")
        yield f"answer from {name}"

    return FunctionModel(function=fn, stream_function=stream, model_name=name)


def history():
    out = [ModelRequest(parts=[c.pin("SNAPSHOT")]), ModelResponse(parts=[TextPart("Snapshot read.")])]
    for i in range(10):
        out += [ModelRequest.user_text_prompt(f"turn {i} " + "x" * 400), ModelResponse(parts=[TextPart("y" * 400)])]
    return out


async def scenario(label, primary_up, backup_up, chain=False):
    calls.clear()
    primary = make("primary", {"ok": primary_up})
    backup = make("backup", {"ok": backup_up})
    summary_model = FallbackModel(primary, backup) if chain else None
    agent = Agent(
        primary,
        capabilities=[
            c.TieredCompaction(
                tiers=[
                    c.FallbackCompaction(
                        fallback_chain=[
                            c.SummarizingCompaction(model=summary_model, max_messages=1, keep_tokens=60, keep_messages=2, keep_user_messages=True, receipts=True),
                            c.SlidingWindowCompaction(max_messages=1, keep_tokens=60, receipts=True),
                        ]
                    )
                ],
                target_tokens=200,
            ),
            ModelFallback(backup),
        ],
    )
    error = None
    text = None
    try:
        async with agent.run_stream("next question", message_history=history()) as r:
            text = await r.get_output()
            all_messages = r.all_messages()
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {str(e)[:80]}"
        all_messages = []
    print(f"== {label}")
    print("   calls:", calls)
    print("   answer:", text, "| error:", error)
    dumped = str(all_messages)
    filler = sum(1 for m in all_messages if isinstance(m, ModelRequest) and "x" * 400 in str(m))
    print("   summary from:", "primary" if "SUMMARY from primary" in dumped else "backup" if "SUMMARY from backup" in dumped else "none", "| messages:", len(all_messages), "| old turns still whole:", filler, "of 10 | snapshot kept:", "SNAPSHOT" in dumped)


async def main():
    await scenario("Main model up", True, True)
    await scenario("Main model down, backup up, summary on the run's model", False, True)
    await scenario("Main model down, backup up, summary on FallbackModel", False, True, chain=True)


asyncio.run(main())
