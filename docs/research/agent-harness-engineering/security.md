# Security, permissions and containment

The best-developed area of the survey, largely because Anthropic has published detailed
post-mortems of their own gaps.

## The organising principle

> **Design for containment at the environment layer first, then steer behaviour at the model
> layer.**

Deterministic boundaries handle the cases where probabilistic defences fail. A classifier that
catches 83% of bad behaviour is useful *behind* a sandbox and dangerous *instead of* one.

A related 2026 framing: **the harness is not a trusted security boundary.** This matters because
the harness is increasingly a third-party product the platform team didn't write, running in a
sandbox they may not own. Security separates into intent authorisation (harness-owned), execution
isolation (contested ownership), and side-effect verification (must be platform-owned).

## Three defence layers

| Layer | Mechanisms |
|---|---|
| **Environment** | Process sandboxes, VMs, filesystem boundaries, egress controls |
| **Model** | System prompts, classifiers, probes, training modifications |
| **External content** | Tool permissions, MCP server auditing, web access controls |

## Permission prompts fail to fatigue

The most useful negative result here.

Claude Code originally used permission dialogs for write, bash, and network operations. Measured
outcome: **users approved roughly 93% of prompts, with fatigue increasing over time.**

A permission prompt approved 93% of the time is not a control. It is a click-through agreement.

**The fix was architectural, not behavioural:** OS-level sandboxing (Seatbelt on macOS, bubblewrap
on Linux) allowing reads and writes inside the workspace, denying network by default. Result:
**84% reduction in permission prompts.** Anthropic open-sourced the runtime so the boundary is
auditable.

The generalisable lesson: **when a control is annoying enough to be reflexively dismissed, replace
it with a boundary rather than a better prompt.**

## Three isolation patterns

Matched to user capacity, not applied uniformly.

**Ephemeral container (claude.ai)** — gVisor on isolated infrastructure, per-session filesystem,
no local machine access. Protects infrastructure and tenant isolation. Costs spin-up latency and
limits capability.

**Human-in-the-loop sandbox (Claude Code)** — OS sandbox plus default-deny network, with a model
classifier catching ~83% of overeager behaviour before execution. For technically capable users
who can exercise judgment.

**Sealed VM (Claude Cowork)** — full VM with a mounted workspace, for non-technical users needing
always-on boundaries. Notable design evolution: the agent loop was moved *outside* the VM so
functionality survives VM failure while code execution stays contained. File access is
read-only / read-write / read-write-no-delete. **Credentials stay in the host keychain**; the VM
gets a per-session scoped token.

## Sandbox mechanics

From *Beyond permission prompts* — the concrete implementation behind the architectural argument.

**Two isolation axes, and both are required:**

> Without network isolation, a compromised agent could exfiltrate sensitive files like SSH keys;
> without filesystem isolation, a compromised agent could easily escape.

**Filesystem isolation** restricts access to specific directories, so prompt injection can't reach
sensitive system files. **Network isolation** limits connections to approved servers, blocking
exfiltration and malware download.

**Implementation:** built on OS-level primitives — **bubblewrap** on Linux, **seatbelt** on macOS.
Crucially it covers **spawned subprocesses**, not just the agent's direct calls. The working
directory is read/write; everything outside is blocked. Network traffic routes through a **proxy
enforcing domain restrictions**, customisable to arbitrary outbound rules. Enabled with
`claude --sandbox`.

**The cloud variant** runs an isolated sandbox per session where **credentials never enter the
sandbox at all** — a custom proxy handles git interactions with scoped credentials on the outside.
That is the strongest version of the pattern: the secret is not protected inside the boundary, it
is simply never placed within it.

Note this post contains **no measured data**; the 93%-approval and 84%-reduction figures above come
from *How we contain Claude*.

## Gaps found in production

Valuable precisely because they were found *after* deploying the defences above.

- **Code execution before trust dialogs.** Project configuration hooks ran before the user was
  asked to trust the project. The gate was downstream of the risk.
- **Direct prompt injection via user-supplied instructions** bypassed model-layer defences
  entirely.
- **Egress through permitted domains.** The sandbox held, and data still left — through an
  allowed destination.
- **Approved domains as exfiltration vectors.** An attacker-controlled API key turned a
  legitimate allowed domain into a channel. Mitigation was a man-in-the-middle proxy inside the
  VM validating that calls carry the VM's own provisioned token.
- **EDR visibility loss** under hypervisor opacity, addressed with pull-based OTLP exports.

### The allowlist principle

> **Allowlist as capability grant** — every function reachable through an approved domain becomes
> attack surface.

Allowing `api.example.com` doesn't allow "the thing you wanted." It allows every operation that
domain exposes, to anyone who can steer the agent.

## Prompt injection — measured

| Measure | Result |
|---|---|
| Opus 4.7 injection resistance, single attempt | ~0.1% attack success |
| Opus 4.7, **100 adaptive attempts** | **5-6% success** |
| Claude Code auto-mode false positives on benign commands | ~0.4% |
| Claude Code auto-mode **miss rate on risky actions** | **~17%** |

Two things to take from this. First, single-shot resistance numbers flatter the defence — an
adversary who retries gets a very different result. Second, a 17% miss rate on risky actions is
the empirical argument for why the model layer cannot be the only layer.

## Memory as persistent attack surface

> **Trust persists as attack surface.**

Persistent agent memory — `CLAUDE.md`, product memory, state directories — enables
post-exploitation **persistence injection**. An attacker who lands once can write instructions
that reload every session afterwards.

This is the direct security cost of the memory patterns in
[`context-engineering.md`](context-engineering.md). Durable memory is a feature and a foothold.

## The MCP sandbox gap

A specific, easily-missed property documented plainly by OpenAI: in Codex, **only the harness's own
shell tool is sandboxed.** Tools supplied by MCP servers and other APIs are *not* — they "are
responsible for enforcing their own guardrails."

The implication generalises to any harness with a plugin/MCP surface: the sandbox boundary is drawn
around the tools the harness *ships*, not around the tools it *runs*. Every third-party tool is an
independent trust decision, and the harness's own security posture says nothing about it.

### The prose-vs-enforcement gap, measured

*When "Do Not" Is Not Deny* (arXiv 2608.23550) studied **481 public agent configuration files**,
extracting security rules with an LLM and having **two independent security practitioners validate
blind**.

**Only 4-16% of stated security rules had a matching built-in control** — the strictest measure
being **4.4% (95% CI 2.6-6.7%)**. A manual audit found the extraction captured 66.3% of eligible
rules, so the estimate is a floor with real slack in it.

Their diagnosis is the memorable part: a plain-text config is a **write-only channel.** Developers
get no feedback about whether a rule they wrote will be *enforced* or merely *interpreted*. The
format hides the distinction that matters most.

*An earlier draft of this survey cited this as a flat "~4%," taken from an index entry. The range
and its caveats are the accurate version.*

## Authorization models

From LangChain's *Two Different Types of Agent Authorization* — a distinction that determines the
whole design and is often decided by accident:

| Model | Shape | Consequence |
|---|---|---|
| **On-behalf-of** | Agent acts with the user's own credentials and scopes | Blast radius equals that user's permissions; auditable per-user |
| **Fixed-credential** | Agent holds its own service identity | Blast radius is the union of everything the agent can reach, for every user |

Related work: **Agent Vault** (Infisical) brokers credentials so the agent never possesses secrets;
**Open Agent Passport** (arXiv 2603.20953) does synchronous pre-action authorization at a median
53 ms; OWASP catalogues the failure as **LLM06:2025 Excessive Agency**; IETF
`draft-klrc-aiagent-auth` is a standards-track attempt built on WIMSE and OAuth 2.0.

## Build on standard primitives

> Battle-tested hypervisors, syscall filters, and container runtimes have survived more
> adversarial attention than anything you'll build.

Explicit guidance to distrust custom security components. Standard primitives beat proprietary
layers, and the reasoning is adversarial exposure, not code quality.

## Threat model checklist

Recurring across sources — injection, denial of service, tool abuse, and approval spoofing, with
multi-level guardrails: input sanitisation, permission checks, output validation.

Categories needing deterministic policy checks, approval gates, sandbox restrictions and audit
trails: **destructive tools, secret access, network egress, external writes.**

The stated architectural preference is **prevention and monitoring together** — neither blocking
alone nor watching alone.

## Research directions

From the awesome-list taxonomy and adjacent papers: type-directed privilege separation for
injection prevention, capability-controlled runtimes for self-evolving agents, agent operating
systems integrating agentic control planes, and eBPF-based runtime enforcement for opaque agents.

The through-line is treating the agent as an untrusted process and applying decades of OS
security thinking to it, rather than inventing agent-specific mechanisms.
