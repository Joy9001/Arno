import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Streamdown } from "streamdown";
import { createServer } from "vite";

const root = fileURLToPath(new URL("../..", import.meta.url));
// One process builds every Python-side fixture. It imports the ledger and presentation modules
// directly, never arno.api, so the test does not pay for the FastAPI app or a model client.
const fixtures = JSON.parse(execFileSync("uv", ["run", "python", "tests/fixtures/presentation_contract.py"], {
  cwd: root,
  encoding: "utf8",
  env: {
    ...process.env,
    CHAT_BASE_URL: "https://api.example.com/v1",
    CHAT_MODEL: "example-chat",
    CHAT_PROVIDER: "example",
    CHAT_API_KEY: "sk-live-must-never-be-echoed",
    VISION_BASE_URL: "http://127.0.0.1:9/v1",
    VISION_MODEL: "local-vision",
    STT_BASE_URL: "",
    STT_MODEL: "",
  },
}));
const server = await createServer({
  configFile: "scripts/answer-contract/vite.config.ts",
  server: { middlewareMode: true }, appType: "custom",
});
after(() => server.close());
const { parseAnswerCard, parseResultCard } = await server.ssrLoadModule("/src/chat/answer-data.ts");
const { AnswerCard, ResultCard, SupportingData } = await server.ssrLoadModule("/src/chat/answer-ui.tsx");
const { Card } = await server.ssrLoadModule("/src/chat/cards.tsx");
const { displayText, remarkStripOutsideMarkers } = await server.ssrLoadModule("/src/chat/display-text.ts");
const { markdownRemarkPlugins } = await server.ssrLoadModule("/src/chat/markdown-plugins.ts");
const { buildSupportingPlan, coveredByNativeCard, fallbackViews, messageCoverage } =
  await server.ssrLoadModule("/src/chat/supporting-data.ts");
const { ProjectionBody, ProjectionChart, ProjectionSummary } = await server.ssrLoadModule("/src/projection.tsx");
const { NoticedView } = await server.ssrLoadModule("/src/chat/AskHome.tsx");
const { PriceChangeRows, PriceChange } = await server.ssrLoadModule("/src/PriceChange.tsx");
const { NeedRow } = await server.ssrLoadModule("/src/panels/Inbox.tsx");
const { CategorizeStatus, ParseRecovery } = await server.ssrLoadModule("/src/panels/Imports.tsx");
const { AccountRow } = await server.ssrLoadModule("/src/panels/Accounts.tsx");
const { ExportScope } = await server.ssrLoadModule("/src/panels/ExportScope.tsx");
const { DeletionScope } = await server.ssrLoadModule("/src/panels/DeletionScope.tsx");
const { EvidencePanel } = await server.ssrLoadModule("/src/EvidencePanel.tsx");
const { dataQualityNotes } = await server.ssrLoadModule("/src/projectionData.ts");
const { HistoryEntries } = await server.ssrLoadModule("/src/panels/History.tsx");
const { MemoryPrevious } = await server.ssrLoadModule("/src/panels/MemoryPrevious.tsx");
const render = (Component, data) => renderToStaticMarkup(React.createElement(Component, { data }));
const cardPart = (data) => ({ type: "data", name: "card", data });
const resultPart = (result) => cardPart({ card: "result", result_id: result.result_id, snapshot: result });
const planFor = (parts) => buildSupportingPlan(parts, messageCoverage(parts));

test("history leads with the readable change, keeps ids secondary, and offers undo only when allowed", () => {
  const base = { created_at: "2026-11-16T10:00:00Z", actor: "user", summary: "Logged expense ₹450.00 · Swiggy", records: [{ table: "movements", id: "mov_1" }] };
  const html = renderToStaticMarkup(React.createElement(HistoryEntries, {
    items: [
      { ...base, undo_id: "und_1", undoable: true, reason: null },
      { ...base, undo_id: "und_2", undoable: false, reason: "A later write changed this record." },
    ], onUndo: () => {}, busy: false,
  }));
  assert.equal((html.match(/>Undo<\/button>/g) ?? []).length, 1);
  assert.equal((html.match(/Logged expense ₹450\.00 · Swiggy/g) ?? []).length, 2);
  assert.match(html, /A later write changed this record/);
  assert.match(html, /movements: mov_1/);
  assert.doesNotMatch(html, /change recorded/);
});

test("export scope renders the server exclusion list and destination", () => {
  const scope = fixtures.scope;
  const html = renderToStaticMarkup(React.createElement(ExportScope, {
    excludedTables: scope.excluded_tables,
  }));
  for (const table of scope.excluded_tables) assert.ok(html.includes(table));
  assert.ok(scope.excluded_tables.includes("model_credentials"));
  assert.match(html, /dump includes credentials and secrets/);
  assert.match(html, /projection inputs and result/);
  assert.match(html, /no export is sent to a cloud destination/);
});

test("a deletion request shows its counts, retained data, backups, and provider limits", () => {
  const facts = fixtures.deletion;
  const html = renderToStaticMarkup(React.createElement(DeletionScope, {
    preview: {
      scope: "everything",
      removes: [
        { table: "movements", count: 41 },
        { table: "preferences", count: 1 },
        { table: "parser_task_records", count: 2 },
      ],
      retains: facts.retains,
      backups: facts.backups,
      providers: facts.providers,
    },
  }));
  assert.match(html, /movements/);
  assert.match(html, /41 rows/);
  assert.match(html, /2 files/);
  assert.match(html, /44 records in total/);
  assert.match(html, /Your sign-in/);
  assert.match(html, /keep-daily 7/);
  assert.match(html, /about 4 weeks/);
  assert.match(html, /api\.example\.com/);
  assert.match(html, /cannot be recalled by Arno/);
  assert.match(html, /stays on this machine/);
  assert.doesNotMatch(html, /sk-live-must-never-be-echoed/);
});

test("real Python result snapshots satisfy the frontend contract", () => {
  for (const result of fixtures.results) {
    assert.ok(parseResultCard(result), `Rejected ${result.kind}`);
    const html = render(ResultCard, { card: "result", snapshot: result });
    assert.doesNotMatch(html, /could not be displayed/);
  }
});

test("computed comparisons render money, Unknown and inherited source evidence", () => {
  const html = render(AnswerCard, fixtures.answers.comparison);
  assert.match(html, /Synthetic comparison/);
  assert.match(html, /Unknown/);
  assert.match(html, /₹450/);
  assert.match(html, /No statement covers 2026-10/);
  assert.match(html, /mov_fixture/);
  assert.match(html, /2026-10-01 to 2026-10-31/);
  assert.match(html, /Show all 12 rows/);
  assert.doesNotMatch(html, /Option 11/);
});

test("composed cards validate and render across all supported views", () => {
  for (const answer of Object.values(fixtures.answers)) {
    assert.ok(parseAnswerCard(answer));
    assert.doesNotMatch(render(AnswerCard, answer), /could not be displayed/);
  }
  const spending = render(AnswerCard, fixtures.answers.spending);
  assert.match(spending, /Spending totals returned by summarize_spending/);
  assert.doesNotMatch(spending, /Source unavailable/);
});

test("malformed nested receipts are rejected without a render crash", () => {
  const changes = [
    (receipt) => { receipt.verdict_reason = { bad: "value" }; },
    (receipt) => { receipt.data_quality.simulation = { categories_flat: [42] }; },
    (receipt) => { receipt.goals = [{ name: {} }]; },
    (receipt) => { receipt.hypothetical = { amount_paise: {} }; },
    (receipt) => { receipt.tradeoffs = [{ kind: "test", detail: {} }]; },
    (receipt) => { receipt.scenario = { changes: [{ kind: "stop_income", label: {} }], assumptions: [], runway_days: null, baseline: {} }; },
  ];
  for (const change of changes) {
    const answer = structuredClone(fixtures.answers.receipt);
    change(answer.results[0].receipt);
    assert.equal(parseAnswerCard(answer), null);
    assert.match(render(AnswerCard, answer), /could not be displayed/);
    assert.match(render(Card, { ...answer.results[0].receipt, card: "projection" }), /could not be displayed/);
  }
});

test("legacy chart cards validate before rendering", () => {
  const chart = fixtures.answers.spending.results[0].chart;
  assert.doesNotMatch(render(Card, { ...chart, card: "chart" }), /could not be displayed/);
  assert.match(render(Card, { ...chart, card: "chart", series: [{ name: "Bad", points: {} }] }), /could not be displayed/);
});

test("Summary and Receipt use the same percentage rounding", () => {
  const base = fixtures.answers.rounded;
  const resultId = base.results[0].result_id;
  for (const view of ["Summary", "Receipt"]) {
    const html = render(AnswerCard, {
      ...base,
      display: { _type: view, result_id: resultId },
    });
    assert.doesNotMatch(html, /96%/);
    assert.match(html, /97%/);
  }
});

test("optimistic variants do not show a probability without a floor", () => {
  const payload = { ...fixtures.answers.receipt.results[0].receipt, optimistic: { floor_probability: 0.99 } };
  const summary = renderToStaticMarkup(React.createElement(ProjectionSummary, { payload }));
  const evidence = renderToStaticMarkup(React.createElement(EvidencePanel, { payload, onClose: () => {} }));
  for (const html of [summary, evidence]) {
    assert.match(html, /No floor set/);
    assert.doesNotMatch(html, /99%|optimistic only/);
  }
});

test("an explicitly unknown starting balance suppresses forecast numbers on every surface", () => {
  const receipt = structuredClone(fixtures.answers.receipt.results[0].receipt);
  const unknown = {
    ...receipt,
    data_quality: {
      ...receipt.data_quality,
      balance_ages: [{ account: "Unknown bank", as_of: null, age_days: null, known: false }],
      setup_gaps: ["no liquid account has a known balance"],
    },
  };

  const card = render(Card, { ...unknown, card: "projection" });
  assert.match(card, /Unknown bank/);
  assert.match(card, /Add a statement balance/);
  assert.match(card, /Data quality and sources/);
  assert.doesNotMatch(card, /Lowest median balance/);
  assert.doesNotMatch(card, /above floor/);
  assert.doesNotMatch(card, /₹3,000/);

  const body = renderToStaticMarkup(React.createElement(ProjectionBody, { payload: unknown }));
  assert.match(body, /Unknown bank/);
  assert.match(body, /Data quality/);
  assert.doesNotMatch(body, /Lowest median point/);
  assert.doesNotMatch(body, /Chance above/);

  const chart = renderToStaticMarkup(React.createElement(ProjectionChart, { payload: unknown }));
  assert.match(chart, /Unknown bank/);
  assert.doesNotMatch(chart, /<svg/);

  const evidence = renderToStaticMarkup(React.createElement(EvidencePanel, {
    payload: { ...unknown, verdict: "yes", floor_set: true }, onClose: () => {},
  }));
  assert.match(evidence, /Unknown bank|A balance is missing/);
  assert.doesNotMatch(evidence, /Lowest median balance|above floor|Yes\./);

  const home = {
    as_of: "2026-11-06",
    spendable_paise: 0,
    cash_quality: {
      status: "unknown",
      known_paise: 0,
      known_count: 0,
      unknown_count: 1,
      unknown_accounts: ["Unknown bank"],
    },
    projection: unknown,
    unread_count: 0,
    needs_you: [],
    recent: [],
  };
  const homeHtml = renderToStaticMarkup(
    React.createElement(NoticedView, { data: home }),
  );
  assert.match(homeHtml, /Unknown bank/);
  assert.match(homeHtml, /statement balance/);
  assert.doesNotMatch(homeHtml, /Floor holds/);
  assert.doesNotMatch(homeHtml, /Lowest median/);
  assert.match(homeHtml, /No forecast yet/);
});

test("a known zero, a stale-but-stated balance and an unset floor still support a forecast", () => {
  for (const balance_ages of [
    [{ account: "Zero bank", as_of: "2026-11-06", age_days: 0, amount_paise: 0, known: true }],
    [{ account: "Stale bank", as_of: "2026-09-01", age_days: 66, amount_paise: 100_000, known: true }],
  ]) {
    const payload = structuredClone(fixtures.answers.receipt.results[0].receipt);
    payload.data_quality = { ...payload.data_quality, balance_ages, setup_gaps: ["no floor set"] };
    const html = render(Card, { ...payload, card: "projection" });
    assert.match(html, /Lowest median balance/);
    assert.match(html, /₹3,000/);
  }

  const legacy = structuredClone(fixtures.answers.receipt.results[0].receipt);
  delete legacy.data_quality.balance_ages;
  legacy.data_quality.setup_gaps = ["no floor set"];
  assert.match(render(Card, { ...legacy, card: "projection" }), /Lowest median balance/);
});

test("invalid charts, table dimensions and missing views cannot hide valid fallback cards", () => {
  const badChart = structuredClone(fixtures.answers.spending);
  badChart.results[0].chart.series[0].points[0].y = Infinity;
  assert.equal(parseAnswerCard(badChart), null);
  const badTable = structuredClone(fixtures.answers.comparison);
  badTable.results[0].table.rows[0] = [];
  assert.equal(parseAnswerCard(badTable), null);
  const missingTable = structuredClone(fixtures.answers.comparison);
  delete missingTable.results[0].table;
  assert.equal(parseAnswerCard(missingTable), null);
});

test("deep, cyclic, duplicate and oversized saved answers fail before recursive parsing", () => {
  const base = fixtures.answers.comparison;
  const deep = { _type: "Stack", children: [] };
  deep.children.push(deep);
  assert.equal(parseAnswerCard({ ...base, display: deep }), null);
  assert.equal(parseAnswerCard({ ...base, results: [base.results[0], base.results[0]] }), null);
  assert.equal(parseAnswerCard({ ...base, display: { _type: "Stack", children: Array(50).fill(base.display) } }), null);
});

test("display text removes only the literal outside envelope, including markdown cells and code", () => {
  assert.equal(
    displayText("<outside>Food</outside> & <script>x</script>"),
    "Food & <script>x</script>",
  );
  assert.equal(displayText("<OUTSIDE>x</OUTSIDE> <outside foo>"), "<OUTSIDE>x</OUTSIDE> <outside foo>");
  assert.equal(displayText("&lt;outside&gt;"), "&lt;outside&gt;");

  const tree = {
    type: "root",
    children: [
      { type: "paragraph", children: [{ type: "text", value: "<outside>Food</outside>" }] },
      { type: "inlineCode", value: "<outside>amount_paise</outside>" },
      {
        type: "table",
        children: [
          {
            type: "tableCell",
            children: [{ type: "text", value: "<outside>captures</outside>" }],
          },
        ],
      },
      { type: "code", value: "<outside>select 1</outside>" },
    ],
  };
  remarkStripOutsideMarkers()(tree);
  assert.equal(tree.children[0].children[0].value, "Food");
  assert.equal(tree.children[1].value, "amount_paise");
  assert.equal(tree.children[2].children[0].children[0].value, "captures");
  assert.equal(tree.children[3].value, "select 1");
});

test("markdown keeps GFM tables while display strips the envelope", () => {
  const html = renderToStaticMarkup(
    React.createElement(Streamdown, {
      mode: "static",
      remarkPlugins: markdownRemarkPlugins,
      children:
        "| Account | Kind |\n|---|---|\n| <outside>captures</outside> | <outside>id</outside> |\n\n" +
        "`<outside>amount_paise</outside>`",
    }),
  );
  assert.match(html, /<table/);
  assert.match(html, /captures/);
  assert.match(html, /amount_paise/);
  assert.doesNotMatch(html, /<outside>|<\/outside>/);
});

test("same-message routine reads share one closed Supporting data disclosure", () => {
  const spending = fixtures.results.find((result) => result.kind === "spending");
  const movements = fixtures.results.find((result) => result.kind === "movements");
  const query = {
    ...fixtures.answers.comparison.results[0],
    result_id: "res_query_fixture",
    kind: "query",
    title: "Query result",
    sql: "SELECT 1",
  };
  const parts = [
    resultPart(spending),
    cardPart({ card: "query", sql: " SELECT 1 ", row_count: 1 }),
    resultPart(movements),
    resultPart(query),
    cardPart({ card: "query", sql: "SELECT 1", row_count: 1 }),
  ];
  const plan = planFor(parts);
  assert.equal(plan.ownerIndex, 0);
  assert.equal(plan.items.length, 3, "the legacy SQL card is replaced by its result snapshot");
  assert.deepEqual(plan.items.map((item) => item.kind), ["result", "result", "result"]);

  const html = renderToStaticMarkup(
    React.createElement(SupportingData, { items: plan.items }),
  );
  assert.equal((html.match(/class="supporting-data"/g) ?? []).length, 1);
  assert.doesNotMatch(html, /supporting-data" open/);
  assert.match(html, /Supporting data/);
  assert.match(html, /3 reads/);
  assert.equal((html.match(/class="answer-result-title"/g) ?? []).length, 3);
  assert.match(html, /Spending by Category/);
  assert.match(html, /Movements/);
  assert.match(html, /Query result/);
  assert.doesNotMatch(html, /<outside>/);
});

test("a valid composed answer hides covered result views but keeps unrelated reads collapsed", () => {
  const spending = fixtures.results.find((result) => result.kind === "spending");
  const movements = fixtures.results.find((result) => result.kind === "movements");
  const parts = [
    resultPart(spending),
    cardPart(fixtures.answers.spending),
    resultPart(movements),
  ];
  const plan = planFor(parts);
  assert.equal(plan.ownerIndex, 2);
  assert.equal(plan.answerOwnerIndex, 1, "the closed disclosure follows the composed answer");
  assert.equal(plan.items.length, 1);
  assert.equal(plan.items[0].result.title, movements.title);

  const html = renderToStaticMarkup(
    React.createElement(SupportingData, { items: plan.items }),
  );
  assert.doesNotMatch(html, /Spending by Category/);
  assert.match(html, /Movements/);
  assert.equal((html.match(/class="supporting-data"/g) ?? []).length, 1);
});

test("a malformed or absent composed answer leaves readable fallback data", () => {
  const spending = fixtures.answers.spending.results[0];
  const malformed = { card: "answer", version: 1, display: { _type: "Summary" }, results: [] };
  for (const parts of [
    [resultPart(spending)],
    [resultPart(spending), cardPart(malformed)],
  ]) {
    const plan = planFor(parts);
    assert.ok(plan);
    assert.equal(plan.items.length, 1);
    const html = renderToStaticMarkup(
      React.createElement(SupportingData, { items: plan.items }),
    );
    assert.match(html, /Spending by Category/);
    assert.match(html, /<table/);
    assert.doesNotMatch(html, /could not be displayed/);
  }
  assert.match(render(AnswerCard, malformed), /This answer could not be displayed/);
});

test("legacy SQL cards deduplicate by SQL and a query fallback leads with its rows", () => {
  const query = {
    ...fixtures.answers.comparison.results[0],
    result_id: "res_query_fixture",
    kind: "query",
    title: "Query result",
    sql: "SELECT 1",
  };
  const duplicate = cardPart({ card: "query", sql: " SELECT 1 ", row_count: 1 });
  const unrelated = cardPart({ card: "query", sql: "SELECT 2", row_count: 4 });
  const plan = planFor([resultPart(query), duplicate, unrelated, duplicate]);
  assert.equal(plan.items.length, 2);
  assert.deepEqual(plan.items.map((item) => item.kind), ["result", "query"]);

  const html = render(ResultCard, { card: "result", snapshot: query });
  assert.match(html, /<table/);
  assert.doesNotMatch(html, /Truncated/);
  assert.doesNotMatch(html, />Rows</);
  assert.doesNotMatch(html, />Columns</);
});

test("SQL deduplication preserves quoted case and whitespace", () => {
  const query = {
    ...fixtures.answers.comparison.results[0],
    result_id: "res_quoted_query_fixture",
    kind: "query",
    sql: "SELECT 'Food  shops'",
  };
  const plan = planFor([
    resultPart(query),
    cardPart({ card: "query", sql: " SELECT 'Food  shops' ", row_count: 1 }),
    cardPart({ card: "query", sql: "SELECT 'food  shops'", row_count: 1 }),
    cardPart({ card: "query", sql: "SELECT 'Food shops'", row_count: 1 }),
  ]);
  assert.equal(plan.items.length, 3);
  assert.deepEqual(plan.items.map((item) => item.kind), ["result", "query", "query"]);
});

test("empty results get one empty treatment and one coverage note", () => {
  const base = fixtures.answers.spending.results[0];
  const empty = {
    ...base,
    result_id: "res_empty_fixture",
    empty: true,
    table: { ...base.table, rows: [] },
    summary: { facts: [] },
  };
  const html = render(ResultCard, { card: "result", snapshot: empty });
  assert.equal((html.match(/No rows were returned/g) ?? []).length, 1);
  assert.equal((html.match(/No statement covers 2026-10/g) ?? []).length, 1);
  const plan = planFor([resultPart(empty)]);
  const supporting = renderToStaticMarkup(
    React.createElement(SupportingData, { items: plan.items }),
  );
  assert.equal((supporting.match(/No rows were returned/g) ?? []).length, 1);
  assert.doesNotMatch(supporting, /<table/);

  const covered = planFor([
    resultPart(empty),
    cardPart({
      card: "answer",
      version: 1,
      display: { _type: "Summary", result_id: empty.result_id },
      results: [empty],
    }),
  ]);
  assert.equal(covered, null, "an empty result shown by a composed answer is not repeated");
});

test("native chart and projection coverage stays view-sensitive and actionable cards stay out", () => {
  const summaryOnly = new Map([["res_fixture", new Set(["Summary"])]]);
  assert.equal(coveredByNativeCard("projection", "res_fixture", summaryOnly), false);
  assert.equal(coveredByNativeCard("chart", "res_fixture", summaryOnly), false);
  const chartOnly = new Map([["res_fixture", new Set(["Chart"])]]);
  assert.equal(coveredByNativeCard("chart", "res_fixture", chartOnly), true);
  assert.equal(coveredByNativeCard("projection", "res_fixture", chartOnly), false);

  const plan = planFor([
    cardPart({ card: "capture", title: "Photo logged", amount_paise: 245000 }),
    cardPart({ card: "import", import_id: "imp_1", status: "review" }),
    cardPart({ card: "projection", ...fixtures.answers.receipt.results[0].receipt }),
    cardPart({ card: "chart", ...fixtures.answers.spending.results[0].chart }),
    { type: "tool-call", toolName: "present" },
  ]);
  assert.equal(plan, null);
});

test("scenario projection cards render assumed changes, holds, runway, and before/after facts", () => {
  const answer = fixtures.answers.scenario;
  assert.ok(parseAnswerCard(answer));
  const html = render(AnswerCard, answer);
  assert.match(html, /Assumed changes/);
  assert.match(html, /Stop all income from 2026-11-10/);
  assert.match(html, /Plan falls short/);
  assert.match(html, /Drops below floor in 38% of simulated futures/);
  assert.match(html, /Runway: 44 days/);
  assert.match(html, /before 96%/);
  assert.match(html, /before ₹2,100/);
  assert.match(html, /before ₹1,500/);

  const receipt = structuredClone(answer.results[0].receipt);
  receipt.scenario.runway_days = null;
  receipt.scenario.changes[0].label = "Pause <outside>Netflix</outside> from 2026-11-10 (3 events, ₹1,947)";
  receipt.data_quality.gaps.push(
    "scenario change matched no occurrence in the horizon: Stopped <outside>Freelance</outside> from 2026-11-10.",
  );
  const noCrossing = render(Card, { ...receipt, card: "projection" });
  assert.match(noCrossing, /no crossing within 90 days/);
  assert.match(noCrossing, /Pause Netflix from 2026-11-10/);
  assert.doesNotMatch(noCrossing, /<outside>|<\/outside>/);

  const body = renderToStaticMarkup(React.createElement(ProjectionBody, { payload: receipt }));
  assert.match(body, /Assumed changes/);
  assert.match(body, /Pause Netflix from 2026-11-10/);
  assert.match(body, /Stopped Freelance from 2026-11-10/);
  assert.match(body, /no crossing within 90 days/);
  assert.doesNotMatch(body, /<outside>|<\/outside>/);

  const evidence = renderToStaticMarkup(React.createElement(EvidencePanel, { payload: receipt, onClose: () => {} }));
  assert.match(evidence, /Assumed changes/);
  assert.match(evidence, /All income flows stop during the scenario window/);
  assert.match(evidence, /Stopped Freelance from 2026-11-10/);
  assert.doesNotMatch(evidence, /<outside>|<\/outside>/);

  const unknownReceipt = structuredClone(receipt);
  unknownReceipt.data_quality.balance_ages = [
    { account: "Unknown bank", as_of: null, age_days: null, amount_paise: null, known: false },
  ];
  const gatedEvidence = renderToStaticMarkup(
    React.createElement(EvidencePanel, { payload: unknownReceipt, onClose: () => {} }),
  );
  assert.doesNotMatch(gatedEvidence, /Runway:/);
  assert.doesNotMatch(gatedEvidence, /Plan falls short|Plan holds/);

  const legacy = render(AnswerCard, fixtures.answers.receipt);
  assert.doesNotMatch(legacy, /Assumed changes/);
});

test("saved result titles, notes and cells strip the envelope without touching the snapshot", () => {
  const result = structuredClone(fixtures.answers.comparison.results[0]);
  const original = structuredClone(result);
  result.title = "<outside>Query</outside> result";
  result.table.columns[0].label = "<outside>Option</outside>";
  result.table.rows[0][0] = { type: "text", text: "<outside>Food</outside>" };
  const html = render(ResultCard, { card: "result", snapshot: result });
  assert.doesNotMatch(html, /<outside>|<\/outside>/);
  assert.match(html, /Query result/);
  assert.match(html, /Food/);
  assert.equal(result.table.rows[0][0].text, "<outside>Food</outside>");
  assert.notDeepEqual(result, original);
  assert.equal(fallbackViews(result)[0], "Table");
});

test("safe_to_spend renders on home and projection cards and hides when null or invalid", () => {
  const receipt = structuredClone(fixtures.answers.receipt.results[0].receipt);
  const notes = dataQualityNotes(receipt);
  assert.ok(notes.length, "the fixture carries a data-quality note");
  const cardHtml = render(Card, { ...receipt, card: "projection" });
  assert.match(cardHtml, /<p class="receipt-note" data-testid="safe-to-spend">₹2,000 safe to spend today/);
  assert.match(cardHtml, /10 Nov 2026/);

  const summaryHtml = renderToStaticMarkup(React.createElement(ProjectionSummary, { payload: receipt }));
  assert.match(summaryHtml, /<p class="projection-facts" data-testid="safe-to-spend">₹2,000 safe to spend today/);
  const panelHtml = renderToStaticMarkup(React.createElement(ProjectionBody, { payload: receipt }));

  const home = {
    as_of: "2026-11-06",
    spendable_paise: 300_000,
    cash_quality: {
      status: "known",
      known_paise: 300_000,
      known_count: 1,
      unknown_count: 0,
      unknown_accounts: [],
    },
    projection: receipt,
    unread_count: 0,
    needs_you: [],
    recent: [],
  };
  const homeHtml = renderToStaticMarkup(React.createElement(NoticedView, { data: home }));
  for (const html of [homeHtml, cardHtml]) {
    assert.match(html, /₹2,000 safe to spend today/);
    assert.match(html, /as of 06 Nov 2026/);
    assert.match(html, /Floor binds on 10 Nov 2026/);
  }
  // One inline note list on each of home and the card; the panel shows the same list through
  // DataQuality, so every note appears exactly once per surface, never twice.
  for (const html of [homeHtml, cardHtml, panelHtml]) {
    for (const note of notes) {
      const shown = displayText(note);
      assert.equal(html.split(shown).length - 1, 1, `${shown} appears once`);
    }
  }

  const nullHome = { ...home, projection: { ...home.projection, safe_to_spend: null } };
  const nullHomeHtml = renderToStaticMarkup(React.createElement(NoticedView, { data: nullHome }));
  assert.doesNotMatch(nullHomeHtml, /safe to spend today/);

  const badHome = {
    ...home,
    projection: { ...home.projection, safe_to_spend: { amount_paise: 150_000 } },
  };
  assert.doesNotMatch(
    renderToStaticMarkup(React.createElement(NoticedView, { data: badHome })),
    /safe to spend today/,
  );

  // A chat message stored before this field existed still parses and renders.
  const oldAnswer = structuredClone(fixtures.answers.receipt);
  delete oldAnswer.results[0].receipt.safe_to_spend;
  assert.ok(parseAnswerCard(oldAnswer));
  assert.doesNotMatch(render(Card, { ...oldAnswer.results[0].receipt, card: "projection" }), /safe to spend today/);

  const badReceipt = structuredClone(fixtures.answers.receipt);
  badReceipt.results[0].receipt.safe_to_spend = { binding: "floor", binding_date: "2026-11-06" };
  assert.equal(parseAnswerCard(badReceipt), null);
});

test("price changes show the amounts and cited payment rows", () => {
  const item = { id: "notice", kind: "price_change", data: { old_paise: 49900, new_paise: 59900, step_date: "2026-06-05", movement_ids: ["mov_1"] } };
  const html = renderToStaticMarkup(React.createElement(PriceChange, { item }));
  for (const text of [/₹499/, /₹599/, /05 Jun 2026/, /Loading payments/]) assert.match(html, text);
  const rows = [{ id: "mov_1", date: "2026-06-05", description: "Storage", payee_name: "Cloud", amount_paise: 59900, transfers: [{ from_name: "Bank" }] }];
  const rendered = renderToStaticMarkup(React.createElement(PriceChangeRows, { rows }));
  for (const text of [/05 Jun 2026/, /Cloud/, /Bank/, /₹599/, /mov_1/]) assert.match(rendered, text);
});

test("failed import categorization offers retry and missed schedules open in chat", () => {
  const failed = renderToStaticMarkup(React.createElement(CategorizeStatus, { importId: "imp_1", state: { state: "failed", error: "Model unavailable" }, onRefresh: () => {} }));
  assert.match(failed, /Categorizing stopped/);
  assert.match(failed, /Retry/);
  const item = { id: "n", kind: "missed_recurring", title: "Missed payment", body: "No payment found", data: {}, read_at: null, created_at: "2026-11-06T00:00:00Z" };
  const html = renderToStaticMarkup(React.createElement(NeedRow, { item, busy: null, dismissing: null, onOpen: () => {}, onDismiss: () => {} }));
  assert.match(html, /Missed payment/);
  assert.match(html, /Open in chat/);
});

test("a failed parse offers retry only while its stored file is still here", () => {
  const failed = { import_id: "imp_1", status: "failed", reason: "The model could not read this statement.", recovery_action: "reupload", can_retry_parse: true };
  const idle = renderToStaticMarkup(React.createElement(ParseRecovery, { detail: failed, onRetry: () => {} }));
  assert.match(idle, /Retry parse/);
  assert.doesNotMatch(idle, /Upload it again/);

  const running = renderToStaticMarkup(React.createElement(ParseRecovery, { detail: failed, busy: "retry" }));
  assert.match(running, /Retrying…/);
  assert.match(running, /disabled=""/);

  const refused = renderToStaticMarkup(React.createElement(ParseRecovery, { detail: failed, error: "The stored statement file is gone." }));
  assert.match(refused, /The stored statement file is gone\./);

  const gone = renderToStaticMarkup(React.createElement(ParseRecovery, { detail: { ...failed, can_retry_parse: false } }));
  assert.doesNotMatch(gone, /Retry parse/);
  assert.match(gone, /Upload it again/);
});

test("projection cards download a workbook of the request they were asked", () => {
  const receipt = structuredClone(fixtures.answers.receipt.results[0].receipt);
  assert.equal(receipt.request.horizon_days, 90);
  const base = render(Card, { ...receipt, card: "projection" });
  assert.match(base, /Download workbook \(\.xlsx\)/);
  assert.match(base, /href="\/api\/projection\/workbook\?horizon_days=90"/);

  const requested = {
    ...receipt,
    request: {
      horizon_days: 180,
      hypothetical: {
        amount_paise: 12_345_600, date: "2026-11-20", method: "emi", emi_months: 9,
        account: "Slice card", emi_rate_percent: 14, emi_fee_paise: 19_900,
      },
      changes: [{ kind: "stop_income", start: "2026-12-01" }],
    },
  };
  const html = render(Card, { ...requested, card: "projection" });
  const href = /href="([^"]*\/api\/projection\/workbook[^"]*)"/.exec(html)?.[1];
  assert.ok(href, "the card renders a workbook link");
  const query = new URL(href.replaceAll("&amp;", "&"), "https://arno.test").searchParams;
  assert.equal(query.get("horizon_days"), "180");
  assert.equal(query.get("amount_paise"), "12345600");
  assert.equal(query.get("date"), "2026-11-20");
  assert.equal(query.get("method"), "emi");
  assert.equal(query.get("emi_months"), "9");
  assert.equal(query.get("account"), "Slice card");
  assert.equal(query.get("emi_rate_percent"), "14");
  assert.equal(query.get("emi_fee_paise"), "19900");
  assert.deepEqual(JSON.parse(query.get("changes")), [{ kind: "stop_income", start: "2026-12-01" }]);

  const home = {
    as_of: "2026-11-06",
    spendable_paise: 300_000,
    cash_quality: { status: "known", known_paise: 300_000, known_count: 1, unknown_count: 0, unknown_accounts: [] },
    projection: receipt,
    unread_count: 0,
    needs_you: [],
    recent: [],
  };
  const homeHtml = renderToStaticMarkup(React.createElement(NoticedView, { data: home }));
  assert.match(homeHtml, /href="\/api\/projection\/workbook"/);
});

test("the last statement date and age remain visible without a reconciliation claim", () => {
  const account = { kind: "bank", name: "Bank", balance_paise: 100000, balance_as_of: "2026-09-01", statement_balance_paise: 100000, statement_balance_as_of: "2026-09-01", statement_age_days: 66 };
  const html = renderToStaticMarkup(React.createElement(AccountRow, { account }));
  assert.match(html, /01 Sept? 2026/);
  assert.match(html, /66 days old/);
  assert.doesNotMatch(html, /reconciled/i);
});

test("amortize cards render fees and APR when present and stay unchanged when absent", () => {
  const legacySnapshot = fixtures.results.find((result) => result.kind === "amortize" && !result.summary.facts.some((f) => f.label === "APR"));
  assert.ok(legacySnapshot);
  assert.ok(parseResultCard(legacySnapshot));
  const legacyResultHtml = render(ResultCard, { card: "result", snapshot: legacySnapshot });
  assert.doesNotMatch(legacyResultHtml, /Processing fee|Fee GST|Net disbursed|Total cost|APR/);

  const legacyCardHtml = render(Card, fixtures.cards.amortize_legacy);
  assert.match(legacyCardHtml, /Loan schedule/);
  assert.doesNotMatch(legacyCardHtml, /Processing fee|Fee GST|Net disbursed|Total cost|APR/);

  const feeAnswer = fixtures.answers.loan_with_fee;
  assert.ok(parseAnswerCard(feeAnswer));
  const feeAnswerHtml = render(AnswerCard, feeAnswer);
  assert.match(feeAnswerHtml, /Processing fee/);
  assert.match(feeAnswerHtml, /Fee GST/);
  assert.match(feeAnswerHtml, /Net disbursed/);
  assert.match(feeAnswerHtml, /Total cost/);
  assert.match(feeAnswerHtml, /APR/);

  const feeCardHtml = render(Card, fixtures.cards.amortize_with_fee);
  assert.match(feeCardHtml, /Processing fee/);
  assert.match(feeCardHtml, /Fee GST/);
  assert.match(feeCardHtml, /Net disbursed/);
  assert.match(feeCardHtml, /Total cost/);
  assert.match(feeCardHtml, /APR/);
  assert.match(feeCardHtml, new RegExp(`${fixtures.cards.amortize_with_fee.apr_percent.toFixed(2)}%`));
});

test("card_payoff cards validate and render totals, assumptions, and collapsible schedules", () => {
  const answer = fixtures.answers.card_payoff;
  assert.ok(parseAnswerCard(answer));
  const answerHtml = render(AnswerCard, answer);
  assert.match(answerHtml, /Card payoff/);
  assert.match(answerHtml, /Cost of carrying/);
  assert.match(answerHtml, /Total finance charge/);
  assert.match(answerHtml, /Total GST/);
  assert.match(answerHtml, /Total paid/);
  assert.match(answerHtml, /Months/);
  assert.match(answerHtml, /Payoff/);
  assert.match(answerHtml, /Finance charge/);
  assert.match(answerHtml, /Show all \d+ rows/);

  const cardHtml = render(Card, fixtures.cards.card_payoff);
  assert.match(cardHtml, /Card payoff/);
  assert.match(cardHtml, /Cost of carrying/);
  assert.match(cardHtml, /Total finance charge/);
  assert.match(cardHtml, /Total GST/);
  assert.match(cardHtml, /Total paid/);
  assert.match(cardHtml, /Finance charge/);
  assert.match(cardHtml, /Show all \d+ rows/);
});

test("a memory that replaced an earlier value shows what it said and when", () => {
  const previous = {
    text: "Prefers tea",
    changed_at: "2026-01-05T09:00:00Z",
    superseded_at: "2026-02-01T09:00:00Z",
  };
  const html = renderToStaticMarkup(React.createElement(MemoryPrevious, { previous }));
  assert.match(html, /Previously: Prefers tea/);
  assert.match(html, /Feb/);
  assert.doesNotMatch(
    renderToStaticMarkup(React.createElement(MemoryPrevious, { previous: null })),
    /Previously/,
  );
});


