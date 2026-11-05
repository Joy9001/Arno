# Web product-readiness evidence

Planning-only evidence for the current web UI. No UI or API changes were made, and no
design direction is chosen here. Line references are to the working tree as inspected on
2026-11-05; several API files are dirty with other workers' observability edits.

## 1. Method, runtime, and constraints

**What ran**

- `pnpm --dir web dev` (Vite 8.3.5) on `http://localhost:5173`, proxying `/api` to
  `127.0.0.1:8000` (`web/vite.config.ts:59-64`).
- No API process listened on port 8000. The first page load therefore rendered the
  App-level error card: `Request failed (502)` with `Try again` (`web/src/App.tsx:38-51`,
  `web/src/api.ts:29-37`).
- Authenticated screens were rendered in the T3 preview browser against **in-page synthetic
  JSON fixtures** installed over `window.fetch`. No backend, database, `.env`, passkey,
  model provider, or real financial data was touched.
- The fixtures were removed with the browser tabs; the dev server was stopped. No
  observability service or file was touched.

**Sizes rendered**

- Desktop: 1366×768, 1280×800, 1200×800.
- Phone: 430×932 (iPhone 14 Pro Max preset) and 390×844.
- Chromium only. No Safari, Firefox, touch synthesis, screen reader, browser zoom, PWA
  install, or push/share test.

**Dirty tree constraints**

- `web/` is clean in Git; no current web edit conflicts with this inspection.
  `.impeccable/config.json` is staged-modified by other work, so do not read the tooling
  directory as a clean baseline.
- `src/fin_agent/api/{app,chat,imports,middleware}.py` and many observability/test files are
  staged modifications from other workers. They were read only. Do not revert, stage, or
  fold them into a design/API change without the parent's decision.
- No `AGENTS.md` exists at the repo root or under `web/`. Only skill packs exist under
  `.agents/`, `.claude/`, and `.opencode/`.
- Current HEAD: `634ac36 feat(web): make the agent the home` (2026-11-04 18:46).

**Source of truth caveats**

- `PRODUCT.md` and `DESIGN.md` were read fully.
- `.impeccable/surfaces/web.md` is stale relative to `DESIGN.md` (236px rail, “ten areas”,
  demo first-viewport figures). `.impeccable/design.json` is also stale. Reported only; not
  repaired, per the parent's instruction.
- Prior screenshots in `.impeccable/review/` predate HEAD (`ux-home.png` 14:13 vs HEAD
  18:46) and are not used as current evidence.

## 2. Rendered vs source-only

**Rendered (synthetic fixtures)**

| Surface | What was seen |
|---|---|
| App error | 502 card, `Try again`; no route to logs/help |
| Login | 1280 and 390; passkey-only card |
| Home | 1280, 1366, 390; chat + Today rail; receipt card (1366); empty new chat (1366) |
| Today dialog | 430; full-screen, spendable, chart, quick actions, queue, recent |
| Money / Accounts | 1280, 390; summary strip, cards, loans, bank, holdings |
| Money / Projection | 1280, 390; verdict, chart, tradeoffs, goals, data quality, sources, citation detail |
| Money / Schedules | 1280, 390; 30-day totals, 90-day list, all-schedules table |
| Money / Goals | 1280; summary, two goal cards, progress |
| Money / People | 1280, 390; summary, owes/owed groups, settled |
| Activity / Ledger | 1280, 390; filters, rows, movement dialog |
| Activity / Imports | 1280, 390; upload, history, review, ambiguous/transfer work, table, commit/reject |
| Settings | Profile 1280/390; Notifications 1280; Categories 1280/390; Passkeys 1280; Scheduled questions 1366; Jobs 1366/1200; Backups 1366; Export 1280 |
| Assistant dialog | 390; full-height sheet from `Ask` on Money |

**Source-only**

First-run setup, real passkey ceremonies, SSE streaming, approvals/confirm cards, tool
cards, photo/voice capture, retry capture, import mutations (upload/commit/undo), PWA
install, push, share target, offline, session expiry/401, and real API payloads. Loading
states were mostly transient under instant fixtures; their code was read, not held on
screen.

**Known fixture artifacts to exclude from app findings**

- `No fixture for /api/chat/.../stream` cards on the first Home render and the first new
  chat were fixture 404s. The real endpoint returns 204 for no active turn
  (`src/fin_agent/api/chat.py:277-290`). The third tab patched this and rendered a clean
  empty conversation.
- The first Home render showed a collapsed `FA·68P·0911` card because the fixture
  accidentally overwrote `card: "projection"` with a serial. The corrected third-tab
  fixture rendered the real receipt shape.
- The import review showed `Rows 3` beside `New 18 / Matched 9`; the fixture had three
  lines and larger counts. Do not read that as a backend inconsistency.
- The citation detail looked sparse because the fixture route returned the account summary,
  not one account.

## 3. Actual navigation and route inventory

Hash routing in `web/src/Shell.tsx:49-59, 76-93`.

| Route | Page |
|---|---|
| `/` | App gate: setup, login, or shell |
| `/setup` (+ `#t=<token>`) | First-run / add-device enrollment |
| `#home` | Home: conversation + Today rail / sheet |
| `#money/accounts` | Money: Accounts |
| `#money/projection` | Money: Projection |
| `#money/schedules` | Money: Schedules |
| `#money/goals` | Money: Goals |
| `#money/people` | Money: People |
| `#activity/ledger` | Activity: Ledger |
| `#activity/imports` | Activity: Imports |
| `#settings/profile` | Settings: Profile & floor |
| `#settings/categories` | Settings: Categories |
| `#settings/notifications` | Settings: Notifications |
| `#settings/passkeys` | Settings: Passkeys |
| `#settings/questions` | Settings: Scheduled questions |
| `#settings/jobs` | Settings: Jobs |
| `#settings/backups` | Settings: Backups |
| `#settings/export` | Settings: Export |
| `#settings/memory` | Settings: Agent memory |

No URL identifies a movement, account, goal, person, import, notification, or chat thread.
Those are local dialog state or `sessionStorage("fin-agent-thread")`. A deep link can open a
tab, but not the exact object inside it.

## 4. User journeys as implemented

1. **First run.** `/` → `/api/auth/state` (`setup_required`) → `/setup` → enrollment token →
   passkey → Home. Origin mismatch is handled before the ceremony
   (`web/src/Setup.tsx:60-81`).
2. **Daily open (phone).** Home conversation; `Today` opens the overview; quick actions seed
   the composer; `Ask` on other areas opens the assistant sheet
   (`web/src/Shell.tsx:135-160, 303-340`).
3. **Ask an affordability question.** Composer or quick action → `/api/chat` stream →
   projection card → disclosure → source chip → inline fact list
   (`web/src/chat/Chat.tsx:271-313, 631-712`; `web/src/projection.tsx:486-519`).
4. **Deal with the queue.** Home's `Needs you` shows up to two unread items; `Open` posts
   `/notifications/{id}/chat`, marks read, and opens the returned thread; `View all` loads up
   to 100 (`web/src/pages/Home.tsx:168-199, 201, 263, 317-340`).
5. **Review the ledger.** Activity → Ledger → filters → cursor `Load more` → `Details` dialog
   → `Ask about this` seeds the assistant (`web/src/panels/Ledger.tsx:79-342`).
6. **Import a statement.** Upload attachment → start import → poll while parsing/password/
   input → review counts and lines → resolve ambiguous/own-account transfers → commit or
   reject → undo if committed (`web/src/panels/Imports.tsx:251-698`).
7. **Understand the plan.** Money → Accounts / Projection / Schedules / Goals / People;
   each panel is read-only, with `Ask` per object and no inline write.
8. **Configure.** Settings → nine sections; profile/floor, categories/rules, notifications/
   push, passkeys, scheduled questions, jobs, backups, export, memory
   (`web/src/panels/Settings.tsx:407-417`).
9. **Leave.** Sign out in the sidebar/topbar; `/api/auth/logout` then App re-gate
   (`web/src/Shell.tsx:126-133`).

## 5. Friction hypotheses

Each is a hypothesis for the design director, not a chosen fix. Severity is an evidence
label, not a critique score.

### F1. Settings collapses at the desktop breakpoint cliff — **observed, P1**

At 1200×800 the main column is 580px; the 220px settings nav leaves a 258px content card
(`web/src/styles.css:255-257, 1077-1082`). Jobs tables become 218px wide inside a 357px
table, so dates wrap and headers/actions are clipped. At 1280 the card is roughly 330px; at
1366 it is 398px. The assistant rail appears at 1181px, dropping the main column from
~960px to ~560px. The target desktop range starts at 1280, where the settings content is
already narrow.

Observed in Jobs (1200 and 1280) and Categories (1280/390).

### F2. Imports review widens wider than the phone — **observed, P1**

At 390×844, `.panel` is 348px but `.imports-detail` measures 472px; prose and the table are
clipped at the right edge and the panel becomes an inner horizontal scroller. The table
wrapper has `overflow-x: auto` (`web/src/styles.css:1723-1728`), but the detail subcard has no
`min-width: 0` constraint (`web/src/styles.css:2541-2545`). The commit/reject row also sits
below the table (`web/src/panels/Imports.tsx:645-671`), so the primary decision is far from
the summary on a long review.

### F3. Settings is all-or-nothing — **observed rendering + source, P1**

`SettingsPanel` returns one error page if **any** of settings, categories, backup, or
preferences fails, and one loading page until all four resolve
(`web/src/panels/Settings.tsx:548-577`). A backup-status outage therefore blocks Passkeys
(including add-device recovery), Export, and memory. The rendered Settings error (missing
fixture) showed exactly this: no section nav, only `Retry`.

### F4. “Needs you” is newest-four, not unread-first — **observed + source, P1**

`/api/home` asks for the newest four notifications with no unread filter
(`src/fin_agent/api/home.py:30`; `src/fin_agent/notifications.py:145-160`). Home then filters
read items out and shows only two (`web/src/pages/Home.tsx:201, 263`). If the newest four are
read but older unread items exist, the rail says “Newer items are read. Open View all for
older unread items.” (`web/src/pages/Home.tsx:255-260`). The daily queue can therefore look
empty while work is waiting. `/api/notifications` has no cursor and caps at 200
(`src/fin_agent/api/watch.py:71-76`).

### F5. Card ledger vs stated balance can disagree without arbitration — **observed + source, P1**

Accounts shows `Stated -₹39,000 · 12 days ago` beside `Owed ₹34,000`, but only warns when
`balance_paise > 0` **and** the statement is negative
(`web/src/panels/Accounts.tsx:53-59`). In the common both-negative case the user sees two
different owed amounts and no statement of which is authoritative or why they differ.
`DESIGN.md:309` asks for the disagreement to be said; this is narrower.

### F6. Projection hypotheticals hide material assumptions — **observed + source, P1**

The form offers `Method: Card EMI` but no tenure field
(`web/src/panels/Projection.tsx:139-149`). The engine silently uses three months when
`emi_months` is absent (`src/fin_agent/engine/affordability.py:114`). Horizon is silently
clamped to 90–3650 (`web/src/panels/Projection.tsx:16`); amount and date have only native
input constraints. The result does not say “3 months” or “clamped”. `method=emi` without
months is a valid API request, so the UI cannot rely on a 422 to catch it.

### F7. Ledger Apply/Clear can strand the loading state — **source-only, P1/P2**

`draft` and `applied` both start as the same `EMPTY` object
(`web/src/panels/Ledger.tsx:29, 80-81`). `Apply`/`Clear` set `loading=true` and reassign the
same object when nothing changed (`:140-156, 208-218`); the effect keyed on `applied` does
not rerun, so `Refreshing…` can persist and the list never settles. Separately, when rows
already exist, applying a filter shows no busy label because Loading is gated on
`items.length === 0` (`:225`).

### F8. Export page gives three equal primaries, literal backticks, and a false pointer — **observed, P2**

Three blue download buttons carry equal weight; no other Settings section presents three
primaries together. The prose contains literal backticks around `` `pg_dump` ``
(`web/src/panels/Settings.tsx:800-803`) and says “This page shows its last success above”
(`:837-840`) although the last backup success is in the Backups section, not above Export.
The passkey re-auth is described but not previewed in the button labels.

### F9. Import history and polling failures are silent — **source-only, P1**

`list.error` is never rendered; only `list.loading` and `list.data` are used
(`web/src/panels/Imports.tsx:435-436`). A failed history request leaves a blank History
section with no message or Retry. The parsing poll swallows every error
(`web/src/panels/Imports.tsx:273-298`), so a stalled parse can sit at “parsing” indefinitely
with no recovery affordance.

### F10. Chat feedback is optimistic without recovery — **source-only, P2**

`Flag` sets the local “Flagged” state before the request and ignores failure
(`web/src/chat/Chat.tsx:181-191, 575-579`), so a failed flag looks successful.
Thread-load errors render as a bare error line with no Retry (`:929`); only conversation
turn errors get `Retry` via `regenerate` (`:620-627`). A 401 or expired session has no global
handling in `api()` (`web/src/api.ts:29-37`), so panels can show “Sign in with your passkey”
without returning to Login.

**Additional observed/source-only observations, not ranked above**

- Raw machine terms leak into labels: `asset`, `person`, `card due`, `priority 1`,
  `cut category`, `delay purchase` (`web/src/panels/Accounts.tsx:26-27`;
  `web/src/panels/Goals.tsx:16-19`; `web/src/projection.tsx:219-233`). There is no shared
  enum-to-copy map.
- `1 rules · sorted by hits` (`web/src/panels/Settings.tsx:683`).
- Select fields sit inline with their label while inputs stack, because
  `.settings-block select` is `width:auto` (`web/src/styles.css:1595`) and labels are block
  (`:160-166`). Seen on Notifications’ “Weekly briefing day”.
- Goal progress uses action blue (`web/src/styles.css:1778-1782`); brand marks are action
  blue (`:283-291`). Both compete with the One Blue Rule (`DESIGN.md:149-153`) unless the
  director treats them as exceptions.
- Today is a full-screen `inset: 0` dialog (`web/src/styles.css:564-572`), not the phone
  sheet with 24px top corners and `0 -20px 60px` shadow described in `DESIGN.md:198-204`.
- The spendable figure is 32px in the rail (`web/src/styles.css:531-533`) and 32px on phone
  (`:2930-2932`), against the 34px display / 46–52px hero language in `DESIGN.md:164-172`.
- The app is dark-only but `index.html` advertises `color-scheme: dark light` and a
  `#0b0f14` theme color (`web/index.html:6-7`); the Vite manifest uses the same blue-black
  (`web/vite.config.ts:22-23`), not the `#000000` ground. Splash/flash will not match the
  product ground.
- Charts are static (`isAnimationActive={false}` in `projection.tsx:182, 191`; only `.today`
  settles in CSS `:2105-2109`), while `DESIGN.md`/the surface brief describe a projection
  that draws once.
- The receipt’s facts line repeats the same probability/threshold/lowest-median facts that
  already appear in the pills and the verdict line; at 1366 it runs well past 68ch inside the
  card. `verdict_reason` can duplicate the summary (`projection.tsx:428-471`).

## 6. State coverage and validation

| Area | Loading | Error | Empty | Partial / long |
|---|---|---|---|---|
| App gate | plain `Loading…` | card + Retry | n/a | no help link/log hint |
| Home | Today skeleton; no thread skeleton | Home card; thread error no Retry | recent/queue copy | queue newest-four; no older-unread fetch |
| Money panels | per-panel Loading | per-panel Retry (except projection form remains) | per-panel, some with Ask | tabs unmount on switch, so refetch each visit |
| Ledger | only when no rows | error + Retry | “No movements match.” | no busy on filter apply when rows exist |
| Imports | list/detail Loading | action errors; **list error silent**; poll silent | “No imports yet.” | 50-row cap + Show all; commit below table |
| Settings | all four payloads | one page error blocks all sections | per-section copy | Jobs/Backups tables cramped at 1200–1280 |
| Assistant | full rail / conversation Loading | thread error no Retry; turn error Retry | example prompts | streaming Stop; approvals; unknown cards fall back to raw JSON behind disclosure |
| Passkeys | Loading | error text | n/a | enrollment countdown/expiry |

**Concrete validation gaps**

- Ledger accepts `start > end` and renders “No movements match.” rather than an inline date
  error (`web/src/panels/Ledger.tsx:159-166`).
- Floor accepts blank as 0 (documented), but negative/invalid input gets the generic “The floor
  must be a number.” even when the number is negative
  (`web/src/panels/Settings.tsx:466-470`).
- Scheduled-question spend limit uses `Number(spend)`; `"abc"` becomes `NaN`, and
  `JSON.stringify` sends `null`, silently removing the limit
  (`web/src/panels/Settings.tsx:234-240`).
- Projection horizon is clamped silently (`:16`); date/method dependency is disabled but not
  explained when amount is blank (`:131-159`).
- The API returns one generic 422 detail for every body/query failure
  (`src/fin_agent/api/app.py:176-178`), so the UI cannot place field errors; `ApiError` only
  keeps the message (`web/src/api.ts:7-15`).

## 7. API contract seams (candidates only)

Do not treat any row as an approved endpoint design. The director owns the final shape.

### Keep

- `GET /api/home` as the single composed Home read (`src/fin_agent/api/home.py:21-38`).
- `GET /api/movements` cursor + filters (`src/fin_agent/api/ledger.py:331-355`).
- `/api/imports` state machine and `PATCH /lines/{id}`; repeat start is idempotent
  (`src/fin_agent/api/imports.py:110-135, 210-226`).
- Passkey/session endpoints and `FreshSession` writes
  (`src/fin_agent/api/deps.py:25-50`).
- `GET /api/projection` as the shared panel/chat engine (`src/fin_agent/api/ledger.py:497-522`).
- `POST /api/notifications/{id}/chat` returning a thread ID and seed text
  (`src/fin_agent/api/watch.py:96-127`).

### Adapt candidates

- **Home queue:** unread-first input to `/api/home` (or an explicit `unread_only` on
  notifications); a cursor on `/api/notifications` so `View all` is not capped at 100.
  Evidence: F4.
- **Home horizon:** today a 90d/1y toggle refetches the whole Home payload, including inbox
  and recent (`web/src/pages/Home.tsx:160-161`; `src/fin_agent/api/home.py:27-38`). A
  projection-only read or a two-horizon payload would remove the side work; either is a
  candidate.
- **Home/assistant bootstrap:** `/notifications/{id}/chat` already returns `thread_id` and
  the seed `message`, but the UI ignores them, re-lists threads, then fetches messages
  (`web/src/pages/Home.tsx:168-184`; `web/src/chat/Chat.tsx:747-847`). A small bootstrap read
  is a candidate; no final shape asserted.
- **Imports progress:** the client polls every 2.5s and swallows failures
  (`web/src/panels/Imports.tsx:273-298`). Candidate: stage/event or explicit failure state
  on the detail payload; the API already owns the state machine.
- **Projection EMI:** require or default-and-state `emi_months` for `method=emi`; the engine
  currently defaults to 3 (`src/fin_agent/engine/affordability.py:114`). Also make clamp/
  invalid input a visible 422 or a returned assumption, not a silent UI clamp.
- **Money shape:** some fields are `{paise,text}`, some are bare `*_paise`; the UI carries
  fallbacks for both (`web/src/Today.tsx:33-43`; `web/src/projection.tsx:82-111`). Normalize
  or add one adapter at a clear boundary.
- **Card schema:** chat cards are stringly typed (`card: "projection"` etc.,
  `src/fin_agent/tools/projection.py:736-780`; `web/src/chat/cards.tsx:149-326`). A versioned
  envelope is a candidate; unknown cards currently fall back to raw JSON behind a disclosure.
- **Schedules totals:** the UI fetches 90 days and computes the 30-day totals itself
  (`web/src/panels/Schedules.tsx:20-35`); a `summary_days`/totals field is a candidate.

### Add candidates (not approved)

- A person detail read; there is no `GET /api/payees/{id}` (only the list,
  `src/fin_agent/api/ledger.py:525-542`). Needed only if People becomes a drilldown journey.
- An import progress/event read or a cancel path if the review journey needs more than a
  status string.
- A Home/assistant bootstrap payload if thread and notification journeys keep requiring
  three round-trips before the first usable answer.
- A receipt citation mapping per figure if the product wants “no figure uncited” literally.
  `source_ids` exists in the projection payload but the web never reads it
  (`web/src/projection.tsx:99`; `src/fin_agent/engine/projection.py:462-501`).

### Retire candidates (verify all clients first)

- `GET /api/push/vapid` is an alias; the web uses `/api/push/key`
  (`src/fin_agent/api/watch.py:130-135`; `web/src/push.ts:39`).
- `PATCH /api/notifications/{id}` is a hidden compatibility alias; the web uses
  `POST /notifications/{id}/read` (`src/fin_agent/api/watch.py:86-93`).
- `POST /api/undo` (latest) is unused by the web; the web uses `/api/undo/{id}`
  (`web/src/chat/cards.tsx:134`). CLI/agent use must be checked before retiring.

## 8. Existing tests and commands

**Documented commands** (`README.md:168-186`)

```bash
pnpm --dir web install
pnpm --dir web dev
pnpm --dir web lint
pnpm --dir web typecheck
pnpm --dir web build          # tsc --noEmit + license collection + vite build
pnpm --dir web audit --audit-level high
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
```

**Relevant API tests**

- `tests/test_home_api.py` — composed payload keys, spendable, horizon 422.
- `tests/test_import_api.py` — upload cap, password write-only, review/commit/undo.
- `tests/test_auth.py` — first-run setup, token consumption, login/session.
- `tests/test_ledger_api.py`, `tests/test_engine_projection.py`,
  `tests/test_projection_tool.py` — ledger/projection contracts.
- `tests/test_release.py` — license and release-image guards.

**Gaps**

- No `web/` unit, component, DOM, or browser test; `web/package.json:7-13` has no test
  script. `tsc` and ESLint are the only automatic web gates.
- No reproducible fixture/demo mode for the web. The web has no mock layer; this inspection
  had to install fetch fixtures in the browser.
- The `ux-redesign.md` verification claims (`inspect.mjs --app`, “0 FAIL each”) are not
  reproducible from this repo; `inspect.mjs` lives in a skill pack, not a repo script.
- No persistent visual-regression goldens; `.impeccable/review/*.png` are untracked local
  artifacts and predate HEAD.
- No test for the queue-unread case, Settings partial failure, import list failure, card
  balance disagreement, or projection EMI assumptions.

## 9. Acceptance-gate suggestions

These are gate ideas, not scores and not a request to run a full audit now.

1. **Responsive overflow gate.** At 390, 430, 768, 1024, 1200, 1280, 1440, and 1600, for
   Home, Money/Accounts, Projection, Schedules, Goals, People, Activity/Ledger,
   Activity/Imports review, and all Settings sections: assert
   `documentElement.scrollWidth === innerWidth` and no panel-level
   `scrollWidth > clientWidth` except a deliberately labelled table scroller. F1/F2 must
   fail first.
2. **Failure-isolation gate.** With `/api/backup/status` returning 500 while the other three
   Settings reads succeed, all nine section links remain reachable and each failing section
   shows its own error/Retry. With `/api/imports` returning 500, History shows an error and
   Retry, not a blank state.
3. **Queue-semantics gate.** Fixture: four newest notifications read, two older unread.
   Home must expose the unread work (or an explicit count + direct path); it must not say
   “Nothing needs you” while unread exists.
4. **Money-trust gate.** Fixture: card ledger −₹34,000, statement −₹39,000. Both figures are
   shown with an explanation of which is authoritative and why they differ; no contradictory
   “Owed”/negative labels.
5. **Hypothetical gate.** `method=emi` without months either asks for months or states the
   assumption in the result; invalid horizon/amount/date produces inline field feedback; no
   silent clamp. A 422 problem detail is surfaced in context.
6. **Receipt gate.** Every displayed receipt figure has a visible source path (chip or
   explicit “not cited”), the disclosure is keyboard-operable, and no raw JSON is the first
   layer. `source_ids` is either used or removed from the contract.
7. **Import-safety gate.** At 390, review does not overflow; Commit is disabled until every
   ambiguous line and own-account transfer is resolved; commit/reject/undo each have a
   visible progress and failure state; a committed import's undo is reachable.
8. **Chat-recovery gate.** A failed flag reverts to “Flag” with an error; a failed thread
   load has Retry; a 401 returns to Login rather than leaving panel-local errors; stream
   failure retry does not resend a successful turn; New chat with no active turn shows no
   error.
9. **Keyboard/accessibility gate.** Keyboard-only pass through nav, Money/Activity tabs,
   Settings nav, dialogs (Escape, focus trap/return), composer, and queue actions. Label the
   message search and composer; verify progress is announced; check touch targets against
   the project's 40px floor and WCAG's 24px floor.
10. **Build/repro gate.** `pnpm --dir web lint`, `typecheck`, `build`; the relevant pytest
    files; plus a checked-in fixture viewer or equivalent so a reviewer can reproduce the
    eleven screens without a real instance. This gate is not currently possible from the
    repo alone.

## 10. Impeccable workflow rules relevant to the plan

- **Context once per session.** `impeccable context` was already run by the parent; do not
  rerun. Read existing `PRODUCT.md`/`DESIGN.md` and the surface brief directly.
- **Workflow questions do not execute.** Routing says give advice and link the docs; never
  auto-run a command or treat the menu as approval (`reference/routing.md:3-5`).
- **`shape` is the journey/UX planning command.** Discovery interview in rounds of two or
  three questions, no code; then a compact brief with job/audience, outcome/proof,
  direction, scope, states, interaction, and constraints; confirm and stop
  (`reference/shape.md:5-59`). This matches “design journeys before adapting APIs.”
- **`critique` is dual-agent on web.** Assessment A (design review) and B (detector/browser
  evidence) must be isolated sub-agents when available; inline is a degraded run and must
  carry the degraded banner. A skipped detector is a failed run unless unavailable/crashed.
  Questions are the last thing in the response when there are 3+ priority issues
  (`reference/critique.md:5-15, 31-53, 258-280`). If the parent keeps “no subagents”, a
  future critique cannot be a normal run.
- **`audit` is technical, not a redesign.** Five dimensions: a11y, performance, theming,
  responsive, implementation integrity; P0–P3; no fixes (`reference/audit.md:1-139`).
- **`polish` preserves the incumbent world.** Triage functional/data-loss/misleading state
  first, then missing states, then flow/hierarchy/responsive, then visual, then cleanup;
  verify at shipped sizes; finish with a source diff; close a critique snapshot only when
  every priority issue is cleared (`reference/polish.md:1-105`).
- **`harden` tests perfect-data assumptions.** Extreme text/numbers/lists, network/API/
  permission failures, interrupted gestures, empty/loading/large states, i18n/overflow
  (`reference/harden.md:1-362`). Applicable to F2/F3/F5/F9.
- **Operate-mode bar is earned familiarity.** Every component needs default/hover/focus/
  active/disabled/loading/error; empty states teach; overlays escape `overflow` ancestors;
  no decorative motion; consistency beats surprise (`reference/operate.md:5-61`).
- **Do not repair drift as a side effect.** `CONTEXT_STALE` and stale `design.json`/surface
  brief are reported, not fixed, unless the user asks (`SKILL.md:84-86`).
- **Brief wins; refinement preserves; redesign replaces.** Visual authority is evidence, not
  a filename. The surface is Operate, so precision/restraint, not expressive redesign
  (`SKILL.md:24-39`).

## 11. Project truth conflicts needing a director choice

1. **Conversation column width.** `DESIGN.md:180-185` says a 900px conversation column with a
   400px rail at 1181px and wider. Arithmetic gives 660px at 1280, 746px at 1366, 820px at
   1440; 900px is reached only at 1520+. Choose: change the rail/breakpoints, change the
   design claim, or accept the narrower effective column.
2. **Surface brief and design.json are stale.** `.impeccable/surfaces/web.md:10,24-27`
   claims a 236px sidebar, ten areas, and a demo first viewport (₹1,84,200, serial
   FA·68P·0911). Actual is 220px and four areas. Do not treat the brief as current until
   refreshed by its owner.
3. **Today sheet vs full-screen dialog.** `DESIGN.md:183-204` describes a Today sheet and
   phone-sheet shadow/24px top corners; `web/src/styles.css:564-572` is `inset: 0` with no
   shadow or corner radius. Choose the document or the implementation.
4. **Type/motion truth.** `DESIGN.md:164-172` says 34px display and 46–52px hero; CSS is
   32px rail, 40px base, 32px phone. `DESIGN.md`/brief describe a drawing projection; charts
   are static. Choose tokens/copy or implementation.
5. **Ground and splash.** `DESIGN.md` ground is `#000000`; `index.html:7` and
   `vite.config.ts:22-23` use `#0b0f14` theme/background. Is the blue-black only a PWA
   splash exception, or should it become a token?
6. **One Blue Rule.** Brand marks and goal progress use action blue
   (`styles.css:283-291, 1778-1782`) while `DESIGN.md:149-153` reserves blue for action and
   the user's voice. Are brand/progress intentional exceptions or drift?
7. **Docs vs current IA.** `PRODUCT.md:53-54` still says an inbox panel;
   `docs/fin-agent/platform.md:90-97` still says an Inbox panel and Settings sessions/trace
   search; `completion-checklist.md:158` points at `web/src/panels/Inbox.tsx`, which no
   longer exists. Current IA is Home queue + nine Settings sections. Which document owns
   truth?
8. **Home API documentation.** `docs/fin-agent/ux-redesign.md:110-132` documents a flat
   Home payload (`spendable` object, top-level floor, four needs, five recent). Current API
   is `spendable_paise` + nested `projection`, newest four (UI shows two) and five recent
   (`src/fin_agent/api/home.py:21-38`). Update the doc or treat it as historical.
9. **“Spendable” semantics.** The value is the sum of liquid account balances
   (`src/fin_agent/ledger/accounts.py:425-445`), not cash after card dues/obligations. The
   word may overstate what is safe to spend; is the metric “cash on hand” or “safe to
   spend”?
10. **Verification claims.** `ux-redesign.md:28-30` claims `impeccable detect` + `inspect.mjs`
    runs with 0 FAIL. Those artifacts are not in the repo and the screenshots predate HEAD.
    Decide whether release evidence must be reproducible in-repo before calling the UI
    shipped.

## 12. Unresolved product questions

- Should Home’s queue show 2, 4, or every unread item, and does “read” mean “handled”?
- Does the agent’s daily surface need a “Today” sheet at all on phone, or is the conversation
  plus composer the whole daily product?
- Are Money panels permanently read-only, or do empty/error states need inline create/edit
  paths rather than `Ask` seeds?
- Is the 900px conversation the target at 1280–1440, or only at 1520+? What happens between
  1181 and 1519?
- Should Settings’ operational tables (Jobs, Backups, Categories rules) live in Settings, a
  separate admin surface, or the host CLI?
- Should the receipt show per-figure citations, a single source list, or both? Is
  `source_ids` intended for the UI?
- Is “Yes, with a change” the intended verdict language, or should it be “Yes, but …” as the
  surface brief says?
- Should imports commit be sticky, moved above the table, or remain at the end of the
  review? The current order matches the written flow but not the visual distance.
- Should export be one primary action with a format choice, or three equal downloads?
- Does the PWA install/ splash need to be true black, or is `#0b0f14` a deliberate brand
  exception?
- Is category “reducible” a user-facing toggle or an expert setting? Its only affordance is
  a chip with a tooltip.

## 13. Limitations and what was not checked

- No real authenticated session, API, database, model stream, vision, STT, import parse,
  commit, passkey, push, or PWA install ran.
- No touch-device test; “Hold to talk” has pointer handlers (`web/src/chat/Chat.tsx:544-554`)
  but no `touch-action` and no context-menu suppression in `styles.css`; long-press behavior
  is unverified.
- No screen reader, zoom, forced-colors, or contrast measurement. No Lighthouse/axe.
- No Safari/Firefox; no offline/throttled network; no large real dataset (939-row import was
  only represented by a small fixture).
- No full test suite, no `impeccable detect`, and no numeric score was produced. Friction
  labels are evidence triage, not heuristic scores.
- Screenshots are browser-session artifacts outside the repo and were left there; no raster
  was copied into this file. The prior `.impeccable/review/` set is stale and was not used
  as current evidence.

## 14. Closest next evidence to gather (if the director asks)

Before any API adaptation, the cheapest missing evidence is: a checked-in fixture viewer
(or a demo JSON mode) that can render the inspected screens at the target widths; one
failure-injection pass for Settings/Imports/Home; and a keyboard/touch pass on the queue,
composer, and import review. Those three would let the design director separate design
choices from implementation defects without a real instance.
