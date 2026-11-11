# Consumer personal finance agents, 2026

What seven shipping products claim to do with a person's money, what their own documentation
proves, and which capabilities a best-in-class personal finance agent should hold. Products:
Monarch Money, Copilot Money, YNAB, PocketSmith, Origin, Rocket Money, and Cleo. Every claim comes
from a vendor help or product page opened on November 11, 2026. Where a page shows a "last updated"
date, that date is given rather than a launch date.

Parent review on November 12, 2026 corrected geographic overclaims and distinguished implemented
Arno jobs from uncompleted live acceptance checks. Manual entry and unsupported bank linking are
different conditions. Origin's documented deterministic engines also mean this survey cannot
establish that tool-computed finance is unique to Arno.

Evidence rule: a capability counts as documented only when a help page describes what a user would
see. Launch posts and feature pages are marked as marketing, never as proof of quality. No vendor's
accuracy, reliability, or user numbers are repeated as fact.

## What the market ships

The table records the named capability, the job a user would hire it for, where it works, and how
solid the evidence is.

| Product | Named capability | Example user job | Availability and data limits | Status | Evidence |
|---|---|---|---|---|---|
| Monarch | AI Assistant, Insights, Weekly Recap | "Why did my net worth change this month?" | US and Canada, USD or CAD; Plaid, MX, or Finicity | Generally available | [AI in Monarch](https://help.monarch.com/hc/en-us/articles/37526856682260-AI-in-Monarch), [FAQs](https://help.monarch.com/hc/en-us/articles/19985735202068-Monarch-FAQs) |
| Monarch | Forecasting, scenarios, chance of success | "What if I retire at 60, not 65?" | Plus tier, web app only | Generally available; Monte Carlo rolling out | [Forecasting](https://help.monarch.com/hc/en-us/articles/48344305092244-Forecasting-in-Monarch), [Scenarios](https://help.monarch.com/hc/en-us/articles/55286775198996-Using-Forecasting-Scenarios) |
| Copilot | Money Assistant: chat, proactive fixes, in-chat edits | "Rename all VZWRLSS transactions as Verizon" | iOS only; US institutions and USD only | Generally available since Oct 27, 2026; earlier beta | [Assistant overview](https://help.copilot.money/en/articles/14462982-money-assistant-overview), [Launch post](https://www.copilot.money/dispatch/meet-your-money-assistant) |
| Copilot | Copilot Intelligence: per-user categorizer | "Sort this merchant the way I sorted it last time" | Starts after 30 reviewed transactions | Generally available since 2023 | [Copilot Intelligence](https://help.copilot.money/en/articles/8182433-copilot-intelligence-for-spending) |
| YNAB | Targets with cadence and funding behavior | "Put aside $50 a month for next year's taxes" | Import in US, Canada, UK, selected Europe; manual entry elsewhere | Generally available | [Targets](https://support.ynab.com/en_us/how-to-use-targets-rk5kkI9ks), [Bank list](https://support.ynab.com/en_us/my-bank-isnt-listed-HJivlLavle) |
| PocketSmith | Forecast scenarios with daily closing balances | "What does a second car do to June's balance?" | Global feeds, plan-limited by country; multi-currency | Generally available | [Scenarios](https://learn.pocketsmith.com/article/1248-everything-you-need-to-know-about-scenarios-in-pocketsmith) |
| Origin | AI Advisor, Financial Health Score, Actions | "What if I buy a home in 2027?" | Mobile US only; account linking US-based users | Generally available, plan or employer gated | [AI Advisor](https://support.useorigin.com/hc/en-us/articles/39419419459085-What-is-the-AI-Advisor), [International](https://support.useorigin.com/hc/en-us/articles/41297663347597-Is-the-mobile-app-available-internationally) |
| Rocket Money | Rowan, subscription detection, cancellation | "Text me which subscriptions went up" | US residents, US banks, US mobile number with RCS | Generally available for Premium+ | [Rowan](https://help.rocketmoney.com/en/articles/16760933-what-is-rowan), [International banks](https://help.rocketmoney.com/en/articles/79778-does-rocket-money-support-international-banks) |
| Cleo | Chat budget, bills, spend limit | "What's left in my budget this week?" | US and UK; UK features reduced | Generally available; UK partial | [Budget](https://web.meetcleo.com/faqs/en/articles/12982480-budget-faqs), [UK availability](https://web.meetcleo.com/faqs/en/articles/12992410-what-s-available-for-cleo-customers-in-the-uk) |

### What each product actually does

**Monarch Money.** The Assistant answers questions over the user's own transactions and explains the
app; it cannot move money, pay bills, or trade, and "always asks before changing anything"
([AI in Monarch](https://help.monarch.com/hc/en-us/articles/37526856682260-AI-in-Monarch)).
Forecasting is the substantial artifact: a twelve-month baseline, per-account growth and withdrawal
assumptions, life events, saved scenarios, and a Monte Carlo chance of success
([Forecasting](https://help.monarch.com/hc/en-us/articles/48344305092244-Forecasting-in-Monarch)).
Monarch calls it "directional accuracy, not false precision".

**Copilot Money.** Two systems share a name. Copilot Intelligence is a per-user categorizer trained
on reviewed transactions that declines to predict when confidence is low ([Copilot Intelligence](https://help.copilot.money/en/articles/8182433-copilot-intelligence-for-spending)).
The Money Assistant is the agent: tab-aware chat, in-chat create and edit for categories,
transactions, budgets, rules, and recurrings, standing instructions, a daily briefing, and charts
([Assistant overview](https://help.copilot.money/en/articles/14462982-money-assistant-overview)). It
is iOS-only, and it checks with the user before changes
([Settings](https://help.copilot.money/en/articles/17678669-money-assistant-settings)).

**YNAB.** Targets are the most carefully specified planning primitive here: four cadences crossed
with three funding behaviors, underfunded warnings, progress bars, and snoozing
([Targets](https://support.ynab.com/en_us/how-to-use-targets-rk5kkI9ks)). Targets are created from
90 days of spending at mobile onboarding only, not for later plans ([Automatic targets](https://support.ynab.com/en_us/does-ynab-create-targets-automatically-HJ4k1nBWfg)). "Cost
to Be Me" totals the plan against expected income ([Edit Plan](https://support.ynab.com/en_us/plan-and-adjust-with-edit-plan-and-cost-to-be-me-ByR7vpqPyx)).
Two absences matter: no biweekly target, and editing a target rewrites past and future months.

**PocketSmith.** A scenario is a set of scheduled budgets attached to an account, producing a daily
closing-balance forecast; secondary scenarios are additive or subtractive and can be toggled off, so
they behave as true what-if layers
([Scenarios](https://learn.pocketsmith.com/article/1248-everything-you-need-to-know-about-scenarios-in-pocketsmith)).
The forecast recalculates from the current balance each day
([Calendar](https://learn.pocketsmith.com/article/1246-using-the-calendar-and-forecast-graph)).
Feeds and offline accounts coexist, and accounts can hold different currencies, though one account
cannot mix them ([Feeds](https://learn.pocketsmith.com/article/500-feeds),
[Currency](https://learn.pocketsmith.com/article/1306-currency-settings-in-pocketsmith)). The "60
years" and "accurate to the day" claims are marketing copy, not help documentation.

**Origin.** The AI Advisor is conversational and proactive: "dozens of expert-built checks" across
spending, cash flow, savings, investments, retirement, credit, protection, housing, education, and
estate planning, surfaced as prioritized Actions with reasoning ([Introducing AI Advisor](https://support.useorigin.com/hc/en-us/articles/39417344659341-Introducing-AI-Advisor-Your-Personal-Financial-Guide)).
A help page describes routing questions to specialist agents and using "deterministic math engines"
for calculations ([How AI Advisor analyzes](https://support.useorigin.com/hc/en-us/articles/20957679919629)). Forecasting models life
events and reports a success rate from thousands of simulated outcomes ([Forecasting tool](https://support.useorigin.com/hc/en-us/articles/36002288809101-What-is-Origin-s-forecasting-tool-and-how-can-it-help-me)).
Savings goals are explicitly workarounds with no delivery date ([Savings goals](https://support.useorigin.com/hc/en-us/articles/37108744089229-How-to-set-and-track-savings-goals)).
Origin publishes a CFP-style benchmark claiming it beat seven models
([Accuracy](https://support.useorigin.com/hc/en-us/articles/39416756786061-How-accurate-is-AI-Advisor));
that is a self-assessment, not independent evidence.

**Rocket Money.** Detection is the product: recurring charges inferred from linked transactions,
shown as all, upcoming, or calendar views with an inactive list, and manual additions available on
mobile ([Bills and subscriptions](https://help.rocketmoney.com/en/articles/2185531-managing-your-bills-and-subscriptions)).
Rowan is a text-message agent that answers spending questions, edits categories, reviews budgets,
and can start cancellation or negotiation requests
([Rowan](https://help.rocketmoney.com/en/articles/16760933-what-is-rowan)). Access is strict:
Premium+, US number, RCS, no VoIP numbers, and no Spectrum Mobile, Xfinity Mobile, or Google Fi on
iPhone.

**Cleo.** Chat-first budgeting: income minus bills yields a spend limit broken down per day, with a
"Budget Rewind" of the last cycle's habits ([Budget setup](https://intercom-help.meetcleo.com/en/articles/5850537-how-do-i-set-up-a-budget-and-how-does-it-work)).
The UK relaunch lists chat, budget, roast, voice, early income, and a spending roadmap, while
subscriptions, credit scores, debt reset, savings, and the card are absent ([UK availability](https://web.meetcleo.com/faqs/en/articles/12992410-what-s-available-for-cleo-customers-in-the-uk)).
Cleo's own limitations page concedes it cannot hold assets, pensions, or tax help, and cannot
remember details for later ([What can't Cleo do?](https://intercom-help.meetcleo.com/en/articles/8124868-what-can-t-cleo-do)).

## Candidate capability requirements

Each requirement is labeled _universal_ when any credible personal finance agent needs the outcome,
or _method_ when it is one good way to reach it. US-only capabilities are separated at the end of
the section. Arno status is read from `plan.md` §2 and `src/arno/agent/toolset.py`, not from this
research.

1. **Complete financial picture: universal.** Products gather account information; Monarch's
   forecast needs accounts, liabilities, and goals in one graph
   ([Forecasting](https://help.monarch.com/hc/en-us/articles/48344305092244-Forecasting-in-Monarch)).
   Acceptance: a user with two banks, two cards, one loan, and a wallet asks "what do I owe this
   month" and gets one reconciled total with per-account due dates. Tradeoff: completeness needs an
   ingestion path for every account type. Arno: account-type support exists through
   `get_financial_context`; actual completeness depends on the user's records and coverage.

2. **Forward projection against a floor: universal.** Monarch projects net worth and yearly cash
   flow, PocketSmith projects daily balances, and Origin reports a success rate
   ([Origin](https://support.useorigin.com/hc/en-us/articles/36002288809101-What-is-Origin-s-forecasting-tool-and-how-can-it-help-me)).
   No page here names a floor; Monarch's nearest analog is an ending net-worth target. Acceptance:
   with a ₹40,000 floor and salary on the 1st, "balance on the 28th" returns a median, a band, and
   the probability of crossing the floor. Tradeoff: a band invites false precision and needs a
   backtest. Arno: implemented; `project()` returns a median band and floor probability, while the
   backtest (milestone 29) is still open per `plan.md` §5.

3. **Affordability verdict computed in code: universal.** Copilot's prompt library invites "Can I
   afford to move to New York City next month?" ([Prompt ideas](https://help.copilot.money/en/articles/16666459-prompt-ideas)), but the help pages
   describe an assistant, not a verdict
   ([Overview](https://help.copilot.money/en/articles/14462982-money-assistant-overview)).
   Acceptance: "Can I buy a ₹85,000 laptop on EMI in March" returns yes, yes-but, or no with the
   tradeoff that changed the answer. Tradeoff: a verdict must refuse on stale or unknown data, which
   is less satisfying than a confident guess. Arno: covered; `project()` takes a hypothetical
   purchase, and `plan.md` §2 forbids the model from issuing a verdict.

4. **Debt modeling with payoff tradeoffs: universal.** Monarch pays liabilities at their rates and
   drops the payment from expenses once cleared, with loan interest and payment assumptions set per
   account ([Forecasting](https://help.monarch.com/hc/en-us/articles/48344305092244-Forecasting-in-Monarch)).
   Acceptance: "₹2,00,000 toward the car loan: shorter tenure or lower EMI, and what interest do I
   save" returns both schedules and the difference. Tradeoff: prepayment rules vary by lender, so a
   generic model can be precisely wrong. Arno: mostly covered; `amortize` and the `Prepay` payload
   handle reduce-tenure and reduce-EMI; contract-specific costs and broader strategy comparisons
   need separate coverage checks.

5. **Goals with funding rules: universal outcome, method-specific detail.** YNAB separates "set
   aside another", "refill up to", and "have a balance of", and warns when a category is underfunded
   ([Targets](https://support.ynab.com/en_us/how-to-use-targets-rk5kkI9ks)). Copilot suggests goals
   from cash flow and estimates time to reach them ([Dispatch](https://changelog.copilot.money/)).
   Acceptance: a ₹3,00,000 goal in 18 months reports the monthly contribution, this month's
   shortfall, and one adjustment when income drops. Tradeoff: three behaviors multiply UI and test
   states; one is simpler but wrong for sinking funds. Arno: partial; goals carry target, date, and
   fenced savings in the projection, not funding behaviors.

6. **Recurring-charge detection with a review step: universal.** Copilot detects likely recurrings
   during onboarding ([Quick start](https://help.copilot.money/en/articles/11157550-quick-start-guide)); Rocket Money moves
   undetected charges to an Inactive list after a month ([Missing subscriptions](https://help.rocketmoney.com/en/articles/934383-missing-subscriptions)).
   Acceptance: a price rise appears as a proposal with old and new amount, never a silent edit.
   Tradeoff: precision versus nagging; false positives erode trust faster than misses. Arno:
   implemented in `engine/patterns.py` and `jobs/patterns.py`, with a registered background handler.
   A completed real-user acceptance journey is a separate evidence requirement.

7. **Proactive review surface: universal.** Four vendors converge: Copilot's [Daily Briefing](https://help.copilot.money/en/articles/14462982-money-assistant-overview) with
   approve-all, Monarch's Weekly Recap ([AI features](https://help.monarchmoney.com/hc/en-us/articles/16116906962452-About-Monarch-s-AI-Features)),
   Origin's prioritized Actions ([AI Advisor](https://support.useorigin.com/hc/en-us/articles/39419419459085-What-is-the-AI-Advisor)),
   and Rocket Money's upcoming-bill view
   ([Bills](https://help.rocketmoney.com/en/articles/2185531-managing-your-bills-and-subscriptions)).
   Acceptance: a weekly digest lists three changes, each opening the rows behind it, with
   dismiss-all. Tradeoff: a digest that repeats itself trains the user to ignore it. Arno:
   implemented in the briefing, reminder, risk, and recap jobs; live delivery and user usefulness
   remain distinct acceptance questions.

8. **Authorized, reversible conversational writes: universal.** Copilot says it "always checks
   with you before making changes"
   ([Settings](https://help.copilot.money/en/articles/17678669-money-assistant-settings)); Monarch
   says it "always asks before changing anything" ([AI in Monarch](https://help.monarch.com/hc/en-us/articles/37526856682260-AI-in-Monarch)). Acceptance:
   correcting a payee's category offers to apply to past and future rows, shows the affected count,
   and undoes in one action. Tradeoff: confirmation on every write is safer but slower; the useful
   split is instant-and-undoable versus confirm-first. Arno: covered; write tools return undo cards,
   and `_gate` forces confirmation for lasting or wide writes after outside text.

9. **Feed-independent onboarding: an important method for India.** PocketSmith keeps offline
   accounts alongside feeds and pulls 90 days on first connection
   ([Feeds](https://learn.pocketsmith.com/article/500-feeds)); YNAB points users to other entry
   methods where import is unsupported ([Bank list](https://support.ynab.com/en_us/my-bank-isnt-listed-HJivlLavle)). Copilot needs a US
   institution according to its [onboarding documentation](https://help.copilot.money/en/articles/5542003-historical-transaction-data),
   but also supports [manual accounts](https://help.copilot.money/en/articles/10682991-understanding-manual-accounts).
   These facts do not establish that every record must come from a bank feed. Acceptance: a new
   user uploads one password-protected PDF and gets a categorized month plus opening balance in
   one review screen. Tradeoff: parser upkeep per layout. Arno: covered and differentiated; the
   import pipeline, review screen, and 12-month backfill are the L3 core in `plan.md` §5.

10. **Saved scenarios and comparison: method, high value.** Monarch saves scenarios with independent
    assumptions and compares two side by side
    ([Scenarios](https://help.monarch.com/hc/en-us/articles/55286775198996-Using-Forecasting-Scenarios));
    PocketSmith toggles secondary scenarios per account
    ([Scenarios](https://learn.pocketsmith.com/article/1248-everything-you-need-to-know-about-scenarios-in-pocketsmith)).
    Acceptance: "retire at 60" and "retire at 65" persist as named versions and the end net-worth
    difference is stated. Tradeoff: saved state adds schema and invalidation work, and Code Mode
    scripts already answer one-off what-ifs. Arno: absent; what-ifs run as one-off scripts.

11. **Household collaboration: conditional scope, parked in Arno.** Monarch models household members
    as forecast participants whose income stops at their retirement
    ([Forecasting](https://help.monarch.com/hc/en-us/articles/48344305092244-Forecasting-in-Monarch));
    Arno parks multiuser households. Acceptance: two earners see one plan with per-person income and
    shared obligations. Tradeoff: permissions, attribution, and privacy turn a single-user ledger
    into a multiuser product.

12. **Behavioral feedback that is checkable: method.** Cleo's Budget Rewind reports top spends and
    habits from the last cycle
    ([Budget](https://web.meetcleo.com/faqs/en/articles/12982480-budget-faqs)); Monarch's recap
    summarizes drivers week over week ([AI features](https://help.monarchmoney.com/hc/en-us/articles/16116906962452-About-Monarch-s-AI-Features)).
    Acceptance: a recap names the two categories that moved most, with amounts. Tradeoff: coaching
    drifts into advice the product cannot stand behind. Arno: partial; recaps are template-driven
    stories, not behavioral coaching.

The strongest proposed outcomes are complete records, useful forecasts, auditable affordability,
debt tradeoffs, goal progress, timely review, and authorized writes. A fixed floor, a particular
funding rule, coach-style feedback, and household access are methods or scope choices. A vendor's
US bank sync, credit-report, cancellation, or messaging service cannot be assumed to work in India;
the underlying capability is not inherently US-only.

## Gaps and uncertainties

1. **No independent accuracy comparison was established here.** Origin's CFP-style benchmark is
   self-run; Monarch warns that AI "can make mistakes". These sources do not support a reliability
   ranking or prove that no vendor publishes additional evidence elsewhere.
2. **"Generally available" is a moving target.** Copilot's assistant moved from beta to general
   availability on October 27, 2026; Monarch's chance of success is still rolling out. A capability
   read today may be gated tomorrow.
3. **Indian availability is not established by this survey.** Several services explicitly restrict
   countries or linked institutions. YNAB documents alternatives where direct import is unavailable,
   and PocketSmith supports offline/file workflows. This does not prove reliable Indian bank feeds,
   UPI reconciliation, or Indian statement parsing, but it also does not establish that Indians
   cannot use any of the products manually. Country eligibility, currency, bank linking, and
   statement format coverage must be checked separately.
4. **Arno status is documentary.** It comes from `plan.md` and `toolset.py`, not the running app, so
   "designed" and "shipped" are distinguished only where the plan marks a milestone.

## Source confidence ledger

Every source is a vendor page. "Proved" means what the page establishes, not what the vendor claims
about quality.

| Source | Owner | What it proved | Limitation |
|---|---|---|---|
| [Forecasting](https://help.monarch.com/hc/en-us/articles/48344305092244-Forecasting-in-Monarch) | Monarch | 12-month baseline, life events, scenarios, Monte Carlo, web-only | No accuracy data; page updated Nov 10, 2026 |
| [AI in Monarch](https://help.monarch.com/hc/en-us/articles/37526856682260-AI-in-Monarch) | Monarch | Assistant scope, no money movement, asks before changes, opt-out | Benefit framing; no evaluation detail |
| [FAQs](https://help.monarch.com/hc/en-us/articles/19985735202068-Monarch-FAQs) | Monarch | US and Canada only; USD or CAD recommended | No page date shown |
| [Assistant overview](https://help.copilot.money/en/articles/14462982-money-assistant-overview) | Copilot | Chat, in-chat edits, standing instructions, briefing, iOS only | Undated; no failure modes |
| [Copilot Intelligence](https://help.copilot.money/en/articles/8182433-copilot-intelligence-for-spending) | Copilot | Per-user model, 30-transaction threshold, low-confidence abstention | Aug 20, 2026; may have changed |
| [Copilot FAQ](https://www.copilot.money/faq) | Copilot | US-only accounts, USD, paid subscription | Vendor FAQ; institution count is marketing |
| [Targets](https://support.ynab.com/en_us/how-to-use-targets-rk5kkI9ks) | YNAB | Cadence and behavior matrix, alerts, refunds, no biweekly target | Sep 28, 2026; UI varies by platform |
| [Bank list](https://support.ynab.com/en_us/my-bank-isnt-listed-HJivlLavle) | YNAB | Import limited to US, Canada, UK, selected Europe | No per-bank list; Nov 10, 2025 |
| [Scenarios](https://learn.pocketsmith.com/article/1248-everything-you-need-to-know-about-scenarios-in-pocketsmith) | PocketSmith | Scenario model, additive layers, per-account forecast | Mar 11, 2025; older than other sources |
| [Currency](https://learn.pocketsmith.com/article/1306-currency-settings-in-pocketsmith) | PocketSmith | Base, bank, and account currencies; one currency per account | Dec 1, 2025 |
| [AI Advisor](https://support.useorigin.com/hc/en-us/articles/39419419459085-What-is-the-AI-Advisor) | Origin | Proactive checks, Actions, Health Score | Sep 8, 2026; plan-gated |
| [Forecasting tool](https://support.useorigin.com/hc/en-us/articles/36002288809101-What-is-Origin-s-forecasting-tool-and-how-can-it-help-me) | Origin | Life events, success rate, plan gating | Oct 24, 2025; article flags its own inference |
| [Rowan](https://help.rocketmoney.com/en/articles/16760933-what-is-rowan) | Rocket Money | Text agent scope, Premium+, RCS and carrier limits | Carrier list changes; page undated |
| [International banks](https://help.rocketmoney.com/en/articles/79778-does-rocket-money-support-international-banks) | Rocket Money | US residents and US banks only | Sep 22, 2026 |
| [UK availability](https://web.meetcleo.com/faqs/en/articles/12992410-what-s-available-for-cleo-customers-in-the-uk) | Cleo | UK feature subset | Jun 18, 2026; relaunch state may change |

## What this does not establish

It does not rank the products, estimate quality, or test them with an Indian account, a UPI
statement, or a password-protected PDF. The requirement list is a hypothesis for the parent task to
weigh against `plan.md`, not a decision.
