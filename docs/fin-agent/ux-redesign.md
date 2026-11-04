# Web app UX redesign

Status: shipped. The ten-tab shell is gone; four areas plus a global assistant are live.

## Outcome

- **Home**: the agent interface. The conversation fills the main column; a 400px overview rail holds
  the defaults from one `GET /api/home` payload (spendable, projection and its 90d/1y curve, the two
  newest unread needs, three recent movements) plus the quick actions. Settings and Money analytics
  are pages, not the Home. Below 1180 the overview is a Today sheet.
  Queue rows are compact (body in the chat/dialog), keyboard-safe buttons (Open / Decide / Mark read),
  and a View all dialog over `/api/notifications?limit=100`.
- **Money**: Accounts, Projection, Schedules, Goals, People under Radix Tabs; Projection is reachable
  from Home's curve.
- **Activity**: Ledger with 50-row cursor pagination, Load more, From/To, kind, reversed, total, Retry,
  and a movement detail dialog with transfers, evidence and Ask; Imports is upload → history → review.
- **Settings**: nine sections in a vertical Radix tab nav — Profile & floor, Categories, Notifications,
  Passkeys, Scheduled questions, Jobs, Backups, Export, Agent memory. Deep-linked by hash.
- **Assistant**: Radix Dialog sheet below 1180px, static rail above; History is a Radix Popover over
  the thread list; quick actions seed the composer and focus it; seeds are cleared after use; Retry
  regenerates; the closed receipt does not mount its chart.
- **Auth**: sign-in and setup share the product's mark, one headline, one action.
- **Libraries**: Radix Dialog/Tabs/Popover for focus, escape, and keyboard; Lucide for icons; the
  visual system remains the committed Apple-quiet canon.
- **Routing**: hash history for areas and sub-tabs (`#money/projection`, `#settings/categories`), so
  back leaves a page instead of the PWA.

Verified with `pnpm build`, `impeccable detect` (`[]`), `inspect.mjs --app` on Home, Projection,
Settings and Imports (0 FAIL each; 2 defended WARNs: the brand wordmark and alpha borders read as
opaque), and fresh UX review passes ending in **ship**.

## Remainder

- A receipt source chip still opens an account/schedule/goal row; the new movement dialog is reached
  from Activity, not yet linked from a receipt.
- A sticky ledger header on long lists.
- The assistant's number check can flag a balance it read from the snapshot; the note now says what
  that means instead of asking for a statement.

## Why

The old web app had ten equal destinations: Chat, Inbox, Ledger, Imports, Accounts, Schedules,
Goals, Projection, People, Settings. That is a sitemap, not a product. The assistant — the only
reason the product exists — was one tab among ten, so every question meant leaving the page you
were reading. Inbox was a destination rather than a queue. Projection existed twice (the chat
header and its own page) and could disagree with itself. Imports was a page even though it is a
flow: upload → parse → review → commit.

## The four areas

| Area | Question it answers | Contains |
|---|---|---|
| **Home** | What needs me today, and can I afford this? | Spendable + floor + the 90-day curve, quick capture, the needs-you queue, recent movements. |
| **Money** | What do I have, owe, plan, and where is it going? | Accounts, People, Goals, Schedules, Projection. |
| **Activity** | What happened, and how do I get it in? | Ledger (with load-more), Imports (a flow). |
| **Settings** | How is this instance set up? | Profile, categories, passkeys, notifications, jobs, backups, export. |

The assistant is not an area. It is a **global rail** on wide screens and a **full-height sheet**
below 1180px. It keeps its own thread, can be seeded from any screen, and is where every write
happens.

## Flows

1. **Ask about this.** Open the assistant from the rail or the Ask button; quick actions seed the
   composer ("Log a spend: ", "Add income: "). The answer is a computed receipt: verdict,
   probability against threshold, lowest median, sources.
2. **Deal with what needs you.** Home lists the four newest notifications. Tapping one opens a chat
   seeded with it; the queue item is marked read. No separate Inbox page.
3. **Capture.** Quick actions on Home seed the assistant; voice and photo stay in the composer.
4. **Review activity.** Activity → Ledger is the record, with columns, filters, and load-more.
   Activity → Imports is the import flow, with review and commit.
5. **Understand the plan.** Money → Accounts / People / Goals / Schedules / Projection share one
   sub-navigation; Projection is no longer a top-level page.
6. **Configure.** Settings keeps its sections.

## Screens

### Home (1440)

Home is the agent. The conversation fills the main column; the overview is a 400px rail, not the
other way round.

- **Conversation**: the current thread, History/New, the composer, and the empty state ("Ask
  anything about your money"). Quick actions seed this composer.
- **Overview rail**: Spendable, the floor state and the 90-day curve with 90d/1y (the engine never
  returns less than 90 days), "Open projection"; the four quick actions; the two newest unread
  needs with View all; the three newest movements with View ledger.
- The topbar is only the date and Sign out on Home; the thread title is the page's heading.
- Below 1180 the rail is a "Today" sheet from the topbar, and the conversation keeps the screen.

### Money

Underline sub-tabs; the page title is "Money". Panels keep their own section headings. The
Projection panel is the full form and curve.

### Activity

Underline sub-tabs Ledger | Imports. Ledger gains load-more (cursor), Imports stays the flow.

### Settings

The existing settings panel, unchanged in content.

### Assistant

Header: "Assistant", the active thread title, History (a popover with search and the thread list),
New, and Close in sheet mode. The conversation and composer are the existing chat, compact: no
Today strip, no duplicate header. Quick-action seeds land in the composer.

## API contract

New `GET /api/home?horizon_days=90`, one composed payload, so every figure on Home comes from one
computation and cannot disagree:

```json
{
  "as_of": "2026-11-04",
  "horizon_days": 90,
  "spendable": { "paise": 18420000, "text": "₹1,84,200" },
  "floor": { "paise": 2500000, "text": "₹25,000" },
  "floor_probability": 0.91,
  "floor_threshold": 0.9,
  "median_low": { "date": "2026-12-02", "paise": 9600000, "text": "₹96,000" },
  "curve": [{ "date": "…", "median_paise": 18420000, "p10_paise": 16000000, "p90_paise": 20500000 }],
  "unread_count": 3,
  "needs_you": [ /* NotificationItem, newest four */ ],
  "recent": [ /* Movement, newest five */ ]
}
```

Existing endpoints stay for the other areas. Ledger already returns `next_cursor`; the client adds
"Load more" rather than a new contract.

## States

- Home: loading skeleton with the same height as the loaded block; error with Retry; empty recent.
- Needs you: empty state "Nothing needs you."
- Assistant: loading threads, loading conversation, error + retry, busy "Replying…" with Stop.
- Money/Activity: each panel keeps its loading, error, and empty states; Ledger has Retry and
  load-more; Imports keeps the review/commit states.
- Settings: existing states.

## Mobile (≤900)

- Sidebar hidden; four-item bottom tab bar (Home, Money, Activity, Settings), 60px + safe area.
- Assistant is a full-height sheet from the Ask button; the backdrop closes it; Escape closes it.
- Home stacks: Today, quick actions, needs you, recent.
- Money/Activity sub-tabs scroll horizontally.
- Tap targets: 40px minimum for quick actions, tab items, and composer actions.

## Build order

1. Shell: four areas, assistant rail/sheet, bottom tabs. (done)
2. Home + assistant refactor. (done)
3. Money and Activity sub-tabs. (done)
4. `/api/home` composed endpoint. (done)
5. Ledger load-more, states, mobile pass. (done)
6. Run the real API from source, screenshot, review, fix. (done)

## Real-data pass

Checked in Chrome against the live instance with a real passkey session. What the demo payload hid:

- Movement headlines were raw bank narrations (`WDL TFR UPI/DR/… AT …`). Ledger, Home and the
  movement dialog now lead with the payee, or with the two accounts for a transfer, and keep the
  narration as statement text in the dialog.
- No floor is configured, and the projection reported `0% above floor` against ₹0. Home and
  Projection now read `No floor set`, drop the floor line, and link to Settings → Profile. The
  projection payload gained `floor_set`; the floor badge is green only when the probability clears
  the threshold.
- A card balance can be positive. It reads `Credit ₹X` instead of masquerading as money owed, and
  the stated balance now shows beside its age.
- A real import is 939 rows. The review caps the table at 50 with `Show all N rows`, and the
  ambiguous/transfer work sits above it instead of a table-length below.
- Empty Schedules, Goals and People no longer show a strip of ₹0 totals.
- Projection's data-quality list is collapsed (`19 notes`) instead of an open wall above Sources.

## Broken windows closed

A second pass fixed the defects the real payload exposed:

- Card ledger and statement can disagree. The row labels a positive balance `Credit`, shows the
  stated balance with its age, and says so when the two point different ways.
- The floor can be cleared. `0` means no floor; the field accepts blank, and Settings says so.
- The learned-rules table is sorted by hits and capped at 50 (357 rules existed), with Show all.
- Import match badges read `New`/`Matched`, not `new`/`certain`, and cancel a 939-row render.
- The projection's data-quality copy says `1 real spending day`, uses rupees per day, and starts
  its verdict reasons with a capital.
- Jobs' retry count is labelled `Retries`, not `Attempts`.
- A 30-day horizon was offered but the engine never returns less than 90. The control is gone and
  the custom horizon starts at 90.
- Purchase-date defaults and the Schedules 30-day cutoff use the local calendar day, not UTC.
- The untraced-number note says what it means and stops asking the user to supply a statement.
- The assistant replies in the language the user just used; a `hey` no longer gets Hinglish.
- `THIRD_PARTY_NOTICES.md` names Radix and Lucide, and the license audit understands `0BSD`.


## Open decisions

- Whether the assistant rail can collapse on desktop. Deferred; it is the product's primary surface
  and stays visible.
- Whether a committed import should keep its full table at all, now that the ledger holds the rows.
