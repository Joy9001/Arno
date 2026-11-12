"""Pricing fixtures: reported, estimated, partial, unknown, local zero, cache and tiers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import override

import httpx2
import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters, override_allow_model_requests
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RequestUsage

from arno.agent.pricing import (
    GENAI_PRICES_VERSION,
    LOCAL_PRICE_REVISION,
    OPENCODE_PRICE_REVISION,
    REGISTRY_PRICE_REVISION,
    CostObservation,
    cost_attributes,
    observe_cost,
    price_response,
)
from arno.agent.provider_usage import BillingMetadataModel, capture_response

WHEN = datetime(2026, 11, 12, tzinfo=UTC)


def _response(
    *,
    usage: RequestUsage | None = None,
    model_name: str = "gpt-5",
    provider_name: str = "openai",
    provider_url: str = "https://api.openai.com/v1",
    details: dict[str, object] | None = None,
) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart("ok")],
        model_name=model_name,
        provider_name=provider_name,
        provider_url=provider_url,
        timestamp=WHEN,
        usage=usage or RequestUsage(),
        provider_details=details,
    )


def _opencode_model(handler) -> BillingMetadataModel:
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        event_hooks={"response": [capture_response]},
    )
    provider = OpenAIProvider(
        base_url="https://opencode.ai/zen/v1", api_key="test", http_client=client
    )
    return BillingMetadataModel(
        OpenAIChatModel("gpt-5", provider=provider),
        provider="opencode",
        billing_mode="metered",
    )


def test_local_zero_is_measured_even_without_usage():
    response = _response(usage=RequestUsage(unmeasured_requests=1))

    observation = observe_cost(response, provider="ollama", billing_mode="local")

    assert observation.usd == Decimal(0)
    assert observation.source == "local_zero"
    assert observation.complete is True
    assert observation.status == "known"
    assert observation.price_revision == LOCAL_PRICE_REVISION
    assert observation.usage == {"unmeasured_requests": 1}


def test_local_zero_uses_the_wrapper_marker_in_the_native_callback():
    response = _response(
        usage=RequestUsage(unmeasured_requests=1),
        provider_name="ollama",
        provider_url="http://127.0.0.1:11434/v1",
        details={"arno_cost": {"provider": "ollama", "billing_mode": "local"}},
    )

    assert price_response(response) == Decimal(0)


def test_catalog_price_uses_cache_and_reasoning_without_double_counting():
    response = _response(
        usage=RequestUsage(
            input_tokens=1000,
            cache_read_tokens=400,
            output_tokens=100,
            output_reasoning_tokens=25,
        )
    )

    observation = observe_cost(response)

    assert observation.usd == Decimal("0.00180")
    assert observation.source == "genai-prices"
    assert observation.complete is True
    assert observation.price_revision == REGISTRY_PRICE_REVISION
    assert observation.rates is not None
    assert observation.rates["cache_read_mtok"] == "0.125"
    assert observation.rates["output_mtok"] == "10"
    assert observation.usage == {
        "input_tokens": 1000,
        "cache_read_tokens": 400,
        "output_tokens": 100,
        "output_reasoning_tokens": 25,
    }
    assert GENAI_PRICES_VERSION in observation.price_revision


def test_catalog_price_honors_the_service_tier_context():
    usage = RequestUsage(input_tokens=1000, output_tokens=100)
    standard = observe_cost(_response(usage=usage))
    priority = observe_cost(_response(usage=usage, details={"service_tier": "priority"}))

    assert standard.usd == Decimal("0.00225")
    assert priority.usd == Decimal("0.00450")


def test_native_usage_cost_without_wire_provenance_is_an_estimate():
    response = _response(usage=RequestUsage(input_tokens=100, cost=Decimal("0.01")))

    observation = observe_cost(response)

    assert observation.usd == Decimal("0.01")
    assert observation.source == "genai-prices"
    assert observation.status == "known"


def test_native_catalog_estimate_preserves_its_effective_rates():
    response = _response(usage=RequestUsage(input_tokens=1000, output_tokens=100))
    response.usage.cost = response.cost().total_price
    observation = observe_cost(response, provider="openai-chatgpt")
    assert observation.rates is not None
    assert observation.rates["output_mtok"] == "10"
    assert observation.billing_source == "chatgpt_plan"


@pytest.mark.parametrize("billing_source", ["go", "wallet", None])
def test_opencode_allowance_alone_is_not_a_dollar_usage_value(billing_source):
    response = _response(
        details={"opencode": {"allowance": "0.123", "billing_source": billing_source}}
    )
    observation = observe_cost(response, provider="opencode")
    assert observation.usd is None
    assert observation.allowance == Decimal("0.123")
    assert observation.status == "unknown"


def test_missing_usage_keeps_the_known_subtotal_and_stays_partial():
    response = _response(usage=RequestUsage(unmeasured_requests=1, cost=Decimal("0.05")))

    observation = observe_cost(response)
    supplied = price_response(response)

    assert observation.usd == Decimal("0.05")
    assert observation.complete is False
    assert observation.status == "partial"
    assert observation.usage == {"unmeasured_requests": 1}
    assert supplied is None
    assert response.usage.cost == Decimal("0.05")


def test_partial_observation_is_not_marked_complete_by_the_native_callback():
    response = _response(usage=RequestUsage(input_tokens=10, cost=Decimal("0.01")))
    response.usage.unmeasured_requests = 1

    assert price_response(response) is None


def test_unknown_paid_price_is_not_replaced_by_an_unverified_registry_rate():
    response = _response(
        model_name="gpt-5",
        provider_name="openai",
        provider_url="https://reseller.example/v1",
    )

    observation = observe_cost(response)

    assert observation.usd is None
    assert observation.source == "unknown"
    assert observation.status == "unknown"


def test_native_callback_refuses_to_fall_through_an_unverified_identity():
    response = _response(
        model_name="gpt-5",
        provider_name="openai",
        provider_url="https://reseller.example/v1",
    )

    with pytest.raises(ValueError, match="No verified price"):
        price_response(response)


def test_unknown_opencode_model_without_reported_fields_stays_unknown():
    response = _response(
        provider_name="openai",
        provider_url="https://opencode.ai/zen/v1",
        details={"arno_cost": {"provider": "opencode", "billing_mode": "metered"}},
    )

    observation = observe_cost(response, provider="opencode")

    assert observation.usd is None
    assert observation.status == "unknown"


def test_invalid_reported_amounts_are_ignored():
    response = _response(
        provider_name="openai",
        provider_url="https://opencode.ai/zen/v1",
        details={
            "opencode": {
                "billing_source": "go",
                "list_cost": -1,
                "allowance": float("nan"),
            }
        },
    )

    observation = observe_cost(response, provider="opencode")

    assert observation.usd is None
    assert observation.status == "unknown"


def test_cost_attributes_omit_an_unknown_total_and_keep_the_reason():
    unknown = CostObservation(usd=None, source="unknown", complete=False, price_revision="r")
    known = CostObservation(
        usd=Decimal("0.004"),
        source="provider_reported",
        complete=True,
        price_revision="r",
        provider_charge=Decimal("0"),
        allowance=Decimal("0.005"),
        billing_source="go",
    )

    unknown_attributes = cost_attributes(unknown)
    known_attributes = cost_attributes(known)

    assert "llm.cost.total" not in unknown_attributes
    assert unknown_attributes["arno.cost.completeness"] == "unknown"
    assert known_attributes["llm.cost.total"] == 0.004
    assert known_attributes["arno.cost.provider_charge"] == 0.0
    assert known_attributes["arno.cost.allowance"] == 0.005
    assert known_attributes["arno.cost.billing_source"] == "go"


async def test_opencode_nonstream_metadata_survives_sdk_mapping():
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1763000000,
        "model": "gpt-5",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cost": 0,
            "opencode": {
                "billing_source": "go",
                "list_cost": 0.02,
                "estimated": False,
                "allowance": 0.02,
                "tokens": {
                    "input": 100,
                    "cache_read": 800,
                    "cache_write_5m": 20,
                    "cache_write_1h": 10,
                    "output": 20,
                    "reasoning": 5,
                },
            },
        },
    }

    async def handler(request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(200, json=body)

    model = _opencode_model(handler)
    with override_allow_model_requests(True):
        response = await model.request([], None, ModelRequestParameters())

    assert response.provider_details is not None
    assert response.provider_details["opencode"]["list_cost"] == Decimal("0.02")
    assert response.usage.input_tokens == 930
    assert response.usage.cache_read_tokens == 800
    assert response.usage.cache_write_tokens == 30
    assert response.usage.cache_write_1h_tokens == 10
    assert response.usage.output_tokens == 20
    assert response.usage.output_reasoning_tokens == 5
    assert response.provider_details["provider_usage"]["cost"] == 0

    observation = observe_cost(response)
    assert observation.usd == Decimal("0.02")
    assert observation.provider_charge == Decimal("0")
    assert observation.allowance == Decimal("0.02")
    assert observation.billing_source == "go"
    assert observation.source == "opencode_reported"
    assert observation.complete is True
    assert observation.price_revision == OPENCODE_PRICE_REVISION


async def test_opencode_terminal_stream_usage_survives_sdk_mapping():
    chunks = [
        {
            "id": "chatcmpl-2",
            "object": "chat.completion.chunk",
            "created": 1763000000,
            "model": "gpt-5",
            "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
        },
        {
            "id": "chatcmpl-2",
            "object": "chat.completion.chunk",
            "created": 1763000000,
            "model": "gpt-5",
            "choices": [],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "cost": 0.001,
                "opencode": {
                    "billing_source": "byok",
                    "list_cost": 0.002,
                    "estimated": True,
                    "allowance": 0,
                },
            },
        },
    ]
    stream_body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
    stream_body += "data: [DONE]\n\n"

    async def handler(request: httpx2.Request) -> httpx2.Response:
        del request

        class Body(httpx2.AsyncByteStream):
            @override
            async def __aiter__(self):
                for line in stream_body.splitlines(keepends=True):
                    yield line.encode()

        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Body(),
        )

    model = _opencode_model(handler)
    with override_allow_model_requests(True):
        async with model.request_stream([], None, ModelRequestParameters()) as stream:
            async for _ in stream:
                pass
            response = stream.get()

    assert response.provider_details is not None
    assert response.provider_details["opencode"]["billing_source"] == "byok"

    observation = observe_cost(response)
    assert observation.usd == Decimal("0.002")
    assert observation.provider_charge == Decimal("0.001")
    assert observation.allowance == Decimal("0")
    assert observation.source == "opencode_estimated"


def test_unverified_endpoint_cannot_use_a_vendor_catalog_cost_filled_by_core():
    response = _response(
        usage=RequestUsage(input_tokens=10, output_tokens=5, cost=Decimal("0.01")),
        provider_url="https://custom.example/v1",
    )
    assert observe_cost(response).usd is None
    with pytest.raises(ValueError):
        price_response(response)


def test_cost_bearing_span_has_usage_and_rates_without_duplicating_reasoning():
    observation = observe_cost(
        _response(
            usage=RequestUsage(
                input_tokens=1000,
                cache_read_tokens=400,
                output_tokens=100,
                output_reasoning_tokens=25,
            )
        )
    )
    attrs = cost_attributes(observation)
    assert attrs["llm.token_count.prompt"] == 1000
    assert attrs["llm.token_count.completion"] == 100
    assert attrs["llm.token_count.total"] == 1100
    assert attrs["llm.token_count.prompt_details.cache_read"] == 400
    assert attrs["llm.token_count.completion_details.reasoning"] == 25
    assert "output_mtok" in str(attrs["arno.cost.rates"])


def test_paid_preflight_refuses_an_unknown_price_before_dispatch():
    from arno.agent.pricing import validate_pricing

    with pytest.raises(ValueError, match="No verified"):
        validate_pricing(
            provider="openai",
            model="gpt-5",
            provider_url="https://custom.example/v1",
            billing_mode="metered",
        )
    validate_pricing(
        provider="openai-responses",
        model="gpt-5",
        provider_url="https://api.openai.com/v1",
        billing_mode="metered",
    )
    validate_pricing(
        provider="ollama", model="local", provider_url="http://localhost/v1", billing_mode="local"
    )


def test_go_wallet_zero_does_not_prove_zero_usage_value():
    response = _response(
        provider_url="https://opencode.ai/inference/openai/v1",
        details={"opencode": {"billing_source": "go", "cost": 0, "allowance": "0.01"}},
    )
    observation = observe_cost(response, provider="opencode")
    assert observation.usd is None
    assert observation.provider_charge == 0
    assert observation.allowance == Decimal("0.01")


async def test_opencode_responses_api_preserves_reported_cost():
    from pydantic_ai.models.openai import OpenAIResponsesModel

    async def handler(request):
        return httpx2.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1763000000,
                "status": "completed",
                "model": "gpt-5",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "hi", "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "cost": 0,
                    "opencode": {"billing_source": "go", "list_cost": 0.003, "allowance": 0.003},
                },
            },
        )

    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler), event_hooks={"response": [capture_response]}
    )
    native = OpenAIResponsesModel(
        "gpt-5",
        provider=OpenAIProvider(
            base_url="https://opencode.ai/inference/openai/v1", api_key="test", http_client=client
        ),
    )
    model = BillingMetadataModel(native, provider="opencode", billing_mode="metered")
    with override_allow_model_requests(True):
        response = await model.request([], None, ModelRequestParameters())
    observation = observe_cost(response, provider="opencode")
    assert observation.usd == Decimal("0.003")
    assert observation.provider_charge == 0
    await client.aclose()


async def test_stream_billing_metadata_survives_a_child_task_drain():
    import asyncio

    from arno.agent import provider_usage
    from arno.agent.provider_usage import CapturingSSEStream

    class Body(httpx2.AsyncByteStream):
        @override
        async def __aiter__(self):
            yield b'data: {"usage":{"cost":0.001,"opencode":{"list_cost":0.002}}}\n\n'

    token = provider_usage._captured.set({})
    try:

        async def drain():
            async for _ in CapturingSSEStream(Body()):
                pass

        await asyncio.create_task(drain())
        captured = provider_usage._captured.get()
        assert captured is not None
        assert captured["cost"] == Decimal("0.001")
    finally:
        provider_usage._captured.reset(token)


@pytest.mark.parametrize(
    "provider,url,model",
    [
        ("openai", "https://api.deepseek.com/v1", "deepseek-chat"),
        ("openai", "https://api.cohere.ai/compatibility/v1", "command-a-03-2025"),
    ],
)
def test_catalog_url_matching_supports_known_vendors_without_an_arno_host_table(
    provider, url, model
):
    from arno.agent.pricing import validate_pricing

    validate_pricing(provider=provider, model=model, provider_url=url, billing_mode="metered")
    response = _response(
        model_name=model,
        provider_name=provider,
        provider_url=url,
        usage=RequestUsage(input_tokens=1000, output_tokens=100),
    )
    observation = observe_cost(response, provider=provider)
    assert observation.source == "genai-prices"
    assert observation.usd is not None and observation.usd > 0
    assert observation.rates


def test_the_existing_free_cohere_fallback_has_an_explicit_published_rate():
    from arno.agent.pricing import validate_pricing

    url = "https://api.cohere.ai/compatibility/v1"
    model = "command-a-plus-05-2026"
    validate_pricing(provider="cohere", model=model, provider_url=url, billing_mode="metered")
    response = _response(
        model_name=model,
        provider_name="openai",
        provider_url=url,
        usage=RequestUsage(input_tokens=1000, output_tokens=100),
    )
    observation = observe_cost(response, provider="cohere")
    assert observation.usd == 0
    assert observation.source == "published_rate"
    assert observation.rates == {"input_mtok": "0", "output_mtok": "0"}
    response.provider_url = "https://reseller.example/v1"
    assert observe_cost(response, provider="cohere").usd is None
