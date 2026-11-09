# Fin-Agent web interface design

**Status:** implementation direction for the agent-first redesign of November 8, 2026
([plan](plan.md)). The [final mocks](../../.impeccable/mocks/final/index.html) show every page with
demo data; actual behavior and fields come from the working app and API.

## Navigation and page count

The agent is the product; money is what it works on. Use **six primary destinations** on a slim
icon rail: **Ask** (the agent's home), **Chats**, **Money**, **Activity**, **Recaps**, and
**Settings**. The agent's orb sits at the top of the rail.

| Destination | Views | Purpose |
| --- | --- | --- |
| Ask | Agent home | Greeting, a large composer, starting questions, and what the agent noticed (notifications and the projection). |
| Chats | Conversation list and a conversation in **Chat** or **Flow** view | Ask, capture, read answers; Flow draws the agent's tool calls for an answer as a graph. A big answer opens a **canvas** beside the conversation. |
| Money | Timeline, Accounts, Schedules, Goals, People | The timeline joins the ledger behind today with the projection ahead. `#money/projection` stays an alias for it. |
| Activity | Ledger, Imports, Inbox | Recorded movements, statement review, and the notification inbox with scheduled questions. |
| Recaps | Daily, weekly and monthly recaps | Stories built in code from ledger movements; every figure is a sum of rows. |
| Settings | Profile and floor, Categories, Notifications, Passkeys, Scheduled questions, Agent memory, Jobs, Backups, Export | Unchanged sections. |
| Outside the shell | Sign in, Set up | Unlock the instance or enroll a passkey. |

On phones the rail becomes a bottom bar (Ask, Money, Activity, Recaps, More); the orb is the Ask
tab. Old links keep working: `#home/<thread>/<message>` opens that conversation.

## Shared composition

The visual system: a near-black olive ground (`#0e0f0c`), layered surfaces, Bricolage Grotesque for
everything and JetBrains Mono for serials and machine text, both self-hosted. **Lime** (`#d4ff3a`)
marks actions and verdicts, **cobalt** (`#2b59ff`) is the user's own voice, and a lime-to-cobalt
mesh carries the headline figure. Orange means due, green means positive or holding, red means
negative. Large radii (20–34px), tabular numerals, and Indian number grouping throughout.

The agent has one presence: the **orb**, a living iridescent sphere. It animates only where the
agent is present and working (the rail mark, the Ask hero, a reply in progress) and stands still
under reduced motion. Each area page opens with one line from the agent only when code can derive
it from API data; otherwise the line is omitted.

An answer shows its conclusion, the steps the agent ran, its computed figures and a verdict card;
the evidence opens on the canvas, with sources first and raw data second. A missing floor
suppresses the probability and says so.

## Page specifications

Each view has one main job. Tables below describe what earns space and the main action; lists collapse into readable rows on narrow screens. All data pages offer a contextual **Ask** action without changing the existing read-only Money model.

| View | Information and layout | Primary action and details |
| --- | --- | --- |
| **Ask** | The orb, a greeting, the composer, starting questions, then cards for cash on hand, forecast readiness, and unread notifications. | Ask, attach a photo, or record voice; act on a noticed item. |
| **Conversation** | Conversation list beside the thread; Chat or Flow view; a canvas for big answers. | Ask, attach, stop, flag, open sources on the canvas. |
| **Accounts** | A cash-on-hand tile leads the bank/cash group; physical credit cards, loans, and holdings follow. Cards distinguish ledger and stated balances, their dates, and any disagreement. | Open account details or Ask about the account. Never label liquid balance as safe-to-spend. |
| **Timeline** | Ledger spending behind today, the projection's median and P10–P90 band ahead, the floor, and dated schedule events. Below: goals, assumptions and data-quality notes, and the hypothetical purchase form. | Explore the projection; a purchase scenario clearly states payment method and EMI tenure, and offers sources. Missing floor and unsupported input have explicit guidance. |
| **Schedules** | Expected incoming and outgoing totals over the displayed period, a 30-day calendar strip, then dated income, obligations, and subscriptions. Scheduled events are not evidence of payment. | Ask about a schedule; choose the supported time range. |
| **Goals** | Progress rings, saved and target amounts, deadlines, and completion dates from the existing 365-day projection. A slipping goal earns an agent note; a dated comparison follows the tiles. | Ask about a goal. Missing balances suppress forecasts; missing targets and dates remain explicit. |
| **People** | Tiles grouped into owed-to-you, you-owe, and settled. A mesh surface marks receivables; each tile names its direction and offers Ask. | Ask about a person or settlement; display enough context to distinguish the two directions. |
| **Ledger** | Date/search/filter controls above a bounded movement list. Each row shows date, payee, category, amount and status; explicit previous/next controls use the available cursor. | Open Details for evidence and balanced transfers, or Ask about a movement. Keep filters and list status visible during refresh. |
| **Inbox** | Notifications by kind (proposal, reminder, risk, pattern, scheduled answer) with their actions; scheduled questions beside them. | Decide, open in chat, or mark read. |
| **Recaps** | A list of recaps and a story viewer; each story is a sum over ledger rows with its row count. | Step through, open the rows, ask about the period. |
| **Imports** | Upload/start and recent imports lead to the selected statement's status, counts, items needing decisions, then reviewed lines. Keep progress and any polling failure visible. | Resolve ambiguous matches and transfers, then commit or reject; show undo where supported. Do not let a persistent action conceal unresolved items. |
| **Profile** | Name and financial floor with concise explanation of what the floor means. | Save changes with field-level feedback. |
| **Categories** | Categories first; merchant rules below or in a clearly labelled subsection. | Edit supported categories/rules and explain any reducible setting in plain language. |
| **Notifications** | Notification preferences and permission state. Unread updates remain discoverable from Ask and the Inbox. | Change preferences or enable notifications where supported. Read and resolved remain distinct. |
| **Passkeys** | Existing devices/passkeys, current session and clear enrollment guidance. | Add a passkey or remove an eligible one, with required re-authentication. |
| **Scheduled questions** | User-defined questions, schedule, enabled state and any spend limit. | Create/edit or enable/disable an existing scheduled question with input validation. |
| **Jobs** | Operational job status and recent results, grouped as advanced. | Inspect available job details and use only existing supported controls. |
| **Backups** | Backup status, last successful backup and recovery guidance; distinguish unavailable status from no backup. | Follow the supported backup/recovery action or instructions. |
| **Export** | Explain the available data formats and when to use each, with the required re-authentication made clear. | Choose and download CSV, JSON or database export. |
| **Agent memory** | An orb introduction and saved memories, each a preference, fact, or rule with a kind chip the owner can change, plus readable content, source, scope, and enabled state. | Search, edit, turn off/on, or confirm permanent deletion; provide a clear empty state. |
| **Sign in** | Fin-Agent name, a short explanation, one prominent passkey action, and relevant setup/recovery link. | Sign in with a passkey; offer actionable error recovery. |
| **Set up** | Explain device enrollment, origin/token requirements and what happens next in a short sequence. | Enroll a passkey; report expiry or origin problems without losing the user's place. |

## Subagent runs

A subagent's work never fills the chat. The thread shows one run card: a host-made status line,
a progress bar while it runs, the unlock and account actions it needs, **Watch** or **Open run**,
and **Continue** when the run is paused. When the run ends while no one is chatting, the agent's
own reply follows the card in the open thread without a reload
([wake turn](harness.md#the-wake-turn)). The run itself opens in two places:

- **The canvas** beside the conversation (a sheet at 1180px and narrower), for following a live run.
- **A full page** at `#activity/run/<task>`, with one step in detail: what it means, the agent's
  note, the script, the result, and folded reasoning.

Both read `GET /api/tasks/{id}`, whose `run` field is built from the step store
(`src/fin_agent/api/run_view.py`):

| Part | Source | Rule |
| --- | --- | --- |
| Stages | The parser's fixed order of work | A step joins the stage of the host calls it made, so the spine is right even when the model never updates its own plan. |
| Step title | The host calls inside the script, counted | Host-made: "Read 74 pages", "Submitted the parser · attempt 2". Never model text. |
| Agent's note | The script's first `#` line | Labeled as the agent's own words. |
| Status | The step's events and result | A script that returned an error is failed; a step the run ended inside is cut off. |
| Step budget | Calls started in each run | One meter per run; a continued task shows a divider and a fresh budget. |
| Ending | The task outcome and import status | Paused (out of steps) offers Continue; failed names the reason; both say nothing reached the ledger. |

Mocks `23` to `27` in `.impeccable/mocks/final/` are the reference.

## Responsive and state rules

- Data pages use the full content width. Their **Ask** actions open the conversation with the question seeded; there is no side assistant. The canvas sits beside the conversation on wide screens and opens full-height on phones, with a labelled close control and return focus. Phone layouts stack rows and summaries, keeping amounts and primary actions visible; large tables may scroll within a labelled table region, while page text never clips.
- Use layout-matched skeletons for first loads: conversation lines and receipt, account rows, chart frame, tables, and settings fields. Skeletons reserve the final space, have a subtle shimmer, and remain identifiable to assistive technology. Refreshes keep existing data visible with a compact progress indicator. Each view explains an empty result, suggests its next useful action, and shows a recoverable error near the affected section. A failed backup-status read must not hide passkeys, export, or unrelated settings. Session expiry offers sign-in and returns the user to their work where feasible; interrupted writes are not silently replayed.
- During an import, show the current stage and unresolved decisions. Keep the user's selections and explain failures. Do not imply a commit or undo succeeded before the server confirms it.
- Use Lucide icons with text for actions, navigation and status where the icon improves recognition; give compact icon-only controls such as close an accessible name and a tooltip. Icons have consistent size and stroke, while the label remains the source of meaning.
- Make the experience lively through coordinated, quick motion: the evidence panel slides in, a receipt settles as it arrives, a chart draws on first presentation, and interactive controls respond to hover, press and focus. Use a subtle skeleton shimmer while loading. Transitions stay interruptible and never delay a task; reduced-motion preferences receive a clear non-moving alternative.
- Lists and tables are bounded. Ledger, import lines, notifications and long settings lists use appropriate filters, sorting and explicit pagination or cursor-based next/previous navigation. Never require infinite scrolling. A conversation can retain its own history navigation without making unrelated pages endlessly long.
- Provide visible focus, labelled controls, readable contrast and touch-friendly actions. Don't rely on color alone for success, warnings or financial meaning. Check desktop, tablet and phone layouts, including long labels, keyboard, touch and landscape orientation.

## Component choices for implementation

Use [Radix Themes](https://www.radix-ui.com/themes/docs/components) for styled React components such as buttons, dialogs, tabs, selects, tables and skeletons, and keep the existing [Radix Primitives](https://www.radix-ui.com/primitives/docs/overview/introduction) for more specialized behavior. The project already uses Radix and Lucide, so this builds on the current stack. Theme the library to the chosen identity instead of hand-building each control.

Use [TanStack Table](https://tanstack.com/table/v8/docs/guide/pagination) with Radix's table components where a view needs filtering, sorting and controlled pagination; retain cursor-based navigation where the backend uses cursors rather than inventing page counts. Use the open-source [Motion for React](https://motion.dev/docs/react-installation) for coordinated transitions where useful, with its [reduced-motion support](https://motion.dev/docs/react-accessibility). Recharts remains the existing chart library. Add dependencies and verify their licenses when implementing; no library has been installed as part of this design update.

## Design review material

The approved [final mocks](../../.impeccable/mocks/final/index.html) define the November 8 direction.
They use fictional data and illustrate composition, rather than extending the API contract. The
built interface preserves supported behavior: a scheduled event is not automatically paid, and
memory entries are preferences until the API exposes other types. Earlier screen studies and
Home direction samples predate the November 8 decision in plan.md and do not set the visual world.

The detailed grouping of settings and any additional API fields are implementation decisions to check against existing behavior. Preserve existing supported actions, domain calculations, passkey/session behavior and undo. Do not touch the separate observability work.
