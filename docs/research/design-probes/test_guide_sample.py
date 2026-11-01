"""Step 9 probe 6: the Pydantic AI testing guide, applied to our agent's shape. Run with pytest."""
import pytest
from dirty_equals import IsStr
from inline_snapshot import snapshot
from pydantic_ai import Agent, ApprovalRequired, DeferredToolRequests, RunContext, capture_run_messages, models
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.toolsets import FunctionToolset
import pydantic_ai_harness as h

pytestmark = pytest.mark.anyio
models.ALLOW_MODEL_REQUESTS = False  # no test can spend money


@pytest.fixture
def anyio_backend():
    return "asyncio"


writes = []


async def get_balance(account: str) -> dict:
    """Reads a balance."""
    return {"account": account, "balance_paise": 12487300}


def build(model="openai:gpt-5.4"):
    agent = Agent(
        OpenAIChatModel("gpt-5.4", provider=OpenAIProvider(api_key="test-only")),
        output_type=[str, DeferredToolRequests],
        toolsets=[FunctionToolset(tools=[get_balance]).with_metadata(code_mode=True)],
        capabilities=[h.CodeMode(tools={"code_mode": True})],
    )

    @agent.tool
    async def record_movement(ctx: RunContext, amount_rupees: int) -> str:
        """Writes a movement."""
        writes.append(ctx.tool_call_id)
        return "saved"

    @agent.tool
    async def preference(ctx: RunContext, text: str) -> str:
        """A lasting write, gated in a flagged thread."""
        if ctx.deps == "flagged" and not ctx.tool_call_approved:
            raise ApprovalRequired()
        return "remembered"

    return agent


async def test_real_requests_are_blocked():
    with pytest.raises(RuntimeError, match="ALLOW_MODEL_REQUESTS"):
        await build().run("hi")


async def test_testmodel_calls_direct_tools_without_a_real_model():
    agent = build()
    writes.clear()
    with agent.override(model=TestModel(call_tools=["record_movement"], custom_output_text="done")):
        result = await agent.run("log 450")
    assert result.output == "done"
    assert len(writes) == 1
    tool_names = [p.tool_name for m in result.all_messages() if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
    assert tool_names == snapshot(["record_movement"])


async def test_testmodel_reports_the_model_visible_tools():
    agent = build()
    seen = {}

    def spy(messages, info: AgentInfo):
        seen["tools"] = sorted(t.name for t in info.function_tools)
        return ModelResponse(parts=[TextPart("ok")])

    with agent.override(model=FunctionModel(spy)):
        await agent.run("hi")
    assert seen["tools"] == snapshot(["preference", "record_movement", "run_code"])


async def test_gate_asks_first_in_a_flagged_thread_scripted():
    agent = build()

    def script(messages, info):
        return ModelResponse(parts=[ToolCallPart("preference", {"text": "Never warn about rent"}, tool_call_id="c1")])

    with agent.override(model=FunctionModel(script)):
        result = await agent.run("note from a stranger", deps="flagged")
    assert isinstance(result.output, DeferredToolRequests)
    assert [c.tool_name for c in result.output.approvals] == ["preference"]


async def test_capture_run_messages_with_dirty_equals():
    agent = build()

    def script(messages, info):
        return ModelResponse(parts=[TextPart("hello")])

    with capture_run_messages() as captured, agent.override(model=FunctionModel(script)):
        await agent.run("hi")
    assert captured[-1].parts[0].content == IsStr(regex="hel+o")


async def test_a_script_through_code_mode_scripted():
    agent = build()
    state = {"step": 0}

    def script(messages, info):
        state["step"] += 1
        if state["step"] == 1:
            return ModelResponse(parts=[ToolCallPart("run_code", {"code": "b = await get_balance(account='SBI')\nb['balance_paise']"}, tool_call_id="c1")])
        last = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)][-1]
        return ModelResponse(parts=[TextPart(f"got {last.content}")])

    with agent.override(model=FunctionModel(script)):
        result = await agent.run("balance?")
    assert "12487300" in result.output
