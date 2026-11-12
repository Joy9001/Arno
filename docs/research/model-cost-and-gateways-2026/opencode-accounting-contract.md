# OpenCode accounting contract

Research follow-up, November 12, 2026. This resolves the meaning of OpenCode's returned cost fields
for the native implementation plan. No provider request or credential was used. The official
[Inference API](https://opencode.ai/docs/console/api/inference/) is the primary source; the
proposals below are application design, not observed account behavior.

## Established wire semantics

The current documentation describes OpenAI Chat Completions, Responses, Anthropic Messages, and
Gemini endpoints. Each model selects an API family; the public models listing describes it. Cost
is returned alongside usage, with terminal streaming usage on the corresponding API event.

The documented fields have distinct meanings:

| Field | Meaning |
|---|---|
| `usage.cost` or the Gemini equivalent | Balance charge; zero for covered Go requests; BYOK may be an estimate |
| `opencode.billing_source` | `free`, `balance`, `go`, `go-plus`, or `byok` |
| `opencode.list_cost` | List-price value with time and context tiers applied |
| `opencode.estimated` | Marks estimated BYOK cost rather than a charge collected by OpenCode |
| `opencode.allowance` | Go allowance debit, expressed in the plan's dollar-denominated units |
| `opencode.tokens` | Billable token breakdown; input excludes cache categories and output includes reasoning |

The response amount is known before the balance or allowance is updated. The documentation says
an exhausted balance or limit can result in a lower final charge. A response-reported charge is
therefore provisional until account reconciliation, even if it is more informative than a catalog
estimate. These facts do not establish the owner's plan eligibility or permission for every client.

## Consequences for Arno

Use a narrow OpenCode adapter to retain numeric usage and these fields before a generic SDK drops
them. Preserve `billing_source` on each call rather than infer it permanently from the configured
model. Validate finite nonnegative decimal values and completeness. Do not turn missing cost into
zero or substitute list price into a field labeled balance charge.

An included Go call can have zero balance charge, positive list-price value, and positive allowance
debit. All three belong to one inference attempt, not three expenses. A BYOK estimate is not an
OpenCode cash deduction. Local budget policy must explicitly name whether it limits usage value,
cash charges, or known allowance debit; a cash-only zero counter cannot stop quota consumption.

`opencode.allowance` and `list_cost` must be stored independently; their equality was not tested.
Normalize cache and reasoning categories once. In particular, do not add reasoning to an output
total that already contains it, and do not run exclusive-input token counts through a calculator
expecting inclusive input without conversion.

Use the configured model's real API family rather than route every model through Chat Completions.
The implementation should retain existing compatible endpoint configuration, with an explicit
migration and compatibility test before recommending the currently documented URL layout. Keep
Arno's existing stable session header, identify it honestly, and never spoof another client to
obtain eligibility.

Preflight admission cannot use a cost that arrives only after generation. It needs a validated
conservative rate revision, supported output and billable-tool bounds, or an explicitly documented
circuit-breaker guarantee. A per-response field closes the accounting gap; it does not by itself
close the concurrent-admission gap.

## Required verification

- Parse synthetic terminal responses for all supported wire formats, including explicit zero,
  missing fields, BYOK estimates, Go allowance, and a reported amount below list price.
- Test streaming final usage, early cancellation, errors after upstream acceptance, and replay.
- Confirm that one provider attempt produces one persisted charge and one cost-bearing Phoenix
  span, with no duplication of raw and normalized usage.
- During separately authorized live acceptance, compare reported values against the provider's
  usage view for a dedicated key and interval. State the reporting lag and explain any variance.
- Validate the selected billing profile's eligibility and documented client requirements during
  setup; API compatibility alone is not an entitlement check.

No live field parity, legacy-endpoint compatibility, provider debit, or plan eligibility was
verified. Missing terminal usage remains an uncertain charge and must not be silently released
from a strict budget reservation.
