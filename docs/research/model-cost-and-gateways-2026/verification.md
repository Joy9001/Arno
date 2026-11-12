# Local verification and remaining experiments

Research evidence, November 12, 2026. These checks used installed packages and synthetic usage only.
No model, gateway, subscription, billing API, or real financial data was accessed.

## Installed boundary reproduced

The installed packages report Pydantic AI 2.64.0, pydantic-ai-harness 0.64.0, and genai-prices
0.2.1. The repository configures Phoenix 21.0 in `compose.yaml`; this does not verify the running
image.

A synthetic response used 1,000 input tokens, including 800 cache-read tokens, and 100 output
tokens, with model `deepseek-v4.1-flash`, provider name `openai`, and an OpenCode-compatible URL.
The following results came from the installed harness's private `_price_of()` diagnostic method;
that method is evidence of current behavior, not a proposed application integration.

```text
registry LookupError
default_unpriced zero
harness_default (Decimal('0'), False, None)
harness_with_reported_usage_cost (Decimal('0'), False, None)
harness_custom_price (Decimal('0.0001836'), True, None)
```

The fourth line matters: assigning a reported amount to `response.usage.cost` alone does not fix the
current harness's ordinary successful-response path. Its default reprices through `response.cost()`.
A `SpendLimits.price` callback can supply the amount explicitly. The synthetic amount is arbitrary;
it is not an observed OpenCode charge or a pricing recommendation.

The reproducer runs with `uv run python -` and this input:

```python
from decimal import Decimal

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage
from pydantic_ai_harness.spend import SpendLimits

response = ModelResponse(
    parts=[TextPart("synthetic")],
    usage=RequestUsage(input_tokens=1000, output_tokens=100, cache_read_tokens=800),
    model_name="deepseek-v4.1-flash",
    provider_name="openai",
    provider_url="https://opencode.ai/zen/go/v1",
)
try:
    print("registry", response.cost().total_price)
except (LookupError, ValueError) as exc:
    print("registry", type(exc).__name__)

limits = SpendLimits()
print("default_unpriced", limits.on_unpriced)
print("harness_default", limits._price_of(response))
response.usage.cost = Decimal("0.0001836")
print("harness_with_reported_usage_cost", limits._price_of(response))
custom = SpendLimits(price=lambda item: item.usage.cost)
print("harness_custom_price", custom._price_of(response))
```

Inspect the implementation in the installed `pydantic_ai_harness/spend/_capability.py`,
`_price_of()`, and the matching pinned [Pydantic AI
source](https://github.com/pydantic/pydantic-ai/tree/v2.64.0). Local source inspection also
confirmed that Arno does not supply `price=` or `on_unpriced=` at its `SpendLimits` construction
sites. Its host-side spend store catches database errors and continues with zero readings or dropped
writes. See [`capabilities.py`](../../../src/arno/agent/capabilities.py) and
[`spend.py`](../../../src/arno/agent/spend.py).

## Acceptance experiments before implementation is called reliable

These are proposed experiments, not completed tests. An implementation should start with synthetic
responses and failures; any paid model validation requires a separate explicit request under Arno's
existing eval policy.

1. Confirm missing, partial, zero, included-in-plan, provider-reported, and estimated costs remain
   distinct in persistence, budgets, API responses, and Phoenix.
2. Verify provider and billing-profile matching with identical model names on two endpoints,
   deployment aliases, fallback responses, and an unknown endpoint. Missing metadata must not
   silently select another provider's price.
3. Check uncached input, cache reads and writes, cache lifetimes, reasoning already included in
   output, context tiers, batch and service tiers, images, audio duration, and charged tool calls.
   Unsupported categories must mark the result partial or unknown.
4. Capture terminal usage for streamed and ordinary responses. Cancel before terminal usage, time
   out after upstream acceptance, retry, fall back, and resume after a crash. Record uncertainty;
   do not invent an unobserved charge or discard a potentially billed attempt.
5. Start chat and import requests concurrently near a budget boundary. Measure overshoot and
   document exactly what admission control guarantees. Test unavailable pricing and database
   reads/writes. A strict policy must stop paid calls when its authority is unavailable.
6. Reconcile request sums against the provider's account report or invoice for a dedicated key and
   reporting interval. Keep discounts, subscription fees, allowance debit, credit top-ups, taxes,
   and currency conversion separate. Reporting lag must remain visible.
7. Restart and restore from the supported backup. Verify pricing revisions, usage events, counters,
   and any in-flight reservations recover consistently. Audit OAuth refresh and revocation without
   logging credentials.
8. If a gateway is proposed, repeat tool calls, structured results, multimodal input, streaming,
   context limits, and trace correlation through its pinned release. Audit the selected image's
   complete license inventory. Confirm that gateway and application spans do not double-count
   one upstream attempt.

No live cost parity, gateway budget, subscription login, image license audit, or backup rehearsal
was performed during this research. Those limits prevent a claim of deployment readiness.

## Capability-event delivery follow-up

On November 12, 2026, an offline `FunctionModel` probe confirmed that a capability method decorated
with `@on_event(SpendRecordedEvent)` receives the spend event from `await agent.run(...)` without
an external event-stream handler. The observed output was:

```text
spend_events_without_external_handler ['0.01']
```

The probe used `SpendLimits(price=lambda response: Decimal('0.01'))`, a synthetic stream yielding
one string, and an observer collecting `event.usd`. No provider was called. A bare `@on_event`
receives every event rather than inferring a filter from the annotation, so an observer must name
`SpendRecordedEvent` explicitly. Adding a listener also means the fake model must support a stream;
the successful probe used `FunctionModel(stream_function=...)`.

This closes the narrow event-delivery uncertainty in
[implementation-seams.md](implementation-seams.md); it does not establish atomicity between native
counter updates, audit persistence, and exporting a cost span. Those are separate design concerns.

## Physical-attempt wrapper probe

After independent review, an offline probe on the installed Pydantic AI 2.64.0 composed
`FallbackModel` from two concrete `FunctionModel` instances, each inside a public `WrapperModel`.
The first raised `ModelHTTPError(503)`; the second returned synthetic text. Each wrapper's
`request` opened an OTel child span and marked the current native span as a logical request.
An in-memory exporter asserted the result:

```text
provider_attempts ['synthetic-failed', 'synthetic-success']
attempt_spans 2
logical_request_spans 1
distinct_attempt_ids True
same_logical_parent True
network_calls 0
```

The successful probe used `Agent(..., capabilities=[Instrumentation(InstrumentationSettings(
tracer_provider=provider))])`; `Agent(instrument=...)` is not accepted by this installed version.
This establishes the nonstreaming public placement and parentage, not reservation persistence,
stream cancellation, crash recovery, or Phoenix filtering. Those remain implementation tests.

Another offline probe priced a synthetic `gpt-5.4` response with provider `openai-chatgpt` and URL
`https://api.openai.com/v1`. Native URL-first lookup correctly selected the OpenAI catalog and
returned `$0.00055` for 100 input and 20 output tokens. Therefore the review's assertion that the
ChatGPT provider name alone makes pricing fail was too broad. Explicit pricing identity remains
useful for preflight and audit; it must preserve successful native URL-first resolution.

## Complexity-audit probes

The later [complexity audit](complexity-audit.md) verified public native APIs rather than replacing
the budget engine. An offline `FunctionModel` returned an incomplete response carrying a known
`usage.cost=Decimal('0.002')` and `unmeasured_requests=1`. The callback returned `None` for
incomplete usage, allowing native subtotal handling; `on_unpriced='raise'` then stopped the run.

```text
partial_subtotal_recorded 0.002
partial_stopped_run True
unpriced_requests 1
batch_negative_delta_and_replay_total 0.07
network_calls 0
```

The batch check used public `SpendEntry` and `InMemorySpendStore.add_many`: post `$0.10`, then post
`$-0.03` twice with the same settlement token. This proves correction and short replay semantics,
not Postgres commit recovery or replay after token expiry. Those remain mandatory batch tests.

## Documentation checks

Local research links were checked for missing files. Thirty unique linked GitHub source files were
retrieved successfully at their cited revisions. That checks reference availability, not the truth
of every claim; the material conclusions were also reviewed against source, and unsupported claims
about unconditional enterprise imports and cost precedence were corrected. Documentation about an
upcoming capture-rate feature was kept qualified by its release banner rather than assumed to be
present in a stable image.
