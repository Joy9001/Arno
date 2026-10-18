# Ecosystem

Who is building what, and which standards are consolidating.

## Production harnesses

The reference implementations everyone studies.

| Harness | Notes |
|---|---|
| **Claude Code** (Anthropic) | Most thoroughly documented publicly. Layered architecture; skills, hooks, sub-agents, compaction, OS sandboxing |
| **Codex** (OpenAI) | Published loop internals — *Unrolling the Codex Agent Loop*, hooks, long-horizon guidance |
| **Cursor** | IDE-native |
| **Aider**, **Cline** | Open-source terminal/IDE agents |
| **GitHub Copilot** | Harness documented by the VS Code team |
| **Azure SRE Agent** (Microsoft) | Best-documented *non-coding* production harness. Source of the 5-tool and 4-handoff findings |
| **Letta Code**, **DeepAgents**, **OpenClaw** | Named as harnesses where agentic memory and skill learning are central features |

These converge on similar patterns despite different models and teams — see
[`foundations.md`](foundations.md).

## Frameworks and SDKs

- **Claude Agent SDK**, **OpenAI Agents SDK** — vendor SDKs exposing harness primitives directly
- **LangGraph** (LangChain) — graph-based stateful orchestration; publishes low-level loop
  concepts and middleware for harness customisation
- **Google ADK** (Agent Development Kit) — plus a documented integrations ecosystem
- **microsoft/TaskWeaver** — planning and decomposition oriented
- **outlines**, **instructor** — structured output and schema-constrained generation

## Protocols and standards

The consolidating layer, and the most consequential development for interoperability.

**MCP (Model Context Protocol)** — Anthropic-originated, now the de facto standard for exposing
tools and resources to agents. Notable pieces:

- `modelcontextprotocol/servers` — reference server collection
- **Streamable HTTP transport**
- **Tool Annotations as Risk Vocabulary** — declared metadata about what a tool can do to the
  world, enabling harness-level gating
- **Code Execution with MCP** (Anthropic) — the general-execution pattern applied to MCP
- A published 2026 roadmap and spec release candidates — the protocol is actively versioned

**A2A Protocol** (Google) — agent-to-agent communication.

**Agentic Resource Discovery** (Google) — how agents find available resources.

**AG-UI** — agent-to-UI interaction layer.

**Skills** — `AGENTS.md`-style memory and skill files have become a cross-vendor convention.
Microsoft has a Skills Framework; SkillNet and SkillsBench exist for benchmarking; Anthropic and
others ship skill registries with progressive disclosure.

Notable MCP servers as capability references: `playwright-mcp` (Microsoft), Chrome DevTools MCP
(Google), Composio, Context7 (Upstash, for library documentation).

## The awesome-list taxonomy

`ai-boost/awesome-harness-engineering` is the field's index. Re-fetched in full from the raw
README to close a truncation gap in the first pass.

> **Reliability caveat.** The early sections return specific, checkable items — named projects,
> arXiv IDs, plausible one-liners. The later sections (Debugging & DX, Human-in-the-Loop, parts of
> Observability, Evals, Templates, Related Awesome Lists) degrade into generic concept names
> —"Print Statements for Agents", "Cost Prediction", "Awesome GitHub" — that read as summariser
> padding rather than real curated entries. **Only the specific sections are recorded below.**
> Treat category *names* as real and the generic item lists as unverified.

### Category structure

Foundations, then **design primitives** — agent loop, planning & task decomposition, context
delivery & compaction, tool design, skills & MCP, permissions & authorization, memory & state,
task runners & orchestration, verification & CI integration, observability & tracing, debugging &
DX, human-in-the-loop — then reference implementations, security/sandbox/permissions, evals &
verification, templates, and related lists.

That structure is itself the most useful artifact: it's a consensus checklist of what counts as
harness surface area.

### Notable items from the specific sections

**Permissions & authorization** — the most developed section, and the one least covered elsewhere
in this survey:

- OWASP **LLM06:2025 Excessive Agency** — risk definition and audit checklist
- **Claude Agent SDK permissions** — five-layer evaluation order and permission modes
- *Two Different Types of Agent Authorization* (LangChain) — **on-behalf-of vs fixed-credential**
  models, a distinction that determines your whole auth design
- **Agent Vault** (Infisical) — credential broker so the agent never possesses secrets
- **Open Agent Passport** (arXiv 2603.20953) — synchronous pre-action authorization, median 53 ms
- **AgentDoG** (arXiv 2601.18491) — three-dimensional risk taxonomy
- *When "Do Not" Is Not Deny* (arXiv 2608.23550) — **4-16% of stated security rules have a
  matching enforced control** (strictest measure 4.4%, CI 2.6-6.7%, across 481 config files).
  The sharpest finding in the section; full detail in [`security.md`](security.md)
- **nah** — intent-level permission guard avoiding command-name allow/deny lists
- IETF `draft-klrc-aiagent-auth` — standards-track agent auth on WIMSE and OAuth 2.0

**Memory & state** — far denser than expected:

- **Letta (MemGPT)** — three-tier core/archival/recall, the reference architecture
- **mem0**, **Zep**, **cognee**, **Hindsight**, **Stash**, **engram**, **MemPalace**
- *Facts as First Class Objects* (arXiv 2603.17781) — Knowledge Objects at **252x lower cost** than
  in-context
- **MAGMA** (arXiv 2601.03236) — four orthogonal graphs, 18.5% accuracy improvement
- *Building Self-Correcting Memory in OpenWiki* (LangChain) — **memory that forgets**, via
  staleness flagging. Under-discussed and important
- *Continual learning for AI agents* (LangChain) — three distinct layers: weights, behavior,
  context
- **trajectory** (Letta) — normalises transcripts from 15+ harnesses into one format

**Context delivery** — a long tail of token-reduction tooling with striking claimed numbers:
Token Savior (77%), codebase-memory-mcp (120x), MinishLab/semble (98%), headroom (60-95%),
PRO-LONG (4.2-5.8x), dirac (50-80% cost), LLMLingua (20x compression). All vendor-claimed.

**Agent loop** — *The Design Space of Today's and Future AI Agent Systems* (arXiv 2604.14228)
documents Claude Code's **five-stage compaction and 27-event hook pipeline**; **statewright**
reports state-machine guardrails reducing tool space and thereby improving *local* models;
**deepclaude** ports the Claude Code loop to other backends.

**Skills & MCP** — *Code Execution with MCP* (Anthropic) claims **98.7% token reduction**;
*Shell + Skills + Compaction* (OpenAI) reports routing improving **73% to 85%**; Stripe's
*You can't whisper at an AI agent* argues hard steering in loaded context beats passive docs.

## Who is publishing

The primary material is overwhelmingly **vendor engineering blogs**: Anthropic, OpenAI, Microsoft,
Google, LangChain, GitHub, Red Hat, deepset. Academic work (arXiv) is growing quickly but tends to
lag practice by months.

Individual practitioners carrying the discourse: Addy Osmani, Viv Trivedy (LangChain, the
`Agent = Model + Harness` framing and "Harness-as-a-Service"), Dex Horthy and HumanLayer (the
"skill issue" reframe), Birgitta Böckeler (user-side perspective), Simon Willison (definitional
minimalism), Martin Fowler's site (harness engineering writeup), Fareed Khan (Claude Code
architecture reverse-engineering).

**Bias worth naming:** almost every quantitative claim in this survey comes from an organisation
describing its own product. The qualitative convergence across independent teams is more
trustworthy than any individual number.

## Where the field is heading

**Dynamic just-in-time assembly.** Harnesses moving from static configuration toward assembling
tools and context on demand — Osmani's analogy is that they start to resemble compilers.

**Model-harness co-evolution.** Standardised harness primitives become training targets; models
overfit to them; the patterns entrench. Harness design and model capability advance together
rather than one leading.

**Harness migration, not harness shrinkage.** Better models remove some scaffolding and unlock
capabilities requiring different scaffolding. The assumption that harness work is a temporary
crutch is not what practitioners report.

**Automated and self-improving harnesses.** The fastest-moving direction, and now with numbers
attached. **Self-Harness** (arXiv 2606.09498) has agents optimise their own harness through three
stages — weakness mining from execution traces, minimal targeted proposals, then regression-tested
validation — reporting **relative gains up to 132%** across Terminal-Bench-2.0, SWE-bench Verified
and AppWorld, over three model families. Alongside it: **Meta-Harness** (end-to-end harness
optimisation, arXiv 2603.28052), **HarnessBridge** (learnable bidirectional controller), and
**AutoHarness** (synthesis from schemas). The harness is becoming something optimised rather than
hand-written.

*Added 2026-10-18:* **Harness²** (Google Research, arXiv 2610.14224, Apache-2.0) is the first of
this cluster to ship as runnable code. It works at two levels. *Task-level* recursion runs
Propose–Probe–Compose: propose harness edits, probe them on the same task, and compose a refined
harness from the behavioural contrasts — **without ground-truth feedback**. *Harnessing-level*
recursion keeps a persistent "improvement harness" of adopted harnesses and evidence-backed lessons
that guides later refinement. The editable surface is eight components: system prompt, guardrails,
memory, tool guidance, subagents, skills, scripts, and plugins. It reports gains of **up to 11.5
points** on WorkBuddy-Bench, **4.5** on JobBench, and **11.8 pp** partial pass rate on LAB, for
GLM-5 and Gemini 3.5 Flash. As released it is a research rig, not a library: the only documented
interface is a CLI over those three benchmarks, each with its own adapter. The executors are
OpenCode and Codex, the models run on Vertex AI, executor network access is unrestricted, and
Codex runs with its sandbox disabled. "Not an officially supported Google product."

**Harness as a trainable dimension.** *The Interplay of Harness Design and Post-Training* (arXiv
2606.25447) treats harness design as an optimisation target rather than fixed engineering. Using an
extended ALFWorld where the harness is a controllable variable, it finds harness-aware
post-training improves in-distribution performance *and* out-of-distribution robustness — while
agents trained with minimal harness effort show a **drastic performance drop under tool
environment shifts.** Since tools and tasks always shift after deployment, this is an argument that
harness design affects robustness, not just peak score.

**Memory as the durable asset.** Letta's claim that memory will become more valuable than the
model — user-specific, compounding over time, and not replaceable by a model upgrade.

**Security as OS problem.** Agent operating systems, capability-controlled runtimes, eBPF
enforcement. The direction is applying established OS security thinking to agents rather than
inventing agent-specific mechanisms.

## Open disagreements

Places where the sources genuinely conflict, worth watching rather than resolving:

1. **How general should tools be?** The consolidation evidence says wide CLI-shaped tools win. The
   validation argument says purpose-built tools with tested semantics are safer. Both are
   well-supported; the resolution likely depends on how costly a wrong answer is in the domain.
2. **Multi-agent — useful or fragile?** Sub-agents for context isolation clearly work. Agent
   hierarchies for task decomposition clearly fail past ~4 handoffs. The boundary between these
   is not crisply defined anywhere.
3. **Does harness work survive better models?** "Harnesses migrate, they don't shrink" is the
   practitioner consensus, but it is asserted rather than demonstrated, and it is a
   self-interested claim for people whose work is harness engineering.
