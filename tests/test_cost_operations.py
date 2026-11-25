"""Owner cost operations: nonretryable accounting and delegated-inference consent."""

from __future__ import annotations

import pytest
from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentials
from pydantic_ai_harness.spend import UnpricedModelError

from arno.jobs.runner import JobRunner, enqueue_job


@pytest.mark.parametrize("error_type", [UnpricedModelError, UserError])
async def test_jobs_do_not_retry_an_already_spent_unpriced_call(db, error_type):
    async def fail(payload):
        raise error_type("synthetic missing price")

    job_id = await enqueue_job(db, key="cost-error", kind="cost-error")
    runner = JobRunner(registry={"cost-error": fail})
    await runner.run_once()
    row = await db.fetchrow_required("SELECT status,attempts FROM jobs WHERE id=$1", job_id)
    assert row["status"] == "failed"
    assert row["attempts"] == 1


async def test_vision_propagates_an_accounting_stop(monkeypatch):
    from pydantic_ai.models.function import FunctionModel

    from arno.agent.spend import SpendStoreError
    from arno.importers.readers import Doc
    from arno.importers.vision import transcribe_page
    from tests.import_fixtures import minimal_pdf

    async def fail(messages, info):
        raise SpendStoreError("synthetic failed cost write")

    monkeypatch.setattr("arno.importers.vision.vision_model", lambda: FunctionModel(fail))
    with pytest.raises(SpendStoreError):
        await transcribe_page(Doc(minimal_pdf(["synthetic page"])))


async def test_delegated_inference_requires_background_plan_consent(db):
    import httpx2
    from pydantic_ai import Agent
    from pydantic_ai.models.function import FunctionModel

    from arno.agent.capabilities import InferenceContext
    from arno.agent.chatgpt_auth import DatabaseChatGPTCredentialSource

    source = DatabaseChatGPTCredentialSource()
    await source.save(
        OpenAIChatGPTCredentials(
            client_id="delegation-client",
            access_token="synthetic-access",
            refresh_token="synthetic-refresh",
            scope="openid chatgpt.tokens.use.direct",
        )
    )

    async def request(messages, info):
        await source.authorize_request(
            httpx2.Request(
                "POST",
                "https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer synthetic-access"},
            )
        )
        raise AssertionError("unconsented delegated dispatch was allowed")

    agent = Agent(FunctionModel(request), capabilities=[InferenceContext(background=True)])
    with pytest.raises(UserError, match="background"):
        await agent.run("synthetic delegation")


@pytest.mark.parametrize(
    "script",
    [
        "await transcribe_page(0)",
        "try:\n    await transcribe_page(0)\nexcept Exception:\n    await transcribe_page(0)",
    ],
)
async def test_a_sandbox_cannot_swallow_a_vision_cost_stop(monkeypatch, script):
    from arno.agent.spend import SpendStoreError
    from arno.importers.monty import ScriptContext, run_script
    from arno.importers.readers import Doc

    calls = []

    async def stop(doc, page):
        calls.append(page)
        raise SpendStoreError("synthetic post-dispatch ledger failure")

    monkeypatch.setattr("arno.importers.monty.vision_transcribe", stop)
    with pytest.raises(SpendStoreError):
        await run_script(
            Doc(b"synthetic page"), script, ScriptContext(allow_vision=True), with_prelude=False
        )
    assert calls == [0]


@pytest.mark.parametrize("catch_error", [False, True])
async def test_code_mode_cannot_retry_a_paid_tool_after_an_accounting_stop(
    monkeypatch, catch_error
):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from arno.agent.deps import AgentDeps
    from arno.agent.parser import build_parser_agent
    from arno.agent.spend import SpendStoreError
    from arno.importers.readers import Doc

    code = "await transcribe_page(import_id='synthetic', page=0)"
    if catch_error:
        code = "try:\n    " + code + "\nexcept Exception:\n    " + code
    model_calls = []
    vision_calls = []

    def script(messages, info):
        model_calls.append(1)
        assert len(model_calls) == 1, "a cost stop must prevent another paid model request"
        return ModelResponse(parts=[ToolCallPart("run_code", {"code": code})])

    async def load_doc(ctx, import_id):
        return Doc(b"synthetic page")

    async def fail_vision(doc, page):
        vision_calls.append(1)
        raise SpendStoreError("synthetic failed post-dispatch write")

    monkeypatch.setattr("arno.agent.parser._load_doc", load_doc)
    monkeypatch.setattr("arno.agent.parser.vision_transcribe", fail_vision)
    agent = build_parser_agent(model=FunctionModel(script))
    with pytest.raises(SpendStoreError):
        await agent.run("synthetic", deps=AgentDeps(conversation_id="synthetic"))
    assert model_calls == [1]
    assert vision_calls == [1]
