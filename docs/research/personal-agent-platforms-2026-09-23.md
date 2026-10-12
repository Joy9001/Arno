# Personal-agent platforms as prior art (2026-09-23)

Follow-up to the [May 2026 prior-art survey](../archive/prior-art/research-2026-05-24.md), which
covered finance apps, MCP bridges and Signal bots but not the general-purpose "personal agent"
platforms that took off in 2026 (OpenClaw, Hermes, the Grok Bot alternatives).

Two questions:

1. Does any of them qualify as a personal finance agent?
2. Could any of them be customized into one, instead of building Fin-Agent?

Unlike the rest of `research/`, this doc is applied to Fin-Agent on purpose. Star counts and dates
come from the GitHub API on 2026-09-23.

---

## Headline findings

1. **None of the six named projects is a personal finance agent.** Four have no finance features
   at all. OpenMuse's "finance" is an 80-line, stateless CSV summarizer
   ([`finance.ts`](https://github.com/CopilotKit/OpenMuse/blob/main/apps/server/src/engine/finance.ts)):
   no ledger, no persistence, no projection. OpenBot's expense bot checks *corporate* expense
   claims against a policy.
2. **The finance capability lives in community skills, not the platforms.** ClawHub (OpenClaw)
   has dozens of personal-finance skills, and Hermes has a few. The closest to Fin-Agent is
   `aka-anoop/personal-finance`: SQLite, categories, budgets, reminders for EMIs and annual
   expenses. **All of them log and summarize. None projects forward or answers "can I afford
   this?"**
3. **The "can I afford this?" engine now has heavy prior art from somewhere else.** The
   HackerRank Orchestrate hackathon (Sept 2026, about 3,062 participants) was *"Buy or Wait?"*:
   a 90-day cash-flow forecast, a per-user minimum-balance floor, and a verdict on whether and how
   to pay. That is Fin-Agent's M16–M17 almost exactly. It produced hundreds of public
   implementations and a **dataset with labelled expected outputs**. This is the most useful find
   in this pass. See [§4](#4-the-affordability-cluster--hackerrank-orchestrate-sept-2026).
4. **Hermes Agent is the only platform worth customizing, and not yet.** It's Python and MIT, and
   ships Signal, Telegram and WhatsApp, cron, voice, MCP and Python tool plugins. But it
   **requires 64K context minimum**, which rules out the plan's local `qwen2.5:3b` on 4 GB of VRAM,
   and it would replace the PydanticAI harness rather than wrap it. It's the right host for the
   *parked* surfaces (Signal bot, briefings, scheduler, voice notes), not for the core.
5. **Supply-chain risk is real.** The main curated ClawHub list excludes **373 skills as
   malicious** (per published security audits) and 886 as crypto, finance or trading. Never
   install a community finance skill onto an agent that has shell access and your ledger.

---

## 1. The platforms: verdict table

| Project | Stars | Lang / licence | What it is | Local models | Finance out of the box | Customizable into Fin-Agent? |
|---|---|---|---|---|---|---|
| [OpenClaw](https://github.com/openclaw/openclaw) | 390k | TS / MIT (Foundation) | Messaging-first personal assistant, 20+ channels incl. Signal | Yes, but needs ≥64K ctx; 3B models choke on its prompts | None in core; many ClawHub skills (§2) | **Possible, poor fit.** TS core, so our Python tools would go in via MCP. Huge default tool surface. Skill-marketplace supply-chain risk |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | 248k | Python / MIT | Self-improving agent: skills engine, memory, cron, gateway (Telegram, Discord, Slack, WhatsApp, Signal, email) | Yes via custom endpoint; **64K ctx minimum** (docs FAQ) | None in core; one community expense skill | **Best fit, later.** See §5 |
| [nanobot](https://github.com/HKUDS/nanobot) (HKUDS) | 48k | Python / MIT | Lightweight OpenClaw alternative: MCP, memory, cron, WebUI | Yes (Ollama, vLLM) | None | Possible. Small enough to read. No Signal or WhatsApp listed |
| [NanoClaw](https://github.com/nanocoai/nanoclaw) | 31k | TS / MIT | Containerized OpenClaw alternative | **No.** Built on Anthropic's Agents SDK | None | No. Breaks provider-agnostic |
| [PicoClaw](https://github.com/sipeed/picoclaw) / [ZeroClaw](https://github.com/zeroclaw-labs/zeroclaw) | 30k / 33k | Go / Rust | Minimal-footprint assistants | Yes | None | As an MCP host only. No advantage over Hermes |
| [OpenMuse](https://github.com/CopilotKit/OpenMuse) | 1.2k | TS / MIT | Mobile/web personal agent with browser, terminal, durable tasks (created 2026-09-15) | **No.** OpenAI, Anthropic or Google only | Demo CSV → category summary. Stateless | No |
| [OpenBot](https://github.com/CopilotKit/OpenBot) | 5.4k | TS / MIT | "AI coworkers" with their own VM, policy gateway, audit log | Not documented | Corporate expense-policy checker (example) | No. Workplace shape |
| [OpenMausBot](https://github.com/milind-soni/OpenMausBot) | 3.3k | TS / Apache-2.0 | Chat app where each bot drives the Claude, Codex or Grok CLI | OpenAI-compatible endpoints possible | None | No. Wraps coding-agent CLIs |
| [rakazo](https://github.com/elie222/rakazo) | 2.9k | TS / Apache-2.0 | Grok Bot alternative, bots with memory and routines, sandboxed computer | BYO credentials via Pi (local not verified) | None | No |
| [openhuman](https://github.com/tinyhumansai/openhuman) | 40k | Rust / GPL-3.0 | Agent harness: memory trees, sub-agent fleets, 17 messaging integrations | — | None | No. GPL, and a harness we already have |
| [paperclip](https://github.com/paperclipai/paperclip) | 81k | TS / MIT | Org charts and governance for agent *teams*. Its "budgets" means agent spend | — | None | No |
| [Buzz](https://github.com/block/buzz) | 34k | Rust / Apache-2.0 | Signed shared event log for agents and humans | — | None | No |

**Two families.** The OpenClaw lineage (OpenClaw, Hermes, nanobot, the *Claw clones) is
*messaging-first*: an assistant you text. The Grok Bot alternatives (OpenMuse, OpenBot,
OpenMausBot, rakazo) are *computer-use* platforms: bots that drive a browser or terminal in a VM,
mostly cloud models. Only the first family is even the right shape for Fin-Agent.

---

## 2. Finance skills on these platforms

Read from registry pages and aggregators (clawskills.sh, clawbot.ai, skills.lc), **not from
source code**. Confidence is medium.

| Skill | Platform | Storage | Maths in code? | What's notable |
|---|---|---|---|---|
| [`aka-anoop/personal-finance`](https://clawhub.ai/aka-anoop/personal-finance) | OpenClaw | SQLite `finance.db` | Unknown | **Closest to Fin-Agent.** EMIs and annual-expense reminders (≈ our `obligations`), weekly digest, dynamic categories. ~2.4k downloads |
| [`safaiyeh/finance-skill`](https://clawskills.sh/skills/safaiyeh-finance-skill) | OpenClaw | JSON + `jq` | Yes (`jq`) | PDF statements → JSON via pypdf; **validates extracted totals against the statement**, the same invariant as `import.md`. ~1.9k downloads |
| [`nicholasrae-expense-tracker`](https://clawskills.sh/skills/nicholasrae-nicholasrae-expense-tracker) | OpenClaw | local ledger, shell + `jq` | Yes | Keyword → 16 categories, 80% budget alerts. **Had to fix a `jq` injection in v1.0.3.** Chat text is untrusted input to shell tools |
| [`spend-pulse`](https://clawskills.sh/skills/jbornhorst1524-spend-pulse) | OpenClaw | Plaid sync | Yes (CLI) | Paces spend against **last month's cumulative curve**, not a straight line, so rent early in the month doesn't trigger false alarms. A good idea for M16 variable spend. Flagged "Suspicious" by OpenClaw's audit |
| [`beancount-skill`](https://clawskills.sh/skills/y1feng200156-beancount-skill) | OpenClaw | Beancount + Fava | Fava queries | Mostly a prompt: "advisor" persona over a plain-text ledger |
| `pocket-lens`, `recite`, `upinvoice` | OpenClaw | — | — | Receipt, screenshot and invoice extraction |
| `actual-budget`, `ynab`, `firefly-iii`, `ezbookkeeping` | OpenClaw | the external app | the app | Bridges, same pattern as the MCP bridges in the May survey |
| `fints-banking`, `starling-bank`, `george`, `raiffeisen-elba`, `greek-banking-integration` | OpenClaw | bank | — | Bank-specific scrapers and APIs. **Nothing Indian** |
| [`yimgao/hermes-skills-collection` → personal-expense-tracker](https://skills.lc/yimgao/hermes-skills-collection/yimgao-hermes-skills-collection-finance-personal-expense-tracker-skill-md) | Hermes | `~/.hermes/data/expenses.json` | Claims exact maths via `jq` | 9 categories, 80% budget alerts, 5-minute duplicate window. 1 star |

**What they all share:** a `SKILL.md` prompt plus shell scripts. The LLM parses the text and
shells out; the scripts store and sum. That's the "LLM never does arithmetic" rule, loosely
applied. **What none of them has:** balance plus income plus obligations as one context, forward
projection, affordability, zero-vs-unknown honesty, or an eval suite.

---

## 3. Chat-app finance bots (Telegram / WhatsApp)

Mostly small 2026 hobby and portfolio repos (0 stars). Same "log + categorize + sheet" shape.
Worth knowing about:

- [mshykhov/expense-ledger-bot](https://github.com/mshykhov/expense-ledger-bot): Telegram,
  **approval-based entries**, Postgres, an outbox. The only one with a proper write path.
- [StepanovArt/expense-tracker](https://github.com/StepanovArt/expense-tracker): Telegram text or
  voice → Google Sheets, runs `qwen3:1.7b` on Ollama on a Raspberry Pi. Evidence that the
  *logging* half works on tiny models.
- [mannat-0776/RupeeMind](https://github.com/mannat-0776/RupeeMind): claims deterministic plus
  Gemini parsing of HDFC, SBI, ICICI, Axis, Kotak, PNB, UPI, Paytm, PhonePe and GPay SMS. Days
  old and unverified, but the only Indian-bank parser found. Worth a look before M8.
- [Deepanshu422/Autonomous-Finance-Tracker-AI-Agent](https://github.com/Deepanshu422/Autonomous-Finance-Tracker-AI-Agent):
  WhatsApp + LLM parsing + Postgres + background reporting agent.

---

## 4. The affordability cluster — HackerRank Orchestrate (Sept 2026)

[`interviewstreet/hackerrank-orchestrate-september26`](https://github.com/interviewstreet/hackerrank-orchestrate-september26)
(116 stars, 506 forks). A 24-hour hackathon. The problem statement is Fin-Agent's projection
engine:

> A plan is safe only if the balance never falls below `minimum_balance_to_keep` … throughout
> the 90-day forecast.

| Hackathon concept | Fin-Agent equivalent |
|---|---|
| `current_available_balance`, `minimum_balance_to_keep` | `profile.balance`, `profile.floor` (answers M16's "what's the floor?": a fixed per-user number) |
| Recurring income and expenses of mixed cadence | `profile.income_*`, `obligations` |
| `expense_categories_to_protect` / `willing_to_reduce` | Not modelled. Candidate "yes-but" levers for M17 |
| `amount_safe_to_pay`, `earliest_date_for_full_payment` | What M17's yes / yes-but / no has to compute |
| Ignore pending, failed, cancelled and duplicate transactions | `content_hash` dedupe + status handling |
| Messages and receipt images that change the picture | Parked (scanned/photo import) |

**Dataset** (synthetic, multi-currency): 275 profiles, ~25,300 financial events, **25 labelled
sample requests with expected outputs**, 250 unlabelled requests, and a grader
(`code/evaluation/main.py`). **The repo has no licence file.** Use it locally as eval material;
don't commit it into this repo.

**Reference implementations**, all "model reads, code decides":

- [iAnmolAgarwal/buy-or-wait](https://github.com/iAnmolAgarwal/buy-or-wait): ranked #6 of 3,062.
- [nalziori/buy-or-wait-financial-agent](https://github.com/nalziori/buy-or-wait-financial-agent):
  deterministic simulation, LLM for evidence only, conformal-risk safety margin (#85).
- [milkandvodka/buy-or-wait-agent](https://github.com/milkandvodka/buy-or-wait-agent): 261 tests,
  one Ollama Cloud model for evidence extraction.
- [varunaditya27/penny](https://github.com/varunaditya27/penny): FastAPI + Pydantic v2,
  `B_t = B_{t-1} + credits − debits`, a "zero-balance breach invariant", 105 tests, LLM
  optional and used only for the explanation.
- [Safeer1877/buy-or-wait](https://github.com/Safeer1877/buy-or-wait): injection-resistant message
  parsing.

Separate, not from the hackathon: [Dephyy/ai-personal-finance-agent](https://github.com/Dephyy/ai-personal-finance-agent).
Every derived row stores `method + rationale + sourceTxnIds`, so each forecast point cites the
transactions it came from. That's a concrete pattern for the plan's honesty requirement.

**What they aren't:** batch CSV pipelines scored on an output file. None is a conversational
agent over a *living* personal ledger. The engine has prior art; the agent-around-the-engine
still doesn't.

---

## 5. Could Hermes (or OpenClaw) *be* Fin-Agent?

What customizing would look like: Fin-Agent's tools, schema and projection engine become a Hermes
plugin (`~/.hermes/plugins/fin-agent/`, where `registry.register()` wires Python handlers
directly) or an MCP server that Hermes, OpenClaw and nanobot can all mount. Hermes supplies the
loop, messaging, cron, voice and memory.

**What it buys:** four parked items for the price of an adapter: the Signal bot, voice notes,
morning/weekly briefings and the scheduler.

**What it costs:**

| Cost | Why it matters here |
|---|---|
| **64K context minimum** (OpenClaw: ≥64K recommended for local) | `plan.md` §3's local target is `qwen2.5:3b` on 4 GB of VRAM. It can't hold a 64K KV cache, and 3B models drown in these system prompts. **The platform path is cloud-only** |
| Replaces the harness | The PydanticAI decision in `harness.md` (Spend Limits, output validation, tool approval, step persistence) goes away. Hermes has its own loop |
| Tool-surface bloat | Ships shell, browser, file and web toolsets by default. `harness.md`'s "the menu is a trap" applies. They can be dropped with `agent.disabled_toolsets`, but the `hermes tools enable/disable` CLI has open bugs where it silently no-ops |
| Autonomous memory and skill writes | The "self-improving" loop writes procedures and memories without asking. For a ledger agent that's an unreviewed write path. It would need to be off |
| Prompt injection | Imported statements and messages are untrusted text reaching an agent that has shell access |
| Evals | Still doable: Hermes exposes `AIAgent` for programmatic runs |

**Recommendation: don't switch. Keep the one seam that makes switching cheap later.**
`plan.md` §4 already has tools as plain Python functions over SQLite. Keep them free of
PydanticAI types so an MCP wrapper (FastMCP, a few dozen lines) or a Hermes plugin can expose
them unchanged. When the Signal bot or briefings come off [`parked.md`](../fin-agent/parked.md),
host the tools in Hermes instead of building a bridge. Until then the CLI and PydanticAI plan
stands.

**One thing to act on now:** the hackathon's 25 labelled requests are ready-made test cases for
M16–M17, and its problem statement is a tested spec for "yes-but". Worth reading before writing
`project_forward`. Whether it goes into the eval plan is a `plan.md` decision, not made here.

---

## 6. Corrections to the May 2026 survey

- It said *"ClawdHub"*. The registry is **ClawHub**. OpenClaw is now run by the OpenClaw
  Foundation (501(c)(3), MIT).
- Gap #1 ("local-only, agent-native, messaging-first personal finance assistant: nobody occupies
  this") is **no longer true for logging.** OpenClaw or Hermes plus a finance skill covers it.
  It still holds for *projection over complete personal context*.
- Gap #5 ("proactive briefings: nobody uses the scheduler machinery for finance") is **partly
  closed**: `spend-pulse` and the digest/reminder skills use OpenClaw's cron.

---

## Sources

Confidence: **H** = read primary source or code, **M** = registry or aggregator page or project
README summary, **L** = third-party blog.

- H: [OpenMuse `finance.ts`](https://github.com/CopilotKit/OpenMuse/blob/main/apps/server/src/engine/finance.ts)
- H: [HackerRank Orchestrate repo](https://github.com/interviewstreet/hackerrank-orchestrate-september26) (problem statement and dataset read directly)
- H: [VoltAgent/awesome-openclaw-skills](https://github.com/VoltAgent/awesome-openclaw-skills) (filter counts and category lists)
- H: [Hermes FAQ](https://hermes-agent.nousresearch.com/docs/reference/faq/) (64K minimum) · [Hermes plugins guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins) · [toolset CLI bug #115866](https://github.com/NousResearch/hermes-agent/issues/115866)
- M: READMEs for [OpenClaw](https://github.com/openclaw/openclaw), [Hermes](https://github.com/NousResearch/hermes-agent), [OpenMuse](https://github.com/CopilotKit/OpenMuse), [OpenBot](https://github.com/CopilotKit/OpenBot), [OpenMausBot](https://github.com/milind-soni/OpenMausBot), [rakazo](https://github.com/elie222/rakazo), [nanobot](https://github.com/HKUDS/nanobot), [penny](https://github.com/varunaditya27/penny), [Dephyy](https://github.com/Dephyy/ai-personal-finance-agent)
- M: [OpenClaw plugin SDK: tool plugins](https://docs.openclaw.ai/plugins/tool-plugins) · [OpenClaw + Ollama](https://docs.ollama.com/integrations/openclaw) · [OpenClaw Ollama context issue #68344](https://github.com/openclaw/openclaw/issues/68344)
- M: ClawHub skill pages: [personal-finance](https://clawhub.ai/aka-anoop/personal-finance), [finance-skill](https://clawskills.sh/skills/safaiyeh-finance-skill), [nicholasrae-expense-tracker](https://clawskills.sh/skills/nicholasrae-nicholasrae-expense-tracker), [spend-pulse](https://clawskills.sh/skills/jbornhorst1524-spend-pulse), [beancount-skill](https://clawskills.sh/skills/y1feng200156-beancount-skill), [Hermes expense tracker](https://skills.lc/yimgao/hermes-skills-collection/yimgao-hermes-skills-collection-finance-personal-expense-tracker-skill-md)
- M: [opensourcealternatives.to: Grok Bot](https://www.opensourcealternatives.to/alternative-to/grok-bot)
- L: [SFAI Labs: OpenClaw for personal finance](https://sfailabs.com/guides/openclaw-personal-finance) (85–90% → 95% categorization claim, unverified) · [Composio: OpenClaw alternatives](https://composio.dev/content/openclaw-alternatives) · [OpenClaw local model fixes](https://www.betterclaw.io/blog/openclaw-local-model-not-working) (8–12K token prompt claim)
