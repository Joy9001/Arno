# Fin-Agent web interface design

**Status:** implementation direction based on the selected conversation-first design with on-demand evidence. The accompanying studies use illustrative data; actual behavior and available fields are defined by the working app and API.

## Navigation and page count

Use **four primary destinations**: Home, Money, Activity, and Settings. Home is the agent; the other three group related views so the top level stays small. The proposal has **17 signed-in views** (Home; five Money views; two Activity views; nine Settings views) and **two access screens** (sign-in and setup). A movement, an import, a source, a conversation, and the Today overview open as contextual details, not additional top-level destinations.

| Destination | Views | Purpose |
| --- | --- | --- |
| Home | Conversation | Ask, capture, see answers, and check today's summary and unread updates. |
| Money | Accounts, Projection, Schedules, Goals, People | Understand the financial picture and ask the agent about any item. |
| Activity | Ledger, Imports | Inspect recorded movements and review statements. |
| Settings | Profile, Categories, Notifications, Passkeys, Scheduled questions, Jobs, Backups, Export, Agent memory | Configure the instance, with operational tools grouped after everyday settings. |
| Outside the shell | Sign in, Set up | Unlock the instance or enroll a passkey. |

On desktop, the primary navigation is a labelled left rail. Money and Activity use a short, visible second-level tab row. Settings uses a local section list when there is enough space. On phones, primary destinations become a labelled bottom bar; section navigation wraps or uses an explicitly labelled selector, never a hidden off-screen tab. The current route and section stay clear. The assistant is available from a labelled **Ask** action on data pages, opening a full-height conversation on smaller screens; it never consumes the width needed for the task.

## Shared composition

The app keeps the quiet dark identity: true-black canvas, layered graphite surfaces, system type, crisp dividers, and a deliberate blue accent for primary actions, focus and the product mark. Green, amber and red communicate states; they are not decoration. Test text and controls against their actual backgrounds, including disabled and hover states. Content is left aligned in a comfortable reading width; page headers contain the page title, a brief useful explanation when needed, and one primary action. Information is grouped by meaning, not by equal-sized cards. Amounts use Indian formatting and tabular numerals. Lists retain a clear row identity and open details without making all content clickable.

The [proposed Fin-Agent mark](./fin-agent-mark.svg) replaces the temporary letter F in the design studies. Its two paths suggest a connected flow of money and evidence; the wordmark remains legible beside it. This is a design proposal to review at navigation, small icon and app-icon sizes, not a claim that the shipped logo has changed.

The **agent remains the main workspace**. On wide screens, Home pairs conversation with a compact Today column. Other pages give their content priority and open the agent on demand. The conversation has a persistent composer, attachment/photo and voice entry points, thread history, and a visible stop/retry state while a response is running. On a phone, Today is a labelled action opening a full-height overview; the composer remains reachable above the bottom bar and keyboard.

An answer shows its conclusion, supporting explanation, computed figures and assumptions in the conversation. The evidence view opens from **Sources**; on wide screens it occupies a contextual right panel, and on phones it opens a full-height sheet. It presents calculation/run, source names and meaningful facts first, with raw data only as a secondary disclosure. Closing it returns to the same point in the conversation. For a projection, show the probability above the configured floor, the lowest **median** balance and its date, the horizon, and payment/EMI assumptions. Clearly distinguish a probability from a guarantee and an account balance from money safe to spend. Show a missing floor or stale data rather than implying certainty.

## Page specifications

Each view has one main job. Tables below describe what earns space and the main action; lists collapse into readable rows on narrow screens. All data pages offer a contextual **Ask** action without changing the existing read-only Money model.

| View | Information and layout | Primary action and details |
| --- | --- | --- |
| **Home** | Conversation occupies the main column. Today contains cash on hand and scope, a 90-day projection preview, unread updates and a short recent list. Thread history is accessible from the conversation header. | Ask, attach a photo, or record voice. Open a result's sources without leaving the thread; open Today on phone. An empty conversation offers a few useful starting questions. |
| **Accounts** | Summary strip for cash on hand and the scope/as-of time; account rows grouped into bank/cash, cards, loans and assets. A card row distinguishes ledger and stated balances, their dates and any disagreement. | Open account details or Ask about the account. Never label liquid balance as safe-to-spend. |
| **Projection** | Current floor and horizon, readable projection chart with median/range and floor, then goals, assumptions and data-quality notes. A hypothetical purchase form is below the current picture. | Explore the projection; a purchase scenario clearly states payment method and EMI tenure, and offers sources. Missing floor and unsupported input have explicit guidance. |
| **Schedules** | Summary of expected incoming and outgoing amounts over the displayed period, followed by dated income, obligations and subscriptions. Show the period on the totals. | Ask about a schedule; choose the supported time range. |
| **Goals** | Summary of active goals, followed by each goal's name, target, current amount, deadline and progress. | Ask about a goal. Explain when a target or date is missing. |
| **People** | Separate amounts others owe the user, amounts the user owes, and settled items. Identify the person and currency amount on each row. | Ask about a person or settlement; display enough context to distinguish the two directions. |
| **Ledger** | Date/search/filter controls above a bounded movement list. Each row shows date, payee, category, amount and status; explicit previous/next controls use the available cursor. | Open Details for evidence and balanced transfers, or Ask about a movement. Keep filters and list status visible during refresh. |
| **Imports** | Upload/start and recent imports lead to the selected statement's status, counts, items needing decisions, then reviewed lines. Keep progress and any polling failure visible. | Resolve ambiguous matches and transfers, then commit or reject; show undo where supported. Do not let a persistent action conceal unresolved items. |
| **Profile** | Name and financial floor with concise explanation of what the floor means. | Save changes with field-level feedback. |
| **Categories** | Categories first; merchant rules below or in a clearly labelled subsection. | Edit supported categories/rules and explain any reducible setting in plain language. |
| **Notifications** | Notification preferences and permission state. Unread updates remain discoverable from Today and the full list. | Change preferences or enable notifications where supported. Read and resolved remain distinct. |
| **Passkeys** | Existing devices/passkeys, current session and clear enrollment guidance. | Add a passkey or remove an eligible one, with required re-authentication. |
| **Scheduled questions** | User-defined questions, schedule, enabled state and any spend limit. | Create/edit or enable/disable an existing scheduled question with input validation. |
| **Jobs** | Operational job status and recent results, grouped as advanced. | Inspect available job details and use only existing supported controls. |
| **Backups** | Backup status, last successful backup and recovery guidance; distinguish unavailable status from no backup. | Follow the supported backup/recovery action or instructions. |
| **Export** | Explain the available data formats and when to use each, with the required re-authentication made clear. | Choose and download CSV, JSON or database export. |
| **Agent memory** | Saved memory entries with type, readable content and scope. | Review and use supported management actions; provide a clear empty state. |
| **Sign in** | Fin-Agent name, a short explanation, one prominent passkey action, and relevant setup/recovery link. | Sign in with a passkey; offer actionable error recovery. |
| **Set up** | Explain device enrollment, origin/token requirements and what happens next in a short sequence. | Enroll a passkey; report expiry or origin problems without losing the user's place. |

## Responsive and state rules

- Desktop shows the full primary rail and uses the available content width; the assistant/evidence panel appears only when sufficient space remains. Tablet and phone use a full-height overlay for conversation or evidence, with a labelled close control and return focus. Phone layouts stack rows and summaries, keeping amounts and primary actions visible; large tables may scroll within a labelled table region, while page text never clips.
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

The [screen studies](./web-interface-screen-studies.html) show the chosen structure applied to representative Accounts, Projection, Ledger, Imports and Settings views. They are static design illustrations using fictional data, not a running app or API specification. The earlier [Home direction samples](./design-direction-samples.html) show the selected A composition and C evidence treatment. These patterns and the page table above cover the remaining views without a separate prototype for each one.

The detailed grouping of settings and any additional API fields are implementation decisions to check against existing behavior. Preserve existing supported actions, domain calculations, passkey/session behavior and undo. Do not touch the separate observability work.
