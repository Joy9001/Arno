import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useAuiState } from "@assistant-ui/react";
import { FileText } from "lucide-react";
import { askAgent } from "../ask";
import { openEvidence, openPlanner, openRun } from "../evidence";
import { formatPaise } from "../format";
import { Orb } from "../Orb";
import type { ProjectionPayload } from "../projection";
import { runEnding, useRun } from "../run/runData";
import { displayText } from "./display-text";
import { stepSummary, type StepRow, type StepSummary } from "./flow-steps";
import "./flow.css";

type Part = {
  type: string;
  text?: string;
  toolName?: string;
  toolCallId?: string;
  argsText?: string;
  args?: unknown;
  input?: unknown;
  result?: unknown;
  status?: { type?: string };
  name?: string;
  data?: unknown;
};

type Message = {
  id: string;
  role: string;
  content: readonly Part[];
  status?: { type?: string };
  createdAt?: Date;
  metadata?: { custom?: { server_id?: string } };
};

type StepState = "running" | "done" | "failed";

type Step = {
  id: string;
  toolName: string;
  args: unknown;
  result: unknown;
  status: StepState;
};

type Turn = {
  id: string;
  answerId: string | null;
  question: string;
  file: string | null;
  at: Date | null;
  doneAt: Date | null;
  steps: Step[];
  answer: string;
  receipt: ProjectionPayload | null;
  running: boolean;
};

type Branch = { key: string; kind: string; text: string; run: () => void };

const VERDICT: Record<string, string> = {
  yes: "Yes",
  "yes-but": "Yes, with a change",
  no: "No",
};

const COBALT = "#2b59ff";
const CYAN = "#1ec8a5";
const LIME = "#d4ff3a";

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function dateOf(value: unknown): Date | null {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === "string" || typeof value === "number") {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  return null;
}

/** The DOM id Chat gives a message, which is the server's id when it has one. */
function messageId(message: Message): string {
  const server = message.metadata?.custom?.server_id;
  return typeof server === "string" && server ? server : message.id;
}

function asReceipt(value: unknown): ProjectionPayload | null {
  if (!isRecord(value)) return null;
  if (typeof value.verdict !== "string" || !VERDICT[value.verdict]) return null;
  if (typeof value.floor_probability !== "number") return null;
  return value as unknown as ProjectionPayload;
}

/** Finds the projection receipt a card or tool result carries, however deeply it is nested. */
function findReceipt(value: unknown, depth = 0): ProjectionPayload | null {
  if (!value || typeof value !== "object" || depth > 4) return null;
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findReceipt(item, depth + 1);
      if (found) return found;
    }
    return null;
  }
  const direct = asReceipt(value);
  if (direct) return direct;
  for (const item of Object.values(value)) {
    const found = findReceipt(item, depth + 1);
    if (found) return found;
  }
  return null;
}

function receiptFromPart(part: Part): ProjectionPayload | null {
  if (isRecord(part.data) && part.data.card === "projection") {
    const receipt = asReceipt(part.data);
    if (receipt) return receipt;
  }
  const nested = part.data === undefined ? null : findReceipt(part.data);
  if (nested) return nested;
  return part.result === undefined ? null : findReceipt(part.result);
}

function toolOf(part: Part): Omit<Step, "id"> | null {
  let name: string | null = null;
  if (part.type === "tool-call") name = part.toolName ?? null;
  else if (part.type.startsWith("tool-")) name = part.type.slice(5);
  if (!name) return null;
  const failed =
    part.status?.type === "incomplete" || part.status?.type === "requires-action";
  return {
    toolName: name,
    args: part.args ?? part.input,
    result: part.result,
    status: failed ? "failed" : part.result === undefined ? "running" : "done",
  };
}

function statementOf(part: Part): string | null {
  const data = isRecord(part.data) ? part.data : null;
  if (!data) return null;
  const named =
    part.type === "data-statement" ||
    (part.type === "data" && part.name === "statement");
  if (!named) return null;
  return typeof data.filename === "string" && data.filename ? data.filename : "statement";
}

/** Pairs each question with the agent's steps, its answer, and any projection receipt. */
function toTurns(messages: readonly Message[], running: boolean): Turn[] {
  const turns: Turn[] = [];
  let current: Turn | null = null;
  for (const message of messages) {
    if (message.role === "user") {
      const text = message.content
        .filter((part) => part.type === "text")
        .map((part) => part.text ?? "")
        .join(" ")
        .trim();
      current = {
        id: messageId(message),
        answerId: null,
        question: text,
        file: message.content.map(statementOf).find((name) => Boolean(name)) ?? null,
        at: dateOf(message.createdAt),
        doneAt: null,
        steps: [],
        answer: "",
        receipt: null,
        running: false,
      };
      turns.push(current);
      continue;
    }
    if (!current) {
      current = {
        id: messageId(message),
        answerId: null,
        question: "The agent started",
        file: null,
        at: dateOf(message.createdAt),
        doneAt: null,
        steps: [],
        answer: "",
        receipt: null,
        running: false,
      };
      turns.push(current);
    }
    current.answerId ??= messageId(message);
    current.doneAt ??= dateOf(message.createdAt);
    for (const part of message.content) {
      const tool = toolOf(part);
      if (tool) {
        current.steps.push({
          id: part.toolCallId ?? `${messageId(message)}-${current.steps.length}`,
          ...tool,
        });
      }
      if (part.type === "text" && part.text?.trim()) {
        current.answer = current.answer ? `${current.answer}\n${part.text}` : part.text;
      }
      const strong =
        isRecord(part.data) && part.data.card === "projection"
          ? asReceipt(part.data)
          : null;
      if (strong) current.receipt = strong;
      else if (!current.receipt) {
        const receipt = receiptFromPart(part);
        if (receipt) current.receipt = receipt;
      }
    }
  }
  if (running && turns.length) turns[turns.length - 1]!.running = true;
  return turns;
}

function clockTime(date: Date | null): string {
  if (!date) return "";
  return date
    .toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true })
    .toLowerCase()
    .replace(/\u202f/g, " ");
}

function duration(from: Date | null, to: Date | null): string | null {
  if (!from || !to) return null;
  const ms = to.getTime() - from.getTime();
  if (!(ms >= 1000 && ms < 3_600_000)) return null;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}m ${whole % 60}s`;
}

function firstSentence(text: string): string {
  const clean = text.replace(/\s+/g, " ").trim();
  const match = /^(.+?[.!?])(\s|$)/.exec(clean);
  return (match?.[1] ?? clean).slice(0, 200);
}

function stripMarks(text: string): string {
  return text.replace(/[*_`#>]/g, "").replace(/\s+/g, " ");
}

/** **bold** only: the answer's own emphasis, built as nodes rather than parsed HTML. */
function markedText(text: string): ReactNode[] {
  // Tables and code blocks belong to the chat view; a summary card keeps the sentences.
  return displayText(text)
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/(?:\|[^|\n]*){2,}\|/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/\s+/g, " ")
    .trim()
    .split(/\*\*(.+?)\*\*/g)
    .map((chunk, index) =>
      index % 2 === 1 ? <b key={index}>{stripMarks(chunk)}</b> : stripMarks(chunk),
    );
}

function RichText({ text }: { text: string }) {
  return <p className="flow-a-body">{markedText(text)}</p>;
}

function percentText(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value * 100)}%`
    : "—";
}

function lowestText(receipt: ProjectionPayload | null): string {
  const low = receipt?.median_low;
  if (!low) return "—";
  const paise = low.amount?.paise ?? low.paise;
  return typeof paise === "number" ? formatPaise(paise) : "—";
}

function saferText(receipt: ProjectionPayload | null): string {
  const date = receipt?.tradeoffs?.[0]?.date;
  if (!date) return "—";
  const parsed = new Date(`${date.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return displayText(date);
  return parsed.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

function scrollMotion(): ScrollBehavior {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}

function branchesOf(receipt: ProjectionPayload | null): Branch[] {  if (!receipt) return [];
  const branches: Branch[] = [];
  for (const tradeoff of (receipt.tradeoffs ?? []).slice(0, 2)) {
    const detail = displayText(String(tradeoff.detail ?? "")).trim();
    const kind = displayText(String(tradeoff.kind ?? ""))
      .replaceAll("_", " ")
      .trim();
    branches.push({
      key: `${tradeoff.kind}-${branches.length}`,
      kind: kind ? kind[0]!.toUpperCase() + kind.slice(1) : "Option",
      text: detail || "Tell me about this option",
      run: () => askAgent(`Tell me more about this option: ${detail || "this option"}`),
    });
  }
  const sources = receipt.sources?.length ?? 0;
  if (sources > 0) {
    branches.push({
      key: "sources",
      kind: "Sources",
      text: `Show the ${sources} source ${sources === 1 ? "row" : "rows"}`,
      run: () => openEvidence(receipt),
    });
  }
  return branches;
}

type Box = {
  kind: "q" | "step" | "a" | "verdict" | "branch" | "try";
  x: number;
  y: number;
  w: number;
  h: number;
};

type Wire = {
  key: string;
  d: string;
  x1: number;
  x2: number;
  gradient: readonly [string, string] | null;
  stroke: string | null;
  dash: string | null;
  live: boolean;
  ports: Array<{ x: number; y: number; color: string }>;
};

type Measurement = {
  width: number;
  height: number;
  wires: Wire[];
  boxes: Box[];
  labels: Array<{ key: string; x: number; y: number }>;
};

const EMPTY: Measurement = { width: 0, height: 0, wires: [], boxes: [], labels: [] };

type Rect = { x: number; y: number; w: number; h: number };

function cubic(start: Rect, end: Rect): string {
  const x1 = start.x + start.w;
  const y1 = start.y + start.h / 2;
  const x2 = end.x;
  const y2 = end.y + end.h / 2;
  const mid = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
}

/** Reads every card the board mounted and turns the gaps between them into wires. */
function measureBoard(board: HTMLElement, turns: readonly Turn[]): Measurement {
  const boardRect = board.getBoundingClientRect();
  const scale = board.offsetWidth > 0 ? boardRect.width / board.offsetWidth : 1;
  const relative = (element: Element): Rect => {
    const rect = element.getBoundingClientRect();
    return {
      x: (rect.left - boardRect.left) / scale,
      y: (rect.top - boardRect.top) / scale,
      w: rect.width / scale,
      h: rect.height / scale,
    };
  };
  const elements = Array.from(board.querySelectorAll<HTMLElement>("[data-card]"));
  const cardsFor = (turnId: string, kind: string) =>
    elements
      .filter((element) => element.dataset.turn === turnId && element.dataset.card === kind)
      .sort((left, right) => Number(left.dataset.index ?? 0) - Number(right.dataset.index ?? 0));

  const wires: Wire[] = [];
  const labels: Measurement["labels"] = [];
  const boxes: Box[] = elements.map((element) => {
    const card = element.dataset.card;
    const kind: Box["kind"] =
      card === "q"
        ? "q"
        : card === "a"
          ? element.dataset.verdict === "1"
            ? "verdict"
            : "a"
          : card === "branch"
            ? "branch"
            : card === "try"
              ? "try"
              : "step";
    return { kind, ...relative(element) };
  });

  const solid = (
    key: string,
    d: string,
    x1: number,
    x2: number,
    from: string,
    to: string,
    live: boolean,
    startPort: { x: number; y: number },
    endPort: { x: number; y: number },
  ) => {
    wires.push({
      key,
      d,
      x1,
      x2,
      gradient: [from, to],
      stroke: null,
      dash: null,
      live,
      ports: [
        { ...startPort, color: from },
        { ...endPort, color: to },
      ],
    });
  };

  turns.forEach((turn, turnIndex) => {
    const questions = cardsFor(turn.id, "q");
    const answers = cardsFor(turn.id, "a");
    const question = questions[0];
    const answer = answers[0];
    if (!question || !answer) return;
    const q = relative(question);
    const a = relative(answer);
    const steps = cardsFor(turn.id, "step").map(relative);
    const branches = cardsFor(turn.id, "branch").map(relative);
    const live = turnIndex === turns.length - 1 && turn.running;

    const fromQuestion = { x: q.x + q.w, y: q.y + q.h / 2 };
    const low = a.y + 8;
    const high = Math.max(low, a.y + a.h - 8);
    const toAnswer = { x: a.x, y: Math.min(Math.max(fromQuestion.y, low), high) };

    if (steps.length === 0) {
      solid(
        `${turn.id}-direct`,
        `M ${fromQuestion.x} ${fromQuestion.y} L ${toAnswer.x} ${toAnswer.y}`,
        fromQuestion.x,
        toAnswer.x,
        COBALT,
        LIME,
        live,
        fromQuestion,
        toAnswer,
      );
      labels.push({
        key: `${turn.id}-label`,
        x: (fromQuestion.x + toAnswer.x) / 2,
        y: (fromQuestion.y + toAnswer.y) / 2,
      });
    } else {
      steps.forEach((step, index) => {
        const end = { x: step.x, y: step.y + step.h / 2 };
        solid(
          `${turn.id}-in-${index}`,
          cubic(q, step),
          fromQuestion.x,
          end.x,
          COBALT,
          CYAN,
          live,
          fromQuestion,
          end,
        );
      });
      steps.forEach((step, index) => {
        const start = { x: step.x + step.w, y: step.y + step.h / 2 };
        const y = a.y + a.h * Math.min(0.8, 0.3 + index * 0.15);
        const end = { x: a.x, y };
        const mid = (start.x + end.x) / 2;
        solid(
          `${turn.id}-out-${index}`,
          `M ${start.x} ${start.y} C ${mid} ${start.y}, ${mid} ${end.y}, ${end.x} ${end.y}`,
          start.x,
          end.x,
          CYAN,
          LIME,
          live,
          start,
          end,
        );
      });
    }

    branches.forEach((branch, index) => {
      const low = a.y + 8;
      const high = Math.max(low, a.y + a.h - 8);
      const start = {
        x: a.x + a.w,
        y: Math.min(Math.max(branch.y + branch.h / 2, low), high),
      };
      const end = { x: branch.x, y: branch.y + branch.h / 2 };
      const mid = (start.x + end.x) / 2;
      wires.push({
        key: `${turn.id}-branch-${index}`,
        d: `M ${start.x} ${start.y} C ${mid} ${start.y}, ${mid} ${end.y}, ${end.x} ${end.y}`,
        x1: start.x,
        x2: end.x,
        gradient: null,
        stroke: "rgba(255,255,255,.28)",
        dash: "4 6",
        live: false,
        ports: [],
      });
    });

    const tryCard = cardsFor(turn.id, "try")[0];
    if (tryCard) {
      const t = relative(tryCard);
      wires.push({
        key: `${turn.id}-try`,
        d: `M ${a.x + a.w / 2} ${a.y + a.h} L ${t.x + t.w / 2} ${t.y}`,
        x1: a.x + a.w / 2,
        x2: t.x + t.w / 2,
        gradient: null,
        stroke: "rgba(255,255,255,.28)",
        dash: "4 6",
        live: false,
        ports: [],
      });
    }
  });

  for (let index = 0; index < turns.length - 1; index += 1) {
    const currentQuestion = cardsFor(turns[index]!.id, "q")[0];
    const nextQuestion = cardsFor(turns[index + 1]!.id, "q")[0];
    if (!currentQuestion || !nextQuestion) continue;
    const from = relative(currentQuestion);
    const to = relative(nextQuestion);
    wires.push({
      key: `${turns[index]!.id}-next`,
      d: `M ${from.x + from.w / 2} ${from.y + from.h} L ${to.x + to.w / 2} ${to.y}`,
      x1: from.x + from.w / 2,
      x2: to.x + to.w / 2,
      gradient: null,
      stroke: "rgba(255,255,255,.16)",
      dash: "3 6",
      live: false,
      ports: [],
    });
  }

  return { width: board.offsetWidth, height: board.offsetHeight, wires, boxes, labels };
}

function StepShell({
  turn,
  index,
  summary,
  tone,
  rows,
  action,
}: {
  turn: string;
  index: number;
  summary: StepSummary;
  tone: StepSummary["tone"];
  rows?: StepRow[];
  action?: ReactNode;
}) {
  const list = rows ?? summary.rows;
  return (
    <article
      className={tone === "done" ? "flow-step" : `flow-step ${tone}`}
      data-card="step"
      data-turn={turn}
      data-index={index}
    >
      <div className="flow-step-head">
        <span className="flow-dot" aria-hidden="true" />
        <b className="flow-step-title">{summary.title}</b>
      </div>
      <span className="flow-step-id">{summary.id}</span>
      {list.length > 0 && (
        <dl className="flow-step-dl">
          {list.map((row, rowIndex) => (
            <div key={`${rowIndex}-${row.label}`}>
              <dt>{row.label}</dt>
              <dd className={row.warn ? "warn" : undefined}>{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {action && <div className="flow-step-action">{action}</div>}
    </article>
  );
}

function DelegateStep({
  turn,
  index,
  taskId,
  summary,
}: {
  turn: string;
  index: number;
  taskId: string;
  summary: StepSummary;
}) {
  const { task } = useRun(taskId);
  const ending = task ? runEnding(task) : null;
  const tone: StepSummary["tone"] = ending
    ? ending.tone === "live"
      ? "running"
      : ending.tone === "done"
        ? "done"
        : "warn"
    : "running";
  const written = task
    ? task.imports.some((item) => item.status === "committed")
    : false;
  const rows: StepRow[] =
    task && ending
      ? [
          {
            label: "Status",
            value: displayText(ending.line),
            warn: ending.tone === "paused" || ending.tone === "failed",
          },
          { label: "Steps", value: `${task.run.used} of ${task.run.limit}` },
          { label: "Your ledger", value: written ? "Written" : "Nothing written" },
        ]
      : summary.rows;
  return (
    <StepShell
      turn={turn}
      index={index}
      summary={summary}
      tone={tone}
      rows={rows}
      action={
        <button type="button" className="pill-button" onClick={() => openRun(taskId)}>
          Open run
        </button>
      }
    />
  );
}

function StepCard({ turn, index, step }: { turn: string; index: number; step: Step }) {
  const summary = stepSummary(step.toolName, step.args, step.result);
  if (summary.taskId) {
    return <DelegateStep turn={turn} index={index} taskId={summary.taskId} summary={summary} />;
  }
  const tone: StepSummary["tone"] =
    step.status === "failed" ? "warn" : step.status === "running" ? "running" : "done";
  return <StepShell turn={turn} index={index} summary={summary} tone={tone} />;
}

const PRICE_STEP = 50_000;

function TryCard({ turn, receipt }: { turn: string; receipt: ProjectionPayload }) {
  const hypothetical = isRecord(receipt.hypothetical) ? receipt.hypothetical : {};
  const original =
    typeof hypothetical.amount_paise === "number" && hypothetical.amount_paise > 0
      ? hypothetical.amount_paise
      : null;
  const [price, setPrice] = useState(original ?? 0);
  if (original === null) return null;
  const min = Math.max(PRICE_STEP, Math.round((original * 0.25) / PRICE_STEP) * PRICE_STEP);
  const max = Math.max(min + PRICE_STEP, Math.round((original * 2) / PRICE_STEP) * PRICE_STEP);
  const value = Math.min(max, Math.max(min, price));
  const commit = () => {
    openPlanner({ ...receipt, hypothetical: { ...hypothetical, amount_paise: value } });
  };
  return (
    <div className="flow-try" data-card="try" data-turn={turn}>
      <div className="flow-step-head">
        <span className="flow-dot" aria-hidden="true" />
        <b className="flow-step-title">Try another price</b>
        <span className="flow-step-id flow-try-note">re-runs the projection</span>
      </div>
      <div className="flow-try-row">
        <span>Price</span>
        <input
          type="range"
          className="flow-range"
          min={min}
          max={max}
          step={PRICE_STEP}
          value={value}
          aria-label="Price to try"
          onChange={(event) => setPrice(Number(event.target.value))}
          onPointerUp={commit}
          onKeyUp={commit}
        />
        <span className="flow-try-value">{formatPaise(value)}</span>
      </div>
    </div>
  );
}

function AnswerCard({
  turn,
  onOpenChat,
}: {
  turn: Turn;
  onOpenChat?: (messageId: string) => void;
}) {
  const receipt = turn.receipt;
  const verdict = receipt?.verdict ? VERDICT[receipt.verdict] : null;
  const sources = receipt?.sources?.length ?? 0;
  const took = duration(turn.at, turn.doneAt);
  const meta = sources > 0 ? `${sources} ${sources === 1 ? "source" : "sources"}` : took ?? "";
  const body = verdict
    ? firstSentence(turn.answer) ||
      displayText(receipt?.verdict_reason ?? "") ||
      "…"
    : turn.running
      ? "…"
      : turn.answer || "No answer yet.";
  return (
    <article
      className={verdict ? "flow-a verdict" : "flow-a"}
      data-card="a"
      data-turn={turn.id}
      data-verdict={verdict ? "1" : undefined}
    >
      <div className="flow-a-head">
        <Orb size={16} live={turn.running} thinking={turn.running} />
        <span>{turn.running ? "Working…" : "Answer"}</span>
        <span className="flow-a-meta">{meta}</span>
      </div>
      {verdict ? (
        <>
          <b className="flow-a-verdict">{verdict}</b>
          <RichText text={body} />
          <div className="flow-a-facts">
            <div>
              <small>Floor holds</small>
              <b>{percentText(receipt?.floor_probability)}</b>
            </div>
            <div>
              <small>Lowest</small>
              <b>{lowestText(receipt)}</b>
            </div>
            <div>
              <small>Safer</small>
              <b>{saferText(receipt)}</b>
            </div>
          </div>
        </>
      ) : (
        <RichText text={body} />
      )}
      {onOpenChat && !turn.running && turn.answerId && (
        <button
          type="button"
          className="flow-a-more"
          onClick={() => onOpenChat(turn.answerId!)}
        >
          Open in chat
        </button>
      )}
    </article>
  );
}

function TurnRow({
  turn,
  onOpenChat,
}: {
  turn: Turn;
  onOpenChat?: (messageId: string) => void;
}) {
  const branches = branchesOf(turn.receipt);
  const time = clockTime(turn.at);
  const hypothetical = turn.receipt?.hypothetical;
  const original = isRecord(hypothetical) ? hypothetical.amount_paise : undefined;
  return (
    <section className="flow-turn" data-turn={turn.id}>
      <article className="flow-q" data-card="q" data-turn={turn.id}>
        <span className="flow-q-k">{time ? `You · ${time}` : "You"}</span>
        <p>{displayText(turn.question || "A photo or voice note")}</p>
        {turn.file && (
          <span className="flow-file">
            <FileText size={14} strokeWidth={2} aria-hidden="true" />
            {displayText(turn.file)}
          </span>
        )}
      </article>

      <div className="flow-steps">
        {turn.steps.map((step, index) => (
          <StepCard key={step.id} turn={turn.id} index={index} step={step} />
        ))}
      </div>

      <div className="flow-answers">
        <AnswerCard turn={turn} onOpenChat={onOpenChat} />
        {turn.receipt &&
          Array.isArray(turn.receipt.curve) &&
          typeof original === "number" && (
            <TryCard
              key={`${turn.id}-${original}`}
              turn={turn.id}
              receipt={turn.receipt}
            />
          )}
      </div>

      <div className="flow-branches">
        {branches.map((branch, index) => (
          <button
            key={branch.key}
            type="button"
            className="flow-branch"
            data-card="branch"
            data-turn={turn.id}
            data-index={index}
            onClick={branch.run}
          >
            <small>{branch.kind}</small>
            <b>{branch.text}</b>
          </button>
        ))}
      </div>
    </section>
  );
}

const MAP_W = 150;
const MAP_H = 92;
const MAP_PAD = 10;

/**
 * Flow: the conversation as the agent's work. Each question wires to the steps it ran and to
 * the answer, and the answer's alternatives branch off it. Everything shown is read from the
 * thread's own messages.
 */
export function FlowView({
  fit = false,
  onOpenChat,
}: {
  fit?: boolean;
  onOpenChat?: (messageId: string) => void;
}) {
  const messages = useAuiState((state) => state.thread.messages) as unknown as readonly Message[];
  const running = useAuiState((state) => state.thread.isRunning);
  const turns = useMemo(() => toTurns(messages, running), [messages, running]);
  // The branch column only takes room when some answer has branches.
  const anyBranches = turns.some((turn) => branchesOf(turn.receipt).length > 0);
  const boardRef = useRef<HTMLDivElement>(null);
  const flowRef = useRef<HTMLDivElement>(null);
  const minimapRef = useRef<HTMLButtonElement>(null);
  const lastKey = useRef("");
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const [measured, setMeasured] = useState<Measurement>(EMPTY);
  const [view, setView] = useState({ x: 0, y: 0, w: 0, h: 0, vw: 0, vh: 0 });

  const readView = useCallback(() => {
    const flow = flowRef.current;
    const board = boardRef.current;
    if (!flow || !board) return;
    const flowRect = flow.getBoundingClientRect();
    const boardRect = board.getBoundingClientRect();
    const scale = board.offsetWidth > 0 ? boardRect.width / board.offsetWidth : 1;
    const next = {
      x: Math.max(0, (flowRect.left - boardRect.left) / scale),
      y: Math.max(0, (flowRect.top - boardRect.top) / scale),
      w: Math.min(board.offsetWidth, flowRect.width / scale),
      h: Math.min(board.offsetHeight, flowRect.height / scale),
      vw: flow.clientWidth,
      vh: flow.clientHeight,
    };
    setView((previous) =>
      previous.x === next.x &&
      previous.y === next.y &&
      previous.w === next.w &&
      previous.h === next.h &&
      previous.vw === next.vw &&
      previous.vh === next.vh
        ? previous
        : next,
    );
  }, []);

  useLayoutEffect(() => {
    const board = boardRef.current;
    if (!board) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const next = measureBoard(board, turns);
      const key = JSON.stringify(next);
      if (key !== lastKey.current) {
        lastKey.current = key;
        setMeasured(next);
      }
      readView();
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    schedule();
    const observer = new ResizeObserver(schedule);
    observer.observe(board);
    for (const element of board.querySelectorAll("[data-card]")) observer.observe(element);
    window.addEventListener("resize", schedule);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", schedule);
    };
  }, [turns, readView]);

  useEffect(() => {
    const flow = flowRef.current;
    if (!flow) return;
    flow.addEventListener("scroll", readView, { passive: true });
    return () => flow.removeEventListener("scroll", readView);
  }, [readView]);

  useEffect(() => {
    readView();
  }, [fit, readView]);

  if (!turns.length) {
    return (
      <div className="flow-empty">
        <Orb size={56} />
        <p>Ask something in Chat and its work shows up here as a graph.</p>
      </div>
    );
  }

  const fitScale =
    fit && measured.width > 0 && view.vw > 0
      ? Math.min(1, view.vw / measured.width, view.vh / measured.height)
      : 1;
  const mapScale = Math.max(
    0.0001,
    Math.min(
      (MAP_W - 2 * MAP_PAD) / Math.max(1, measured.width),
      (MAP_H - 2 * MAP_PAD) / Math.max(1, measured.height),
    ),
  );
  const mapX = (MAP_W - measured.width * mapScale) / 2;
  const mapY = (MAP_H - measured.height * mapScale) / 2;

  const jump = (clientX: number, clientY: number) => {
    const minimap = minimapRef.current;
    const flow = flowRef.current;
    if (!minimap || !flow || measured.width === 0) return;
    const rect = minimap.getBoundingClientRect();
    const x = (((clientX - rect.left) / rect.width) * MAP_W - mapX) / mapScale;
    const y = (((clientY - rect.top) / rect.height) * MAP_H - mapY) / mapScale;
    flow.scrollTo({
      left: Math.max(0, x - flow.clientWidth / 2),
      top: Math.max(0, y - flow.clientHeight / 2),
      behavior: scrollMotion(),
    });
  };

  return (
    <div className="flow-shell">
      <div className="flow" ref={flowRef} aria-label="The agent's work, as a graph">
        <div
          className={anyBranches ? "flow-board with-branches" : "flow-board"}
          ref={boardRef}
          style={fitScale === 1 ? undefined : { transform: `scale(${fitScale})` }}
        >
          <svg
            className="flow-wires"
            width={measured.width}
            height={measured.height}
            aria-hidden="true"
          >
            <defs>
              {measured.wires.map((wire, index) =>
                wire.gradient ? (
                  <linearGradient
                    key={wire.key}
                    id={`${uid}-g${index}`}
                    gradientUnits="userSpaceOnUse"
                    x1={wire.x1}
                    x2={wire.x2}
                    y1={0}
                    y2={0}
                  >
                    <stop offset="0" stopColor={wire.gradient[0]} />
                    <stop offset="1" stopColor={wire.gradient[1]} />
                  </linearGradient>
                ) : null,
              )}
            </defs>
            {measured.wires.map((wire, index) => (
              <path
                key={wire.key}
                className={wire.live ? "flow-wire live" : "flow-wire"}
                d={wire.d}
                stroke={wire.gradient ? `url(#${uid}-g${index})` : wire.stroke ?? "transparent"}
                strokeDasharray={wire.dash ?? undefined}
              />
            ))}
            {measured.wires.flatMap((wire) =>
              wire.ports.map((port, index) => (
                <circle
                  key={`${wire.key}-${index}`}
                  className="flow-port"
                  cx={port.x}
                  cy={port.y}
                  r={4.5}
                  stroke={port.color}
                />
              )),
            )}
          </svg>
          {measured.labels.map((label) => (
            <span
              key={label.key}
              className="flow-direct"
              style={{ left: label.x, top: label.y }}
            >
              answered from the conversation
            </span>
          ))}
          {turns.map((turn) => (
            <TurnRow key={turn.id} turn={turn} onOpenChat={onOpenChat} />
          ))}
        </div>
      </div>

      <button
        type="button"
        ref={minimapRef}
        className="flow-minimap"
        aria-label="Minimap: jump to a part of the conversation"
        onPointerDown={(event) => jump(event.clientX, event.clientY)}
        onPointerMove={(event) => {
          if (event.buttons === 1) jump(event.clientX, event.clientY);
        }}
        onKeyDown={(event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          const flow = flowRef.current;
          flow?.scrollBy({ top: flow.clientHeight * 0.8, behavior: scrollMotion() });
        }}
      >
        <svg width={MAP_W} height={MAP_H} viewBox={`0 0 ${MAP_W} ${MAP_H}`} aria-hidden="true">
          {measured.boxes.map((box, index) => {
            const x = mapX + box.x * mapScale;
            const y = mapY + box.y * mapScale;
            const width = Math.max(2, box.w * mapScale);
            const height = Math.max(2, box.h * mapScale);
            if (box.kind === "branch") {
              return (
                <rect
                  key={index}
                  x={x}
                  y={y}
                  width={width}
                  height={height}
                  rx={2}
                  fill="none"
                  stroke="rgba(255,255,255,.35)"
                  strokeWidth={0.75}
                />
              );
            }
            const fill =
              box.kind === "q" ? COBALT : box.kind === "verdict" ? LIME : "var(--surface-3)";
            return (
              <rect
                key={index}
                x={x}
                y={y}
                width={width}
                height={height}
                rx={2}
                fill={fill}
                opacity={box.kind === "a" ? 0.55 : 1}
              />
            );
          })}
          {view.w > 0 && (
            <rect
              x={mapX + view.x * mapScale}
              y={mapY + view.y * mapScale}
              width={Math.max(4, view.w * mapScale)}
              height={Math.max(4, view.h * mapScale)}
              fill="none"
              stroke="rgba(255,255,255,.5)"
              strokeWidth={1.5}
            />
          )}
        </svg>
      </button>
    </div>
  );
}
