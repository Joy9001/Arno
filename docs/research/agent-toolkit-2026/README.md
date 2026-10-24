# Research: Open Source Toolkit for Building AI Agents 2026

An analysis of Let's Code's catalog,
[_Open Source Toolkit for Building AI Agents 2026_](https://docs.google.com/spreadsheets/d/1cGylccKIuHDxnPpg68tPZxp8_XZP8VoPqkM7HgofPdk/edit),
read on October 25, 2026 and mapped onto Fin-Agent's eight system-design steps: requirements,
trade-offs, API design, data model, high-level design, component deep dives, production
readiness, and capacity planning.

Unlike the two surveys beside it
([`../agentic-system-design/`](../agentic-system-design/) and
[`../agent-harness-engineering/`](../agent-harness-engineering/)), this file is _filtered_ by
Fin-Agent's requirements. It shortlists candidates for each design step. It doesn't decide
anything—each step makes its own choice when it comes up.

## What the source is

- **Shape:** 404 entries across 20 sheets, plus a "Start Here" sheet. Each entry lists what the
  tool does, its best use case, language, licence, difficulty, adoption, and a link.
- **Date:** compiled in August 2026, according to the source.
- **Evidence quality:** difficulty and adoption are editorial judgments, not benchmarks—the
  author says so. Treat the catalog as a shortlist of what exists. The two surveys remain the
  evidence for _what works_.
- **Bias:** the catalog is India-aware—it has Indic models and Indian hardware prices in rupees—which
  suits Fin-Agent.

Its "five things worth knowing" match the surveys' findings: workflows with a small agentic core,
accuracy that compounds downward per step, context engineering over prompt wording, tracing
before features, and adoption over features.

## The data

The compilation is Let's Code's work and states no licence, so the raw data isn't in this
repository—only this analysis is. To reproduce it, export each sheet to `data/` (git-ignored)
through the sheet's CSV export URL, `/export?format=csv&gid=<sheet id>`; each sheet's ID is in
the link on its tab. Each catalog sheet has three banner rows before its header row. The copy
this analysis used was exported on October 25, 2026.

---

## Licence filter (step 1 constraint)

Fin-Agent ships only permissively licensed code: "MIT or Apache-2.0. No GPL or AGPL in the
shipped image" (`plan.md` §1). The following entries fail that rule. Most matter only if they
would ship inside the image—a tool used on the developer's machine, such as a tracing UI, is a
separate question for step 1.

| Sheet | Fails the permissive-only constraint |
|---|---|
| 01 Agent frameworks | Dify (modified Apache), n8n (fair-code) |
| 02 Coding and CUA agents | Open Interpreter, Skyvern (AGPL-3.0) |
| 04 Open-weight models | Llama, Gemma, Nemotron, Krutrim (custom terms); Kimi K2 (modified MIT); Command (non-commercial weights); Sarvam (custom and Apache, per model) |
| 05 Vector and retrieval | Elasticsearch (Elastic and SSPL), Typesense (GPL-3.0), RediSearch (RSAL), Neo4j (GPL-3.0) |
| 06 RAG and data pipeline | Marker, Surya (GPL-3.0); Firecrawl (AGPL-3.0); Airbyte (Elastic v2) |
| 07 Memory and context | Redis (RSAL and AGPL) |
| 08 Tools, MCP and sandbox | Composio (Elastic v2), Daytona, SearxNG (AGPL-3.0) |
| 09 Observability and evals | Arize Phoenix (Elastic v2), Langtrace (AGPL-3.0) |
| 10 Guardrails and security | Llama Guard, ShieldGemma (custom terms); Vault (BUSL-1.1) |
| 12 Agent UI | Open WebUI (custom), Jan and Text generation web UI (AGPL-3.0), Activepieces (part Elastic) |
| 13 Voice and multimodal | ChatTTS (AGPL-3.0), ComfyUI (GPL-3.0) |
| 14 Deploy and infra | Grafana, MinIO (AGPL-3.0) |

**Weak copyleft depends on how the rule is read.** Agno, Coqui TTS, and OpenTofu (MPL-2.0) and
Semgrep (LGPL-2.1) pass "no GPL or AGPL" but fail "MIT or Apache-2.0 only." Step 1 settles which
reading holds.

Two exclusions bite directly: Marker and Surya are the catalog's entries aimed at financial
reports and scanned Indian-language documents, and both are GPL-3.0.

`pydantic-monty` 1.3.0, the Code Mode sandbox that ships in the image, is MIT (checked against
its PyPI package metadata on October 25, 2026).

---

## Candidates by design step

### Step 1: Requirements

- **India's Digital Personal Data Protection (DPDP) Act** is listed as the law for any agent
  handling Indian user data. A single person processing their own data for personal use is
  likely outside its scope, but that needs checking against the Act before it's written down as
  a constraint.
- **Indic languages:** the catalog lists Sarvam, AI4Bharat IndicTrans2, and Krutrim. Today's
  requirement is English and Hinglish (Hindi written in Latin script). Whether Devanagari Hindi or other
  Indian languages are in scope is a step 1 question.

### Step 2: Trade-offs

- **Cost versus performance:** sheet 19 puts the break-even for self-hosting at roughly 20–50
  million tokens a month, with API use cheaper below it. A VPS that runs the agent, database, and
  UI without a GPU costs about ₹400–1,200 a month. Step 8 checks Fin-Agent's token volume
  against that range.
- **Privacy versus capability:** Presidio (MIT) masks personal data before it reaches a model.
  The plan has no masking layer and lets the self-hoster choose a provider for each role. Masking
  would conflict with answering questions about named people and merchants.
- **Framework:** the catalog's one-line guidance is "LangGraph for control, CrewAI for speed,
  Pydantic AI for type safety." PydanticAI is already chosen ([`harness.md`](../../fin-agent/harness.md)).

### Step 3: API design

- **MCP as a second surface:** already parked with a trigger under chat-app integrations in
  [`parked.md`](../../fin-agent/parked.md). FastMCP (Apache-2.0) and the official MCP Python SDK
  (MIT) are the candidates when it returns. Rule 3 in `plan.md` §4 keeps tools as plain Python,
  so the adapter is thin, and the catalog lists a personal MCP server as a standout portfolio
  project. Any external MCP host that also browses the web completes the lethal trifecta—private
  data, untrusted content, and a way to send data out—so only read tools would go out.
- **Agent-to-UI stream:** AG-UI (MIT) is an alternative to the Vercel AI stream. PydanticAI
  supports both.

### Step 4: Data model

- **Postgres** is the catalog's "one database for state, audit trail and retrieval," with
  pgvector if semantic search returns. That matches the plan.
- **Memory layers:** Mem0, Graphiti, Letta, Cognee, Memobase, and Supermemory (all permissive).
  Memobase—structured user attributes over time—is the closest analog to Fin-Agent's typed
  preferences. The others extract hidden facts, which is parked.

### Step 5: High-level design

Sheet 15's stack recipes, compared with Fin-Agent's current plan:

| Layer | Closest recipe ("Fully private") | Fin-Agent today |
|---|---|---|
| Orchestration | LangGraph or Agno | PydanticAI + `pydantic-ai-harness` |
| Model | vLLM serving Qwen3 or gpt-oss on your own GPU | Frontier API by default; any OpenAI-compatible endpoint, local through Ollama |
| Retrieval | Qdrant self-hosted | SQL over the ledger; no vector store |
| Memory | Postgres and Redis | Postgres only |
| Observability | Langfuse self-hosted | The `agent_runs` table |
| Deployment | Docker Compose behind your firewall | Docker Compose on a Tailscale network |

The gap the recipes flag is observability—a step 7 question.

### Step 6: Deep dive into components

| Component | Candidates (permissive) | Note |
|---|---|---|
| Statement and receipt parsing | Docling (MIT, already a fallback candidate), PaddleOCR (Apache-2.0), Unstructured (Apache-2.0), MarkItDown (MIT) | PaddleOCR for scanned statements and receipts; Marker and Surya are excluded by licence |
| Structured extraction | Instructor (MIT), Outlines (Apache-2.0) | PydanticAI's own structured output covers this |
| Speech to text | faster-whisper (MIT, already planned through `speaches`), whisper.cpp (MIT) | Hinglish accuracy is the eval question. Sarvam's speech models need a per-model licence check |
| Code Mode sandbox | Monty (in-process); E2B, microsandbox, gVisor, Firecracker | Monty has no host access, so the heavier sandboxes aren't needed |
| Background jobs | Celery, Prefect, Temporal | An in-process scheduler covers one user; these fit fleets of workers |
| Local model runtime | Ollama (MIT), llama.cpp (MIT) | vLLM and SGLang optimize throughput under concurrency, which one user doesn't have |

### Step 7: Production readiness

- **Tracing:** Langfuse (MIT core), OpenLLMetry and the OpenTelemetry Collector (Apache-2.0),
  and Arize Phoenix (Elastic v2—allowed for local use, but not shippable). PydanticAI emits
  OpenTelemetry spans, so any of them can read its traces. The open question is weight: a
  self-hosted Langfuse brings its own services beside a one-container app.
- **Evals:** Inspect AI (MIT), DeepEval (Apache-2.0), and Promptfoo (MIT, with red teaming).
  tau-bench (MIT) is the model for grading on final database state, which `harness.md` already
  adopts.
- **Security:** garak (Apache-2.0) and PyRIT (MIT) for red teaming; Trivy (Apache-2.0) for image
  and dependency scans; the OWASP Top 10 for LLM Applications as the review checklist. The
  catalog's guardrails sheet opens with "Prompt injection has no complete fix. Limit what the
  agent is allowed to do instead"—the stance behind Fin-Agent's write tiers.
- **Deployment to a VPS:** Coolify and Dokploy (Apache-2.0) deploy Docker Compose stacks from git.

### Step 8: Capacity planning

Sheet 19 bounds what a self-hoster's local model can be:

| Hardware | Local models it runs |
|---|---|
| 8 GB laptop, no GPU | 1–4B, quantized |
| 16 GB Apple silicon laptop | 7–14B at usable speed |
| RTX 3060 12 GB (about ₹25,000–30,000 used) | 7–14B at 4-bit |
| RTX 4090 24 GB | 32B at 4-bit |

Scorecard candidates from sheet 4: Qwen3 (the catalog's default open-weight agent model), gpt-oss
(which the catalog says runs on a 16 GB laptop), and Granite, which the existing `evals/` run
already covers.

---

## Not relevant to Fin-Agent

- **Coding and computer-use agents (sheet 02):** Fin-Agent doesn't write code or drive a desktop.
- **Fine-tuning and RL (sheet 11):** the design target is a frontier model, and customization
  goes through tools and prompts first.
- **Serving at scale (most of sheet 03), Kubernetes, Ray, and Kafka (sheet 14):** built for
  concurrency and fleets.
- **Low-code builders, GraphRAG, web crawling, and image or video generation:** no matching
  requirement.
