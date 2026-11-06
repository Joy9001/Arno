import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { Streamdown } from "streamdown";
import { createServer } from "vite";

const root = fileURLToPath(new URL("../..", import.meta.url));
const fixtures = JSON.parse(execFileSync("uv", ["run", "python", "tests/fixtures/presentation_contract.py"], {
  cwd: root, encoding: "utf8",
}));
const server = await createServer({
  configFile: "scripts/assistant-ui-fixture/vite.config.ts",
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
const { ProjectionSummary } = await server.ssrLoadModule("/src/projection.tsx");
const { EvidencePanel } = await server.ssrLoadModule("/src/EvidencePanel.tsx");
const render = (Component, data) => renderToStaticMarkup(React.createElement(Component, { data }));
const cardPart = (data) => ({ type: "data", name: "card", data });
const resultPart = (result) => cardPart({ card: "result", result_id: result.result_id, snapshot: result });
const planFor = (parts) => buildSupportingPlan(parts, messageCoverage(parts));

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
