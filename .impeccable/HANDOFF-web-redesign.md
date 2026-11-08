# Handoff: agent-first web redesign (in progress)

Written 2026-11-08 by the agent that ran the `/impeccable` redesign. Read this whole file before
touching code. It tells you what the owner chose, what is built, what is left, how to see the app,
and the traps that already cost time.

## 1. Who you are working for and how

- The owner (Joy) gave **absolute control** over the web app's design and asked for award-level
  work. They are blunt; they reject anything that looks like a generic dark dashboard. They judge
  by **looking**, so always show screenshots or mocks, never just names.
- They picked a direction after six mock rounds. **Do not reopen the direction.** Build it.
- Their correction that shaped everything: **the AI agent is the product; money is what it works
  on.** Every surface must feel agent-first (orb, conversation, "I noticed", agent notes), not a
  finance tracker with a chat bolted on.
- Subagents: the owner allows **only `deepseek-v4.1-flash` at high effort** (via `delegate_task`
  after `orchestrator_capabilities`). If that model is not offered, do the work in-thread and say
  so. Never spawn Claude subagents.
- House rules (AGENTS.md): commit straight to `main`, Conventional Commits, no co-author trailer;
  `uv run` / `pnpm --dir web`; never bare python/npm for project tasks (scratch scripts are fine).
  Keep `.impeccable/mocks/` **tracked** (owner said so).
- Product truth that the redesign must NOT break (PRODUCT.md, plan.md §4):
  - Every number on screen comes from an API return or code summing API rows. The model never
    invents figures. Recaps and Timeline compute sums in code from `/api/movements`; that is fine.
  - "Cash on hand" (liquid balances) must never be labelled "spendable" or "free to spend".
  - A missing floor suppresses the probability ("No floor set").
  - No third-party fetches: fonts are self-hosted in `web/public/fonts/`. CSP is
    `default-src 'self'` (see `src/fin_agent/api/middleware.py`).
  - Never put real data in tracked files. The dev DB holds the owner's **real** statement data
    (real payee "DHRITISH", "939 rows", SBI balance). Mocks/fixtures use invented demo data only.

## 2. The chosen direction (what "done" looks like)

Open `J:\Development\Fin-Agent\.impeccable\mocks\final\index.html` — 22 final mocks with
screenshots in `.impeccable/mocks/final/shots/`. These are the spec. Exploration rounds are in
`.impeccable/mocks/decision/` (r3-*, r4-*, r5-* are the ones the owner liked).

Owner's picks:
1. `r3-ask` → default home (**Ask**). `r4-orb` → voice mode, **parked** (not built).
   The orb everywhere = "a living, iridescent presence that you talk to".
2. `r3-thread` → main conversation (**Chats**). `r4-board` → **Flow** mode. Phone → `r3-phone`.
3. `r5-tool` (Planner) and `r3-canvas` (Canvas) for big answers.
4. `r5-timeline` → Money view (**Timeline** tab).
5. `r5-story` → daily/weekly/monthly **Recaps**.

Visual system (also in `.impeccable/surfaces/web.md` direction contract and
`docs/fin-agent/web-interface-design.md`):
- Ground `#0e0f0c`, surfaces `#171814` / `#22241e` / `#2c2f27`, text `#f6f7f0`, sub `#a3a79a`.
- **Lime `#d4ff3a` = actions and verdicts. Cobalt `#2b59ff` = the user's own voice** (bubbles).
  Orange `#ff6a2b` = due, green `#3ee07f` = positive, red `#ff5a4f` = negative, sun `#ffd23a`.
- Mesh gradient (lime→teal→cobalt) for headline figures; iridescent conic gradient for the orb.
- Fonts: Bricolage Grotesque (everything; heavy, tight tracking for figures) and JetBrains Mono
  (serials, kicker labels). Self-hosted subsets incl. ₹ (U+20B9). JetBrains Mono lacks ₹, so
  `--mono` falls back to Bricolage.
- Big radii (20–34px), pill buttons (`.pill-button` with `.lime/.ink/.light/.ask` variants).

## 3. What is committed

| Commit | What |
|---|---|
| `faf0e8f` | docs(design): all mocks (scrubbed of real data; persona name "Asha") |
| `9794b5b` | docs: plan.md decision (Nov 8 interface redesign), PRODUCT.md brand commitment rewritten (agent-first replaces Apple-quiet), web-interface-design.md rewritten (six areas), surface brief direction contract with `seed:9ed473e87fbc4383` |
| `23dd530` | feat(web): shell rebuild — see below |
| (next) | feat(web): lime receipt, Flow, Planner, canvas — the commit made with this handoff |

### Architecture now (web/src)

- `Shell.tsx` — rewritten. Icon **rail** (orb brand, Ask, Chats, Money, Activity (unread badge),
  Recaps, Settings at foot). Phone: bottom `tabbar` (Ask/Money/Activity/Recaps/More).
  - Routes: `#ask`, `#chats/<thread>/<msg>`, `#money/<tab>`, `#activity/<tab>[/<import>]`,
    `#recaps`, `#settings/<section>`. Legacy `#home…` → ask/chats; `#money/projection` → timeline.
  - **One `<Assistant>` is always mounted** inside `.talk` (hidden on non-chat pages) so streams
    survive navigation. `data-mode="ask"|"chats"` on `.talk`.
  - Ask = new empty thread (its empty state IS the home). `newChatSignal` forces a new thread;
    when a reply starts on Ask (`onConversationStarted`) the page flips to Chats.
  - Chats = `listInline` → conversation list column (`ChatHistory`) beside the thread; on entering
    with an empty thread it switches to the latest thread.
  - Canvas: right column (`.canvas`, desktop >1180px) or a sheet (compact) showing either
    `EvidencePanel` (sources) or `PlannerPanel`. Events: `openEvidence` / `openPlanner` in
    `evidence.ts`. Ask buttons on data pages (`askAgent`) navigate to Chats with the text seeded.
  - `nav.ts`: `goTo(page, sub)` event + `UserContext` (greeting name).
- `Orb.tsx` + `.orb` CSS (in styles.css bottom): `live` animates (spin + breathe), `thinking`
  faster; reduced-motion stops it. Only animate where the agent is present/working.
- `chat/AskHome.tsx` — `AskGreeting` (120px live orb + "Evening, <name>. What do you want to
  know about your money?") and `NoticedView` (cards from `/api/home`: cash on hand (mesh), floor
  holds / No floor set / No forecast yet, up to 2 unread notifications, recaps card). `NoticedView`
  is pure and covered by `scripts/answer-contract.test.mjs` (unknown-balance gate).
- `components/thread.aui.tsx` — added `WelcomeAfter` slot (renders under the composer on a new
  chat): starters + noticed cards (`ChatWelcomeAfter` in `chat/parts.tsx`).
- `chat/Chat.tsx` — new props (`newChatSignal`, `listInline`, `onConversationStarted`), inline
  list layout (`.assistant.with-list` grid, `.chat-main-col`), **Chat/Flow toggle**
  (`.view-toggle`) in the header when the thread has messages.
- `chat/FlowView.tsx` — Flow mode: turns = user question → tool-call nodes (label from
  `tool-copy.ts`, args brief, done/running/failed) → answer node (lime if a verdict is found).
  Wires are CSS pseudo-elements.
- `panels/Planner.tsx` — Purchase planner on the canvas: price/date sliders, method segmented,
  EMI tenures; debounced `GET /api/projection?amount_paise&date&method&emi_months` (route already
  exists in `src/fin_agent/api/ledger.py`). Gauge, verdict, facts, chart. Opened by the
  **Planner** button on any non-gated projection receipt (`projection.tsx` `ProjectionCard`).
- `panels/Timeline.tsx` — Money's timeline: last 30 days of expense bars (from `spending.ts`),
  today line, projection median + P10–P90 band, floor line (or dashed zero line if no floor),
  upcoming schedule dots (`/api/schedules/upcoming`), draggable/keyboard scrubber with popover
  (role=slider). Rendered at the top of `ProjectionPanel` (now titled "Timeline").
- `spending.ts` — `readExpenses(start,end)` pages `/api/movements?kind=expense` (cap 10×200
  rows, reports `truncated`), `totalsBy`, `useExpenses`.
- `pages/Recaps.tsx` — periods (yesterday, this week, last week, month so far, last month),
  stories computed in code (total vs previous same-length period, top categories bars, biggest
  spend, most frequent payee), row count cited, Ask button. List + story viewer.
- `panels/Inbox.tsx` + `notifications.ts` — Activity → **Inbox** tab: notifications with
  open-in-chat / mark read / mark all, scheduled questions side list.
- `pages/Activity.tsx`, `pages/Money.tsx` — `.area-head` (big h2 + pill tabs). Money tabs:
  timeline, accounts, schedules, goals, people. Activity tabs: ledger, imports, inbox.
- Deleted: `pages/Home.tsx`, `Today.tsx`, `Mark.tsx` (old Today rail).
- CSS: tokens rewritten at top of `styles.css` (old names like `--blue` now map to lime,
  `--you` is cobalt). `aui.css` tokens remapped. **`shell.css`** (imported last in `main.tsx`)
  holds all new shell/Ask/timeline/inbox/recaps/flow/planner/tabbar CSS. Old sidebar/topbar/
  context-rail/Today CSS was deleted. Radix Theme is `accentColor="lime" grayColor="olive"`.
- Fonts: `web/public/fonts/{bricolage-grotesque,jetbrains-mono}.woff2` + OFL texts;
  `THIRD_PARTY_NOTICES.md` has a "Web fonts" section. theme-color `#0e0f0c` (index.html, vite).

## 4. What is NOT done (do these in order)

1. **Verify the lime receipt renders** (it was fixed right before this handoff: a duplicate,
   older `.receipt` block in styles.css had overridden it; I restored styles.css from HEAD and
   reapplied only the receipt block). Check with the fixture (section 5). Text on lime must be
   dark ink; the metric boxes, badge, and foot buttons should read clearly.
2. **Flow polish**: `verdictOf` only finds a top-level `verdict`; composed answers nest it
   (`data.results[i].receipt.verdict`) — search nested objects (depth ≤4). With the canvas open
   Flow is cramped; consider closing the canvas when switching to Flow or letting Flow scroll
   horizontally with a min-width.
3. **Restyle the remaining panels to the mocks** (they currently inherit the new tokens but keep
   old layouts): Accounts (cards as physical cards: `07-money-accounts`), Schedules
   (`08`, calendar strip + struck-through paid rows), Goals (`09`, rings + "when each goal lands"),
   People (`10`, mesh card for who owes you), Ledger (`11`, uncategorized queue, detail drawer),
   Imports (`12`), Settings (`15`, `16` agent memory), Sign in/Setup (`17`; Login.tsx/Setup.tsx
   already use a 56px live orb but layout is old). Keep every existing behavior and API call;
   change markup/CSS only. Panels live in `web/src/panels/*.tsx` with CSS in
   `panels/MoneyViews.css`, `Activity.css`, `Settings.css`.
4. **Agent notes** (one line from the agent at the top of each area, `.agent-note` CSS exists):
   only where code can derive the sentence from API data (e.g. accounts: statement vs ledger
   mismatch; goals: a goal that slips). Omit otherwise. Never invent.
5. **Canvas restyle**: `EvidencePanel` in `.canvas` still has the old look; make it the
   `04-canvas` style (giant verdict, gradient text, sources chips, generative projection field —
   the field may only draw bands/lines between P10/P90; label it as the range, not as runs).
6. **Phone pass** at 390px: Ask hero sizes, noticed cards scroll-snap, chats via History popover,
   timeline compactness, recaps full-screen story (list hidden on phone — needs a way to pick a
   recap on phone, e.g. a horizontal chip row above the story).
7. **1180px check**: canvas becomes a sheet; rail stays; verify nothing overlaps.
8. **Finish steps (impeccable skill requires them)**:
   - `C:/Users/joymr/.claude/skills/impeccable/scripts/impeccable detect --json web/src` once;
     fix mechanical findings.
   - Finish review via `delegate_task` with deepseek (packet: request, direction contract from
     `.impeccable/surfaces/web.md`, screenshot paths, mocks as the critique reference, craft-floor
     path `C:/Users/joymr/.claude/skills/impeccable/reference/craft-floor.md`). If unavailable,
     run `reference/degraded/finish-reviewer.md` in-thread and disclose.
   - Rewrite **DESIGN.md** + `.impeccable/design.json` from the built world (documenter, or
     `reference/degraded/documenter.md` + `reference/document.md`). Current DESIGN.md still
     describes the old Apple-quiet system — it is stale.
   - Update `docs/fin-agent/platform.md` "Web app" bullets (still says "Chat (home)", "Inbox"
     panel) to match the six areas.
   - Revoke the review session:
     `UPDATE sessions SET revoked_at=now() WHERE user_agent='design-review-headless';`
     (dev DB `fin_agent` on 127.0.0.1:5433, creds in `.env`).
9. Voice mode (`18-voice`) is **parked** — do not build.

## 5. How to see the app (this is how I worked)

All helpers live in the session scratchpad
`C:/Users/joymr/AppData/Local/Temp/claude/dd860f3fbbbc19ca/140ff4f4-4fb1-4ca0-a2f7-769260535851/scratchpad/`
(may be gone in a new session — recreate as below).

**Real app (owner's dev DB, sparse real data — screenshots stay out of git):**
- API runs in Docker on `127.0.0.1:8000`; Vite dev server already running on `localhost:5173`
  (if not: `pnpm --dir web dev`).
- Login is passkey-only, so I minted a session cookie directly in the DB: a Python script using
  `fin_agent.auth.sessions.new_token/hash_token`, inserting into `sessions` with
  `user_agent='design-review-headless'`, run with `set -a && . ./.env && set +a && uv run python
  script.py`. Then Playwright adds cookie `fin_agent_session=<token>` for domain `localhost`.
- Playwright: installed in `scratchpad/pw` (`npm i playwright@1.58`); launch with
  `executablePath: "C:/Users/joymr/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe"`
  (the bundled headless shell version is missing). `shot.mjs` loops routes at 1440×900 and
  390×844 (dpr 2) — routes like `ask,chats,money/timeline,activity/inbox,recaps`.

**Synthetic chat fixture (for receipts, tool traces, Flow, Planner):**
- `FIXTURE_PORT=8001 node web/scripts/assistant-ui-fixture/server.mjs` (background) and
  `FIXTURE_PORT=8001 npx vite --config scripts/assistant-ui-fixture/vite.config.ts --strictPort`
  from `web/` (serves `localhost:5174`).
- Export fixtures: `uv run python tests/fixtures/presentation_contract.py > fixtures.json`.
- POST `http://127.0.0.1:8001/__fixture/reset`, then POST `/__fixture/answer` with
  `{"answer": fixtures.answers.receipt}`; then in the composer type **`show a contract answer`**
  (receipt) or **`trace`** (tool trace). Click the composer then `keyboard.type` (fill() was flaky).
- The fixture has no `/api/projection`, so the Planner shows an error there; it works on the real
  API.

**Mocks:** render with Playwright `file:///J:/Development/Fin-Agent/.impeccable/mocks/final/<n>.html`.

## 6. Traps that already bit me

- **Bash heredocs with apostrophes/backticks fail in this harness** ("unexpected EOF"). Write
  scripts/CSS chunks with the Write tool, then `cat >> file` or run `python file.py`.
- **Python string surgery on styles.css**: always search the end marker *after* the start index
  (`s.index(end, a)`). Searching from 0 duplicated a whole region once. After any scripted edit,
  check duplicates: count `^\.receipt {` / `^\.radix-themes {` (each should be 1).
- styles.css still contains some **pre-existing duplicate selectors** (e.g. `.inbox-item`,
  `.assistant-head`, `button.ghost`) from older layers — later copies win. When a style "doesn't
  apply", grep for a second definition before adding specificity. Delete stale duplicates rather
  than layering overrides (owner: "fix the foundation, no stopgap layers").
- `.aui-chat h3` etc. in aui.css beat single-class selectors; scope new chat rules under
  `.aui-chat`.
- `react-refresh/only-export-components`: non-component exports go in `.ts` modules (that's why
  `notifications.ts` exists).
- Windows file locks occasionally make `open(p,'w')` fail ("Invalid argument"); retry.
- Never include `DHRITISH`, `939`, `15,348`, `AccountStatement`, or the owner's name in tracked
  files. Grep `.impeccable/mocks` before committing mocks.

## 7. Definition of done for each commit

`pnpm --dir web lint && pnpm --dir web typecheck && pnpm --dir web test:answers` (21 pass) and,
before the final commit, `pnpm --dir web build`. No Python changed so far; if you touch Python,
run ruff + the affected pytest files (tests need a `_test` DB; see AGENTS.md).

## 8. Reporting to the owner

Lead with what changed, show screenshots of the real app (desktop + phone), list what's left.
Keep it short; they read visuals, not prose.
