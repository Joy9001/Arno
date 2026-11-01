"""Step 10 probe 2, no LLM: where a card's data survives after a reload, and what a stored message looks like to the client."""
import asyncio
import warnings

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ToolReturn, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_ai.ui.vercel_ai.request_types import SubmitMessage, TextUIPart, UIMessage
from pydantic_ai.ui.vercel_ai.response_types import DataChunk

warnings.filterwarnings("ignore")


async def stream(messages, info):
    tail = messages[-1]
    if not (isinstance(tail, ModelRequest) and any(isinstance(p, ToolReturnPart) for p in tail.parts)):
        yield {0: DeltaToolCall(name="record_movement", json_args='{"amount_rupees": 450}', tool_call_id="c1")}
        return
    yield "Logged."


agent = Agent(FunctionModel(stream_function=stream))


@agent.tool_plain
async def record_movement(amount_rupees: int) -> ToolReturn:
    """Writes."""
    return ToolReturn(return_value={"status": "saved", "undo_id": "u1"}, metadata=[DataChunk(type="data-card", data={"kind": "undo", "undo_id": "u1"})])


async def main():
    saved = {}

    async def done(result):
        saved["all"] = result.all_messages()

    adapter = VercelAIAdapter(agent=agent, run_input=SubmitMessage(id="t", messages=[UIMessage(id="u1", role="user", parts=[TextUIPart(text="chai 450")])], trigger="submit-message"), manage_message_history="server", sdk_version=7)
    live = []
    async for ev in adapter.run_stream(on_complete=done):
        live.append(type(ev).__name__)
    print("live stream has a data chunk:", "DataChunk" in live)
    stored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(saved["all"]))
    part = next(p for m in stored if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart))
    print("stored return content:", part.content, "| stored metadata:", part.metadata)
    ui = VercelAIAdapter.dump_messages(stored)
    for m in ui:
        print("  ", m.role, [(type(p).__name__, getattr(p, "state", None), getattr(p, "output", None)) for p in m.parts])


asyncio.run(main())
