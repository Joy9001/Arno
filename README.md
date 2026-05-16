# AI Finance Assistant — Vault README

> **For future AI agents landing here cold:** read this first. It tells you what this vault is, where to look for what, and what state the project is in.

## What this is
An **Obsidian vault for planning** a private, self-hosted personal finance agent. The single user/owner is **Joy** (developer, based in Delhi). The agent will eventually be implemented in Go, run on a local machine (Asus TUF A15), use a local LLM via Ollama, and be reached through Telegram/WhatsApp plus a local web UI.

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
- **Stack direction (tentative):** Go backend, PostgreSQL ledger, Ollama for inference, LangChainGo for agent loop, Cloudflare Tunnel for webhook exposure. None of this is written yet; revisit in implementation planning.
