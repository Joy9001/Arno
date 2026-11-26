# Web interface

This specification owns Arno's web navigation, hash routes, page layouts,
responsive and state rules, and frontend stack; [`DESIGN.md`](../../../DESIGN.md) owns all visual
design tokens, typography, color roles, and orb rules, and [`platform.md`](platform.md) owns the
underlying API and authentication contracts.

## Navigation and routes

The web app uses **six primary destinations** on a slim desktop icon rail: **Ask** (agent home),
**Chats**, **Money**, **Activity**, **Recaps**, and **Settings**, with the orb brand button at the
top of the rail. On
phones, the rail collapses into a five-slot bottom tab bar (**Ask**, **Money**, **Activity**,
**Recaps**, and **More**), while conversation history stays inside the chat view.

The conversation stays mounted while you browse other destinations, so an in-flight reply can
continue streaming. Open a citation or interact with a result directly in the conversation.

The following table lists every live route and alias handled by `web/src/Shell.tsx`,
`web/src/pages/Recaps.tsx`, `web/src/panels/Settings.tsx`, and `web/src/App.tsx`:

| Route | Destination and behavior |
|---|---|
| Sign-in view | Outside the shell whenever unauthenticated: a passphrase form when `ARNO_PASSPHRASE` is set, otherwise a note that access is through Tailscale |
| `#ask` | Agent home: greeting, composer, starting questions, and noticed items |
| `#chats`, `#chats/<thread>`, `#chats/<thread>/<message>` | Conversation list beside the thread in **Chat** or **Flow** view, jumping to a specific message when valid |
| `#money/timeline` (alias `#money/projection`) | Ledger spending behind today joined with the Monte Carlo projection ahead |
| `#money/accounts` | Account tiles grouped by liquid cash/bank, credit cards, loans, and holdings |
| `#money/schedules` | Upcoming and recent schedule occurrences with payment matching evidence |
| `#money/goals` | Goal progress rings and 365-day forecast completion dates |
| `#money/people` | Split and lending balances grouped into owed-to-you, you-owe, and settled |
| `#activity/ledger` | Paginated movement ledger, provenance drawer, and **Uncategorized** queue |
| `#activity/imports`, `#activity/imports/<import_id>` | Statement upload, active import review, and paginated import history |
| `#activity/inbox` | Notification inbox and scheduled-question management |
| `#recaps`, `#recaps/<period_id>` | Daily, weekly, and monthly ledger-computed recap stories |
| `#settings/<section>` | One of 10 settings sections (`profile`, `categories`, `notifications`, `questions`, `memory`, `history`, `diagnostics`, `backups`, `export`, `deletion`) |
| `#home`, `#home/<thread>[/<message>]` | Legacy aliases redirected to `#ask` or `#chats/<thread>[/<message>]` |

## Page specifications

Every read-only data page offers contextual **Ask** actions that carry a pre-filled question into
chat rather than embedding a side assistant. Visual tokens, elevation, and orb behavior follow
[`DESIGN.md`](../../../DESIGN.md).

### Agent and conversation views

The following table describes the interactive agent views.

| View | Layout and content | Primary actions |
|---|---|---|
| **Ask** | Orb hero, greeting, composer (text, photo upload, voice recording, statement attachment), starting questions, and cards for cash on hand, forecast readiness, and unread notifications; replaced by a guided first-run checklist and statement drop zone until the ledger has data | Ask a question, attach a receipt or statement, record voice, or open a noticed item |
| **Chats** | Thread list and conversation, with widgets backed by tool results, inline citations, and reply flagging | Send messages, stop streaming, inspect source previews, review proposed writes, and undo completed writes |
| **Flow** | Zoomable, pannable graph of the prompt, tool activity, and visual results from the same conversation | Inspect the work behind a reply and open the reply in **Chat** |
| **Affordability lab** | Projection verdict, floor-probability gauge, and P10–P90 range, shared by **Chats** and **Money → Timeline** | Adjust purchase price, date, financing, or the cut to reducible spending. Compare results from `/api/projection` |

### Money, Activity, and Recaps

The following table specifies the financial workspace panels:

| View | Layout and content | Primary actions and details |
|---|---|---|
| **Money → Timeline** | Historical ledger spending paired with median and P10–P90 projection band, financial floor, dated schedule events, goals, assumptions, data-quality notes, and a hypothetical purchase form | Inspect forecast horizons, export the projection `.xlsx` workbook, or test purchase scenarios; missing floor suppresses probability and explains why |
| **Money → Accounts** | Cash-on-hand summary leading bank and cash accounts, followed by credit cards, loans, and asset holdings; distinguishes ledger and stated balances, their dates, and discrepancies | Inspect account details or ask about an account; never labels liquid balance as safe-to-spend |
| **Money → Schedules** | Expected incoming and outgoing totals for the selected period (`30`–`730` days), a scrollable calendar strip (past 7 days + next 30 days), and dated rows grouped by type. Paid/received occurrences appear struck through; partial rows show matched and remaining amounts. Generated card-due entries are labeled as forecasts rather than payment evidence | Inspect read-only payment evidence (`SchedulePaymentDetails`), seed chat to link or unlink a payment, or filter by schedule bucket |
| **Money → Goals** | Progress rings, saved versus target amounts, deadlines, and projected completion dates from the 365-day forecast | Ask about a goal; missing balances suppress forecasts and missing targets stay explicit |
| **Money → People** | Tiles grouped into owed-to-you, you-owe, and settled, clearly stating direction | Ask about a person or record a settlement in chat |
| **Activity → Ledger** | Date, kind, account, and search filters above a cursor-paginated movement list (25 per page) plus the **Uncategorized** queue (`GET /api/uncategorized`) grouping unlabeled payees by frequency and total | Open movement provenance and balanced transfers, undo eligible rows, ask about a movement, create custom categories, or assign categories directly to uncategorized payee groups |
| **Activity → Imports** | Statement upload/start controls, active import review (status badge, counts, **Needs a decision** filter for ambiguous matches, suggested own-account transfers, and credit-card credit classifications, manual opening balance input, and closing adjustment confirmation), keyset-paginated import history | Resolve line decisions, enter statement passwords, commit, reject, undo committed imports, or purge stored statement files |
| **Activity → Inbox** | Filterable notifications by kind (proposal, reminder, risk, pattern, scheduled answer, job failure) beside scheduled-question shortcuts | Mark read or open in a seeded chat |
| **Recaps** | Sidebar period list grouped into **Daily**, **Weekly**, and **Monthly** recaps with **New** badges, paired with a keyboard-navigable story viewer (`ArrowLeft` / `ArrowRight`) where every figure links to its underlying ledger rows; empty ledgers display an onboarding state instead of ₹0 stories | Step through stories, open contributing ledger rows, track detected recurring charges in chat, or ask about a period |

### Settings

**Settings** organizes its sections into two groups (**Everyday** and **Advanced**), each
loading and handling errors independently so a failing status check never hides unrelated sections.

The following table lists the settings sections.

| Group | Section (`#settings/<id>`) | Content and primary actions |
|---|---|---|
| Everyday | **Profile & floor** (`profile`) | Display name, financial floor (INR with client validation), currency, time zone, default horizon, and last model error summary |
| Everyday | **Categories** (`categories`) | Review active and archived categories. Use **Ask** to change a category in chat, or manage merchant rules directly here. |
| Everyday | **Notifications** (`notifications`) | Briefing day, notification time, and reminder lead days |
| Everyday | **Scheduled questions** (`questions`) | Paginated user-defined recurring questions with RRULE schedules and an active toggle |
| Everyday | **Agent memory** (`memory`) | Review saved preferences, facts, and rules. Use **Ask** to change a memory or delete it permanently in Settings. |
| Everyday | **History** (`history`) | Cursor-paginated audit log (`HistoryPanel`) of write groups with actor, timestamp, summary, and undo action |
| Advanced | **Diagnostics** (`diagnostics`) | **Models and diagnostics** (`RuntimeInfo`) displaying each role's configured model and host and an **Open Phoenix** link when `ARNO_PHOENIX_UI_URL` is configured |
| Advanced | **Backups** (`backups`) | Last successful backup status, age, and recovery instructions; distinguishes unavailable status from no backup yet |
| Advanced | **Export** (`export`) | Format guidance, `ExportScope` listing the excluded credential tables, and downloads for JSON, CSV ZIP, and `pg_dump` |
| Advanced | **Delete everything** (`deletion`) | `DeletionScope` preview of every table affected, requiring the typed `"delete everything"` confirmation phrase |

### Badges and categorization status

The shell refreshes the inbox and recap badges every 30 seconds. A committed import shows its
categorize state (`imports.categorize`) and looks again every few seconds while the state is
`queued` or `running`; a `failed` state offers **Retry**, which calls
`POST /api/imports/{id}/categorize`.

## Thread results and statement review

Each stored reply renders as its own message (`joinStrategy: "none"`). A thread that is still
running when it opens (Arno answering an inbox item, or a turn that outlived its connection) follows
its stream. A new conversation gets its server id from `POST /api/threads` before its first message,
photo, or voice note.

Widgets read native assistant message parts. Source previews show web titles, snippets, and links
or the exact movement date, payee, and amount from a ledger result. An attached statement opens
the same review controls used in **Activity → Imports**, including the password field for an
encrypted file. See [Statement extraction](import.md#extraction).

## Responsive and state rules

- **Layout and focus:** Data pages span the full workspace width. Narrow viewports stack summary
  tiles and rows while keeping amounts and primary actions visible; wide tables scroll inside
  focusable labelled regions (`TableRegion`) without clipping text. Dialog sheets provide a
  labelled close control and restore focus on close.
- **Loading and errors:** Initial loads display layout-matched skeleton placeholders
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
| Components and styling | `radix-ui`, `tailwindcss` v4 (`@tailwindcss/vite`), `tw-animate-css`, `tw-shimmer`, `class-variance-authority`, `clsx`, `tailwind-merge` |
| Visualization, icons, and motion | `@xyflow/react`, `recharts`, `lucide-react`, `motion`, `react-spinners` |
| State and validation | `zustand`, `zod` |
| Testing and linting | `vitest`, `@testing-library/react`, `@testing-library/dom`, `jsdom`, `eslint`, `typescript-eslint` |
