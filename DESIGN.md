---
name: Fin-Agent
description: A private, single-user money console in true black, built around the computed receipt.
colors:
  ground: "#000000"
  surface: "#1c1c1e"
  surface-raised: "#2a2a2c"
  hairline: "rgba(255,255,255,0.10)"
  ink: "#f5f5f7"
  ink-secondary: "#a1a1a6"
  ink-tertiary: "#8b8b90"
  action-blue: "#0b6cf0"
  positive: "#30d158"
  warning: "#ff9f0a"
  negative: "#ff453a"
typography:
  display:
    fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable", "Segoe UI", Roboto, sans-serif'
    fontSize: "34px"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.035em"
  headline:
    fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable", "Segoe UI", Roboto, sans-serif'
    fontSize: "20px"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-0.02em"
  body:
    fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable", "Segoe UI", Roboto, sans-serif'
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0em"
  label:
    fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable", "Segoe UI", Roboto, sans-serif'
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "0.01em"
  mono:
    fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace'
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.4
rounded:
  control: "10px"
  card: "18px"
  sheet: "24px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  xxl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.action-blue}"
    textColor: "#ffffff"
    rounded: "{rounded.control}"
    padding: "9px 16px"
  button-primary-hover:
    backgroundColor: "#2b82ff"
  button-ghost:
    backgroundColor: "rgba(118,118,128,0.16)"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "9px 12px"
  nav-item:
    backgroundColor: "transparent"
    textColor: "#d6d6da"
    rounded: "{rounded.control}"
    padding: "7px 10px"
  nav-item-active:
    backgroundColor: "rgba(118,118,128,0.24)"
    textColor: "#ffffff"
    rounded: "{rounded.control}"
    padding: "7px 10px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
    padding: "14px 16px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "9px 12px"
  badge:
    backgroundColor: "transparent"
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.pill}"
    padding: "2px 8px"
---

# Design System: Fin-Agent

## Overview

**Creative North Star: "The Quiet Ledger"**

Fin-Agent looks like a first-party system app, not a finance dashboard. The ground is true black,
surfaces are graphite, and the only saturated colours are the ones that mean something: blue for
action, green for a floor that holds, amber for money that is due, red for money that is not
there. Type is the platform's own face, so the interface feels installed rather than visited.

The product's distinctive surface is the **computed receipt**: a verdict, the probability that the
floor holds against its threshold, the lowest median with its date, the reason, the source count,
and one disclosure that opens the curve, tradeoffs, and the rows behind it. Everything else stays
conventional on purpose. There is no metaphor, no themed skin, no decorative texture, and no
gradient that is not data.

Density is moderate: one centred reading column, a persistent navigation rail, and generous space
between groups. Money is set in tabular numerals at display scale; prose never runs past 68
characters.

**Key Characteristics:**
- True-black ground; graphite surfaces; 1px hairline borders instead of shadows.
- System type (SF on Apple, Segoe on Windows) with tabular numerals.
- One action blue; semantic green, amber, red only where money state changes.
- The receipt is the signature component; the chat is the shell.
- Flat at rest; depth appears only on the composer and the phone sheet.

## Colors

A near-monochrome system with four semantic signals; colour never decorates.

### Primary
- **Action Blue** (#0b6cf0): every primary button, the selected horizon, the user's chat bubble,
  active focus. It is the only colour that means "press this". Hover lightens to #2b82ff.

### Secondary
- **Floor Green** (#30d158): a probability at or above threshold, a positive balance, a settled
  settlement. Never used for decoration or success confetti.
- **Due Amber** (#ff9f0a): money owed soon, a degraded data state, a warning, the purchase pin.
- **Overdraft Red** (#ff453a): a crossed floor, a negative balance, a destructive action.

### Neutral
- **Ground** (#000000): the page; never a card.
- **Graphite** (#1c1c1e): every raised surface — cards, composer, inputs.
- **Graphite Raised** (#2a2a2c): table headers, hover fills, the selected segment.
- **Ink** (#f5f5f7): primary text.
- **Ink Secondary** (#a1a1a6): labels, descriptions, placeholders.
- **Ink Tertiary** (#8b8b90): timestamps, serials, metadata; never below 12px.

### Named Rules
**The One Blue Rule.** Blue is reserved for actions and the user's own voice. If a screen has two
blues competing for attention, one of them is wrong.

**The Meaning Rule.** Green, amber, and red may only appear where they carry a money state. A
green heading, a red border for decoration, or an amber icon that means nothing is a failure.

## Typography

**Display Font:** system UI stack (SF Pro Text / Segoe UI Variable)
**Body Font:** the same stack
**Label/Mono Font:** system monospace for serials, SQL, and source IDs only

**Character:** the platform's own voice — quiet, exact, and familiar. No display face competes
with the numbers. Weight and size carry hierarchy; letter-spacing never goes below -0.035em.

### Hierarchy
- **Display** (700, 34px, 1.1, -0.035em): page titles and the spendable figure on phone; 46–52px
  for the hero figure at desktop widths.
- **Headline** (600, 20px, 1.25): panel sections and the receipt title.
- **Title** (600, 15px, 1.3): card titles, thread titles, account names.
- **Body** (400, 15px, 1.5, max 68ch): chat prose and panel descriptions.
- **Label** (500, 12px, 0.01em): nav groups, field labels, metadata; 11px is the floor for
  functional text.
- **Mono** (400, 12px): SQL, source IDs, serial-like identifiers only.

### Named Rules
**The 68ch Rule.** Assistant prose never exceeds 68 characters per line, even when the card beside
it is wider. Cards may be wide; reading is not.

## Layout

A 220px navigation rail holds the four areas. Home is the agent: a 900px conversation column with a
400px overview rail on the right at 1181px and wider, holding only the defaults (spendable,
projection, unread, recent, quick actions). On every other area the assistant occupies that 400px
rail, and panels sit inside a 1200px maximum. Below 1180px the overview becomes a Today sheet on
Home and the assistant becomes a full-height dialog elsewhere; below 900px the navigation becomes a
four-item bottom bar and the sidebar disappears.

Spacing follows a 4 / 8 / 12 / 16 / 24 / 32 rhythm. Tight groups sit inside cards; groups are
separated by 16–24px. More space sits above a heading than below it.

At 900px the phone top bar carries the Fin-Agent wordmark, the date, Today and Sign out on Home;
other areas carry their page title, Ask and Sign out. The assistant opens from Ask, and Home's
overview opens from Today; content clears the 60px bottom bar. The projection chart remains visible
at 84px in the overview; it is never dropped from the daily surface.

## Elevation & Depth

Flat by default. Depth is tonal: ground, graphite, graphite-raised, separated by 1px hairlines.
Shadows are not used to imply hierarchy; they appear only where a floating object needs separation
from the page: the composer (`0 12px 32px rgba(0,0,0,0.5)`) and the phone sheet
(`0 -20px 60px rgba(0,0,0,0.6)`).

### Shadow Vocabulary
- **Composer** (`0 12px 32px rgba(0,0,0,0.5)`): the one persistent floating input.
- **Bottom sheet** (`0 -20px 60px rgba(0,0,0,0.6)`): the phone area menu.

### Named Rules
**The Hairline Rule.** If a boundary needs to be visible, use a 1px `rgba(255,255,255,0.10)` line,
not a shadow. Shadows lift; borders separate.

## Shapes

Controls use a 10px radius; cards and the receipt use 18px; the phone sheet uses 24px on its top
corners only; pills and badges are fully round. The composer is a pill (22px). There are no
cut corners, no sharp industrial edges, and no nested cards: a card inside a card is a bug.

The chat is the one place with an asymmetric shape: the user's bubble is a 18/18/4/18 rounded
rectangle, the assistant has no bubble at all — prose sits directly on the ground and cards carry
the surface.

## Components

### Buttons
- **Shape:** 10px radius; 9px 16px padding; 14px/600.
- **Primary:** Action Blue with white text; hover #2b82ff; disabled 45% opacity.
- **Ghost:** `rgba(118,118,128,0.16)` fill, ink text; hover `0.28`; minimum 32px tall (40px on
  phone for capture controls).
- **Destructive:** red text or red-tinted fill only when the action reverses or deletes.

### Chips
- **Style:** transparent fill, hairline border, 12px/500 ink-secondary, fully round.
- **State:** selected uses Action Blue border and text; reducible categories use floor green.

### Cards / Containers
- **Corner Style:** 18px.
- **Background:** Graphite (#1c1c1e).
- **Border:** 1px `rgba(255,255,255,0.10)`; no shadow.
- **Internal Padding:** 14–16px; panels themselves are not cards.

### Inputs / Fields
- **Style:** Graphite fill, 1px hairline, 10px radius, 9px 12px padding, 14px text.
- **Focus:** border shifts to `rgba(90,169,255,0.65)` with a 3px `rgba(11,108,240,0.18)` ring.
- **Placeholder:** Ink Secondary (#a1a1a6), never tertiary.

### Navigation
- **Desktop:** 220px rail, four areas — Home, Money, Activity, Settings — 13.5px items, 17px
  Lucide one-stroke icons, active = 24% gray fill + white.
- **Phone:** a 60px bottom bar with four items and an 11px label; the assistant is not a tab.
- **Sub-navigation:** Money and Activity use underline tabs on a 1px rule (Radix Tabs), active text
  ink and a 2px ink indicator.
- **Assistant:** 400px rail at desktop; a full-height Radix Dialog below 1180px with a 36–40px
  header (History, New, Close). History is a 320px Radix Popover over the thread list.

### Queue Row
A notification in "Needs you". On Home it is compact: badge + time, title, and explicit
Open/Decide + Mark read buttons — the body lives in the chat it opens. In the "All notifications"
dialog the body is shown. The whole card is a pointer target; keyboard access is the buttons, so
the card carries no `role="button"` and no keydown handler.

### Summary Strip
The shared figure block: one surface, 1px dividers, label over value over a one-line note. Used by
Accounts, Schedules, Goals, People and an import review. It replaces KPI tiles; it never carries an
icon, a sparkline, or a delta it cannot source.

### Settings Workspace
A 220px vertical Radix tab nav on desktop, a horizontal scroll strip on phone; nine sections, each
one view, deep-linked as `#settings/<section>`. Section content is a single card per section.

### Import Flow
Upload card → a history row of import chips → the selected import's review: summary strip (Rows,
New, Matched, Ambiguous, Logged elsewhere), the lines table, ambiguous lines and own-account
transfers, then commit/reject. Full-width review; history wraps above it.

### Movement Dialog
A Radix Dialog from a Ledger row's Details button: date, kind, category, payee, amount, state,
evidence, the balanced transfers, and Ask about this.

### Loading
One library, three shapes, all from `react-spinners`. A panel's first load is `Loading`: a 14px
`ClipLoader` in the text colour and a sentence saying what is loading. A button whose label already
changed (Saving…, Opening…, Refreshing…) carries `Busy`, the 12px version. A streaming turn uses
`Typing`, three 5px `PulseLoader` dots in the "Replying…" header. No hand-drawn spinners; the old
Home skeleton is gone.

### Bottom Bar
Phones only: 60px plus safe-area padding, four items, 20px icons, 11px labels, active in ink, unread
count on Home. It sits under the assistant dialog, never over content.

### [Signature Component] The Receipt
The projection card. Header carries the horizon, source count, and run count. Body carries the
verdict pill, `floor probability` against `threshold`, the lowest median with its date, the reason,
and one disclosure: **Open the curve, tradeoffs, and sources**. Sources are buttons; opening one
shows a readable fact list (id, kind, name, amount, date) with the raw row behind a second
disclosure. No figure in a receipt is allowed to be uncited.

### Structured Answers

An answer composes backend result references into summaries, semantic tables, charts, and existing
projection receipts. Answers read as a compact financial report on the true-black ground, not as a
stack of cards: sections are unboxed, and a single hairline separates distinct sections. One result
has one title, one primary value where a summary leads, and one quiet Sources disclosure; a table
or summary is the primary view, never both repeating the same heading. Tables show ten rows
initially and expand to the saved limit, with subdued column heads, row rules, and tabular numerals.
Contain horizontal table scroll inside the answer on phones. Unknown values stay explicit,
incomplete coverage is visible in amber beside the values, and source assumptions and filters remain
inspectable. A missing floor suppresses its probability in both a summary and a receipt.

Routine reads in one assistant message, including legacy read-only SQL cards, collect into one
closed **Supporting data** disclosure with a read count. A valid composed answer hides only the
result views it already shows; unrelated or malformed fallback data stays available in that same
disclosure. Capture, import, undo, movement, approval, chart, and projection cards never enter it.
Invalid historical widgets show a local recovery message while the conversation and composer remain
usable.

## Do's and Don'ts

### Do:
- **Do** keep the ground true black and put every surface on Graphite with a 1px hairline.
- **Do** set every amount in tabular numerals at the largest size the hierarchy allows.
- **Do** show the verdict, the probability, the floor, and the source count before any explanation.
- **Do** lead every Money or Activity page with its summary strip, then the rows.
- **Do** keep the Home queue compact and put the detail in the chat or the full dialog.
- **Do** make Home the conversation; the overview rail carries only defaults.
- **Do** let every analytics question start in the chat, not in a dashboard first.
- **Do** use explicit buttons for every action inside a clickable row; the card itself is pointer-only.
- **Do** give every row an Ask action that seeds the assistant with that object's name.
- **Do** headline a row with the human payee; keep the bank narration as statement text.
- **Do** say "No floor set" and link to Settings instead of reporting a probability against ₹0.
- **Do** show a card's stated balance beside the ledger balance, and say when they disagree.
- **Do** keep routine reads in one closed Supporting data disclosure and put one title and one Sources disclosure on each result.
- **Do** cap long statement tables at 50 rows with a Show all control.
- **Do** offer only horizons the engine honors; the projection's floor is 90 days.
- **Do** use the platform's system font; never ship a display face for the interface.

### Don't:
- **Don't** nest a button inside a card that also owns a keyboard handler; the child's key event
  will be swallowed.
- **Don't** nest cards. The assistant's surface is the page; the card is the only raised object.
- **Don't** rebuild KPI tiles, hero headers, or icon circles; the summary strip is the pattern.
- **Don't** add a kicker or eyebrow above a heading; the heading carries its own weight.
- **Don't** use blue for anything that is not actionable or the user's own voice.
- **Don't** hide a destination behind horizontal scrolling on phone; the bottom bar carries all four.
- **Don't** add a gradient, glass, texture, or themed motif that is not data.
- **Don't** state a number in prose without its receipt or its source chip.
- **Don't** let a receipt's detail view become raw JSON as the first layer.
