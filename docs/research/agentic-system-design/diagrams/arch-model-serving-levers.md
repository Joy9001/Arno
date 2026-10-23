# Self-hosted serving: where the levers act

![Self-hosted serving: where the levers act](./arch-model-serving-levers.png)

**What it shows.** Inside the self-hosted pool a request flows queue, prefill, KV cache, decode; each lever (continuous batching, PagedAttention and GQA, quantization, speculative decoding) is attached to the stage it acts on.

**Diagram type.** Architecture-style flow with one zone (a pipeline of stages with attached lever nodes).

**What the budget forced out or split.** Split from the old combined figure: the managed vs self-hosted route is now `arch-model-serving-routes`. The output node was dropped (Decode's job is the streamed token), arrow labels between stages became sublabels, and the vLLM/TGI caveat is one callout.

**Assumptions.** Quantization is attached to Decode and speculative decoding to Decode, as in the original; GQA is grouped with PagedAttention as in the original.

**Source.** `arch-model-serving-levers.html` (diagram-design skill, default skin, minimal light, doc-inline width 960; height extended where content needs it). Content comes from the topic file genai-system-architecture.md; no claims or numbers added.
