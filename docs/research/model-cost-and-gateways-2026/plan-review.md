# Implementation plan review

Historical review of the larger first draft. The later
[complexity audit](complexity-audit.md) and revised [plan](../../arno/cost-tracking.md) supersede
its choices to replace native counters and add receipt/version/export infrastructure. The source
findings about batching, fallback spans, and fail-open stores remain relevant evidence.

Independent review, November 12, 2026, using DeepSeek V4.1 Flash with High effort. The reviewer
inspected the proposed plan and installed framework source. This records the parent's disposition;
it does not certify an implementation or replace the unchecked feature definition of done.

| Finding | Disposition in the implementation plan |
|---|---|
| Native batch enqueue can auto-submit before `flush`; default resubmissions add attempts | Holds precede the first enqueue/run; disable native resubmission on creation/resume and admit explicit retries |
| One native model span can contain multiple fallback attempts | Concrete-provider wrappers create real attempt spans; native logical parents are excluded from cost/token rollups |
| Wrapper entry can occur for suspended cancellation | Admission belongs only at actual dispatch; cancellation finalization is shielded and lifecycle entry never reserves |
| Existing stores fail open on database errors | Name every legacy fallback for removal, including bootstrap returning `None`, and add database-down acceptance |
| Stock OTLP exporter hides partial-success bodies; duplicate span insertion cannot repair cost | One processor stages export; response-parsing standard OTLP transport plus verified receipt upsert handles delivery |
| Phoenix correction was a gate without a designed mechanism | Define a service migration, immutable versioned observations, current cost projection, authenticated POST/GET, and GraphQL/UI behavior |
| STT only reads text today | Parse documented token/duration usage, validate preflight bounds, keep estimates explicit, and support audio cost without fake language tokens |
| OpenRouter amounts live in `provider_details` | Read explicit fields, preserve zero by presence, and cover the installed adapter's dropped-zero edge |
| ChatGPT provider name allegedly prevents pricing | Partly rejected: an offline URL-first probe prices it correctly. Keep explicit pricing identity without overriding working native resolution |
| Native catalog refresh is process-local | Persist the entire validated catalog before activation, restore from the database, and disable independent refresh authority |
| Parent allocation propagation was implicit | Trusted dependencies/run metadata plus task-local context, persisted job IDs, and summarizer/parser concurrency fixtures |
| Local model heuristics can misclassify billing | Explicit provider/billing profile; preserve URL configuration compatibility without granting free inference by hostname |
| Checklist could pass without exercising these seams | Add targeted checks for every mechanism above, including missing cost rows, version conflicts, batch auto-submit, and a single export path |

The review suggested persisting the provider job ID in the submit transaction. A remote provider
and Postgres do not share a transaction. The final plan instead persists holds first, records the
acknowledged job ID afterward, and retains an explicit indeterminate state across acknowledgement
loss. A missing acknowledgement never authorizes blind resubmission.

The [offline wrapper probe](verification.md#physical-attempt-wrapper-probe) confirms the proposed
nonstreaming fallback span placement and parentage without network calls. Streaming, cancellation,
database concurrency, service correction, UI rollups, and restore remain unchecked acceptance work.

The strongest objection remains operational cost: the exact Phoenix dashboard contract requires a
maintained service patch until upstream qualifies, plus application accounting and durable cost
delivery. Keeping the deployment topology simple does not make those semantics free. The plan
states that maintenance obligation instead of shipping a misleading dashboard or silent zero.
