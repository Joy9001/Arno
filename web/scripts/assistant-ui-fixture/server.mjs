/**
 * Synthetic Fin-Agent API for the assistant-ui migration checks.
 *
 * It speaks the Vercel UIMessage SSE protocol (`text/event-stream`) for
 * `POST /api/chat`, keeps detached turn buffers, and replays them from
 * `GET /api/chat/:id/stream`. No production auth, model, ledger, or finance
 * data is involved. The Vite dev server proxies `/api` here.
 */
import { createServer } from "node:http";

const PORT = Number(process.env.FIXTURE_PORT ?? 8000);
const BASE = `http://127.0.0.1:${PORT}`;
const asOf = () => new Date().toISOString().slice(0, 10);

const threads = new Map();
const turns = new Map();
const imports = new Map();
const importsByAttachment = new Map();
const attachments = new Map();
const counters = {
  chatPosts: [],
  stopPosts: [],
  streamGets: [],
  photoPosts: [],
  transcribePosts: [],
  uploadPosts: [],
  importDetailGets: [],
  requests: [],
};
let nextAttachment = 1;
let nextImport = 1;
let nextMessage = 1;
let contractAnswer = null;
// The synthetic parser run behind the "statement" scenario: running, paused, or done.
let taskState = "running";
// The thread whose `statement` turn started the fixture task, and a reply the server started.
let taskThread = null;
let wakeTurn = null;
const TASK_ID = "5ad1e0c0ffee4b6e9e7a3c2d1f0b9a87";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function resetState() {
  threads.clear();
  turns.clear();
  taskThread = null;
  wakeTurn = null;
  homeState = "ready";
  counters.chatPosts.length = 0;
  counters.stopPosts.length = 0;
  counters.streamGets.length = 0;
  counters.photoPosts.length = 0;
  counters.transcribePosts.length = 0;
  counters.uploadPosts.length = 0;
  counters.importDetailGets.length = 0;
  counters.requests.length = 0;
  imports.clear();
  importsByAttachment.clear();
  attachments.clear();
  nextAttachment = 1;
  nextImport = 1;
  nextMessage = 1;
  contractAnswer = null;
}

function newThread(id, title = "New chat") {
  const thread = {
    id,
    title,
    messages: [],
    updated_at: new Date().toISOString(),
  };
  threads.set(id, thread);
  return thread;
}

function ensureThread(id, title = "New chat") {
  const existing = threads.get(id);
  if (existing) return existing;
  // The real server titles a photo-created thread "Photo" and a chat-created
  // thread from its first words; later turns refresh the title.
  return newThread(id, title);
}

function touch(thread, title) {
  thread.updated_at = new Date().toISOString();
  if (title) thread.title = title;
}

function titleFor(text) {
  const trimmed = String(text ?? "").trim().replace(/\s+/g, " ");
  return (trimmed || "New chat").slice(0, 48);
}

function serverId(prefix) {
  return `${prefix}_${nextMessage++}`;
}

function statementRefs(messages) {
  const refs = [];
  for (const message of [...(messages ?? [])].reverse()) {
    if (message?.role !== "user") continue;
    for (const part of message.parts ?? []) {
      if (part?.type !== "data-statement" || !part.data?.attachment_id) continue;
      refs.push({
        attachment_id: String(part.data.attachment_id),
        filename: String(part.data.filename ?? "statement"),
      });
    }
    break;
  }
  return refs;
}

function photoRefs(messages) {
  const refs = [];
  for (const message of [...(messages ?? [])].reverse()) {
    if (message?.role !== "user") continue;
    for (const part of message.parts ?? []) {
      if (part?.type !== "data-photo" || !part.data?.attachment_id) continue;
      refs.push({
        attachment_id: String(part.data.attachment_id),
        filename: String(part.data.filename ?? "photo.png"),
      });
    }
    break;
  }
  return refs;
}

function captureCardFor(ref) {
  const id = ref.attachment_id;
  const failed = /fail/i.test(ref.filename);
  if (failed) {
    return {
      type: "data-card",
      id: `capture_retry_${id}`,
      data: {
        card: "capture-retry",
        title: "Photo not logged",
        kind: "photo",
        attachment_id: id,
        url: `/api/attachments/${id}`,
        note: "That image didn't show a clear payee and amount.",
      },
    };
  }
  return {
    type: "data-card",
    id: `capture_${id}`,
    data: {
      card: "capture",
      title: "Photo logged",
      amount_paise: 245000,
      merchant: "Toit",
      date: asOf(),
      method: "upi",
      upi_ref: "UPI-4421",
      attachment_id: id,
      url: `/api/attachments/${id}`,
      undo_id: `undo_${id}`,
    },
  };
}

function importFor(ref) {
  if (importsByAttachment.has(ref.attachment_id)) {
    return importsByAttachment.get(ref.attachment_id);
  }
  const id = `imp_${nextImport++}`;
  const locked = /locked/i.test(ref.filename);
  const failed = /fail/i.test(ref.filename);
  const record = {
    import_id: id,
    status: failed ? "failed" : locked ? "needs_password" : "parsing",
    attachment: {
      filename: ref.filename,
      content_type: "application/pdf",
      size_bytes: 2048,
      sha256: "fixture",
      purged_at: null,
    },
    account: null,
    period: { start: "2026-10-01", end: "2026-10-31" },
    opening_paise: null,
    closing_paise: null,
    check: failed ? "FAILED" : null,
    check_detail: null,
    counts: null,
    review: null,
    reason: failed
      ? "The parser could not read this file."
      : locked
        ? "password required"
        : null,
    parser_saved: false,
    created_at: new Date().toISOString(),
    committed_at: null,
    rejected_at: null,
    undone_at: null,
    lines: [],
    started_at: Date.now(),
  };
  imports.set(id, record);
  importsByAttachment.set(ref.attachment_id, record);
  counters.importDetailGets.push({ importId: id, at: Date.now(), starts: true });
  return record;
}

function importPayload(record) {
  if (record.status === "parsing" && Date.now() - record.started_at > 1200) {
    record.status = "review";
    record.check = "VERIFIED";
    record.counts = { new: 4, certain: 2, ambiguous: 1, logged_not_on_statement: 0 };
    record.review = { counts: record.counts, ambiguous: [] };
  }
  return record;
}

function uiTextMessage(role, id, text, metadata) {
  return {
    id,
    role,
    parts: [{ type: "text", text }],
    ...(metadata ? { metadata } : {}),
  };
}

function partsFromChunks(chunks) {
  const parts = [];
  let text = "";
  const tools = new Map();
  const reasonings = new Map();
  for (const chunk of chunks) {
    if (chunk.type === "text-delta") text += chunk.delta;
    if (chunk.type === "reasoning-start") {
      reasonings.set(chunk.id, { type: "reasoning", text: "" });
    }
    if (chunk.type === "reasoning-delta") {
      const part = reasonings.get(chunk.id);
      if (part) part.text += chunk.delta;
    }
    if (chunk.type === "reasoning-end") {
      const part = reasonings.get(chunk.id);
      if (part) parts.push(part);
    }
    if (chunk.type === "tool-input-available") {
      const part = {
        type: `tool-${chunk.toolName}`,
        toolCallId: chunk.toolCallId,
        state: "input-available",
        input: chunk.input,
      };
      tools.set(chunk.toolCallId, part);
      parts.push(part);
    }
    if (chunk.type === "tool-approval-request") {
      const part = tools.get(chunk.toolCallId);
      if (part) {
        part.state = "approval-requested";
        part.approval = {
          id: chunk.approvalId,
          ...(chunk.approvalDescriptor ? chunk.approvalDescriptor : {}),
          ...(chunk.isAutomatic ? { isAutomatic: true } : {}),
        };
      }
    }
    if (chunk.type === "tool-output-available") {
      const part = tools.get(chunk.toolCallId);
      if (part) {
        part.state = "output-available";
        part.output = chunk.output;
      }
    }
    if (chunk.type === "data-card" || chunk.type.startsWith("data-")) {
      parts.push({
        type: chunk.type,
        id: chunk.id,
        data: chunk.data,
      });
    }
  }
  if (text) parts.unshift({ type: "text", text });
  return parts;
}

function persistAssistant(thread, messageId, chunks) {
  const parts = partsFromChunks(chunks);
  if (parts.length === 0) return;
  if (thread.messages.some((message) => message.id === messageId)) return;
  thread.messages.push({
    id: messageId,
    role: "assistant",
    parts,
    metadata: { server_id: messageId, flagged_at: null },
  });
  touch(thread);
}

function sse(chunk) {
  return `data: ${JSON.stringify(chunk)}\n\n`;
}

function streamHeaders(threadId) {
  return {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache, no-transform",
    connection: "keep-alive",
    "x-vercel-ai-ui-message-stream": "v1",
    "x-accel-buffering": "no",
    "x-thread-id": threadId,
  };
}

function finishTurn(turn) {
  if (turn.done) return;
  turn.done = true;
  turn.thread.updated_at = new Date().toISOString();
  persistAssistant(turn.thread, turn.messageId, turn.chunks);
  for (const listener of turn.listeners) listener();
  turn.listeners.clear();
}

function pushChunk(turn, chunk) {
  if (turn.done) return;
  turn.chunks.push(chunk);
  for (const listener of turn.listeners) listener();
}

/**
 * A detached turn: it keeps buffering after the POST reader leaves, and a
 * reconnect gets every chunk from zero, then the live tail.
 */
function startTurn(thread, messageId, chunks, delayMs, adapters = {}) {
  const turn = {
    thread,
    messageId,
    chunks: [],
    listeners: new Set(),
    done: false,
  };
  turns.set(thread.id, turn);
  void (async () => {
    for (const chunk of chunks) {
      if (turn.done) return;
      await sleep(adapters.beforeChunk ? adapters.beforeChunk(chunk) : delayMs);
      if (turn.done) return;
      pushChunk(turn, chunk);
    }
    finishTurn(turn);
  })();
  return turn;
}

function streamTurn(req, res, turn) {
  let index = 0;
  let closed = false;
  const cleanup = () => {
    closed = true;
    turn.listeners.delete(flush);
  };
  const flush = () => {
    if (closed) return;
    while (index < turn.chunks.length) {
      res.write(sse(turn.chunks[index++]));
    }
    if (turn.done) {
      cleanup();
      res.end();
    }
  };
  res.writeHead(200, streamHeaders(turn.thread.id));
  flush();
  if (closed) return;
  turn.listeners.add(flush);
  req.on("close", cleanup);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

function readJson(req) {
  return readBody(req).then((buffer) => {
    try {
      return JSON.parse(buffer.toString("utf8") || "{}");
    } catch {
      return {};
    }
  });
}

function json(res, status, body) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(body));
}

function lastUserText(messages) {
  for (const message of [...(messages ?? [])].reverse()) {
    if (message?.role !== "user") continue;
    for (const part of message.parts ?? []) {
      if (part?.type === "text" && part.text) return String(part.text);
    }
  }
  return "";
}

function approvalResponse(messages) {
  for (const message of [...(messages ?? [])].reverse()) {
    for (const part of message?.parts ?? []) {
      if (part?.type === "tool-approval-response") return part;
      // AI SDK v7 carries a manual answer on the tool part itself.
      if (
        part?.state === "approval-responded" &&
        part.approval &&
        typeof part.approval.approved === "boolean"
      ) {
        return {
          type: "tool-approval-response",
          approvalId: part.approval.id,
          toolCallId: part.toolCallId,
          approved: part.approval.approved,
        };
      }
    }
  }
  return null;
}

function threadsPayload() {
  return {
    threads: [...threads.values()]
      .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
      .map((thread) => ({
        id: thread.id,
        title: thread.title,
        updated_at: thread.updated_at,
        message_count: thread.messages.length,
        snippet: thread.messages.at(-1)?.parts?.find((part) => part.type === "text")?.text ?? null,
        running: Boolean(turns.get(thread.id) && !turns.get(thread.id).done),
        flagged: false,
      })),
    as_of: asOf(),
  };
}

function threadPayload(thread) {
  return {
    thread: {
      id: thread.id,
      title: thread.title,
      flagged: false,
      snapshot_at: null,
    },
    messages: thread.messages.map((message) => ({
      ...message,
      server_id: message.id,
      metadata: {
        ...(message.metadata ?? {}),
        server_id: message.id,
        flagged_at: null,
      },
    })),
    running: Boolean(turns.get(thread.id) && !turns.get(thread.id).done),
    as_of: asOf(),
  };
}

const SHOWCASE_TEXT = `## Where the money went

You spent **₹42,180** across 31 transactions this month. That is ₹3,900 less than October, mostly because the annual insurance debit did not repeat.

| Category | This month | Last month | Change |
| --- | ---: | ---: | ---: |
| Rent | ₹18,000 | ₹18,000 | — |
| Food & dining | ₹8,420 | ₹10,110 | −₹1,690 |
| Transport | ₹4,260 | ₹3,980 | +₹280 |
| Subscriptions | ₹1,890 | ₹1,890 | — |
| Shopping | ₹6,110 | ₹8,950 | −₹2,840 |
| Everything else | ₹3,500 | ₹3,340 | +₹160 |

A few things worth knowing:

1. The **₹2,450 dinner at Toit** is the largest single dining charge. It is tagged Dining and tied to your HDFC card statement.
2. Two Swiggy orders are still uncategorised because the merchant string changed to "BUNDL TECHNOLOGIES".
3. Your next card due is ₹12,640 on 12 November; the projection already reserves it.

> The November floor still holds at 94%, even with the phone purchase modelled.

- [x] Rent paid
- [x] Card due reserved
- [ ] Review the two Swiggy rows

The figures above come from your ledger and the pinned November snapshot.`;

const SHOWCASE_CARD = {
  card: "movement",
  title: "Logged: Dinner at Toit",
  summary: "Paid ₹2,450 from the HDFC card. Tagged Dining; the matching statement row is already reconciled.",
  amount: { text: "₹2,450" },
  rule: "Dining",
  rows_changed: 0,
  undo_id: "undo_showcase",
};

const SHOWCASE_CHART = {
  card: "chart",
  type: "bar",
  title: "Spend by category",
  unit: "paise",
  as_of: asOf(),
  computed_at: new Date().toISOString(),
  script: "SELECT category, SUM(amount_paise) FROM movements GROUP BY category;",
  series: [
    {
      name: "November",
      points: [
        { x: "Rent", y: 1800000 },
        { x: "Food", y: 842000 },
        { x: "Transport", y: 426000 },
        { x: "Shopping", y: 611000 },
      ],
    },
  ],
};

const COMPOSED_RESULT = {
  result_id: "res_fixture_1",
  kind: "spending",
  title: "Spending by category, 1–31 October 2026",
  views: ["Summary", "Table", "Chart"],
  summary: {
    facts: [
      { label: "Total", value: { type: "money", paise: 3679000, text: "₹36,790" } },
      { label: "Period", value: { type: "text", text: "2026-10-01 to 2026-10-31" } },
      { label: "Groups", value: { type: "number", value: 4 } },
    ],
  },
  table: {
    caption: "Spending by category, 1–31 October 2026",
    columns: [
      { key: "group", label: "Category", type: "text" },
      { key: "amount", label: "Amount", type: "money" },
      { key: "count", label: "Entries", type: "number" },
    ],
    rows: [
      [
        { type: "text", text: "Rent" },
        { type: "money", paise: 1800000, text: "₹18,000" },
        { type: "number", value: 1 },
      ],
      [
        { type: "text", text: "Food" },
        { type: "money", paise: 842000, text: "₹8,420" },
        { type: "number", value: 12 },
      ],
      [
        { type: "text", text: "Transport" },
        { type: "money", paise: 426000, text: "₹4,260" },
        { type: "number", value: 8 },
      ],
      [
        { type: "text", text: "Shopping" },
        { type: "money", paise: 611000, text: "₹6,110" },
        { type: "number", value: 5 },
      ],
    ],
    shown_rows: 4,
    total_rows: 4,
    truncated: false,
    note: null,
  },
  chart: {
    type: "bar",
    title: "Spending by category",
    unit: "paise",
    as_of: "2026-10-31",
    computed_at: new Date().toISOString(),
    script: "SELECT category, SUM(amount_paise) FROM movements GROUP BY category;",
    series: [
      {
        name: "October",
        points: [
          { x: "Rent", y: 1800000 },
          { x: "Food", y: 842000 },
          { x: "Transport", y: 426000 },
          { x: "Shopping", y: 611000 },
        ],
      },
    ],
  },
  notes: [],
  coverage: { complete: true, gaps: [] },
  as_of: "2026-10-31",
  empty: false,
};

const COMPOSED_ANSWER_CARD = {
  card: "answer",
  version: 1,
  display: {
    _type: "Stack",
    children: [
      { _type: "Summary", result_id: "res_fixture_1" },
      { _type: "Table", result_id: "res_fixture_1" },
      { _type: "Chart", result_id: "res_fixture_1" },
    ],
  },
  results: [COMPOSED_RESULT],
};

const COMPOSED_FALLBACK_CARD = {
  card: "result",
  result_id: "res_fixture_1",
  snapshot: COMPOSED_RESULT,
};

const MALFORMED_ANSWER_CARD = {
  card: "answer",
  version: 1,
  display: { _type: "Summary" },
  results: [],
};

const EMPTY_RESULT = {
  result_id: "res_fixture_empty",
  kind: "spending",
  title: "Spending by category, 1–31 July 2026",
  views: ["Summary", "Table"],
  summary: {
    facts: [
      { label: "Total", value: { type: "unknown" } },
      { label: "Period", value: { type: "text", text: "2026-07-01 to 2026-07-31" } },
    ],
  },
  table: {
    caption: "Spending by category, 1–31 July 2026",
    columns: [
      { key: "group", label: "Category", type: "text" },
      { key: "amount", label: "Amount", type: "money" },
    ],
    rows: [],
    shown_rows: 0,
    total_rows: 0,
    truncated: false,
    note: null,
  },
  notes: ["No statement covers 2026-07."],
  coverage: { complete: false, gaps: ["2026-07"] },
  as_of: "2026-07-31",
  empty: true,
};

const EMPTY_ANSWER_CARD = {
  card: "answer",
  version: 1,
  display: {
    _type: "Stack",
    children: [
      { _type: "Summary", result_id: "res_fixture_empty" },
      { _type: "Table", result_id: "res_fixture_empty" },
    ],
  },
  results: [EMPTY_RESULT],
};


function fixtureTask() {
  const at = (minute, second) => `2026-11-08T22:${String(minute).padStart(2, "0")}:${String(second).padStart(2, "0")}+00:00`;
  const step = (id, stage, title, status, calls, extra = {}) => ({
    id, stage, segment: 0, title, status, calls, error: null, purpose: null, reasoning: null, code: null,
    started_at: at(36, 44), seconds: 4, ...extra,
  });
  const fn = (name, count = 1, failed = false) => ({ name, count, failed });
  const paused = taskState === "paused";
  const steps = [
    step("s1", "open", "Opened the statement files and waited for your password", "ok", [fn("open_imports"), fn("wait_for_password")], { seconds: 38, purpose: "Open this turn's files" }),
    step("s2", "saved", "Read the file’s details and looked for a saved parser", "ok", [fn("doc_info"), fn("saved_parser")], { seconds: 2 }),
    step("s3", "read", "Read 2 pages", "ok", [fn("doc_words", 2)], { purpose: "Find the column headers and where the rows start", reasoning: "The header row sits on page 1; rows start below the account summary." }),
    step("s4", "read", "Read a range of pages", "failed", [fn("doc_words_range", 1, true)], { seconds: 1, error: "last is before first" }),
    step("s5", "read", paused ? "Read 74 pages" : "Read 50 pages", paused ? "ok" : "running", [fn("doc_words", paused ? 74 : 50)], { seconds: 58, purpose: "Collect every page so the parser can be tested on all of them" }),
  ];
  if (paused) {
    steps.push(
      step("s6", "submit", "Submitted the parser · attempt 1", "failed", [fn("submit_parser")], {
        seconds: 6, error: "NameError: name 'files' is not defined", purpose: "Build the final SBI parser from the line grouping that worked",
        code: "# Build the final SBI parser from the line grouping that worked\nfor f in files:\n    pages = await doc_words_range(import_id=f[\"import_id\"], first=0, last=73)\n    rows = parse_pages(pages)",
      }),
      step("s7", "submit", "Submitted the parser · attempt 2", "cut_off", [fn("submit_parser")], { seconds: null, purpose: "Try again without the files list" }),
    );
  }
  const stageStatus = (key) => {
    const order = ["open", "saved", "read", "submit", "moves", "report"];
    const reached = paused ? 3 : 2;
    const i = order.indexOf(key);
    if (i < reached) return "done";
    if (i === reached) return paused ? "stopped" : "running";
    return paused ? "not_reached" : "todo";
  };
  const titles = [["open", "Open the statement"], ["saved", "Check for a saved parser"], ["read", "Read the statement and write a parser"], ["submit", "Submit the parser and fix it until the checks pass"], ["moves", "Suggest moves between your own accounts"], ["report", "Report back"]];
  const stages = titles.map(([key, title]) => {
    const own = steps.filter((item) => item.stage === key);
    return { key, title, status: stageStatus(key), steps: own.map((item) => item.id), seconds: own.reduce((sum, item) => sum + (item.seconds ?? 0), 0) };
  });
  const used = paused ? 120 : 96;
  return {
    task_id: TASK_ID,
    agent_name: "parser",
    state: paused ? "finished" : "running",
    outcome: paused ? "budget" : null,
    started_at: Date.now() / 1000 - (paused ? 260 : 134),
    finished_at: paused ? Date.now() / 1000 : null,
    report: null,
    question: null,
    plan: [],
    imports: [{
      import_id: "imp_fixture_sbi", filename: "AccountStatement_Asha.pdf",
      status: paused ? "paused" : "parsing",
      reason: paused ? "The parser ran out of steps. Continue to give it another 120." : null,
      progress: paused ? null : "reading page 52 of 74", counts: null,
    }],
    messages: [],
    model: "deepseek-v4.1-flash",
    conversation_id: taskThread ?? "",
    follow_up: !paused ? null : wakeTurn && !wakeTurn.done ? "replying" : null,
    run: {
      stages, steps,
      segments: [{ run_id: "run_a", used, limit: 120, started_at: at(36, 41), ended_at: paused ? at(40, 59) : null, stop: paused ? "budget" : null }],
      used, limit: 120, stop: paused ? "budget" : null,
    },
  };
}

function seedShowcase() {
  const now = Date.now();
  const history = [
    ["thread_october", "October review", 1],
    ["thread_phone", "Can I afford a ₹40,000 phone?", 2],
    ["thread_rent", "Rent increase from January", 3],
    ["thread_cards", "Which card should I use?", 4],
    ["thread_goals", "Japan trip savings", 5],
    ["thread_backup", "Backup failed last night", 6],
    ["thread_insurance", "Insurance premium due", 7],
  ];
  for (const [id, title, daysAgo] of history) {
    const thread = newThread(id, title);
    thread.updated_at = new Date(now - daysAgo * 86_400_000).toISOString();
    thread.messages.push(
      uiTextMessage("user", `user_${id}`, title),
      {
        id: `assistant_${id}`,
        role: "assistant",
        parts: [{ type: "text", text: `Earlier conversation about ${title.toLowerCase()}.` }],
        metadata: { server_id: `assistant_${id}`, flagged_at: null },
      },
    );
  }

  const thread = newThread("thread_showcase", "Spending this month");
  thread.updated_at = new Date(now - 60_000).toISOString();
  thread.messages.push(
    uiTextMessage("user", "user_showcase", "Where did my money go this month?"),
    {
      id: "srv_showcase",
      role: "assistant",
      parts: [
        { type: "text", text: SHOWCASE_TEXT },
        { type: "data-card", id: "card_showcase", data: SHOWCASE_CARD },
        { type: "data-card", id: "chart_showcase", data: SHOWCASE_CHART },
        { type: "data-reply-model", id: "model_showcase", data: { model: "gpt-5.2", fallback: false } },
        { type: "data-number-flags", id: "flags_showcase", data: { amounts: [] } },
      ],
      metadata: { server_id: "srv_showcase", flagged_at: null },
    },
  );
}

function projectionFixture() {
  const curve = Array.from({ length: 12 }, (_, index) => {
    const date = new Date(Date.now() + index * 7 * 86_400_000).toISOString().slice(0, 10);
    const median = 4_800_000 - index * 38_000;
    return {
      date,
      median_paise: median,
      p10_paise: median - 280_000,
      p90_paise: median + 240_000,
    };
  });
  return {
    as_of: asOf(),
    horizon_days: 90,
    curve,
    floor_paise: 1_500_000,
    floor_set: true,
    floor_probability: 0.94,
    floor_threshold: 0.9,
    median_low: { date: curve.at(-1).date, paise: curve.at(-1).median_paise },
    first_floor_crossing: null,
    data_quality: { note: "Synthetic fixture data.", complete: true },
    runs: 500,
    sources: [],
  };
}

// Test-only: `ready` (default), `empty` (a first run), or `partial` (one balance still unknown).
let homeState = "ready";

function ledgerSpan() {
  if (homeState === "empty") return { movements: 0, spending: 0, first_date: null };
  return { movements: 212, spending: 168, first_date: "2026-09-01" };
}

function homePayload() {
  if (homeState === "empty") {
    return {
      as_of: asOf(),
      spendable_paise: 0,
      cash_quality: { status: "no_accounts", known_paise: 0, known_count: 0, unknown_count: 0, unknown_accounts: [] },
      projection: { ...projectionFixture(), data_quality: { setup_gaps: ["no liquid account"], balance_ages: [] } },
      unread_count: 0,
      needs_you: [],
      recent: [],
      ledger: ledgerSpan(),
    };
  }
  if (homeState === "partial") {
    return {
      as_of: asOf(),
      spendable_paise: 4_832_000,
      cash_quality: { status: "unknown", known_paise: 4_832_000, known_count: 1, unknown_count: 1, unknown_accounts: ["HDFC Savings"] },
      projection: {
        ...projectionFixture(),
        data_quality: { balance_ages: [{ account: "HDFC Savings", known: false }], setup_gaps: [] },
      },
      unread_count: 0,
      needs_you: [],
      recent: [],
      ledger: ledgerSpan(),
    };
  }
  return {
    ledger: ledgerSpan(),
    as_of: asOf(),
    spendable_paise: 4_812_400,
    cash_quality: {
      status: "known",
      known_paise: 4_812_400,
      known_count: 2,
      unknown_count: 0,
      unknown_accounts: [],
    },
    projection: projectionFixture(),
    unread_count: 0,
    needs_you: [],
    recent: [],
  };
}

const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jX2sAAAAASUVORK5CYII=",
  "base64",
);

function chunksForScenario(thread, scenario, text, messageId, approved = true) {
  const meta = { server_id: messageId };
  const start = { type: "start", messageId, messageMetadata: meta };

  if (scenario === "approval") {
    return [
      start,
      { type: "text-start", id: "txt_appr" },
      { type: "text-delta", id: "txt_appr", delta: "That transfer needs your confirmation before it leaves your account." },
      { type: "text-end", id: "txt_appr" },
      {
        type: "tool-input-available",
        toolCallId: "call_transfer",
        toolName: "transfer_money",
        input: { from_account: "HDFC Savings", to_payee: "Asha", amount_rupees: 12500 },
      },
      {
        type: "tool-approval-request",
        toolCallId: "call_transfer",
        approvalId: "approval_transfer",
        reason: "This moves money between accounts.",
        approvalDescriptor: { prompt: "Transfer ₹12,500 to Asha from HDFC Savings?" },
      },
      { type: "finish", finishReason: "tool-calls" },
    ];
  }

  if (scenario === "statement") {
    taskThread = thread.id;
    return [
      start,
      { type: "text-start", id: "txt_stmt" },
      { type: "text-delta", id: "txt_stmt", delta: "Got it. The parser is reading it now; follow each step from the card." },
      { type: "text-end", id: "txt_stmt" },
      {
        type: "tool-input-available",
        toolCallId: "call_parse",
        toolName: "delegate_task",
        input: { agent: "parser", task: "Import the statement files from this turn.", background: true },
      },
      {
        type: "tool-output-available",
        toolCallId: "call_parse",
        output: `Task ${TASK_ID} is running in the background. You'll get its report when it finishes.`,
      },
      { type: "finish", finishReason: "stop" },
    ];
  }

  if (scenario === "auto") {
    return [
      start,
      {
        type: "tool-input-available",
        toolCallId: "call_auto",
        toolName: "log_movement",
        input: { merchant: "Blue Tokai", amount_rupees: 380, category: "Dining" },
      },
      {
        type: "tool-approval-request",
        toolCallId: "call_auto",
        approvalId: "approval_auto",
        isAutomatic: true,
        approvalDescriptor: { prompt: "Automatic approval for a small, reversible entry." },
      },
      {
        type: "tool-output-available",
        toolCallId: "call_auto",
        output: { status: "logged", movement: "Blue Tokai ₹380" },
      },
      { type: "text-start", id: "txt_auto" },
      { type: "text-delta", id: "txt_auto", delta: "Logged Blue Tokai ₹380 under Dining automatically. Undo is available on the card." },
      { type: "text-end", id: "txt_auto" },
      { type: "data-card", id: "auto_card", data: { ...SHOWCASE_CARD, title: "Logged: Blue Tokai", amount: { text: "₹380" } } },
      { type: "finish", finishReason: "stop" },
    ];
  }

  if (scenario === "trace" || scenario === "trace-approval") {
    const pending = scenario === "trace-approval";
    return [
      start,
      { type: "reasoning-start", id: "rsn_trace" },
      {
        type: "reasoning-delta",
        id: "rsn_trace",
        delta: "Checking the ledger and the pinned November snapshot before answering.",
      },
      { type: "reasoning-end", id: "rsn_trace" },
      {
        type: "tool-input-available",
        toolCallId: "call_trace",
        toolName: "query_ledger",
        input: {
          script:
            "SELECT category, SUM(amount_paise) FROM movements GROUP BY category;",
        },
      },
      {
        type: "tool-output-available",
        toolCallId: "call_trace",
        output: { rows: 31, total_paise: 4218000 },
      },
      ...(pending
        ? [
            {
              type: "tool-input-available",
              toolCallId: "call_trace_transfer",
              toolName: "transfer_money",
              input: { to_payee: "Asha", amount_rupees: 12500 },
            },
            {
              type: "tool-approval-request",
              toolCallId: "call_trace_transfer",
              approvalId: "approval_trace_transfer",
              approvalDescriptor: { prompt: "Transfer ₹12,500 to Asha?" },
            },
          ]
        : []),
      { type: "text-start", id: "txt_trace" },
      {
        type: "text-delta",
        id: "txt_trace",
        delta:
          "You spent ₹42,180 across 31 transactions. The biggest change is dining, down ₹1,690.",
      },
      { type: "text-end", id: "txt_trace" },
      { type: "finish", finishReason: pending ? "tool-calls" : "stop" },
    ];
  }

  if (scenario === "markdown") {
    const body = `## Streaming markdown

The table and the fence below arrive in pieces while the answer streams.

| Item | Amount | Note |
| --- | ---: | --- |
| Rent | ₹18,000 | fixed |
| Dining | ₹8,420 | down from last month |
| Transport | ₹4,260 | auto and cabs |

\`\`\`sql
SELECT category, SUM(amount_paise) AS total
FROM movements
WHERE occurred_on >= '2026-11-01'
GROUP BY category
ORDER BY total DESC;
\`\`\`

\`\`\`sql
SELECT category, SUM(amount_paise) AS total, 'reconciliation-source: hdfc-credit-card-november-2026-statement-row-0042' AS source_label, COUNT(*) AS movement_count FROM movements WHERE occurred_on >= '2026-11-01' AND occurred_on <= '2026-11-30' GROUP BY category, source_label ORDER BY total DESC;
\`\`\`

Inline \`amount_paise\` stays readable. A [link](https://example.com) is text only, and ![remote](https://example.com/pixel.png) never loads.

<script>window.__fixtureXss = true</script>
<img src="https://example.com/pixel.png" alt="remote">`;
    const deltas = body.match(/[\s\S]{1,28}/g) ?? [body];
    return [
      start,
      { type: "text-start", id: "txt_markdown" },
      ...deltas.map((delta) => ({
        type: "text-delta",
        id: "txt_markdown",
        delta,
      })),
      { type: "text-end", id: "txt_markdown" },
      { type: "finish", finishReason: "stop" },
    ];
  }

  if (scenario === "contract" && contractAnswer) {
    return [
      start,
      ...(contractAnswer.fallback ? [{type: "data-card", id: "contract_fallback", data: contractAnswer.fallback}] : []),
      {type: "data-card", id: "contract_answer", data: contractAnswer.answer},
      {type: "finish", finishReason: "stop"},
    ];
  }

  if (scenario === "composed") {
    return [
      start,
      { type: "text-start", id: "txt_composed" },
      {
        type: "text-delta",
        id: "txt_composed",
        delta:
          "October spending was ₹36,790 across four categories. The table and chart below come from the same saved result.",
      },
      { type: "text-end", id: "txt_composed" },
      { type: "data-card", id: "fallback_composed", data: COMPOSED_FALLBACK_CARD },
      { type: "data-card", id: "answer_composed", data: COMPOSED_ANSWER_CARD },
      { type: "finish", finishReason: "stop" },
    ];
  }

  if (scenario === "malformed-answer") {
    return [
      start,
      { type: "text-start", id: "txt_malformed" },
      {
        type: "text-delta",
        id: "txt_malformed",
        delta: "This answer card is deliberately malformed so the fallback can be checked.",
      },
      { type: "text-end", id: "txt_malformed" },
      { type: "data-card", id: "answer_malformed", data: MALFORMED_ANSWER_CARD },
      { type: "finish", finishReason: "stop" },
    ];
  }

  if (scenario === "empty-answer") {
    return [
      start,
      { type: "text-start", id: "txt_empty" },
      {
        type: "text-delta",
        id: "txt_empty",
        delta: "July has no statement, so the total is unknown rather than zero.",
      },
      { type: "text-end", id: "txt_empty" },
      { type: "data-card", id: "answer_empty", data: EMPTY_ANSWER_CARD },
      { type: "finish", finishReason: "stop" },
    ];
  }

  if (scenario === "busy" || scenario === "reload") {
    const word = scenario === "busy" ? "Working through your ledger now." : "Replaying the full answer after reload.";
    const deltas = [];
    for (let index = 0; index < 24; index += 1) {
      deltas.push(`${index === 0 ? "" : " "}Part ${index + 1} of the answer.`);
    }
    return [
      start,
      { type: "text-start", id: "txt_busy" },
      { type: "text-delta", id: "txt_busy", delta: word },
      ...deltas.map((delta) => ({ type: "text-delta", id: "txt_busy", delta })),
      { type: "text-end", id: "txt_busy" },
      { type: "finish", finishReason: "stop" },
    ];
  }

  if (scenario === "auto-approval-continuation") {
    return [
      start,
      { type: "text-start", id: "txt_done" },
      { type: "text-delta", id: "txt_done", delta: text },
      { type: "text-end", id: "txt_done" },
      { type: "finish", finishReason: "stop" },
    ];
  }

  if (scenario === "approval-continuation") {
    return [
      { type: "start", messageId, messageMetadata: meta },
      {
        type: approved ? "tool-output-available" : "tool-output-denied",
        toolCallId: "call_transfer",
        ...(approved ? { output: { status: "moved", receipt: "transfer_fixture_1" } } : {}),
      },
      { type: "text-start", id: "txt_approved" },
      { type: "text-delta", id: "txt_approved", delta: text },
      { type: "text-end", id: "txt_approved" },
      { type: "finish", finishReason: "stop" },
    ];
  }

  return [
    start,
    { type: "text-start", id: `txt_${messageId}` },
    { type: "text-delta", id: `txt_${messageId}`, delta: text },
    { type: "text-end", id: `txt_${messageId}` },
    { type: "finish", finishReason: "stop" },
  ];
}

async function handleChat(req, res) {
  const body = await readJson(req);
  const messages = Array.isArray(body.messages) ? body.messages : [];
  const threadId = String(body.id ?? `thread_${Date.now()}`);
  const approval = approvalResponse(messages);
  const userText = lastUserText(messages);
  const statements = approval ? [] : statementRefs(messages);
  const photos = approval ? [] : photoRefs(messages);
  const scenario = approval ? "continuation" : userText.trim().toLowerCase();
  const requestMessage = messages.at(-1);

  counters.chatPosts.push({
    threadId,
    trigger: body.trigger,
    messageId: body.messageId,
    voiceAttachmentId: body.voice_attachment_id ?? null,
    statements,
    photos,
    lastMessage: requestMessage,
    messageCount: messages.length,
    approval,
  });

  const isPhotoCreated = threads.has(threadId);
  const thread = ensureThread(threadId, titleFor(userText));
  if (!approval) {
    const messageId = String(requestMessage?.id ?? body.messageId ?? `user_${Date.now()}`);
    if (!thread.messages.some((message) => message.id === messageId)) {
      thread.messages.push({
        id: messageId,
        role: "user",
        parts:
          requestMessage?.role === "user" && Array.isArray(requestMessage.parts)
            ? requestMessage.parts
            : [{ type: "text", text: userText }],
        ...(requestMessage?.metadata ? { metadata: requestMessage.metadata } : {}),
      });
    }
    if (!isPhotoCreated || thread.title === "Photo" || thread.title === "New chat") {
      thread.title = titleFor(userText);
    }
    touch(thread);
  }

  let responseText = `Here is a synthetic answer to: ${userText}\n\nThis paragraph exists only in the fixture. It has **bold**, a list, and a [link](https://example.com) that stays a link without loading an image.`;
  let chunks;

  if (approval) {
    // A tool continuation stays on the same assistant message id, so the tool
    // output resolves the approval that is already on that message.
    const messageId = String(requestMessage?.id ?? serverId("srv"));
    responseText = approval.approved
      ? "Transfer confirmed. ₹12,500 moved from HDFC Savings to Asha and the ledger entry is saved."
      : "Okay, I did not make that change. Nothing moved.";
    chunks = chunksForScenario(
      thread,
      "approval-continuation",
      responseText,
      messageId,
      approval.approved === true,
    );
    const turn = startTurn(thread, messageId, chunks, 60);
    streamTurn(req, res, turn);
    return;
  }

  const messageId = serverId("srv");
  if (scenario === "error") {
    json(res, 500, { title: "Synthetic failure", detail: "The fixture asked the client to retry." });
    return;
  } else {
    const delays = {
      busy: 650,
      reload: 450,
      approval: 60,
      auto: 60,
      trace: 80,
      "trace-approval": 80,
      markdown: 90,
      composed: 30,
      "malformed-answer": 30,
      "empty-answer": 30,
    };
    const scenarioKey = [
      "approval",
      "auto",
      "busy",
      "reload",
      "trace",
      "trace-approval",
      "markdown",
      "composed",
      "malformed-answer",
      "empty-answer",
      "statement",
    ].includes(scenario)
      ? scenario
      : {
          "show a composed answer": "composed",
          "show a contract answer": "contract",
          "show a malformed answer": "malformed-answer",
          "show an empty answer": "empty-answer",
        }[scenario] ?? "";
    chunks = chunksForScenario(thread, scenarioKey, responseText, messageId);
    if (statements.length || photos.length) {
      const importCards = statements.map((ref) => {
        const record = importFor(ref);
        return {
          type: "data-card",
          id: `card_${record.import_id}`,
          data: {
            card: "import",
            title: "Import started",
            import_id: record.import_id,
            filename: record.attachment.filename,
            status: record.status,
            reason: record.reason,
          },
        };
      });
      chunks = [
        ...chunks.slice(0, -1),
        ...importCards,
        ...photos.map(captureCardFor),
        chunks.at(-1),
      ];
    }
    const turn = startTurn(thread, messageId, chunks, delays[scenarioKey] ?? 30);
    return streamTurn(req, res, turn);
  }

  const turn = startTurn(thread, messageId, chunks, 60);
  streamTurn(req, res, turn);
}

function parseMultipartField(buffer, name) {
  const text = buffer.toString("latin1");
  const marker = `name="${name}"`;
  const start = text.indexOf(marker);
  if (start === -1) return null;
  const afterHeaders = text.indexOf("\r\n\r\n", start);
  if (afterHeaders === -1) return null;
  const valueStart = afterHeaders + 4;
  const valueEnd = text.indexOf("\r\n", valueStart);
  if (valueEnd === -1) return null;
  return text.slice(valueStart, valueEnd);
}

function parseMultipartFilename(buffer) {
  const text = buffer.toString("latin1");
  const match = /filename="([^"]*)"/i.exec(text);
  return match?.[1] || "statement.pdf";
}

function detectedKind(filename) {
  const name = filename.toLowerCase();
  if (name.endsWith(".pdf")) return "pdf";
  if (name.endsWith(".xlsx")) return "xlsx";
  if (name.endsWith(".xlsb")) return "xlsb";
  if (name.endsWith(".xls")) return "xls";
  if (name.endsWith(".ods")) return "ods";
  return "text";
}

async function handleUpload(req, res) {
  const buffer = await readBody(req);
  const filename = parseMultipartFilename(buffer);
  const purpose = parseMultipartField(buffer, "purpose") === "photo" ? "photo" : "statement";
  const id = `att_${nextAttachment++}`;
  const image = /\.(png|jpe?g|webp|gif)$/i.test(filename);
  const result = {
    id,
    filename,
    kind: purpose === "photo" ? "photo" : "statement",
    content_type: purpose === "photo" ? (image ? "image/png" : "application/octet-stream") : "application/pdf",
    size_bytes: buffer.length,
    detected_kind: purpose === "photo" ? "photo" : detectedKind(filename),
    created_at: new Date().toISOString(),
  };
  attachments.set(id, result);
  counters.uploadPosts.push({ filename, bytes: buffer.length, purpose });
  json(res, 201, result);
}

async function handlePhoto(req, res, threadId, attachmentId) {
  const buffer = await readBody(req);
  const thread = ensureThread(threadId, "Photo");
  counters.photoPosts.push({ threadId, attachmentId, bytes: buffer.length });
  await sleep(attachmentId ? 250 : 1600);
  const id = attachmentId ?? `att_${nextAttachment++}`;
  const clientId = `cap_${id}`;
  const card = {
    card: "capture",
    title: "Photo logged",
    amount_paise: 245000,
    merchant: "Toit",
    date: asOf(),
    method: "upi",
    upi_ref: "UPI-4421",
    attachment_id: id,
    undo_id: `undo_${id}`,
  };
  if (!thread.messages.some((message) => message.id === clientId)) {
    thread.messages.push({
      id: clientId,
      role: "assistant",
      parts: [{ type: "data-card", id: clientId, data: card }],
      metadata: { server_id: clientId, flagged_at: null },
    });
  }
  if (thread.title === "New chat") thread.title = "Photo";
  touch(thread);
  json(res, 200, { status: "logged", attachment_id: id, card });
}

async function handleTranscribe(req, res, attachmentId) {
  const buffer = await readBody(req);
  const threadId = parseMultipartField(buffer, "thread_id") ?? "";
  const resultId = attachmentId ?? `audio_${nextAttachment++}`;
  counters.transcribePosts.push({ threadId, attachmentId, resultId, bytes: buffer.length });
  await sleep(attachmentId ? 250 : 1400);
  json(res, 200, {
    status: "transcribed",
    attachment_id: resultId,
    transcript: "Spent ₹840 on groceries at BigBasket",
  });
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, BASE);
  const path = decodeURIComponent(url.pathname);
  counters.requests.push({ method: req.method, path, at: Date.now() });

  if (path === "/__fixture/state") {
    json(res, 200, {
      threads: [...threads.values()].map((thread) => ({
        id: thread.id,
        title: thread.title,
        messages: thread.messages.length,
        running: Boolean(turns.get(thread.id) && !turns.get(thread.id).done),
      })),
      counters,
      attachments: [...attachments.values()],
    });
    return;
  }
  if (path === "/__fixture/reset" && req.method === "POST") {
    resetState();
    json(res, 200, { ok: true });
    return;
  }
  // Test-only: exercise Python-produced cards through the real chat stream and saved history.
  if (path === "/__fixture/task" && req.method === "POST") {
    taskState = String((await readJson(req)).state ?? "running");
    json(res, 200, { ok: true });
    return;
  }
  // Test-only: the task pauses and the server starts a turn about its report, as `wake` does.
  if (path === "/__fixture/wake" && req.method === "POST") {
    const thread = taskThread ? threads.get(taskThread) : null;
    if (!thread) {
      json(res, 409, { error: "no statement thread" });
      return;
    }
    taskState = "paused";
    const messageId = `msg_wake_${Date.now()}`;
    wakeTurn = startTurn(thread, messageId, [
      { type: "start", messageId },
      { type: "text-start", id: "txt_wake" },
      { type: "text-delta", id: "txt_wake", delta: "Your statement import paused: the parser used all 120 of its steps. " },
      { type: "text-delta", id: "txt_wake", delta: "Nothing reached your ledger. Press Continue on the card to give it another 120." },
      { type: "text-end", id: "txt_wake" },
      { type: "finish", finishReason: "stop" },
    ], 2500);
    json(res, 200, { ok: true });
    return;
  }
  if (path === `/api/tasks/${TASK_ID}`) {
    json(res, 200, fixtureTask());
    return;
  }
  if (path === "/__fixture/answer" && req.method === "POST") {
    contractAnswer = await readJson(req);
    json(res, 200, {ok: true});
    return;
  }
  if (path === "/__fixture/seed" && req.method === "POST") {
    resetState();
    seedShowcase();
    json(res, 200, { ok: true });
    return;
  }
  if (path === "/__fixture/showcase-thread") {
    seedShowcase();
    const thread = threads.get("thread_showcase");
    json(res, 200, threadPayload(thread));
    return;
  }

  if (path === "/api/auth/state") {
    json(res, 200, {
      setup_required: false,
      authenticated: true,
      user: { id: "fixture_user", display_name: "Fixture" },
      rp_origin: BASE,
    });
    return;
  }
  if (path === "/api/auth/session") {
    json(res, 200, { user: { id: "fixture_user", display_name: "Fixture" } });
    return;
  }
  if (path === "/api/threads" && req.method === "GET") {
    json(res, 200, threadsPayload());
    return;
  }
  const threadMessages = path.match(/^\/api\/threads\/([^/]+)\/messages$/);
  if (threadMessages && req.method === "GET") {
    const thread = threads.get(threadMessages[1]);
    if (!thread) {
      json(res, 404, { title: "Not found", detail: "That thread does not exist." });
      return;
    }
    json(res, 200, threadPayload(thread));
    return;
  }
  const stop = path.match(/^\/api\/chat\/([^/]+)\/stop$/);
  if (stop && req.method === "POST") {
    const threadId = stop[1];
    counters.stopPosts.push({ threadId, at: Date.now() });
    const turn = turns.get(threadId);
    let stopped = false;
    if (turn && !turn.done) {
      stopped = true;
      pushChunk(turn, { type: "finish", finishReason: "stop" });
      finishTurn(turn);
    }
    json(res, 200, { stopped, thread_id: threadId });
    return;
  }
  const stream = path.match(/^\/api\/chat\/([^/]+)\/stream$/);
  if (stream && req.method === "GET") {
    const threadId = stream[1];
    counters.streamGets.push({ threadId, at: Date.now() });
    const turn = turns.get(threadId);
    if (!turn || turn.done) {
      res.writeHead(204);
      res.end();
      return;
    }
    streamTurn(req, res, turn);
    return;
  }
  const photoRetry = path.match(/^\/api\/chat\/([^/]+)\/photo\/([^/]+)$/);
  if (photoRetry && req.method === "POST") {
    await handlePhoto(req, res, photoRetry[1], photoRetry[2]);
    return;
  }
  const photo = path.match(/^\/api\/chat\/([^/]+)\/photo$/);
  if (photo && req.method === "POST") {
    await handlePhoto(req, res, photo[1]);
    return;
  }
  const transcribeRetry = path.match(/^\/api\/transcribe\/([^/]+)$/);
  if (transcribeRetry && req.method === "POST") {
    await handleTranscribe(req, res, transcribeRetry[1]);
    return;
  }
  if (path === "/api/transcribe" && req.method === "POST") {
    await handleTranscribe(req, res);
    return;
  }
  if (path === "/api/attachments" && req.method === "POST") {
    await handleUpload(req, res);
    return;
  }
  if (path === "/api/import-history") {
    json(res, 200, {
      items: [...imports.values()].map((record) => ({
        import_id: record.import_id,
        status: importPayload(record).status,
        filename: record.attachment.filename,
        account: null,
        period: record.period,
        check: record.check,
        counts: record.counts,
        reason: record.reason,
        created_at: record.created_at,
        committed_at: null,
        undone_at: null,
      })),
      next_cursor: null,
    });
    return;
  }
  const importDetail = path.match(/^\/api\/imports\/([^/]+)$/);
  if (importDetail && req.method === "GET") {
    const record = imports.get(importDetail[1]);
    counters.importDetailGets.push({ importId: importDetail[1], at: Date.now() });
    if (!record) {
      json(res, 404, { title: "Not found", detail: "No such import" });
      return;
    }
    json(res, 200, importPayload(record));
    return;
  }
  const attachment = path.match(/^\/api\/attachments\/([^/]+)$/);
  if (attachment && req.method === "GET") {
    res.writeHead(200, { "content-type": "image/png", "content-length": PNG.length });
    res.end(PNG);
    return;
  }
  if (path === "/api/chat" && req.method === "POST") {
    await handleChat(req, res);
    return;
  }
  if (path === "/__fixture/home" && req.method === "POST") {
    homeState = String((await readJson(req)).state ?? "ready");
    json(res, 200, { ok: true });
    return;
  }
  if (path === "/api/ledger/span") {
    json(res, 200, ledgerSpan());
    return;
  }
  if (path === "/api/home") {
    json(res, 200, homePayload());
    return;
  }
  if (path === "/api/notifications") {
    json(res, 200, { notifications: [], unread_count: 0, as_of: asOf(), next_cursor: null });
    return;
  }
  if (path === "/api/accounts") {
    json(res, 200, {
      accounts: [],
      liquid_total_paise: 0,
      card_owed_paise: 0,
      loan_outstanding_paise: 0,
      cash_quality: {
        status: "no_accounts",
        known_paise: 0,
        known_count: 0,
        unknown_count: 0,
        unknown_accounts: [],
      },
      as_of: asOf(),
    });
    return;
  }
  if (path === "/api/settings") {
    json(res, 200, {
      base_currency: "INR",
      time_zone: "Asia/Kolkata",
      floor_paise: 1500000,
      default_horizon_days: 90,
      notification_time: "08:00",
      weekly_briefing_dow: 0,
      reminder_lead_days: 3,
      profile: { user_id: "fixture_user", display_name: "Fixture" },
      last_model_error_type: null,
      last_model_error_at: null,
      updated_at: new Date().toISOString(),
    });
    return;
  }
  if (path === "/api/categories") {
    json(res, 200, { categories: [], rules: [], as_of: asOf() });
    return;
  }
  if (path === "/api/backup/status") {
    json(res, 200, {
      last_success_at: null,
      last_success_age_seconds: null,
      last_attempt: null,
      due_at: null,
      due: false,
      alert_at: null,
      overdue: false,
      checked_at: new Date().toISOString(),
    });
    return;
  }
  if (path === "/api/preferences") {
    json(res, 200, { preferences: [], as_of: asOf() });
    return;
  }
  if (path === "/api/jobs") {
    json(res, 200, { jobs: [], as_of: asOf() });
    return;
  }
  if (path === "/api/runtime-info") {
    json(res, 200, { as_of: asOf(), services: [] });
    return;
  }

  json(res, 404, { title: "Fixture route missing", detail: `${req.method} ${path}` });
});

resetState();
seedShowcase();
server.listen(PORT, "127.0.0.1", () => {
  console.log(`assistant-ui fixture API at ${BASE}`);
});
