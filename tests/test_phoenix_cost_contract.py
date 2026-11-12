"""Contract tests for the pinned Phoenix cost patch, against a real instance.

The service is not started here. Build `deploy/phoenix-cost` and run it on a
scratch database, then point these variables at it (both loopback):

    PHOENIX_COST_URL=http://127.0.0.1:6007

Readings this file gates, one synthetic OTLP write per project:

- a supplied total, explicit zero, partial subtotal, and unknown are each
  shown as themselves rather than as a catalog price or a silent zero;
- incoming ``llm.*`` attributes survive the ``gen_ai.*`` conversion and a
  supplied amount is never recomputed;
- a logical request parent is excluded from cost and token counts while its
  attempt is priced once;
- replaying a span does not double its cost;
- audio usage keeps its cost without invented language tokens;
- the same model name on two providers stays two models, not one merged row;
- a bin with only unpriced calls is labeled unknown, never drawn as a free or
  empty range, and a mixed bin reports its subtotal with its unpriced count;
- span, trace, session, project, and time-range views agree.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource as SdkResource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic_ai import Agent, InstrumentationSettings
from pydantic_ai.capabilities import Instrumentation
from pydantic_ai.models import override_allow_model_requests
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from arno import observability
from arno.agent import metering
from arno.agent.capabilities import InferenceContext
from arno.agent.cost_journal import cost_identity, replay_costs
from arno.agent.metering import MeteredModel
from arno.agent.provider_usage import BillingMetadataModel, capture_response
from arno.agent.spend import make_spend_store, shared_day_status, spend_limits

try:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    from opentelemetry.proto.resource.v1.resource_pb2 import Resource
    from opentelemetry.proto.trace.v1.trace_pb2 import (
        ResourceSpans,
        ScopeSpans,
    )
    from opentelemetry.proto.trace.v1.trace_pb2 import (
        Span as OtlpSpan,
    )
except ModuleNotFoundError:  # pragma: no cover - depends on the dev extras
    pytest.skip("the OTLP client libraries are not installed", allow_module_level=True)

PHOENIX_URL = os.environ.get("PHOENIX_COST_URL", "http://127.0.0.1:6007").rstrip("/")
TRACES_URL = f"{PHOENIX_URL}/v1/traces"
GRAPHQL_URL = f"{PHOENIX_URL}/graphql"
POLL_TIMEOUT_SECONDS = 45.0

SUMMARY_FIELDS = """
  total { cost tokens }
  prompt { cost tokens }
  completion { cost tokens }
  unpricedCount
  partialCount
  costedCount
  completeness
"""

MODEL_BREAKDOWN_QUERY = """
query ModelBreakdown(
  $name: String!
  $projectId: ID!
  $start: DateTime!
  $end: DateTime!
) {
  projects(first: 1, filter: {col: name, value: $name}) {
    edges {
      node {
        topModelsByCost(timeRange: {start: $start, end: $end}) {
          id
          name
          provider
          costSummary(projectId: $projectId, timeRange: {start: $start, end: $end}) {
            __SUMMARY__
          }
          costDetailSummaryEntries(
            projectId: $projectId
            timeRange: {start: $start, end: $end}
          ) {
            tokenType
            isPrompt
            value {
              cost
              tokens
            }
          }
        }
        topModelsByTokenCount(timeRange: {start: $start, end: $end}) {
          id
          name
          provider
          costSummary(projectId: $projectId, timeRange: {start: $start, end: $end}) {
            __SUMMARY__
          }
          costDetailSummaryEntries(
            projectId: $projectId
            timeRange: {start: $start, end: $end}
          ) {
            tokenType
            isPrompt
            value {
              cost
              tokens
            }
          }
        }
      }
    }
  }
}
""".replace("__SUMMARY__", SUMMARY_FIELDS)

COST_SERIES_QUERY = """
query CostSeries($name: String!, $start: DateTime!, $end: DateTime!) {
  projects(first: 1, filter: {col: name, value: $name}) {
    edges {
      node {
        traceTokenCostTimeSeries(
          timeRange: {start: $start, end: $end}
          timeBinConfig: {scale: HOUR, utcOffsetMinutes: 0}
        ) {
          data {
            timestamp
            promptCost
            completionCost
            totalCost
            unpricedCount
            partialCount
            costedCount
          }
        }
      }
    }
  }
}
"""

PROJECT_QUERY = """
query ProjectCost($name: String!, $start: DateTime, $end: DateTime) {
  projects(first: 1, filter: {col: name, value: $name}) {
    edges {
      node {
        id
        name
        costSummary(__RANGE__) { __SUMMARY__ }
        tokenCountTotal
        spans(first: 25) {
          edges {
            node {
              spanId
              attributes
              costSummary { __SUMMARY__ source priceRevision billingSource }
              trace {
                id
                costSummary { __SUMMARY__ }
              }
            }
          }
        }
        sessions(first: 10) {
          edges { node { sessionId costSummary { __SUMMARY__ } } }
        }
      }
    }
  }
}
""".replace("__RANGE__", "timeRange: {start: $start, end: $end}").replace(
    "__SUMMARY__", SUMMARY_FIELDS
)


def _gql(
    query: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload: dict[str, Any] = json.loads(response.read())
    if payload.get("errors"):
        raise AssertionError(f"GraphQL errors: {payload['errors']}")
    data: dict[str, Any] = payload["data"]
    return data


def _service_reachable() -> bool:
    try:
        _gql("{ __typename }")
    except urllib.error.URLError, TimeoutError, OSError:
        return False
    return True


if not _service_reachable():
    pytest.skip(
        f"patched Phoenix is not reachable at {GRAPHQL_URL}; build and run "
        "deploy/phoenix-cost on a scratch database to run this contract",
        allow_module_level=True,
    )


def _project_name() -> str:
    return f"arno-cost-contract-{uuid.uuid4().hex[:12]}"


def _new_ids() -> tuple[str, str]:
    return uuid.uuid4().hex, uuid.uuid4().hex[:16]


def _attr(key: str, value: Any) -> KeyValue:
    if isinstance(value, bool):
        return KeyValue(key=key, value=AnyValue(bool_value=value))
    if isinstance(value, int):
        return KeyValue(key=key, value=AnyValue(int_value=value))
    if isinstance(value, float):
        return KeyValue(key=key, value=AnyValue(double_value=value))
    return KeyValue(key=key, value=AnyValue(string_value=str(value)))


def _span(
    *,
    trace_id: str,
    span_id: str,
    name: str,
    attributes: dict[str, Any],
    parent_span_id: str | None = None,
    start_offset_ms: int = 0,
) -> OtlpSpan:
    start_ns = time.time_ns() + start_offset_ms * 1_000_000
    return OtlpSpan(
        trace_id=bytes.fromhex(trace_id),
        span_id=bytes.fromhex(span_id),
        parent_span_id=bytes.fromhex(parent_span_id) if parent_span_id else b"",
        name=name,
        kind=OtlpSpan.SPAN_KIND_INTERNAL,
        start_time_unix_nano=start_ns,
        end_time_unix_nano=start_ns + 1_000_000,
        attributes=[_attr(key, value) for key, value in attributes.items()],
    )


def _export(project: str, spans: list[OtlpSpan]) -> None:
    request = ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=Resource(
                    attributes=[_attr("openinference.project.name", project)],
                ),
                scope_spans=[ScopeSpans(spans=spans)],
            )
        ]
    )
    http_request = urllib.request.Request(
        TRACES_URL,
        data=request.SerializeToString(),
        headers={"Content-Type": "application/x-protobuf"},
    )
    with urllib.request.urlopen(http_request, timeout=20) as response:
        assert response.status == 200, response.status


def _project(
    name: str,
    *,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any] | None:
    data = _gql(PROJECT_QUERY, {"name": name, "start": start, "end": end})
    edges = data["projects"]["edges"]
    if not edges:
        return None
    node: dict[str, Any] = edges[0]["node"]
    return node


def _hour_range() -> tuple[str, str]:
    now = time.time()
    return (
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600)),
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + 3600)),
    )


def _breakdown(name: str, start: str, end: str) -> dict[str, Any]:
    """The two model charts' data, including each model's scoped summary."""
    project = _project(name)
    assert project is not None, name
    data = _gql(
        MODEL_BREAKDOWN_QUERY,
        {"name": name, "projectId": project["id"], "start": start, "end": end},
    )
    edges = data["projects"]["edges"]
    assert edges, name
    node: dict[str, Any] = edges[0]["node"]
    return node


def _cost_series(name: str, start: str, end: str) -> list[dict[str, Any]]:
    data = _gql(COST_SERIES_QUERY, {"name": name, "start": start, "end": end})
    edges = data["projects"]["edges"]
    assert edges, name
    series = edges[0]["node"]["traceTokenCostTimeSeries"]["data"]
    result: list[dict[str, Any]] = series
    return result


def _wait_project(
    name: str,
    check: Any,
    *,
    timeout: float = POLL_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = _project(name)
        if last is not None and check(last):
            return last
        time.sleep(0.25)
    raise AssertionError(f"timed out waiting for {name}: {json.dumps(last)[:2000]}")


def _single_span(project: dict[str, Any]) -> dict[str, Any]:
    edges = project["spans"]["edges"]
    assert len(edges) == 1, f"expected one span, got {len(edges)}"
    node: dict[str, Any] = edges[0]["node"]
    return node


def _cost(summary: dict[str, Any]) -> float | None:
    value: float | None = summary["total"]["cost"]
    return value


def test_supplied_total_is_preferred_over_the_catalog() -> None:
    project = _project_name()
    trace_id, span_id = _new_ids()
    session_id = f"session-{uuid.uuid4().hex[:8]}"
    _export(
        project,
        [
            _span(
                trace_id=trace_id,
                span_id=span_id,
                name="chat gpt-4o",
                attributes={
                    # A model the stock catalog also prices, so a regression to
                    # catalog pricing would show a different number.
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "gpt-4o",
                    "llm.provider": "openai",
                    "llm.token_count.prompt": 1000,
                    "llm.token_count.completion": 100,
                    "llm.token_count.total": 1100,
                    "llm.cost.total": 0.0000924,
                    "arno.cost.completeness": "known",
                    "arno.cost.source": "provider_reported",
                    "arno.cost.billing_source": "balance",
                    "arno.cost.price_revision": "rates-2026-11-12",
                    "arno.cost.role": "attempt",
                    "session.id": session_id,
                },
            )
        ],
    )

    node = _wait_project(project, lambda p: p["costSummary"]["costedCount"] == 1)
    summary = node["costSummary"]
    assert _cost(summary) == pytest.approx(0.0000924, abs=1e-12)
    assert summary["completeness"] == "known"
    assert summary["unpricedCount"] == 0
    assert summary["partialCount"] == 0
    assert summary["total"]["tokens"] == 1100

    span = _single_span(node)
    span_summary = span["costSummary"]
    assert _cost(span_summary) == pytest.approx(0.0000924, abs=1e-12)
    assert span_summary["completeness"] == "known"
    assert span_summary["source"] == "provider_reported"
    assert span_summary["billingSource"] == "balance"
    assert span_summary["priceRevision"] == "rates-2026-11-12"

    trace_summary = span["trace"]["costSummary"]
    assert _cost(trace_summary) == pytest.approx(0.0000924, abs=1e-12)
    session_summary = node["sessions"]["edges"][0]["node"]["costSummary"]
    assert _cost(session_summary) == pytest.approx(0.0000924, abs=1e-12)

    # The same total is reported for a range that contains the call.
    start, end = _hour_range()
    ranged = _project(project, start=start, end=end)
    assert ranged is not None
    assert _cost(ranged["costSummary"]) == pytest.approx(0.0000924, abs=1e-12)

    # Both model charts group the supplied observation by the model it served,
    # even though it has no catalog model row.
    breakdown = _breakdown(project, start, end)
    assert [model["name"] for model in breakdown["topModelsByCost"]] == ["gpt-4o"]
    cost_model = breakdown["topModelsByCost"][0]["costSummary"]
    assert _cost(cost_model) == pytest.approx(0.0000924, abs=1e-12)
    assert cost_model["total"]["tokens"] == 1100
    assert cost_model["prompt"]["tokens"] == 1000
    assert cost_model["completion"]["tokens"] == 100

    assert [model["name"] for model in breakdown["topModelsByTokenCount"]] == ["gpt-4o"]
    token_model = breakdown["topModelsByTokenCount"][0]["costSummary"]
    assert token_model["total"]["tokens"] == 1100
    assert _cost(token_model) == pytest.approx(0.0000924, abs=1e-12)

    # The cost series carries the supplied total even though no prompt or
    # completion component was supplied to split it into.
    series = _cost_series(project, start, end)
    points = [point for point in series if point["totalCost"] is not None]
    assert points, series
    assert sum(point["totalCost"] for point in points) == pytest.approx(0.0000924, abs=1e-12)
    assert all(point["promptCost"] is None and point["completionCost"] is None for point in points)


def test_supplied_total_without_components_is_shown_for_its_model() -> None:
    """A total-only observation fills the model charts and the cost series.

    This is the shape Arno exports: one attempt supplies ``llm.cost.total``
    and token counts but no prompt/completion amounts, so the charts must not
    wait for a catalog model or invent a split.
    """
    project = _project_name()
    priced_trace, priced_id = _new_ids()
    unpriced_trace, unpriced_id = _new_ids()
    _export(
        project,
        [
            _span(
                trace_id=priced_trace,
                span_id=priced_id,
                name="chat priced total",
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "gpt-4o",
                    "llm.provider": "openai",
                    "llm.cost.total": 0.0001,
                    "arno.cost.completeness": "known",
                    "arno.cost.source": "provider_reported",
                    "arno.cost.role": "attempt",
                },
            ),
            _span(
                trace_id=unpriced_trace,
                span_id=unpriced_id,
                name="chat unpriced usage",
                start_offset_ms=1,
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "gpt-4o",
                    "llm.provider": "openai",
                    "llm.token_count.prompt": 7,
                    "arno.cost.completeness": "unknown",
                    "arno.cost.role": "attempt",
                },
            ),
        ],
    )

    node = _wait_project(
        project,
        lambda p: p["costSummary"]["costedCount"] == 1 and p["costSummary"]["unpricedCount"] == 1,
    )
    summary = node["costSummary"]
    assert _cost(summary) == pytest.approx(0.0001, abs=1e-12)
    assert summary["completeness"] == "partial"
    assert summary["total"]["tokens"] == 7

    start, end = _hour_range()
    breakdown = _breakdown(project, start, end)
    assert [model["name"] for model in breakdown["topModelsByCost"]] == ["gpt-4o"]
    cost_model = breakdown["topModelsByCost"][0]["costSummary"]
    assert _cost(cost_model) == pytest.approx(0.0001, abs=1e-12)
    assert cost_model["total"]["tokens"] == 7

    assert [model["name"] for model in breakdown["topModelsByTokenCount"]] == ["gpt-4o"]
    token_model = breakdown["topModelsByTokenCount"][0]["costSummary"]
    assert token_model["total"]["tokens"] == 7
    assert token_model["prompt"]["tokens"] == 7

    points = [
        point for point in _cost_series(project, start, end) if point["totalCost"] is not None
    ]
    assert points, points
    assert sum(point["totalCost"] for point in points) == pytest.approx(0.0001, abs=1e-12)
    assert all(point["promptCost"] is None and point["completionCost"] is None for point in points)


def test_catalog_priced_legacy_span_stays_in_the_model_breakdown() -> None:
    """An older instrumentation supplies no cost, so the catalog still prices it."""
    project = _project_name()
    trace_id, span_id = _new_ids()
    _export(
        project,
        [
            _span(
                trace_id=trace_id,
                span_id=span_id,
                name="chat legacy",
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "gpt-4o",
                    "llm.provider": "openai",
                    "llm.token_count.prompt": 1000,
                    "llm.token_count.completion": 100,
                    "llm.token_count.total": 1100,
                },
            )
        ],
    )

    node = _wait_project(project, lambda p: p["costSummary"]["costedCount"] == 1)
    assert node["costSummary"]["total"]["tokens"] == 1100
    start, end = _hour_range()
    breakdown = _breakdown(project, start, end)
    assert [model["name"] for model in breakdown["topModelsByCost"]] == ["gpt-4o"]
    model = breakdown["topModelsByCost"][0]
    catalog_cost = _cost(model["costSummary"])
    assert catalog_cost is not None
    assert catalog_cost > 0
    assert model["costSummary"]["total"]["tokens"] == 1100
    assert model["costDetailSummaryEntries"], "the catalog breakdown is preserved"


def test_explicit_zero_is_a_measured_zero() -> None:
    project = _project_name()
    trace_id, span_id = _new_ids()
    _export(
        project,
        [
            _span(
                trace_id=trace_id,
                span_id=span_id,
                name="chat local",
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "qwen-local",
                    "llm.cost.total": 0.0,
                    "arno.cost.completeness": "known",
                    "arno.cost.source": "local_zero",
                },
            )
        ],
    )

    node = _wait_project(project, lambda p: p["costSummary"]["costedCount"] == 1)
    summary = node["costSummary"]
    assert _cost(summary) == 0
    assert summary["completeness"] == "known"
    assert summary["unpricedCount"] == 0
    assert _single_span(node)["costSummary"]["completeness"] == "known"


def test_same_model_name_on_two_providers_stays_two_models() -> None:
    """The served identity is name plus provider; the same name must not merge.

    Two providers can serve one model name at different rates, so a breakdown
    keyed by name alone would add unlike costs together and label both rows
    with one provider.
    """
    project = _project_name()
    opencode_trace, opencode_id = _new_ids()
    anthropic_trace, anthropic_id = _new_ids()
    _export(
        project,
        [
            _span(
                trace_id=opencode_trace,
                span_id=opencode_id,
                name="chat opencode",
                start_offset_ms=0,
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "deepseek-v4.1-flash",
                    "llm.provider": "opencode",
                    "llm.token_count.total": 10,
                    "llm.cost.total": 0.001,
                    "arno.cost.completeness": "known",
                    "arno.cost.source": "opencode_reported",
                    "arno.cost.role": "attempt",
                },
            ),
            _span(
                trace_id=anthropic_trace,
                span_id=anthropic_id,
                name="chat anthropic",
                start_offset_ms=2,
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "deepseek-v4.1-flash",
                    "llm.provider": "anthropic",
                    "llm.token_count.total": 20,
                    "llm.cost.total": 0.002,
                    "arno.cost.completeness": "known",
                    "arno.cost.source": "provider_reported",
                    "arno.cost.role": "attempt",
                },
            ),
        ],
    )

    node = _wait_project(project, lambda p: p["costSummary"]["costedCount"] == 2)
    assert _cost(node["costSummary"]) == pytest.approx(0.003, abs=1e-12)

    start, end = _hour_range()
    breakdown = _breakdown(project, start, end)
    models = breakdown["topModelsByCost"]
    assert len(models) == 2, models
    assert {model["provider"] for model in models} == {"opencode", "anthropic"}
    assert {model["name"] for model in models} == {"deepseek-v4.1-flash"}
    assert len({model["id"] for model in models}) == 2
    model_costs: list[float] = []
    for model in models:
        model_cost = _cost(model["costSummary"])
        assert model_cost is not None
        model_costs.append(model_cost)
    assert sorted(model_costs) == pytest.approx([0.001, 0.002], abs=1e-12)
    token_models = breakdown["topModelsByTokenCount"]
    assert len(token_models) == 2, token_models
    assert sorted(
        token_model["costSummary"]["total"]["tokens"] for token_model in token_models
    ) == [10, 20]


def test_unpriced_only_time_range_reads_unknown_not_zero() -> None:
    """A bin with no priced call has no total; it is unknown, not a free zero."""
    project = _project_name()
    trace_id, span_id = _new_ids()
    _export(
        project,
        [
            _span(
                trace_id=trace_id,
                span_id=span_id,
                name="chat unpriced",
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "gpt-4o",
                    "llm.token_count.prompt": 40,
                    "llm.token_count.completion": 10,
                    "llm.token_count.total": 50,
                    "arno.cost.completeness": "unknown",
                    "arno.cost.role": "attempt",
                },
            )
        ],
    )

    _wait_project(project, lambda p: p["costSummary"]["unpricedCount"] == 1)
    start, end = _hour_range()
    bins = [point for point in _cost_series(project, start, end) if point["unpricedCount"]]
    assert len(bins) == 1, bins
    bin_ = bins[0]
    assert bin_["totalCost"] is None
    assert bin_["unpricedCount"] == 1
    assert bin_["costedCount"] == 0
    assert bin_["partialCount"] == 0


def test_mixed_time_bin_reports_counts_beside_its_subtotal() -> None:
    """A bin with a priced and an unpriced call is a subtotal, and says so."""
    project = _project_name()
    priced_trace, priced_id = _new_ids()
    unpriced_trace, unpriced_id = _new_ids()
    _export(
        project,
        [
            _span(
                trace_id=priced_trace,
                span_id=priced_id,
                name="chat priced",
                start_offset_ms=0,
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "gpt-4o",
                    "llm.cost.total": 0.0001,
                    "arno.cost.completeness": "known",
                    "arno.cost.source": "provider_reported",
                    "arno.cost.role": "attempt",
                },
            ),
            _span(
                trace_id=unpriced_trace,
                span_id=unpriced_id,
                name="chat unpriced",
                start_offset_ms=2,
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "gpt-4o",
                    "llm.token_count.prompt": 7,
                    "arno.cost.completeness": "unknown",
                    "arno.cost.role": "attempt",
                },
            ),
        ],
    )

    _wait_project(
        project,
        lambda p: p["costSummary"]["unpricedCount"] == 1,
    )
    start, end = _hour_range()
    bins = [
        point
        for point in _cost_series(project, start, end)
        if point["unpricedCount"] or point["costedCount"]
    ]
    assert len(bins) == 1, bins
    bin_ = bins[0]
    assert bin_["totalCost"] == pytest.approx(0.0001, abs=1e-12)
    assert bin_["unpricedCount"] == 1
    assert bin_["costedCount"] == 1
    assert bin_["partialCount"] == 0


def test_unknown_is_not_priced_and_is_counted() -> None:
    project = _project_name()
    trace_id, span_id = _new_ids()
    _export(
        project,
        [
            _span(
                trace_id=trace_id,
                span_id=span_id,
                name="chat unpriced",
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "gpt-4o",
                    "llm.token_count.prompt": 40,
                    "llm.token_count.completion": 10,
                    "llm.token_count.total": 50,
                    "arno.cost.completeness": "unknown",
                    "arno.cost.source": "genai-prices",
                },
            )
        ],
    )

    node = _wait_project(project, lambda p: p["costSummary"]["unpricedCount"] == 1)
    summary = node["costSummary"]
    assert _cost(summary) == 0
    assert summary["unpricedCount"] == 1
    assert summary["costedCount"] == 0
    assert summary["completeness"] == "unknown"
    # The usage stays visible even though its price does not.
    assert node["tokenCountTotal"] == 50
    span_summary = _single_span(node)["costSummary"]
    assert _cost(span_summary) is None
    assert span_summary["completeness"] == "unknown"


def test_invalid_amount_is_unknown_not_a_catalog_price() -> None:
    project = _project_name()
    trace_id, span_id = _new_ids()
    _export(
        project,
        [
            _span(
                trace_id=trace_id,
                span_id=span_id,
                name="chat invalid amount",
                attributes={
                    # A catalog-priced model, so a fallback would be visible.
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "gpt-4o",
                    "llm.cost.total": -1.0,
                    "arno.cost.completeness": "known",
                    "arno.cost.source": "provider_reported",
                    "arno.cost.role": "attempt",
                },
            )
        ],
    )

    node = _wait_project(project, lambda p: p["costSummary"]["unpricedCount"] == 1)
    assert _cost(node["costSummary"]) == 0
    assert node["costSummary"]["completeness"] == "unknown"
    assert _single_span(node)["costSummary"]["total"]["cost"] is None


def test_partial_subtotal_is_labeled_partial() -> None:
    project = _project_name()
    trace_id, span_id = _new_ids()
    _export(
        project,
        [
            _span(
                trace_id=trace_id,
                span_id=span_id,
                name="chat partial",
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "deepseek-v4.1-flash",
                    "llm.token_count.prompt": 1000,
                    "llm.cost.total": 0.002,
                    "arno.cost.completeness": "partial",
                    "arno.cost.source": "genai-prices",
                    "arno.cost.price_revision": "genai-prices-2.58.0",
                },
            )
        ],
    )

    node = _wait_project(project, lambda p: p["costSummary"]["partialCount"] == 1)
    summary = node["costSummary"]
    assert _cost(summary) == pytest.approx(0.002, abs=1e-12)
    assert summary["completeness"] == "partial"
    assert summary["partialCount"] == 1
    assert _single_span(node)["costSummary"]["completeness"] == "partial"


def test_logical_parent_is_excluded_and_the_attempt_is_priced_once() -> None:
    project = _project_name()
    trace_id, parent_id = _new_ids()
    attempt_id = uuid.uuid4().hex[:16]
    _export(
        project,
        [
            _span(
                trace_id=trace_id,
                span_id=parent_id,
                name="chat logical request",
                start_offset_ms=0,
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "deepseek-v4.1-flash",
                    "llm.token_count.prompt": 999,
                    "llm.token_count.completion": 111,
                    "llm.token_count.total": 1110,
                    # A logical parent must not be priced even when cost
                    # metadata is attached to it.
                    "llm.cost.total": 9.99,
                    "arno.cost.completeness": "known",
                    "arno.cost.role": "logical_request",
                },
            ),
            _span(
                trace_id=trace_id,
                span_id=attempt_id,
                name="chat attempt",
                parent_span_id=parent_id,
                start_offset_ms=1,
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "deepseek-v4.1-flash",
                    "llm.token_count.prompt": 10,
                    "llm.token_count.completion": 5,
                    "llm.token_count.total": 15,
                    "llm.cost.total": 0.0005,
                    "arno.cost.completeness": "known",
                    "arno.cost.source": "provider_reported",
                    "arno.cost.role": "attempt",
                    "arno.cost.attempt_id": attempt_id,
                    "arno.cost.workload": "chat",
                    "arno.cost.modality": "text",
                },
            ),
        ],
    )

    node = _wait_project(project, lambda p: p["costSummary"]["costedCount"] == 1)
    summary = node["costSummary"]
    assert _cost(summary) == pytest.approx(0.0005, abs=1e-12)
    assert summary["unpricedCount"] == 0
    assert summary["costedCount"] == 1
    assert node["tokenCountTotal"] == 15

    by_name = {edge["node"]["spanId"]: edge["node"] for edge in node["spans"]["edges"]}
    assert by_name[parent_id]["costSummary"] is None
    assert _cost(by_name[attempt_id]["costSummary"]) == pytest.approx(0.0005, abs=1e-12)

    # The model breakdown prices the attempt once and does not add the
    # logical request parent's larger token count.
    start, end = _hour_range()
    breakdown = _breakdown(project, start, end)
    assert [model["name"] for model in breakdown["topModelsByCost"]] == ["deepseek-v4.1-flash"]
    cost_model = breakdown["topModelsByCost"][0]["costSummary"]
    assert _cost(cost_model) == pytest.approx(0.0005, abs=1e-12)
    assert cost_model["total"]["tokens"] == 15
    token_model = breakdown["topModelsByTokenCount"][0]["costSummary"]
    assert token_model["total"]["tokens"] == 15


def test_replaying_a_span_does_not_double_count() -> None:
    project = _project_name()
    trace_id, span_id = _new_ids()
    span = _span(
        trace_id=trace_id,
        span_id=span_id,
        name="chat replayed",
        attributes={
            "openinference.span.kind": "LLM",
            "llm.model_name": "gpt-4o",
            "llm.cost.total": 0.25,
            "arno.cost.completeness": "known",
            "arno.cost.source": "provider_reported",
        },
    )
    _export(project, [span])
    node = _wait_project(project, lambda p: p["costSummary"]["costedCount"] == 1)
    assert _cost(node["costSummary"]) == pytest.approx(0.25, abs=1e-12)

    _export(project, [span])
    time.sleep(2)
    replayed = _project(project)
    assert replayed is not None
    assert _cost(replayed["costSummary"]) == pytest.approx(0.25, abs=1e-12)
    assert len(replayed["spans"]["edges"]) == 1


def test_audio_cost_keeps_its_amount_without_invented_tokens() -> None:
    project = _project_name()
    trace_id, span_id = _new_ids()
    _export(
        project,
        [
            _span(
                trace_id=trace_id,
                span_id=span_id,
                name="transcribe audio",
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "whisper-1",
                    "llm.provider": "openai",
                    "llm.cost.total": 0.0001,
                    "arno.cost.completeness": "known",
                    "arno.cost.source": "provider_reported",
                    "arno.cost.role": "attempt",
                    "arno.cost.modality": "audio",
                    "arno.cost.workload": "transcription",
                    # No token counts: duration is the billable unit.
                },
            )
        ],
    )

    node = _wait_project(project, lambda p: p["costSummary"]["costedCount"] == 1)
    assert _cost(node["costSummary"]) == pytest.approx(0.0001, abs=1e-12)
    assert node["costSummary"]["total"]["tokens"] == 0
    span = _single_span(node)
    assert span["costSummary"]["total"]["tokens"] is None
    assert json.loads(span["attributes"])["llm"]["model_name"] == "whisper-1"


def test_gen_ai_attributes_do_not_overwrite_a_supplied_amount() -> None:
    """A Pydantic AI-style span carries gen_ai.* usage and Arno's llm.cost.*.

    Ingestion synthesizes the OpenInference attributes Phoenix needs, but a
    supplied amount must survive that conversion and stay authoritative.
    """
    project = _project_name()
    trace_id, span_id = _new_ids()
    _export(
        project,
        [
            _span(
                trace_id=trace_id,
                span_id=span_id,
                name="chat gen_ai",
                attributes={
                    "gen_ai.operation.name": "chat",
                    "gen_ai.provider.name": "openai",
                    "gen_ai.request.model": "gpt-4o",
                    "gen_ai.response.model": "gpt-4o-2026-01-01",
                    "gen_ai.usage.input_tokens": 1000,
                    "gen_ai.usage.output_tokens": 100,
                    "openinference.span.kind": "LLM",
                    "llm.cost.total": 0.0000924,
                    "arno.cost.completeness": "known",
                    "arno.cost.source": "genai-prices",
                    "arno.cost.price_revision": "genai-prices-2.58.0",
                    "arno.cost.role": "attempt",
                },
            )
        ],
    )

    node = _wait_project(project, lambda p: p["costSummary"]["costedCount"] == 1)
    assert _cost(node["costSummary"]) == pytest.approx(0.0000924, abs=1e-12)
    span = _single_span(node)
    assert _cost(span["costSummary"]) == pytest.approx(0.0000924, abs=1e-12)
    assert span["costSummary"]["source"] == "genai-prices"
    # gen_ai usage supplies the tokens; the supplied amount is untouched.
    assert node["costSummary"]["total"]["tokens"] == 1100
    attributes = json.loads(span["attributes"])
    assert attributes["llm"]["cost"]["total"] == pytest.approx(0.0000924, abs=1e-12)
    assert attributes["llm"]["model_name"] == "gpt-4o"
    assert attributes["llm"]["token_count"]["total"] == 1100


def test_span_trace_and_session_views_agree_with_the_project() -> None:
    project = _project_name()
    trace_id, priced_id = _new_ids()
    unpriced_id = uuid.uuid4().hex[:16]
    session_id = f"session-{uuid.uuid4().hex[:8]}"
    _export(
        project,
        [
            _span(
                trace_id=trace_id,
                span_id=priced_id,
                name="chat priced",
                start_offset_ms=0,
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "gpt-4o",
                    "llm.cost.total": 0.0001,
                    "arno.cost.completeness": "known",
                    "arno.cost.source": "provider_reported",
                    "session.id": session_id,
                },
            ),
            _span(
                trace_id=trace_id,
                span_id=unpriced_id,
                name="chat unpriced",
                start_offset_ms=1,
                attributes={
                    "openinference.span.kind": "LLM",
                    "llm.model_name": "gpt-4o",
                    "llm.token_count.prompt": 7,
                    "arno.cost.completeness": "unknown",
                    "session.id": session_id,
                },
            ),
        ],
    )

    node = _wait_project(project, lambda p: p["costSummary"]["unpricedCount"] == 1)
    summary = node["costSummary"]
    assert _cost(summary) == pytest.approx(0.0001, abs=1e-12)
    assert summary["completeness"] == "partial"
    assert summary["costedCount"] == 1

    trace_summary = node["spans"]["edges"][0]["node"]["trace"]["costSummary"]
    assert _cost(trace_summary) == pytest.approx(0.0001, abs=1e-12)
    assert trace_summary["unpricedCount"] == 1
    assert trace_summary["completeness"] == "partial"

    session_summary = node["sessions"]["edges"][0]["node"]["costSummary"]
    assert _cost(session_summary) == pytest.approx(0.0001, abs=1e-12)
    assert session_summary["unpricedCount"] == 1
    assert session_summary["completeness"] == "partial"


async def test_sdk_journal_native_budget_and_phoenix_agree_after_replay(db, monkeypatch):
    """Exercise the complete accounting path, with only the paid endpoint replaced by a stub."""
    project = _project_name()
    session_id = f"acceptance-{uuid.uuid4().hex}"
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setattr(observability, "_collector_endpoint", lambda: TRACES_URL)
    monkeypatch.setattr(observability, "_project_name", lambda: project)
    monkeypatch.setattr(observability, "_headers", lambda: None)
    provider = TracerProvider(resource=SdkResource.create({"openinference.project.name": project}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=TRACES_URL, timeout=5))
    )
    monkeypatch.setattr(metering, "_tracer", lambda: provider.get_tracer("arno.cost.acceptance"))
    calls = []

    async def paid_endpoint(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        payload = json.loads(request.content)
        assert payload.get("max_completion_tokens", payload.get("max_tokens")) == 8192
        return httpx2.Response(
            200,
            json={
                "id": "synthetic-cost-call",
                "object": "chat.completion",
                "created": 1763000000,
                "model": "deepseek-v4.1-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Synthetic answer."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                    "cost": 0,
                    "opencode": {
                        "list_cost": 0.0000924,
                        "allowance": 0.0001,
                        "billing_source": "go",
                        "estimated": False,
                    },
                },
            },
        )

    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(paid_endpoint), event_hooks={"response": [capture_response]}
    )
    native = OpenAIChatModel(
        "deepseek-v4.1-flash",
        provider=OpenAIProvider(
            base_url="https://opencode.ai/inference/openai/v1",
            api_key="synthetic",
            http_client=client,
        ),
    )
    model = MeteredModel(
        BillingMetadataModel(native, provider="opencode", billing_mode="metered"),
        provider="opencode",
        billing_mode="metered",
        role="chat",
    )
    store, pool = await make_spend_store(os.environ["DATABASE_URL"])
    try:
        status = await shared_day_status(store)
        before = (await store.get_many([status.key]))[status.key].usd
        limits = spend_limits(store)
        assert limits is not None
        agent = Agent(
            model,
            capabilities=[
                Instrumentation(
                    InstrumentationSettings(tracer_provider=provider, include_content=False)
                ),
                InferenceContext(),
                limits,
            ],
        )
        with override_allow_model_requests(True), cost_identity(session_id=session_id):
            result = await agent.run("Synthetic request.", conversation_id=session_id)
        assert result.output == "Synthetic answer."
        assert len(calls) == 1
        row = await db.fetchrow_required("SELECT * FROM llm_calls")
        assert row["usd"] == Decimal("0.0000924")
        assert row["provider_charge"] == 0
        assert row["allowance"] == Decimal("0.0001")
        assert row["session_id"] == session_id
        assert row["project_name"] == project
        after = (await store.get_many([status.key]))[status.key].usd
        assert after - before == row["usd"]
        assert await asyncio.to_thread(provider.force_flush)
        node = await asyncio.to_thread(
            _wait_project,
            project,
            lambda p: p["costSummary"]["costedCount"] == 1 and bool(p["sessions"]["edges"]),
        )
        assert _cost(node["costSummary"]) == pytest.approx(float(row["usd"]))
        assert node["costSummary"]["total"]["tokens"] == 15
        assert node["sessions"]["edges"], [
            (
                edge["node"]["spanId"],
                {
                    key: value
                    for key, value in json.loads(edge["node"]["attributes"]).items()
                    if key in {"session", "session.id", "arno", "openinference"}
                },
            )
            for edge in node["spans"]["edges"]
        ]
        assert node["sessions"]["edges"][0]["node"]["sessionId"] == session_id
        await db.execute("UPDATE llm_calls SET finished_at=now()-interval '6 minutes'")
        monkeypatch.setattr(observability, "_project_name", lambda: "changed-after-call")
        # Recovery keeps the original project even after CLI/eval configuration changes.
        from arno.agent.cost_journal import build_replay_span

        assert build_replay_span(row).resource.attributes["openinference.project.name"] == project
        assert (await replay_costs())["exported"] == 1
        await asyncio.sleep(0.2)
        replayed = await asyncio.to_thread(_project, project)
        assert replayed is not None
        assert replayed["costSummary"]["costedCount"] == 1
        assert _cost(replayed["costSummary"]) == pytest.approx(float(row["usd"]))
        assert (await store.get_many([status.key]))[status.key].usd == after
    finally:
        await client.aclose()
        if pool is not None:
            await pool.close()
        await asyncio.to_thread(provider.shutdown)
