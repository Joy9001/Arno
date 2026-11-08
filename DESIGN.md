---
name: Fin-Agent
description: An agent-first money interface with an iridescent presence and computed receipts.
colors:
  ground: "#0e0f0c"
  surface: "#171814"
  surface-raised: "#22241e"
  surface-high: "#2c2f27"
  hairline: "rgba(255, 255, 255, 0.08)"
  ink: "#f6f7f0"
  ink-secondary: "#a3a79a"
  ink-tertiary: "#82867a"
  action-lime: "#d4ff3a"
  action-hover: "#e2ff74"
  lime-ink: "#111209"
  user-cobalt: "#2b59ff"
  teal: "#1ec8a5"
  positive: "#3ee07f"
  warning: "#ff8a3d"
  due: "#ff6a2b"
  sun: "#ffd23a"
  negative: "#ff5a4f"
typography:
  display:
    fontFamily: '"Bricolage Grotesque", ui-sans-serif, system-ui, sans-serif'
    fontSize: "58px"
    fontWeight: 800
    lineHeight: 1.05
    letterSpacing: "-0.04em"
  headline:
    fontFamily: '"Bricolage Grotesque", ui-sans-serif, system-ui, sans-serif'
    fontSize: "28px"
    fontWeight: 800
    lineHeight: 1.15
    letterSpacing: "-0.03em"
  body:
    fontFamily: '"Bricolage Grotesque", ui-sans-serif, system-ui, sans-serif'
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: '"Bricolage Grotesque", ui-sans-serif, system-ui, sans-serif'
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.45
  mono:
    fontFamily: '"JetBrains Mono", "Bricolage Grotesque", ui-monospace, monospace'
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.45
rounded:
  control: "14px"
  card: "24px"
  tile: "28px"
  goal: "30px"
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
    backgroundColor: "{colors.action-lime}"
    textColor: "{colors.lime-ink}"
    rounded: "{rounded.pill}"
    padding: "8px 15px"
  button-primary-hover:
    backgroundColor: "{colors.action-hover}"
  button-secondary:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "8px 15px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.tile}"
    padding: "24px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "9px 12px"
  badge:
    textColor: "{colors.ink-secondary}"
    rounded: "{rounded.pill}"
    padding: "2px 8px"
---

# Design system: Fin-Agent

## Overview

**Creative North Star: "The living agent"**

The agent is the product; money is what it works on. An iridescent orb, a generous composer,
confident typography, and computed verdicts give the agent a recognizable presence. The dark
olive ground holds a layered, expressive interface that stays precise about financial evidence.

The November 8 direction replaces the earlier Quiet Ledger system. The built source is in
`web/src/styles.css`, `shell.css`, `aui.css`, and the panel stylesheets. The surface brief owns
composition; this file records the reusable visual system.

**Key Characteristics:**

- Near-black olive ground and three tonal surfaces.
- Self-hosted Bricolage Grotesque, with JetBrains Mono for identifiers and measurements.
- Lime actions and verdicts, cobalt user messages, mesh figures, and one iridescent orb.
- Large rounded objects with readable evidence and explicit unknown states.
- Conversation remains mounted while the user explores their money.

## Colors

Lime carries the agent's action; cobalt carries the user's voice. Teal connects them in the
headline mesh. Positive, due, warning, and negative states keep their own semantic colors.

The `--mesh` radial gradient moves through lime, soft green, teal, and cobalt. It appears on the
lead summary card and receivables; those surfaces use dark text. The `--iris` conic gradient is
the orb's material. Neither gradient encodes a financial probability.

**The two voices rule.** Primary actions use lime with dark ink; user message bubbles use cobalt
with white text. Blue-looking compatibility variables such as `--blue` resolve to lime.

**The evidence rule.** Color can emphasize a result, but its amount, direction, date, and uncertainty
must remain readable in text. Cash on hand never means money free to spend.

## Typography

Bricolage Grotesque supplies the interface, including large figures. Its heavy weights and tight
tracking give answers their presence. JetBrains Mono is for serials, dates in dense lists, code,
and machine text; Bricolage is its rupee-glyph fallback. Fonts ship locally under `public/fonts`.

The Ask greeting reaches display size, area headings use 28–32px, tile titles use 23–26px, and
body copy is 15px. Canvas conclusions can reach 80px. Amounts use tabular numerals and Indian
grouping; long names wrap. Body explanations stay near 65–75 characters per line.

**The type floor rule.** Use weight and size to create hierarchy. Keep new tracking at or above
-0.04em; smaller legacy label styles are not a template for future body text.

## Layout

An 84px icon rail frames desktop content. Six destinations share the frame: Ask, Chats, Money,
Activity, Recaps, and Settings. Data pages use 32px gutters and their own scroll container.
Area headings and pill tabs stay visible while their content scrolls.

Chats adds a conversation list and an optional canvas. Above 1180px the canvas sits beside the
conversation, with a 420px minimum width; at 1180px and below it becomes a full-height sheet.
At 900px and below, the rail gives way to a five-item bottom bar, and page gutters become 16px.
Phone content reserves the bar's height and safe area. Wide tables scroll inside their region.

Spacing follows 4 / 8 / 12 / 16 / 24 / 32px steps. Tile gaps are 16px; sections have more space
above their heading than below it. Account grids retain object-sized columns with sparse data.
Goal tiles and People groups adapt to their content; phone layouts stack.

## Elevation & Depth

Tonal surfaces provide most separation. Hairlines divide rows, table sections, and the rail.
Shadows lift the composer, dialogs, and sheets. The orb has its own bounded glow; keep it tied to
agent presence rather than adding unrelated halos to controls.

Hover and focus respond quickly. The orb spins and breathes when the agent is present or working;
the thinking state runs faster. Reduced motion stops the orb and removes arrival transitions.
Selection, caret, scrollbars, and focus rings derive from the same palette.

## Shapes

Cards use 20–30px corners, goal tiles use 30px, and primary controls are pills. Small fields use
14px corners. The cobalt user bubble has a tighter lower corner. Lucide supplies functional
icons, while SVG geometry draws rings, timelines, and charts.

## Components

### Orb and Ask actions

One iridescent orb represents the agent. The rail mark and Ask hero can animate; static agent
notes use a still orb. Contextual Ask pills carry a small iridescent dot and seed the composer
without submitting the question.

### Buttons, inputs, and navigation

Primary pills use lime and dark ink. Secondary pills use raised olive surfaces and light ink.
Disabled controls remain identifiable. Focus uses a visible lime outline. Inputs retain labels,
readable placeholders, field errors, and native keyboard behavior. Radix supplies accessible
dialogs, tabs, confirmations, and related control behavior.

### Summary cards and money objects

A summary uses separate rounded cells when aggregate figures earn space; its lead cell carries
the mesh. Accounts put cash on hand directly in the banking grid, Goals lead with their rings,
and People keep totals unboxed so their balances appear early. Semantic colors must yield to
dark ink on mesh. Account tiles show actual ledger and statement facts;
credit cards use cobalt or orange faces. People tiles show who owes whom, with mesh receivables
and orange payables. Goals use SVG progress rings and projection-backed dates. A calendar strip
marks incoming and outgoing scheduled events without claiming payment.

### Receipt and canvas

The computed receipt is the signature answer: a lime verdict, probability against the threshold,
lowest median with its date, and source count. Its disclosures lead to the curve, tradeoffs, and
readable rows. The canvas enlarges the conclusion with mesh type, measured facts, a median curve
and P10–P90 range, source chips, assumptions, and quality notes. Source chips sit next to the
conclusion facts, before the curve and assumptions. Raw rows remain a second layer.
Unknown starting balances suppress forecasts; an unset floor suppresses its probability.

### Activity and memory

The uncategorized queue uses a restrained yellow surface with bounded preview rows. A movement
opens a rounded side drawer with a prominent amount, evidence, and balanced transfers. Imports
start with a dashed upload surface, followed by history and review controls. The memory workspace
has an orb introduction, preference chips, and explicit edit, enable/disable, and delete actions.

### Loading, empty, and error states

Layout-shaped skeletons reserve space on first load. Refreshes retain existing data. Empty panels
explain what is missing and offer an appropriate Ask action. Errors stay beside the affected
section with recovery controls. A missing balance, target, date, or forecast stays explicit.

## Do's and Don'ts

- Do keep the orb as the agent's single visual presence.
- Do pair every displayed amount with its meaning and source.
- Do use dark ink on lime and mesh surfaces.
- Do preserve keyboard focus, native controls, and reduced-motion behavior.
- Do distinguish a projection range from individual simulated runs.
- Don't invent balances, completion dates, payment states, or memory types.
- Don't add lasting writes or submit chat prompts as a styling shortcut.
- Don't put real financial data in committed mockups or fixtures.
- Don't restore the superseded system-font, blue-action, four-area design.
- Don't add kicker labels above headings or decorative side stripes.
