"""Owner cost operations: no provider calls, safe login dispatch, and nonretryable accounting."""

from __future__ import annotations

import asyncio

import pytest
from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTCredentials
from pydantic_ai_harness.spend import UnpricedModelError
from typer.testing import CliRunner

from arno.cli import app
from arno.jobs.runner import JobRunner, enqueue_job


def test_cost_replay_cli_uses_the_recovery_path_only(monkeypatch):
    async def replay(limit):
        assert limit == 7
        return {"exported": 2, "failed": 1, "pending": 3}

    monkeypatch.setattr("arno.agent.cost_journal.replay_costs", replay)
    result = CliRunner().invoke(app, ["cost-replay", "--limit", "7"])
    assert result.exit_code == 0, result.output
    assert "exported 2; failed 1; pending 3" in result.output


async def test_native_login_cli_saves_background_consent_without_printing_credentials(
    db, monkeypatch
):
    class Flow:
        async def exchange_code_from_callback(self):
            return OpenAIChatGPTCredentials(
                client_id="cli-client",
                access_token="secret-access",
                refresh_token="secret-refresh",
                scope="openid chatgpt.tokens.use.direct",
            )

    monkeypatch.setattr(
        "arno.agent.chatgpt_auth.chatgpt_sign_in",
        lambda **kwargs: ("https://auth.example/consent", Flow()),
    )
    result = await asyncio.to_thread(
        CliRunner().invoke, app, ["chatgpt-login", "--no-browser", "--allow-background"]
    )
    assert result.exit_code == 0, result.output
    assert "secret-access" not in result.output
    assert "secret-refresh" not in result.output
    assert await db.fetchval("SELECT allow_background FROM model_credentials") is True


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


@pytest.mark.parametrize("state,success", [("expected-state", True), ("wrong-state", False)])
async def test_container_login_pastes_a_state_checked_callback(db, monkeypatch, state, success):
    class Flow:
        redirect_uri = "http://127.0.0.1:1455/auth/callback"
        state = "expected-state"

        async def exchange_code(self, code, *, client_id):
            assert code == "private-code"
            assert client_id == "issued-client"
            return OpenAIChatGPTCredentials(
                client_id=client_id,
                access_token="private-access",
                refresh_token="private-refresh",
                scope="openid chatgpt.tokens.use.direct",
            )

    monkeypatch.setattr(
        "arno.agent.chatgpt_auth.chatgpt_sign_in",
        lambda **kwargs: ("https://auth.example/consent", Flow()),
    )
    callback = (
        f"http://127.0.0.1:1455/auth/callback?state={state}"
        "&code=private-code&client_id=issued-client\n"
    )
    result = await asyncio.to_thread(
        lambda: CliRunner().invoke(
            app, ["chatgpt-login", "--no-browser", "--manual"], input=callback
        )
    )
    assert (result.exit_code == 0) is success, result.output
    assert "private-code" not in result.output
    assert "private-access" not in result.output
    assert bool(await db.fetchval("SELECT count(*) FROM model_credentials")) is success


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
