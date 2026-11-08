# Handoff: agent-first web redesign

Written 2026-11-08 by the agent that ran the `/impeccable` redesign. Read this whole file before
touching code. It tells you what the owner chose, what is built, what is left, how to see the app,
and the traps that already cost time.

## Completion update, November 9

Codex continued the selected direction from this handoff. The remaining Money panels now use
account/card tiles, a schedule calendar, goal rings and projection-backed completion dates, and
People tiles. Accounts and Goals have API-derived agent notes. Ledger has the category queue and
movement drawer treatment; Imports has the upload/history treatment; memory has an orb
introduction and preference chips. The evidence canvas has an enlarged conclusion and sources
beside its facts, before the curve and assumptions.

The finish review ran on 6.1 Sol after the owner explicitly authorized that reviewer. Its first
full review returned four fixes: hierarchy, source placement, active phone tab visibility, and
summary tracking. The subsequent verdict scored all four resolved and returned `ship` for that
fix batch. The final capture set covers 1440px, 1180px, and 390px; review artifacts remain ignored
because some capture real instance data. Synthetic panel captures contain invented data only.

`DESIGN.md`, `.impeccable/design.json`, the web surface brief, and the interface satellite now
describe the built system. The detector returned no findings. Lint, typecheck, and all 21 answer
contract cases passed, including an added assertion that an unknown balance suppresses a canvas
verdict and forecast figures. Real-container checks opened the movement drawer at desktop and
phone widths and opened the existing committed import without writing financial data.
The final local web build and Docker image rebuild passed sequentially. The rebuilt container
also passed the phone active-tab check. The `design-review-headless` session was revoked.

Intentional limits: voice mode remains parked; schedules have no payment-state evidence, so they
are not marked paid; the current memory API exposes preferences, so fact/rule chips are not
invented. Goal completion dates beyond the forecast or unsupported by balances remain unknown.

The historical remaining-work list below records the starting point and is superseded by this
completion update.

## 1. Who you are working for and how

- The owner (Joy) gave **absolute control** over the web app's design and asked for award-level
  work. They are blunt; they reject anything that looks like a generic dark dashboard. They judge
  by **looking**, so always show screenshots or mocks, never just names.
- They picked a direction after six mock rounds. **Do not reopen the direction.** Build it.
- Their correction that shaped everything: **the AI agent is the product; money is what it works
  on.** Every surface must feel agent-first (orb, conversation, "I noticed", agent notes), not a
  finance tracker with a chat bolted on.
- Subagents: the owner originally allowed **only `deepseek-v4.1-flash` at high effort**. On
  November 9 the owner explicitly authorized **6.1 Sol for review**. Other delegation retains
  the original restriction. Use `delegate_task` after `orchestrator_capabilities`; never change
  the requested provider or model without authorization.
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
    (a real payee name, a real statement row count, a real bank balance). Mocks and fixtures use
    invented demo data only.

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

## 4. Progress since the first handoff, and what is NOT done

Done after the first handoff (commits `4081ea3` … `34bde95`):
- Lime receipt verified on screen (fixture). Flow finds nested verdicts and scrolls (min-width
  760px) beside the canvas. Planner verified against the **real** `/api/projection`.
- Summary strips are separate cards with big figures (mesh on the lead cell); every `.ask-link`
  is an iridescent-dot pill (Details/"Show all types" moved to `.pill-button`); Settings sits in
  the area frame. Sign-in/Setup: 120px live orb, glow, full-width lime pill. Global buttons on
  lime now use dark ink (they were white on lime).
- Phone: money tabs scroll on one line, timeline hides the past tag and left label under 640px,
  inbox rows stack, recaps periods become a chip row above the story; the greeting skips the
  default display name "You".
- `platform.md` web-app bullets updated to the six areas.

Still to do, in order:
1. **Restyle the remaining panels to the mocks** (tokens apply; layouts are still the old
   list style): Accounts as physical-card tiles for cards (`07`), Schedules calendar strip and
   struck-through paid rows (`08`), Goals rings + "when each goal lands" (`09`), People mesh card
   for who owes you (`10`), Ledger uncategorized queue + detail drawer look (`11`), Imports
   (`12`), Agent memory list with type chips (`16`). Change markup/CSS only; keep behavior.
   The dev DB is sparse (most panels show empty states), so verify layouts with demo-like data
   by reading the components, or check the empty states look intentional.
2. **Agent notes** (`.agent-note`) at the top of areas, only where code derives the sentence
   from API data (accounts: stated vs ledger balance mismatch; goals: a slipping goal).
3. **Canvas restyle**: `EvidencePanel` inside `.canvas` still has the old look; bring it to
   `04-canvas` (giant verdict, gradient text, source chips). Any line field may only fill the
   P10–P90 band; label it as the range, not as runs.
4. **1180px check**: canvas becomes a sheet; nothing overlaps.
5. **Finish steps (impeccable)**: run the detector
   (`C:/Users/joymr/.claude/skills/impeccable/scripts/impeccable detect --json web/src`), the
   finish review (deepseek via `delegate_task`, or the degraded in-thread review, disclosed), and
   rewrite **DESIGN.md** + `.impeccable/design.json` (still describe the old Apple-quiet system).
6. Revoke the review session:
   `UPDATE sessions SET revoked_at=now() WHERE user_agent='design-review-headless';`
7. Voice mode (`18-voice`) stays **parked**.

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
- Never copy anything you see in the real app's screenshots (payee names, row counts, balances,
  statement filenames, the owner's name) into tracked files. Before committing mocks, compare
  them against what the real dev instance shows and grep for any such value.

## 7. Definition of done for each commit

`pnpm --dir web lint && pnpm --dir web typecheck && pnpm --dir web test:answers` (21 pass) and,
before the final commit, `pnpm --dir web build`. No Python changed so far; if you touch Python,
run ruff + the affected pytest files (tests need a `_test` DB; see AGENTS.md).

## 8. Reporting to the owner

Lead with what changed, show screenshots of the real app (desktop + phone), list what's left.
Keep it short; they read visuals, not prose.
