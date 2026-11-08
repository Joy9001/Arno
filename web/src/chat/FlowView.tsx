import { useAuiState } from "@assistant-ui/react";
import { Orb } from "../Orb";
import { toolLabel } from "./tool-copy";

type Part = {
  type: string;
  text?: string;
  toolName?: string;
  toolCallId?: string;
  argsText?: string;
  args?: unknown;
  result?: unknown;
  status?: { type?: string };
  name?: string;
  data?: unknown;
};
type Message = { id: string; role: string; content: readonly Part[]; status?: { type?: string } };

type Turn = {
  id: string;
  question: string;
  tools: Array<{ id: string; label: string; detail: string; state: string }>;
  answer: string;
  verdict: string | null;
  running: boolean;
};

const VERDICT: Record<string, string> = { yes: "Yes", "yes-but": "Yes, with a change", no: "No" };

function brief(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value.slice(0, 90);
  try {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== null && item !== undefined && typeof item !== "object")
      .slice(0, 3)
      .map(([key, item]) => `${key.replaceAll("_", " ")} ${String(item).slice(0, 28)}`);
    return entries.join(" · ");
  } catch {
    return "";
  }
}

function verdictOf(parts: readonly Part[]): string | null {
  for (const part of parts) {
    const data = (part.type === "data" ? part.data : part.result) as Record<string, unknown> | undefined;
    const verdict = data && typeof data === "object" ? (data.verdict as string | undefined) : undefined;
    if (verdict && VERDICT[verdict]) return VERDICT[verdict];
  }
  return null;
}

/** Pairs each question with the agent's tool calls and its answer, straight from the messages. */
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
      current = { id: message.id, question: text || "A photo or voice note", tools: [], answer: "", verdict: null, running: false };
      turns.push(current);
      continue;
    }
    if (!current) {
      current = { id: message.id, question: "The agent started", tools: [], answer: "", verdict: null, running: false };
      turns.push(current);
    }
    for (const part of message.content) {
      if (part.type === "tool-call") {
        current.tools.push({
          id: part.toolCallId ?? `${message.id}-${current.tools.length}`,
          label: toolLabel(part.toolName ?? "tool"),
          detail: brief(part.args) || part.argsText?.slice(0, 90) || "",
          state: part.result === undefined ? "running" : part.status?.type === "incomplete" ? "failed" : "done",
        });
      }
      if (part.type === "text" && part.text) current.answer = `${current.answer} ${part.text}`.trim();
    }
    current.verdict = verdictOf(message.content) ?? current.verdict;
  }
  if (running && turns.length) turns[turns.length - 1].running = true;
  return turns;
}

/**
 * Flow: the conversation as the agent's work. Each question wires to the tools it ran, and
 * those wire to the answer. Everything shown is read from the thread's own messages.
 */
export function FlowView() {
  const messages = useAuiState((state) => state.thread.messages) as unknown as readonly Message[];
  const running = useAuiState((state) => state.thread.isRunning);
  const turns = toTurns(messages, running);

  if (!turns.length) {
    return (
      <div className="flow-empty">
        <Orb size={56} />
        <p>Ask something in Chat and its work shows up here as a graph.</p>
      </div>
    );
  }

  return (
    <div className="flow" aria-label="The agent's work, as a graph">
      {turns.map((turn) => (
        <section key={turn.id} className="flow-turn">
          <div className="flow-col">
            <article className="flow-node flow-q">
              <span className="flow-k">You</span>
              <p>{turn.question}</p>
            </article>
          </div>
          <div className="flow-col flow-tools">
            {turn.tools.length ? (
              turn.tools.map((tool) => (
                <article key={tool.id} className={`flow-node flow-tool ${tool.state}`}>
                  <span className="flow-k">
                    <i aria-hidden="true" />
                    {tool.label}
                  </span>
                  {tool.detail && <p className="flow-detail">{tool.detail}</p>}
                </article>
              ))
            ) : (
              <article className="flow-node flow-tool idle">
                <span className="flow-k">No tools</span>
                <p className="flow-detail">Answered from the conversation.</p>
              </article>
            )}
          </div>
          <div className="flow-col">
            <article className={turn.verdict ? "flow-node flow-a verdict" : "flow-node flow-a"}>
              <span className="flow-k">
                <Orb size={16} live={turn.running} thinking={turn.running} />
                {turn.running ? "Working…" : "Answer"}
              </span>
              {turn.verdict && <strong>{turn.verdict}</strong>}
              <p>{turn.answer ? `${turn.answer.slice(0, 220)}${turn.answer.length > 220 ? "…" : ""}` : "…"}</p>
            </article>
          </div>
        </section>
      ))}
    </div>
  );
}
