import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { DefaultChatTransport, isToolUIPart, getToolName, lastAssistantMessageIsCompleteWithApprovalResponses } from "ai";
import { useChat } from "@ai-sdk/react";
import { Streamdown } from "streamdown";
import { api, errorMessage } from "../api";
import { Card, NumberFlags } from "./cards";
import type { ChatMessage, ThreadBody, ThreadList, ThreadSummary } from "./types";

const LINKED = {
  a: ({ children }: { children?: ReactNode }) => (
    <span className="plain-link">{children}</span>
  ),
  img: () => null,
};

function newThreadId(): string {
  return `cnv_${crypto.randomUUID()}`;
}

function groupMessages(messages: ChatMessage[]): ChatMessage[] {
  const grouped: ChatMessage[] = [];
  for (const message of messages) {
    const previous = grouped[grouped.length - 1];
    if (previous && previous.role === "assistant" && message.role === "assistant") {
      grouped[grouped.length - 1] = {
        ...previous,
        parts: [...previous.parts, ...message.parts],
        metadata: message.metadata ?? previous.metadata,
      };
    } else {
      grouped.push(message);
    }
  }
  return grouped;
}

function ThreadSidebar({
  threads,
  activeId,
  query,
  onQuery,
  onSelect,
  onNew,
}: {
  threads: ThreadSummary[];
  activeId: string;
  query: string;
  onQuery: (value: string) => void;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <aside className="chat-sidebar">
      <button type="button" className="new-chat" onClick={onNew}>
        + New chat
      </button>
      <input
        className="thread-search"
        value={query}
        placeholder="Search past messages"
        onChange={(event) => onQuery(event.target.value)}
      />
      <div className="thread-list">
        {threads.map((thread) => (
          <button
            key={thread.id}
            type="button"
            className={thread.id === activeId ? "thread active" : "thread"}
            onClick={() => onSelect(thread.id)}
          >
            <span className="thread-title">{thread.title}</span>
            <span className="fine">
              {thread.running ? "replying…" : `${thread.message_count} message(s)`}
            </span>
            {thread.snippet && <span className="thread-snippet">{thread.snippet}</span>}
          </button>
        ))}
        {threads.length === 0 && <p className="muted fine">No conversations yet.</p>}
      </div>
    </aside>
  );
}

function Part({ part }: { part: ChatMessage["parts"][number] }) {
  if (part.type === "text") {
    return (
      <Streamdown components={LINKED} mode="static" className="markdown">
        {part.text}
      </Streamdown>
    );
  }
  if (part.type.startsWith("data-")) {
    const data = (part as { data?: unknown }).data;
    if (part.type === "data-card" && data && typeof data === "object") {
      return <Card data={data as Record<string, unknown>} />;
    }
    if (part.type === "data-number-flags") {
      const amounts = (data as { amounts?: string[] } | undefined)?.amounts ?? [];
      return <NumberFlags amounts={amounts} />;
    }
    if (part.type === "data-model-badge") {
      const model = String((data as { model?: string } | undefined)?.model ?? "backup");
      return <p className="note">Answered by the backup model ({model}).</p>;
    }
    if (part.type === "data-ending") {
      const note = String((data as { note?: string } | undefined)?.note ?? "");
      return note ? <p className="note">{note}</p> : null;
    }
    return null;
  }
  return null;
}

function Message({
  message,
  busy,
  onApprove,
  onFlag,
}: {
  message: ChatMessage;
  busy: boolean;
  onApprove: (approvalId: string, approved: boolean) => void;
  onFlag: (message: ChatMessage) => void;
}) {
  const [flagged, setFlagged] = useState(Boolean(message.metadata?.flagged_at));
  const role = message.role;
  return (
    <article className={`bubble ${role}`}>
      <div className="bubble-head">
        <span className="eyebrow">{role === "user" ? "You" : "Fin-Agent"}</span>
        {role === "assistant" && (
          <button
            type="button"
            className="ghost small"
            disabled={flagged}
            onClick={() => {
              onFlag(message);
              setFlagged(true);
            }}
          >
            {flagged ? "Flagged" : "Flag"}
          </button>
        )}
      </div>

      {message.parts.map((part, index) => {
        if (isToolUIPart(part)) {
          const state = part.state;
          if (state === "approval-requested") {
            const reason =
              (part.approval as { requestReason?: string }).requestReason ??
              "This change lasts. Confirm it?";
            return (
              <div key={index} className="chat-card confirm">
                <strong>Confirm: {getToolName(part)}</strong>
                <p>{reason}</p>
                <pre className="sql">{JSON.stringify(part.input, null, 2)}</pre>
                <div className="button-row">
                  <button
                    type="button"
                    onClick={() => onApprove(part.approval.id, true)}
                    disabled={busy}
                  >
                    Confirm
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => onApprove(part.approval.id, false)}
                    disabled={busy}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            );
          }
          const toolName = getToolName(part);
          const output =
            state === "output-available" ? (part as { output?: unknown }).output : undefined;
          const outputText =
            typeof output === "string" ? output : output ? JSON.stringify(output, null, 2) : "";
          return (
            <details key={index} className="tool-line">
              <summary>
                {toolName} · {state.replaceAll("-", " ")}
              </summary>
              {outputText && <pre className="sql">{outputText.slice(0, 4000)}</pre>}
            </details>
          );
        }
        return <Part key={index} part={part} />;
      })}
    </article>
  );
}

function Conversation({
  threadId,
  initialMessages,
  onThreadChanged,
}: {
  threadId: string;
  initialMessages: ChatMessage[];
  onThreadChanged: () => void;
}) {
  const transport = useMemo(
    () =>
      new DefaultChatTransport<ChatMessage>({
        api: "/api/chat",
        prepareSendMessagesRequest: ({ id, messages, trigger, messageId }) => ({
          body: {
            id,
            trigger,
            messageId,
            // The server holds the thread's history; it only needs this turn's message.
            messages: messages.slice(-1),
          },
        }),
        prepareReconnectToStreamRequest: ({ id }) => ({
          api: `/api/chat/${id}/stream`,
        }),
        resumeStreamIsReplay: true,
      }),
    []
  );

  const {
    messages,
    sendMessage,
    status,
    stop,
    error,
    addToolApprovalResponse,
  } = useChat<ChatMessage>({
    id: threadId,
    messages: initialMessages,
    transport,
    resume: true,
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithApprovalResponses,
    onFinish: onThreadChanged,
  });

  const [input, setInput] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const busy = status === "submitted" || status === "streaming";
  const visibleMessages = useMemo(() => groupMessages(messages), [messages]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    await sendMessage({ text });
  }

  async function stopTurn() {
    try {
      await fetch(`/api/chat/${threadId}/stop`, {
        method: "POST",
        credentials: "same-origin",
      });
    } finally {
      await stop();
    }
  }

  async function flag(message: ChatMessage) {
    const serverId = message.metadata?.server_id;
    if (!serverId) return;
    await api(`/api/messages/${serverId}/flag`, { method: "POST" });
  }

  return (
    <section className="conversation">
      <header className="conversation-head">
        <div>
          <p className="eyebrow">Chat</p>
          <h2>{threadId.startsWith("cnv_") ? "Conversation" : "Conversation"}</h2>
        </div>
        {busy && (
          <button type="button" className="ghost" onClick={() => void stopTurn()}>
            Stop
          </button>
        )}
      </header>

      <div className="messages" aria-live="polite">
        {messages.length === 0 && (
          <div className="empty-chat">
            <h3>Ask anything about your money.</h3>
            <p className="muted">
              "How much did I spend on food last month?" · "Can I afford a ₹40,000 phone in
              December?" · "Paid 450 on Swiggy."
            </p>
          </div>
        )}
        {visibleMessages.map((message) => (
          <Message
            key={message.id}
            message={message}
            busy={busy}
            onApprove={(approvalId, approved) =>
              addToolApprovalResponse({ id: approvalId, approved })
            }
            onFlag={(item) => void flag(item)}
          />
        ))}
        {status === "submitted" && <p className="muted fine">Thinking…</p>}
        {error && (
          <div className="chat-card">
            <p className="error">{errorMessage(error)}</p>
            <button type="button" className="ghost small" onClick={() => void sendMessage({ text: "Try again" })}>
              Retry
            </button>
          </div>
        )}
        <div ref={bottom} />
      </div>

      <form className="composer" onSubmit={(event) => void submit(event)}>
        <textarea
          value={input}
          rows={2}
          placeholder="Message Fin-Agent…"
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void submit(event);
            }
          }}
        />
        <button type="submit" disabled={busy || !input.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}

export function ChatHome() {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activeId, setActiveId] = useState<string>(() => newThreadId());
  const [initial, setInitial] = useState<ChatMessage[]>([]);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadThreads(search = query) {
    const result = await api<ThreadList>(
      `/api/threads${search ? `?q=${encodeURIComponent(search)}` : ""}`
    );
    setThreads(result.threads);
    return result.threads;
  }

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const list = await loadThreads("");
        if (!alive) return;
        if (list.length > 0) {
          setActiveId(list[0].id);
        } else {
          setLoadedFor(activeId);
        }
      } catch (caught) {
        if (alive) setError(errorMessage(caught));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let alive = true;
    if (!threads.some((thread) => thread.id === activeId)) {
      return () => {
        alive = false;
      };
    }
    api<ThreadBody>(`/api/threads/${activeId}/messages`)
      .then((body) => {
        if (alive) {
          setInitial(body.messages);
          setLoadedFor(activeId);
        }
      })
      .catch((caught: unknown) => {
        if (alive) setError(errorMessage(caught));
      });
    return () => {
      alive = false;
    };
  }, [activeId, threads]);

  async function refresh() {
    try {
      await loadThreads(query);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  useEffect(() => {
    const handle = window.setTimeout(() => {
      void loadThreads(query).catch((caught: unknown) => setError(errorMessage(caught)));
    }, 250);
    return () => window.clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  if (loading) {
    return <p className="muted">Loading conversations…</p>;
  }

  return (
    <div className="chat-home">
      <ThreadSidebar
        threads={threads}
        activeId={activeId}
        query={query}
        onQuery={setQuery}
        onSelect={(id) => {
          setInitial([]);
          setLoadedFor(null);
          setActiveId(id);
        }}
        onNew={() => {
          const id = newThreadId();
          setInitial([]);
          setLoadedFor(id);
          setActiveId(id);
        }}
      />
      {error && <p className="error chat-error">{error}</p>}
      {loadedFor === activeId ? (
        <Conversation
          key={activeId}
          threadId={activeId}
          initialMessages={initial}
          onThreadChanged={() => void refresh()}
        />
      ) : (
        <p className="muted chat-loading">Loading conversation…</p>
      )}
    </div>
  );
}
