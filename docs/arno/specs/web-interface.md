# Web interface

This specification owns Arno's web navigation, hash routes, page layouts, subagent run views,
responsive and state rules, and frontend stack; [`DESIGN.md`](../../../DESIGN.md) owns all visual
design tokens, typography, color roles, and orb rules, and [`platform.md`](platform.md) owns the
underlying API and authentication contracts.

## Navigation and routes

The web app uses **six primary destinations** on a slim desktop icon rail: **Ask** (agent home),
**Chats**, **Money**, **Activity**, **Recaps**, and **Settings**, with the orb brand button at the
top of the rail and a live **Background work** indicator above **Settings** whenever jobs run. On
phones, the rail collapses into a five-slot bottom tab bar (**Ask**, **Money**, **Activity**,
**Recaps**, and **More**), while conversation history stays inside the chat view.

The conversation component stays mounted in the shell while you browse other destinations so an
in-flight reply continues streaming without interruption. On wide screens (`> 1180px`), an active
**canvas** (`EvidencePanel`, `PlannerPanel`, or `RunPanel`) docks beside the conversation; at
`1180px` and narrower, the canvas opens inside a Radix Dialog sheet.

The following table lists every live route and alias handled by `web/src/Shell.tsx`,
`web/src/pages/Recaps.tsx`, `web/src/panels/Settings.tsx`, and `web/src/App.tsx`:

| Route | Destination and behavior |
|---|---|
| `/setup` | Passkey enrollment page (outside the shell); also shown automatically when `setup_required` is true |
| Sign-in view | Passkey login screen shown outside the shell whenever unauthenticated |
| `#ask` | Agent home: greeting, composer, starting questions, and noticed items |
| `#chats`, `#chats/<thread>`, `#chats/<thread>/<message>` | Conversation list beside the thread in **Chat** or **Flow** view, jumping to a specific message when valid |
| `#money/timeline` (alias `#money/projection`) | Ledger spending behind today joined with the Monte Carlo projection ahead |
| `#money/accounts` | Account tiles grouped by liquid cash/bank, credit cards, loans, and holdings |
| `#money/schedules` | Upcoming and recent schedule occurrences with payment matching evidence |
| `#money/goals` | Goal progress rings and 365-day forecast completion dates |
| `#money/people` | Split and lending balances grouped into owed-to-you, you-owe, and settled |
| `#activity/ledger` | Paginated movement ledger, provenance drawer, and **Uncategorized** queue |
| `#activity/imports`, `#activity/imports/<import_id>` | Statement upload, active import review, paginated import history, and saved parser layouts |
| `#activity/inbox` | Notification inbox and scheduled-question management |
| `#activity/run/<task_id>` | Full-page step-by-step view of a parser subagent run |
| `#recaps`, `#recaps/<period_id>` | Daily, weekly, and monthly ledger-computed recap stories |
| `#settings/<section>` | One of 11 settings sections (`profile`, `categories`, `notifications`, `passkeys`, `questions`, `memory`, `history`, `jobs`, `backups`, `export`, `deletion`) |
| `#home`, `#home/<thread>[/<message>]` | Legacy aliases redirected to `#ask` or `#chats/<thread>[/<message>]` |

## Page specifications

Every read-only data page offers contextual **Ask** actions that carry a pre-filled question into
chat rather than embedding a side assistant. Visual tokens, elevation, and orb behavior follow
[`DESIGN.md`](../../../DESIGN.md).

### Agent and conversation views

The following table specifies the interactive agent views and canvas panels:

| View | Layout and content | Primary actions |
|---|---|---|
| **Ask** | Orb hero, greeting, composer (text, photo upload, voice recording, statement attachment), starting questions, and cards for cash on hand, forecast readiness, and unread notifications; replaced by a guided first-run checklist and statement drop zone until the ledger has data | Ask a question, attach a receipt or statement, record voice, or open a noticed item |
| **Chats** | Thread list beside the open conversation; **Chat** view or **Flow** view (graph of tool calls for an answer); rich tool cards (logged movement with undo, **Remembered** card, projection receipt, spending breakdown, import run card, charts, tables); inline untraced-number notes and reply flagging; plain-text links only | Send messages, answer confirm cards, stop streaming, flag replies, and open evidence or the planner on the canvas |
| **Evidence canvas (`EvidencePanel`)** | Sources and computed breakdown backing a projection or answer card, with sources first and raw figures second | Inspect contributing rows, jump to **Money → Timeline**, or ask a follow-up |
| **Purchase planner (`PlannerPanel`)** | Interactive canvas what-if panel backed by `/api/projection`: price slider (`₹1,000`–`₹5,00,000`), purchase-date slider (today through +60 days), payment method selector (**From an account**, **Card in full**, **Card EMI** with 3–24 month tenures), debounced floor-probability gauge, verdict, lowest median, tightest day, and chart | Explore purchase timing and financing scenarios directly against the projection engine without invoking the LLM |

### Money, Activity, and Recaps

The following table specifies the financial workspace panels:

| View | Layout and content | Primary actions and details |
|---|---|---|
| **Money → Timeline** | Historical ledger spending paired with median and P10–P90 projection band, financial floor, dated schedule events, goals, assumptions, data-quality notes, and a hypothetical purchase form | Inspect forecast horizons, export the projection `.xlsx` workbook, or test purchase scenarios; missing floor suppresses probability and explains why |
| **Money → Accounts** | Cash-on-hand summary leading bank and cash accounts, followed by credit cards, loans, and asset holdings; distinguishes ledger and stated balances, their dates, and discrepancies | Inspect account details or ask about an account; never labels liquid balance as safe-to-spend |
| **Money → Schedules** | Expected incoming and outgoing totals for the selected period (`30`–`730` days), a scrollable calendar strip (past 7 days + next 30 days), and dated rows grouped by type. Paid/received occurrences appear struck through; partial rows show matched and remaining amounts. Generated card-due entries are labeled as forecasts rather than payment evidence | Inspect read-only payment evidence (`SchedulePaymentDetails`), seed chat to link or unlink a payment, or filter by schedule bucket |
| **Money → Goals** | Progress rings, saved versus target amounts, deadlines, and projected completion dates from the 365-day forecast | Ask about a goal; missing balances suppress forecasts and missing targets stay explicit |
| **Money → People** | Tiles grouped into owed-to-you, you-owe, and settled, clearly stating direction | Ask about a person or record a settlement in chat |
| **Activity → Ledger** | Date, kind, account, and search filters above a cursor-paginated movement list (25 per page) plus the **Uncategorized** queue (`GET /api/uncategorized`) grouping unlabeled payees by frequency and total | Open movement provenance and balanced transfers, undo eligible rows, ask about a movement, or assign categories directly to uncategorized payee groups |
| **Activity → Imports** | Statement upload/start controls, active import review (status badge, counts, **Needs a decision** filter for ambiguous matches, suggested own-account transfers, and credit-card credit classifications, manual opening balance input, and closing adjustment confirmation), keyset-paginated import history, and **Saved layouts** listing parser versions per bank | Resolve line decisions, enter statement passwords, commit, reject, retry failed imports, undo committed imports, purge stored statement files, or roll back a saved parser layout to an earlier version (`POST /api/layouts/{id}/rollback`) |
| **Activity → Inbox** | Filterable notifications by kind (proposal, reminder, risk, pattern, scheduled answer, job failure) beside scheduled-question shortcuts | Mark read, open in a seeded chat, or resolve items |
| **Recaps** | Sidebar period list grouped into **Daily**, **Weekly**, and **Monthly** recaps with **New** badges, paired with a keyboard-navigable story viewer (`ArrowLeft` / `ArrowRight`) where every figure links to its underlying ledger rows; empty ledgers display an onboarding state instead of ₹0 stories | Step through stories, open contributing ledger rows, track detected recurring charges in chat, or ask about a period |

### Settings

**Settings** organizes **11 sections** into two groups (**Everyday** and **Advanced**), each
loading and handling errors independently so a failing status check never hides unrelated sections.

The following table lists all 11 Settings sections:

| Group | Section (`#settings/<id>`) | Content and primary actions |
|---|---|---|
| Everyday | **Profile & floor** (`profile`) | Display name, financial floor (INR with client validation), currency, time zone, default horizon, and last model error summary |
| Everyday | **Categories** (`categories`) | Active and archived categories (rename, archive/restore, toggle `reducible` for affordability tradeoffs) and paginated merchant rules (add, promote `llm` rules to user rules, delete) |
| Everyday | **Notifications** (`notifications`) | Briefing day, notification time, reminder lead days, and Web Push browser permission and subscription toggle (`PushStatus`) |
| Everyday | **Passkeys** (`passkeys`) | Registered passkeys, current session info, rename/delete controls (protecting the last passkey), and **Add a device** QR enrollment flow |
| Everyday | **Scheduled questions** (`questions`) | Paginated user-defined recurring questions with RRULE schedules, active toggle, and per-run USD spend limit |
| Everyday | **Agent memory** (`memory`) | Saved memories across all three live kinds (`preference`, `fact`, `rule`) with kind chips, editable body text, source, scope, enable/disable toggle, and permanent delete with deletion preview |
| Everyday | **History** (`history`) | Cursor-paginated audit log (`HistoryPanel`) of write groups with actor, timestamp, summary, and undo action |
| Advanced | **Background work** (`jobs`) | Live and recent job statuses with retry controls for failed jobs, plus **Models and diagnostics** (`RuntimeInfo`) displaying each role's configured model and host and an **Open Phoenix** link when `ARNO_PHOENIX_UI_URL` is configured |
| Advanced | **Backups** (`backups`) | Last successful backup status, age, and recovery instructions; distinguishes unavailable status from no backup yet |
| Advanced | **Export** (`export`) | Format guidance, `ExportScope` listing the eight excluded credential tables, and fresh-passkey-gated downloads for JSON, CSV ZIP, and `pg_dump` |
| Advanced | **Delete everything** (`deletion`) | `DeletionScope` preview of every table and parser task file affected, requiring fresh passkey re-verification and the typed `"delete everything"` confirmation phrase |

### Background work indicators and toasts

The shell runs a single polling loop over `GET /api/jobs/activity` (every 3 seconds while a job is
running or due within 30 seconds, and every 30 seconds while idle), backed by a `zustand` store
(`web/src/jobs.ts`):

- **Rail and tab-bar indicator (`BackgroundWork`):** Shows a spinner and active task summary on the
  desktop rail and mobile tab bar, navigating to `#settings/jobs` on select.
- **Job toasts (`JobToasts`):** Uses Radix `Toast` to announce finished categorizations (with a
  **Review** shortcut to **Uncategorized**), batch submissions, scheduled answers, weekly
  briefings, and job failures (persisting until dismissed, with inline **Retry** and **Details**
  actions).

## Subagent run views

A parser subagent's execution stays folded out of the main transcript. The chat thread displays one
import run card with a host-generated status line, progress indicator, inline password or
account-selection controls when waiting, **Watch** or **Open run**, and **Continue** when paused on
the step budget. When a task settles while you are away, the wake-turn reply streams into the open
thread automatically ([`harness.md#wake-turn`](harness.md#wake-turn)). Its status line is a plain
sentence built from the task's outcome; the raw error stays on the run page. Every mounted card for
one task shares a single poller (`useRun` in `web/src/run/runData.ts`), and a settled task is read
once.

Each stored reply renders as its own message (`joinStrategy: "none"`), so a reply a wake turn
wrote after an earlier answer never folds that answer into the activity panel. A thread that is
still running when it opens (a wake turn, or Arno answering an inbox item) follows its stream. A
new conversation gets its server id from `POST /api/threads` before its first message, photo, or
voice note.

Detailed run progress opens in two places:

- **Live canvas (`RunPanel`):** Docks beside the conversation (or as a dialog sheet at `<= 1180px`)
  for watching an active parse.
- **Full page (`RunPage` at `#activity/run/<task_id>`):** Shows every stage and step in full,
  including step meaning, the agent's comment note, executed script, result, and folded reasoning.

Both views read `GET /api/tasks/{id}`, whose `run` payload is built on the server from
`PostgresStepStore` (`src/arno/api/run_view.py`):

| Field | Source | Rule |
|---|---|---|
| Stages | Fixed parser workflow order | Steps attach to the stage matching their host calls, keeping the spine accurate even if the model skips plan updates |
| Step title | Counted host calls inside each script | Always host-generated (for example, `"Read 74 pages"` or `"Submitted the parser · attempt 2"`), never free-form model text |
| Agent note | First `#` comment line in the script | Explicitly labeled as the agent's own note |
| Status | Step events and return payload | Scripts returning errors mark as failed; steps interrupted at turn end mark as cut off |
| Step budget | Tool calls started per run | One meter per run; resuming a paused task inserts a divider and resets the budget meter |
| Ending | Task outcome and import status | Paused runs (`budget`) offer **Continue**; failed runs state the reason; both confirm nothing reached the ledger |

## Responsive and state rules

- **Layout and focus:** Data pages span the full workspace width. Narrow viewports stack summary
  tiles and rows while keeping amounts and primary actions visible; wide tables scroll inside
  focusable labelled regions (`TableRegion`) without clipping text. Dialog sheets provide a
  labelled close control and restore focus on close.
- **Loading and errors:** Initial loads display layout-matched Radix `Skeleton` placeholders
  reserving final height. Refreshes keep existing data visible beside a compact status indicator.
  Empty views explain what is missing and suggest the next step. Errors render inline near the
  affected section with a retry control; expired sessions prompt sign-in without silently replaying
  interrupted writes.
- **Import integrity:** Unresolved decisions stay visible throughout review, selections persist
  across polling ticks, and the UI never implies a commit or undo succeeded before the server
  confirms it.
- **Pagination:** All long lists (ledger movements, import lines, import history, notifications,
  category rules, memories, scheduled questions, and audit history) use filters and explicit
  cursor or page navigation (`Previous` / `Next`) rather than infinite scrolling.
- **Motion and accessibility:** Coordinated transitions use `motion` (`AnimatePresence`) and honor
  `prefers-reduced-motion`. Controls keep visible focus rings, accessible names on icon-only
  buttons, and text or icon redundancy so financial meaning never depends on color alone.

## Frontend stack

The web app is a static TypeScript bundle built by Vite and served directly by FastAPI (no Node
server in production). The following table lists the key dependencies installed in
[`web/package.json`](../../../web/package.json):

| Area | Installed packages |
|---|---|
| Core runtime and build | `react` 19, `react-dom` 19, `typescript` 5.9, `vite` 8, `vite-plugin-pwa`, `workbox-precaching` |
| Chat and streaming UI | `ai` 7 (pinned to match `VercelAIAdapter` `sdk_version=7`), `@ai-sdk/react`, `@assistant-ui/react`, `@assistant-ui/ai-sdk`, `@assistant-ui/generative-ui`, `@assistant-ui/react-streamdown`, `assistant-stream`, `streamdown`, `@streamdown/code` |
| Components and styling | `@radix-ui/themes`, Radix Primitives (`@radix-ui/react-dialog`, `@radix-ui/react-popover`, `@radix-ui/react-tabs`, `radix-ui`), `tailwindcss` v4 (`@tailwindcss/vite`), `tw-animate-css`, `tw-shimmer`, `class-variance-authority`, `clsx`, `tailwind-merge` |
| Visualization, icons, and motion | `recharts`, `lucide-react`, `motion`, `react-spinners` |
| State, validation, auth, and QR | `zustand`, `zod`, `@simplewebauthn/browser`, `qrcode.react` |
| Testing and linting | `vitest`, `@testing-library/react`, `@testing-library/dom`, `jsdom`, `eslint`, `typescript-eslint` |
