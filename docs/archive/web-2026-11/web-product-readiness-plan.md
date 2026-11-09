# Phase 1: Web UI design and implementation plan

This replaces the previous roadmap. The target is unchanged: a self-hosted, single-user, agent-first finance tool. Phase 1 produces a coherent, good-looking, working web UI — not a release program. Keep the established quiet dark direction and existing tokens and components where useful. Layout and information hierarchy may change freely to fit the task, not the shape of backend responses.

## Stage 1 — Design the actual interface

Deliver:

1. **Sitemap**: final page count and navigation hierarchy. The four areas below are a starting point, not final; page counts are decided here.
2. **Page-by-page specifications**, as detailed below.
3. **Lightweight static wireframes** of the main layout patterns, desktop and phone — not interactive prototypes, not every journey. Reuse patterns rather than drawing every state. Show them before implementation with supported inline visual tools; none here.
4. **One reviewed direction**, agreed with the user.

Avoid turning every data type into a top-level page. The table below lists what must stay reachable; the final sitemap decides what merges or remains.

| Area | Starting inventory |
| --- | --- |
| Home / agent | Conversation and composer primary; cash summary, projection preview, unread as secondary. |
| Money | Accounts, projection, schedules, goals and people as subordinate views, not five top-level pages. |
| Activity | Ledger and imports; selected movement detail and import review as appropriate. |
| Settings | Preferences, security and data controls; group advanced operational settings so they do not dominate. |
| Login / setup | Concise supporting screens. |

Existing useful functionality is retained and reachable; simplifying must not silently drop it.

For **each** final page, specify: purpose; primary action; content priority; components and sections with why each earns space (driven by useful content, never a card quota); layout, width, spacing and density; always-visible versus disclosed; navigation to related information and the agent; desktop and mobile behavior; and essential empty, loading and error feedback. Compose pages before mapping APIs — never organize the UI by endpoint. One main path dominates. Avoid nested cards and duplicated figures. The assistant rail must not squeeze working content.

Stage 1 ends with an agreed sitemap, page contents and components, and desktop/mobile wireframes.

Checklist (all unchecked):

- [ ] Sitemap with final page count and navigation hierarchy
- [ ] Page specs for every final page
- [ ] Representative desktop and phone wireframes
- [ ] Simplification and reachability reviewed; nothing dropped
- [ ] One direction reviewed and agreed

Use `impeccable` for design judgement and craft. `shape` is the single planning entry if a named command helps — no per-journey commands, audit program, or separate prototype/extract/document/optimize phase.

## Stage 2 — Adapt APIs and implement

Build approved layouts with existing components. Add, change or remove APIs only for chosen screens' data and actions. Preserve financial calculations, security and non-web clients; do not rewrite the backend wholesale. Work page by page, related frontend and backend together. Visual finishing and practical behavior belong to implementation, not a later polish stage.

Address existing issues only where they affect the chosen screens, verifying each hypothesis while fixing it: responsive Settings and import overflow, section failure isolation, visible import and chat errors, the unread queue, cash/balance/EMI labels and assumptions (finance truth), and ledger stuck refresh.

Verify with targeted desktop and phone browser checks against the real API for changed key flows, plus the existing relevant lint, type, build and API checks. No new test platform or fixture harness is a prerequisite, nor a device lab, external testers, performance budgets, benchmarks or extensive certification. Keyboard access, readability and usable touch targets are normal build quality.

Checklist (all unchecked):

- [ ] Layouts match agreed wireframes
- [ ] Required APIs connected
- [ ] Important actions work with the real API
- [ ] Empty, loading and error states plus key controls usable on desktop and phone
- [ ] Relevant existing checks pass

## Stop

Phase 1 is done when the agreed pages look coherent and match the wireframes on desktop and phone; the intended actions work against the actual backend with no known blocker in the changed flows; and the important states are clear while relevant checks pass. Then stop. Optimization, new features and abstraction beyond immediate need are deferred.

## Ownership and scope

The parent owns design decisions, DeepSeek V4.1 Flash executes, observability belongs to other agents and is untouched. The user requested this plan revision only, so no UI or API work happens yet. Stage 2 builds on [web-product-readiness-evidence.md](web-product-readiness-evidence.md), whose inspection used synthetic browser fixtures; real-API checks belong to implementation. That report stays unchanged and is not repeated here.
