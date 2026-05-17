# AI Finance Assistant — Vault README

> **For future AI agents landing here cold:** read this first. It tells you what this vault is, where to look for what, and what state the project is in.

## What this is
An **Obsidian vault for planning** a private, self-hosted personal finance agent. The single user/owner is **Joy** (developer, based in Delhi). The agent is built in Python 3.12+ (FastAPI + PydanticAI), runs on a local machine (Asus TUF A15), uses a local LLM via Ollama, and is reached through **Signal** (E2E-encrypted bot) plus a local-only web UI bound to `127.0.0.1`.

**Current phase: planning only. No code has been written.** Do not generate implementation code unless explicitly asked.

## Where to start reading

| If you need… | Read this first |
|---|---|
| The canonical product vision | [🎯 Product Picture/v1.0.0.md](🎯%20Product%20Picture/v1.0.0.md) |
| The full backstory and reasoning behind decisions | [🗣️ Initial Conversation.md](🗣️%20Initial%20Conversation.md) |
| Hardware, deployment, backup strategy | [🏭 Infrastructure.md](🏭%20Infrastructure.md) |
| What the agent can do (feature list) | [💪🏻 Capabilities/v1.0.0.md](💪🏻%20Capabilities/v1.0.0.md) |
| Tool & MCP server plans | [🧰 Tools and MCPs/v1.0.0.md](🧰%20Tools%20and%20MCPs/v1.0.0.md) |
| Why specific tech was chosen | [🛠️ Tech Decisions/](🛠️%20Tech%20Decisions/) |
| Local model selection notes | [🤖 Models/Ollama Try-Outs.md](🤖%20Models/Ollama%20Try-Outs.md) |
| Threat model + security decisions | [🔒 Security/v1.0.0.md](🔒%20Security/v1.0.0.md) |

**The Product Picture is the source of truth.** If anything in another file conflicts with it, the Product Picture wins until the conflict is resolved.

## Vault structure & conventions

```
AI Finance Assistant/
├── README.md                    ← you are here
├── 🎯 Product Picture/          ← canonical product spec (what we're building)
│   └── v1.0.0.md
├── 💪🏻 Capabilities/             ← feature list per version
│   └── v1.0.0.md
├── 🧰 Tools and MCPs/           ← tool servers / MCP integrations per version
│   └── v1.0.0.md
├── 🛠️ Tech Decisions/           ← one file per significant decision + rationale
├── 🤖 Models/                   ← model selection notes (Ollama candidates etc.)
├── 🔒 Security/                 ← threat model + security decisions per version
│   └── v1.0.0.md
├── 🏭 Infrastructure.md         ← hardware, hosting, backup, networking
└── 🗣️ Initial Conversation.md   ← original brainstorm transcript (the "why")
```

**Conventions:**
- Folder names use emoji + name. Keep the pattern when adding new folders.
- Versioned specs use `vMAJOR.MINOR.PATCH.md` (e.g., `v1.0.0.md`). When the product picture evolves, create `v1.1.0.md` rather than overwriting.
- One concern per file under `🛠️ Tech Decisions/` — title states the decision.

## Project state snapshot

- **Phase:** product picture locked at v1.0.0; implementation planning is the next step.
- **Scope discipline:** Joy initially considered a multi-skill personal-assistant platform, then **explicitly scoped down to a Finance Agent only**. Do not re-expand scope without Joy's direction. (Other skills like journaling, calendar, research, health were discussed but are out of scope until finance is built and stable.)
- **Open questions** waiting for the next planning session are listed at the bottom of [🎯 Product Picture/v1.0.0.md](🎯%20Product%20Picture/v1.0.0.md).

## How to be useful here

1. **Default to planning, not coding.** Joy is deliberately in a "build the picture before building the thing" phase.
2. **Ask before fanning out.** Joy has already pulled back conversations that drifted into platform/multi-skill territory. If a question opens up scope, surface it explicitly rather than just expanding.
3. **Update specs in place, version on real change.** Small edits to v1.0.0.md are fine while drafting. Once the picture is referenced by other documents, bump the version for any breaking change.
4. **Use the current date** when reasoning about anything time-bound — research, tech landscape, model availability, etc.
5. **Respect the no-list in the Product Picture.** Boundaries (no investment advice, no moralizing, no autonomous actions) are product decisions, not implementation suggestions to revisit.

## Known constraints worth re-reading before suggesting anything

- **Hardware:** Asus TUF A15, AMD Ryzen 5 4600H, 24GB RAM, **GTX 1650 Mobile with only 4GB VRAM**. Local LLM choice is constrained — 8B models will need aggressive quantization or CPU offload.
- **Privacy:** All inference and financial data stay on the local machine. Only encrypted backups leave it.
- **Stack (decided May 17, 2026):** Python 3.12+ (FastAPI + PydanticAI + uv), PostgreSQL (SQLAlchemy 2.0 + pgvector + pgcrypto), Ollama for local inference (Qwen3 8B, Q4 quantized), mem0 for personalization memory, pdfplumber + pymupdf4llm for bank statements, pytesseract for receipts, **signalbot + signal-cli-rest-api for Signal** (replaces Telegram for E2E privacy), APScheduler for briefings, Docker Compose for deploy. **No Cloudflare Tunnel in v1** (Signal is outbound-only; web is `127.0.0.1`). See [[🛠️ Tech Decisions/Language Python over Go]], [[🛠️ Tech Decisions/Agent Framework PydanticAI]], and [[🔒 Security/v1.0.0]] for rationale. No code written yet.
