# Verification: Arno capability coverage

Read-only code and documentation audit, November 12, 2026. This file checks the capabilities in [the
capability report](README.md) against the current Arno tree. It is evidence for the parent's catalog
work, not a catalog and not a product decision. `plan.md` and
[`completion-checklist.md`](../../arno/completion-checklist.md) still own scope and acceptance.

I read `AGENTS.md`, `docs/README.md`, and the applicable `docs/arno/` files, then inspected source,
migrations, tests, and the published README blocks. I did not run tests, models, evals, database
queries, a live UI, imports, or restores. A test name means what the test asserts in source, not a
fresh green result. No owner data, secrets, or private statements were opened.

_Status._ _Present_ means current code plus a direct test or documented path. _Partial_ means a real
subset plus a named gap. _Absent_ means no implementation and no parking note. _Parked_ means
`parked.md` deliberately excludes it with a trigger. A checklist `[x]` is not repeated unless code
and checklist agree; user journeys and forecasts stay open.

## The model-visible surface, exactly

Arno registers 18 Arno tools: 12 reads behind Code Mode and 6 writes
([toolset.py:473](../../../src/arno/agent/toolset.py#L473),
[toolset.py:746](../../../src/arno/agent/toolset.py#L746)). The model sees `run_code` plus the six
writes directly. Inside `run_code`: `get_financial_context`, `query_movements`,
`summarize_spending`, `recap`, `project`, `run_query`, `amortize`, `compound`, `rupees`,
`show_chart`, `show_table`, `present`
([capabilities.py:385](../../../src/arno/agent/capabilities.py#L385)). The writes are
`record_movement`, `correct_movement`, `update_setup`, `schedule_payment`, `preference`, `undo`.
With the parser subagent, five harness task tools also appear: `delegate_task`, `list_tasks`,
`message_task`, `answer_task`, `cancel_task`
([builder.py:174](../../../src/arno/agent/builder.py#L174); names confirmed in installed
`pydantic_ai_harness/subagents/`). So a chat agent with a parser has 12 visible tools, not seven as
`design.md` says. There is no `can_i_afford`; affordability is `project(hypothetical=...)`
([design.md:301](../../arno/design.md#L301)). Role variables are `CHAT_*`, `CHAT_FALLBACK_*`,
`VISION_*`, `STT_*`, `PARSE_*`, `BATCH_*` ([models.py:190](../../../src/arno/agent/models.py#L190));
readers are `pypdfium2`, `python-calamine`, `msoffcrypto-tool`, `python-docx`, and `striprtf`; jobs
use the `jobs` table; backup uses `pg_dump` and restic.

The reads are plain Python over Postgres and shared with REST, as rule 3 requires
([platform.md:49](../../arno/platform.md#L49)). `run_query` uses `agent_ro`, SELECT only, 2 s
timeout ([read.py:833](../../../src/arno/tools/read.py#L833)). `number_check.py` compares quantities
with tool returns, arguments, the snapshot, and user text, but its docstring says a real number used
with the wrong label is an eval's job
([number_check.py:1](../../../src/arno/agent/number_check.py#L1)). The test says “right number,
wrong label” is not flagged
([test_agent_number_check.py:124](../../../tests/test_agent_number_check.py#L124)). It is a warning
system, not a semantic guarantee.

## Implementation matrix

Live tags are defined below the table.

| Capability | Status | Current evidence | Tests: establish, and do not establish | Live |
|---|---|---|---|---|
| Onboarding, profile, memory | Partial | Setup is prompt-led: the model walks accounts, income, obligations, goals, floor, and rough baselines when `setup_gaps` is nonempty ([instructions.py:132](../../../src/arno/agent/instructions.py#L132)). `update_setup` covers account, balance, schedule, goal, category, payee, settings, scheduled question, baseline ([toolset.py:623](../../../src/arno/agent/toolset.py#L623)); gaps at [read.py:420](../../../src/arno/tools/read.py#L420). Memory is typed preference/fact/rule ([0017_memory_kinds.sql](../../../src/arno/db/migrations/0017_memory_kinds.sql)). No dedicated structured planning-profile fields for dependents, income stability, risk, horizon, or constraints; typed memory can retain facts and `users` has a name only ([0003_schema.sql:29](../../../src/arno/db/migrations/0003_schema.sql#L29)). | Setup undo: `test_ledger_setup_undo.py`; kinds: `test_memory_kinds.py`; fields: `test_agent_write_contract.py`. Not: a new user reaches a useful first answer, gaps are prioritized by question, a rich profile persists. | L9, L7 |
| Accounts, coverage, freshness, conflicts | Present; narrow conflicts | Kinds: bank, card, cash, wallet, loan, asset, goal, person, system ([common.py:12](../../../src/arno/ledger/common.py#L12)). Known is anchored on an opening, stated balance, or committed statement; `liquid_total` is null if any liquid account is unknown ([read.py:153](../../../src/arno/tools/read.py#L153)). Coverage is monthly and requires every active account that ever committed a statement ([read.py:52](../../../src/arno/tools/read.py#L52)). Stated-versus-statement disagreement needs confirmation before commit ([commit.py:332](../../../src/arno/importers/commit.py#L332)). | `test_coverage.py`, `test_audit_reads2.py`, `test_ledger_accounts.py`, `test_import_pipeline.py:149`: gaps, unknown cards/loans, backdated balances. Not: a “next missing fact” review, conflicting goals, profile conflicts. | L1, L2 |
| Movements, splits, people, reconciliation | Present | One `record_movement` handles every kind; code derives transfers ([tools.md:616](../../arno/tools.md#L616)). Split shares go to person accounts; an equal split's paise remainder is the user's ([movements.py:457](../../../src/arno/ledger/movements.py#L457), [movements.py:1022](../../../src/arno/ledger/movements.py#L1022)). Reconciliation is certain on a UPI reference or one account/amount/date candidate within ±3 days, ambiguous on several, new otherwise ([reconcile.py:214](../../../src/arno/importers/reconcile.py#L214)). Merge keeps labels/people; statement wins on amount, date, account. | `test_ledger_movements.py`: spending, split arithmetic, lending, loan parts, correction, idempotency, undo, rules. `test_import_reconcile.py`: UPI/amount match, ambiguity, dedupe, two sides, baselines. Not: every narration variant, every partial repayment, the live review screen. | L1, L2 |
| Document capture and passwords | Present; live incomplete | Readers: PDF text, spreadsheets, CSV/text, DOCX, RTF, OFX/MT940, XML, ZIP ([import.md:169](../../arno/import.md#L169)). Parser tools: `open_imports`, `wait_for_password`, `saved_parser`, `doc_info`, `doc_words`, `doc_words_range`, `doc_cells`, `import_state`, `ledger_opening`, `check_statement`, `transcribe_page`, `submit_parser`, `suggest_transfers` ([parser.py:1080](../../../src/arno/agent/parser.py#L1080)). A password lives in an in-memory vault for one import, never in the database, job, log, or either model ([passwords.py:1](../../../src/arno/importers/passwords.py#L1); [imports.py:151](../../../src/arno/api/imports.py#L151)). | `test_import_gate.py`: replay, mismatch, undone/purged exclusion, version append/rollback, unlocked copy. `test_import_pipeline.py`: saved-layout replay, verified commit, visible adjustments, non-statement answer, no password persistence. Not: Union Bank, every Slice cycle, 150-page split, live restart. | L1, L2, L6 |
| Voice and photo capture | Present; live unverified | A photo is saved first, then a tool-less `VISION_*` call returns five capped fields; the server logs one movement, keeps the image, marks text as outside ([photo.py:1](../../../src/arno/capture/photo.py#L1), [capture.py:473](../../../src/arno/api/capture.py#L473)). Voice saves first, posts to `{STT_BASE_URL}/audio/transcriptions`, retries by attachment. Android share target is built ([vite.config.ts:31](../../../web/vite.config.ts#L31)). | `test_capture.py`: retry cards, endpoint/payee prompt, transcript path without flag, one marked photo movement, five fields, size cap. `test_chat_photo_attachment.py`: one log, caption, mixed, replay, stop. Not: real VISION/STT accuracy, Hinglish WER, a real UPI-app share. | L3 |
| Aggregate projection vs account funding | Aggregate present; account-level parked | The engine starts from the sum of liquid accounts ([projection.py:347](../../../src/arno/engine/projection.py#L347)); it reports each account's age but does not check that a named account funds a named due date. Account-level projection is parked ([parked.md:142](../../arno/parked.md#L142)). | `test_projection_tool.py`: curve, quality, source IDs, unknown-balance refusal, hypotheticals, panel. `test_engine_projection.py`: event order, card cycles, goals, resampling, owed money. No per-account shortfall test. | L10 |
| Affordability, scenarios, saved baseline | Affordability present; scenarios absent | `project(hypothetical=...)` returns yes/yes-but/no with wait, card/EMI, delay-goal, cut-category tradeoffs at 90% ([affordability.py:1](../../../src/arno/engine/affordability.py#L1), [affordability.py:413](../../../src/arno/engine/affordability.py#L413)). A wait date alone leaves `no`. Code Mode can call `project` repeatedly, but there is no scenario table, named baseline, override bundle, assumptions record, or reopenable comparison. The return's `baseline` is the engine's before/after object, not a saved user baseline. | `test_engine_affordability.py`: yes, wait-only no, card float, over-limit, category cut, delayed goal, EMI tenure, unknown balance. `test_audit_engine.py:146`: two what-ifs share a baseline and write nothing. Not: saved multi-event scenarios, income-shock bundles, reopening after edits. | L4, L10 |
| Savings, baselines, irregular expenses | Partial | Baselines are daily spend per category from 90 days after 3 movements/2 days; an estimate keeps its `source` ([baselines.py:16](../../../src/arno/importers/baselines.py#L16)). Goals have target, deadline, priority, fenced account, optional contribution schedule ([0003_schema.sql:313](../../../src/arno/db/migrations/0003_schema.sql#L313)). No annual-premium/festival reserve object; they must be dated obligations or goals. No affordable-contribution output. Category budgets are parked ([parked.md:124](../../arno/parked.md#L124)). | `test_import_reconcile.py:421`: baselines from real rows. `test_engine_projection.py:176`: goal contribution leaves liquid; `:207`: unconfirmed income optimistic; `:237`: thin category flat estimate. Not: a sinking fund, planned-versus-actual plan, variable-income contribution advice. | L4, L10 |
| Debt: amortization, prepay, card, fees | Partial | `amortize` computes EMI, interest, payoff, schedule, and prepay under tenure or EMI reduction ([toolset.py:299](../../../src/arno/agent/toolset.py#L299)). Loan payments split principal/interest with one interest charge per calendar month ([movements.py:771](../../../src/arno/ledger/movements.py#L771)). The projection pays full card outstanding on the due date and models charges against the limit ([projection.py:282](../../../src/arno/engine/projection.py#L282), [projection.py:560](../../../src/arno/engine/projection.py#L560)). No minimum due, APR, processing fee, tax on fee, foreclosure, or promo rate. | `test_engine_money.py`: known EMI, zero rate, prepay tenure/EMI, never-repaying EMI, compounding. `test_audit_engine.py:305`: rounded-down principal, prepayment outside tenure. `test_ledger_accounts.py`: card outstanding/available credit. Not: a fee-inclusive total cost or minimum-payment path. | L10 |
| Holdings, performance, FX, retirement | Parked/absent | Asset accounts hold hand-updated balances only; no unit, NAV, valuation date, XIRR, or return series ([common.py:12](../../../src/arno/ledger/common.py#L12), [design.md:86](../../arno/design.md#L86)). Net worth from statements is parked ([parked.md:94](../../arno/parked.md#L94)). One base currency; foreign purchases logged at charged amount; no FX ([0003_schema.sql:13](../../../src/arno/db/migrations/0003_schema.sql#L13), [plan.md:93](../../arno/plan.md#L93)). Retirement, longevity, inflation absent; `compound` is a primitive ([toolset.py:349](../../../src/arno/agent/toolset.py#L349)). | No test covers holdings, NAV, XIRR, FX, or retirement. | L10 |
| Tax, insurance, deposit protection | Parked/absent | Tax engine parked: salary TDS today; trigger is freelance demand ([parked.md:115](../../arno/parked.md#L115)). Insurance is only an obligation subtype for a premium reminder ([0003_schema.sql:286](../../../src/arno/db/migrations/0003_schema.sql#L286)); no policy, nominee, renewal, or adequacy. No DICGC aggregation or per-bank exposure. | No tax, policy, or deposit-insurance test. | L10 |
| Research and source grounding | Research absent; ledger grounding present | No configured search/fetch tool; installed framework dependencies alone do not enable search ([toolset.py:473](../../../src/arno/agent/toolset.py#L473), [pyproject.toml:7](../../../pyproject.toml#L7)). Code Mode has no mount or `os_access`; Monty has no network ([design.md:307](../../arno/design.md#L307)). Ledger answers carry source IDs; `present` copies only referenced snapshots ([tools.md:570](../../arno/tools.md#L570)). `number_check` flags untraced quantities, not wrong meaning. | `test_agent_number_check.py`: traced, derived, untraced, Hinglish, wrong-label pass. `test_agent_chart_citation.py`: script citation. `test_agent_presentation.py`: unknown money/gaps stay unknown. Not: external retrieval, current-rule dates, official-passage citation. | L11, L10 |
| Proactive reminders, risk, patterns | Present | The loop runs weekly briefing, reminders at a configurable lead, nightly/post-write risk, nightly/post-import patterns, nightly baselines, scheduled questions ([platform.md:173](../../arno/platform.md#L173)). Reminders key on source and due date; risk warns once per episode and re-arms ([risk.py:25](../../../src/arno/jobs/risk.py#L25)). Recurring needs 3 occurrences within 3 days of 7/30/365-day gaps and 10% of median; only proposes ([patterns.py:27](../../../src/arno/engine/patterns.py#L27)). | `test_watch_workflows.py`: briefing numbers/fallback, one reminder, risk re-arm, recurring/missed with outside text, read-only question. `test_watch_schedules.py`: missed-run and period keys. Not: live push, real phone, prioritization. | L3, L5 |
| Connector and job recovery | Jobs present; connectors absent | Jobs live in `jobs`; boot requeue returns `running` to `pending`; failure retries up to 3 attempts with backoff, then fails and notifies ([runner.py:113](../../../src/arno/jobs/runner.py#L113), [runner.py:207](../../../src/arno/jobs/runner.py#L207)). Parser tasks use `DelegationTasks`/`PostgresStepStore`; after restart the task is cancelled and the user asks to resume ([import.md:113](../../arno/import.md#L113)). No external connector. No bank APIs is a product constraint ([plan.md:197](../../arno/plan.md#L197)). | `test_jobs.py`: dedupe, boot requeue, 3-attempt failure, results, give-up, timeout, 2-job limit. `test_import_tasks.py:145`: stopped task resumable. `test_run_view.py:213`: startup settles missing tasks. Not: live restart mid-parse. | L6 |
| Exports | Present; spreadsheet-native absent | JSON and one CSV per table in a ZIP, plus `pg_dump` ([export.py:1](../../../src/arno/ledger/export.py#L1)). JSON/CSV omit credentials and sessions; `pg_dump` stays complete ([platform.md:55](../../arno/platform.md#L55)). No `.xlsx` export or formatting; no `openpyxl`/`xlsxwriter` dependency. | `test_l8_ship.py:61`: export needs a fresh passkey. `test_audit_platform.py:106`: missing `pg_dump` is reported. No test checks CSV typing or an opening spreadsheet; no xlsx path exists. | L6 |
| Secrets, taint, approvals, undo | Present | Secrets are redacted before model and storage ([redaction.py:1](../../../src/arno/agent/redaction.py#L1)). Outside text is capped at 200 chars, marked, recorded on `outside_text_at`; `run_query` flags ([mark.py:1](../../../src/arno/tools/mark.py#L1), [read.py:833](../../../src/arno/tools/read.py#L833)). A lasting write in a flagged thread raises `ApprovalRequired`; approval revalidates ([toolset.py:507](../../../src/arno/agent/toolset.py#L507)). Writes are one audit group with undo; a later row write conflicts ([tools.md:918](../../arno/tools.md#L918)). | `test_agent_safety.py`: redaction, mark caps, stored redaction, run_query flag, number notes. `test_import_injection.py`: injection imported marked and flags. `test_audit_agent.py`: duplicate approval once, denial writes nothing. `test_agent_card_undo.py`: undo IDs. Not: a model never reasons around a mark, live tap rate. | L11 |
| Correction, evals, calibration, backups | Present; measurement gaps | `correct_movement` edits labels without touching the ledger, reverses for amount/account/delete, learns `this`/`future`/`all` rules ([tools.md:705](../../arno/tools.md#L705)). Evals: 134 cases, 50 injection notes, `pass^3`, one cloud run ([README.md:67](../../../README.md#L67), [README.md:71](../../../README.md#L71)). Backtest is seeded and refuses an unusable month ([backtest.py:403](../../../src/arno/engine/backtest.py#L403)). Backups: `pg_dump`/restic; `arno restore` checks manifest/balances ([platform.md:295](../../arno/platform.md#L295)). | `test_ledger_movements.py:335`: payee correction relabels past rows and creates a rule; `:385`: user over llm. `test_evals.py`: categories, injection gate, graders, `pass^k`. `test_l8_ship.py:148`: restic round trip on the build host. Not: real-data calibration, HackerRank, multiple models, fresh-machine restore. | L4-L7 |
| Optional: merchant enrichment | Absent; seed rules substitute | About 163 seed rules for Indian merchants, matched by `contains`/regex; payment apps excluded from name matching ([seed_rules.py:1](../../../src/arno/importers/seed_rules.py#L1)). No external metadata, logo, website, geocode, or enrichment write. | `test_categorizer.py:187`: both merchant shapes, payment-app exclusion; `:199`: user rule survives seed upsert. No enrichment test. | L10 |
| Optional: freelancer receivables | Partial | `income_variable`/`income_oneoff` carry `confirmed`; unconfirmed income is only optimistic ([test_engine_projection.py:207](../../../tests/test_engine_projection.py#L207); [0003_schema.sql:278](../../../src/arno/db/migrations/0003_schema.sql#L278)). A payee can be an employer with a ledger account ([payees.py:72](../../../src/arno/ledger/payees.py#L72)); owed money needs `expected_date`. No invoice, aging, due-date ladder, or expected-versus-confirmed report. | `test_schedules_india.py:100`: confirmed flag; `test_engine_projection.py:207`: unconfirmed excluded. Not: invoice tracking or a receivables report. | L10 |
| Optional: ERP/channel integrations | Absent | Upload plus the parser is the only acquisition channel. No ERP, accounting, bank, broker, or messaging connector; no webhook or sync worker ([pyproject.toml:7](../../../pyproject.toml#L7)). Chat-app integrations are parked ([parked.md:17](../../arno/parked.md#L17)). | No connector test exists. | L10 |

### Live-acceptance tags

- **L1:** A real SBI import through the UI with a passkey, restart mid-parse, second-import replay,
  and categorizing without timeouts
  ([completion-checklist.md:95](../../arno/completion-checklist.md#L95)); milestone 24's
  review-screen backfill is open
  ([completion-checklist.md:157](../../arno/completion-checklist.md#L157)).
- **L2:** Union Bank has no statement; remaining Slice cycles wait on corrected exports
  ([completion-checklist.md:151](../../arno/completion-checklist.md#L151),
  [completion-checklist.md:180](../../arno/completion-checklist.md#L180)).
- **L3:** Tailscale/HTTPS on a phone, real PWA install/push, a real UPI-app share, a live briefing
  and scheduled question, real `STT_*` and `VISION_*`
  ([completion-checklist.md:222](../../arno/completion-checklist.md#L222)).
- **L4:** Milestone 29's real backtest and milestone 30's HackerRank score are open
  ([completion-checklist.md:192](../../arno/completion-checklist.md#L192)). The README backtest is
  synthetic; HackerRank is “not run” ([README.md:85](../../../README.md#L85)).
- **L5:** Local-model scorecard, median cost, and latency are open; the published cloud median is
  18.4 s against a 5 s/15 s target
  ([completion-checklist.md:202](../../arno/completion-checklist.md#L202)).
- **L6:** Restore on another machine, Compose restarts on Linux, and the restart-mid-parse rehearsal
  are open ([completion-checklist.md:237](../../arno/completion-checklist.md#L237),
  [completion-checklist.md:327](../../arno/completion-checklist.md#L327)).
- **L7:** Milestone 40 (second installer), milestone 41 (four weeks), and the freeze week are open
  ([completion-checklist.md:242](../../arno/completion-checklist.md#L242)).
- **L8:** The observability owner deployment update is open
  ([completion-checklist.md:276](../../arno/completion-checklist.md#L276)).
- **L9:** No checklist item covers onboarding or a first useful answer. The plan's “useful from the
  first conversation” claim has no recorded journey ([plan.md:129](../../arno/plan.md#L129)).
- **L10:** No live acceptance exists for account funding, saved scenarios, irregular reserves,
  holdings, retirement, tax, insurance, deposit protection, research, enrichment, receivables, or
  connectors. Status rests on code absence, parked notes, or unit tests.
- **L11:** Number-check noise, confirm-card tap rate, and wrong-label cases are open; the published
  run records 8 flags, 7 untraced ([README.md:74](../../../README.md#L74)).

## Corrected statuses and proof gaps

- The model sees **12** tools with a parser, not seven. Seven is Arno's count before harness task
  tools ([design.md:245](../../arno/design.md#L245)).
- `category_rules` is not a single `merchant_pattern`; migration 0015 adds `match`, `pattern`,
  `priority`, and `seed`
  ([0015_categorizing.sql](../../../src/arno/db/migrations/0015_categorizing.sql)).
- **Asset accounts are not a holdings model.** They accept hand-updated balances; no unit, NAV,
  valuation date, or return. This is parked, not a ledger gap.
- **`project`'s `baseline` is not a saved scenario baseline.** It is the affordability engine's
  before/after object. Code Mode explores paths; nothing stores a named scenario or assumptions.
- **`number_check` is quantity matching.** It catches an amount no return printed; it does not catch
  a correct amount used for the wrong category, account, or period
  ([test_agent_number_check.py:124](../../../tests/test_agent_number_check.py#L124)).
- **A read tool is an API surface.** `tools/read.py` is called by the toolset and REST; a fix in one
  is a fix in both.
- **Data access limits are product constraints.** No bank APIs and no Account Aggregator means
  statement/manual capture is the product path ([plan.md:197](../../arno/plan.md#L197)). It is not
  missing recovery code.
- **The backtest's limitation is real.** A later edit to an existing account, schedule, or goal is
  read in its current form, not as known then
  ([backtest.py:448](../../../src/arno/engine/backtest.py#L448)).
- **The eval scorecard is not multi-model calibration.** One cloud model, one run, no price column;
  local-model and pricing gaps are open
  ([completion-checklist.md:202](../../arno/completion-checklist.md#L202)).

## Candidate catalog evidence suggestions

These are 22 user-facing candidates. They are suggestions for the parent, not final prose.
“Dependencies” means what must already be true to test the candidate, not a build order.

| # | Candidate | Scope | Dependencies | Missing-data scenario |
|---|---|---|---|---|
| E1 | Progressive onboarding and profile | Essential | E2, E3, E12 | One salary day and balance; no statements or profile. |
| E2 | Coverage, freshness, conflict review | Essential | E5, E6 | One account verified, another not; stated balance disagrees. |
| E3 | Accounts, obligations, payment readiness | Essential | E5, E12 | A card has a due day but no statement balance. |
| E4 | Daily capture and correction | Essential | E5, E12 | A receipt photo has no VISION endpoint; no password typed. |
| E5 | Statement import, review, reconciliation | Essential | Parser, gate, E2 | A new bank's statement is locked, one page lacks text, two rows ambiguous. |
| E6 | Splits, people, reimbursements | Essential | E3 | A friend repays part of a split; no matching movement. |
| E7 | Aggregate projection and affordability | Essential | E2, E3, E10 | No floor, or one liquid account unknown. |
| E8 | Debt cost and prepayment | Essential | E3 | A loan lacks rate/tenure; a card lacks statement balance. |
| E9 | Spending insight and categorization | Essential | E5, E6 | A month has no categories; a payment app fronts an unknown merchant. |
| E10 | Goals and fenced savings | Essential | E7 | A goal has a deadline but no contribution schedule. |
| E11 | Proactive warnings and proposals | Essential | E7, E12 | Push is off; a recurring charge is new and unscheduled. |
| E12 | Inspectability, control, recovery | Essential | Audit, backups | A user asks why a figure is; restore lacks the restic password. |
| C1 | Account-level funding readiness | Whole-life conditional | E7, balances | Total cash is enough, but the EMI account is short on its due date. |
| C2 | Saved scenarios and stress tests | Whole-life conditional | E7, scenario store | Compare buy now, delay, and job loss; reopen next month. |
| C3 | Irregular expenses, variable-income plan | Whole-life conditional | E9, E10 | Annual premium and festival spend exist; no reserve recorded. |
| C4 | Holdings, valuation, performance | Whole-life conditional | E3, holdings source | Only a consolidated statement; no unit, NAV, or valuation date. |
| C5 | Retirement and life events | Whole-life conditional | C4, C2 | Retirement is 25 years away; no inflation or longevity assumption. |
| C6 | Tax, insurance, deposit protection | Whole-life conditional | C4, C5 | A tax question names a year; a policy and two deposits are missing. |
| C7 | External research and source grounding | Whole-life conditional | Trust boundary, C2 | A current RBI rule is asked; retrieval fails or is undated. |
| O1 | Merchant enrichment | Optional | E9 | The merchant is a payment app; no external metadata. |
| O2 | Freelancer receivables | Optional | E6, C3 | An invoice is due in 45 days; no cash or aging record. |
| O3 | ERP and channel integrations | Optional | E5 | An ERP export has a changed schema and no connector. |

### Completion checks by group

**Essential (5 checks).** (1) A fresh instance answers one question from a spoken snapshot without
an import, and improves after one added fact. (2) With one known and one unknown liquid account, the
answer reports partial knowledge, names the unknown account, and never claims a complete cash total.
(3) A real statement goes through upload, password, review, and commit; a second import replays
without a model; one undo restores balances, labels, payees, and rules. (4) A partial payment shows
partial; an elapsed date or confirmed schedule never marks paid; a supported payment resolves the
warning and undo restores it. (5) A statement-injection note cannot cause a lasting write without a
confirm card, and a restore returns the ledger with roles and balances.

**Whole-life conditional (5 checks).** (1) With enough total cash but a short named account, the
answer names the account, date, and shortfall, executes no transfer, and does not mark an unpaid
bill paid. (2) A saved scenario compares buy now, delay, and an income interruption, shows
assumptions, and reopens with its original data date after ledger changes. (3) An annual premium and
festival spend appear as reserved commitments; unconfirmed freelance income stays optimistic, and an
income change updates the plan. (4) Holdings show valuation dates and missing coverage; retirement
and tax state assumptions and refuse to invent a current rule; insurance and deposit protection
state what is unknown. (5) A research answer cites the owning source, separates announcement from
effective date, keeps private data out of the query, and declines when retrieval fails.

**Optional (3 checks).** (1) Enrichment is provenance-labeled and can be disabled; a wrong
enrichment never changes a ledger category without a rule or correction. (2) An invoice creates a
person or employer balance with an expected date and aging, and no income counts before it is
confirmed or received. (3) A connector failure is visible and retryable, imported rows carry source
IDs and dedupe, and no connector writes without review. A spreadsheet export, if added, needs stable
table names and paise columns.

Arno is strongest on the ledger, unknown-versus-zero, statement review and replay, payment evidence,
deterministic affordability, goals, typed memory, approvals/undo, and encrypted recovery. The
decisive gaps are account funding, saved scenarios, irregular reserves, full debt cost, holdings,
tax/insurance/deposit review, external research, and live proof of imports, capture, notifications,
and restore. Treat L1-L11 as the live acceptance truth, not the presence of a test.
