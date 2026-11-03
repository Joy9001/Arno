import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { DefaultChatTransport, isToolUIPart, getToolName, lastAssistantMessageIsCompleteWithApprovalResponses } from "ai";
import { useChat } from "@ai-sdk/react";
import { Streamdown } from "streamdown";
import { api, errorMessage } from "../api";
import { takeSharedImage } from "../share";
import { Card, NumberFlags } from "./cards";
import type { ChatMessage, ThreadBody, ThreadList, ThreadSummary } from "./types";

type CaptureCard = Record<string, unknown>;

type CaptureResult =
  | { status: "logged"; attachment_id: string; card: CaptureCard }
  | { status: "failed"; attachment_id: string; note: string; card: CaptureCard };

type TranscribeResult =
  | { status: "transcribed"; attachment_id: string; transcript: string }
  | { status: "failed"; attachment_id: string; note: string; card: CaptureCard };

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

function Part({
  part,
  onRetryCapture,
}: {
  part: ChatMessage["parts"][number];
  onRetryCapture?: (kind: string, attachmentId: string) => void;
}) {
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
      return (
        <Card data={data as Record<string, unknown>} onRetryCapture={onRetryCapture} />
      );
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
  onRetryCapture,
}: {
  message: ChatMessage;
  busy: boolean;
  onApprove: (approvalId: string, approved: boolean) => void;
  onFlag: (message: ChatMessage) => void;
  onRetryCapture: (kind: string, attachmentId: string, messageId: string) => void;
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
        return (
          <Part
            key={index}
            part={part}
            onRetryCapture={(kind, attachmentId) =>
              onRetryCapture(kind, attachmentId, message.id)
            }
          />
        );
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
  const voiceRef = useRef<string | null>(null);
  const transport = useMemo(
    () =>
      new DefaultChatTransport<ChatMessage>({
        api: "/api/chat",
        prepareSendMessagesRequest: ({ id, messages, trigger, messageId }) => {
          const voiceAttachment = voiceRef.current;
          voiceRef.current = null;
          return {
            body: {
              id,
              trigger,
              messageId,
              voice_attachment_id: voiceAttachment ?? undefined,
              // The server holds the thread's history; it only needs this turn's message.
              messages: messages.slice(-1),
            },
          };
        },
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
    setMessages,
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
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const holdingRef = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const pendingPhoto = useRef<File | null>(null);
  const [recording, setRecording] = useState(false);
  const canRecord =
    typeof MediaRecorder !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia);
  const [uploading, setUploading] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [captureError, setCaptureError] = useState<string | null>(null);
  const busy = status === "submitted" || status === "streaming";
  const visibleMessages = useMemo(() => groupMessages(messages), [messages]);

  useEffect(() => {
    return () => {
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      streamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  function appendCard(card: Record<string, unknown>, id: string) {
    setMessages((current) => [
      ...current,
      {
        id,
        role: "assistant",
        parts: [{ type: "data-card", id, data: card }],
      } as unknown as ChatMessage,
    ]);
  }

  function removeMessage(id: string) {
    setMessages((current) => current.filter((message) => message.id !== id));
  }

  async function uploadPhoto(file: File) {
    if (busy) {
      pendingPhoto.current = file;
      return;
    }
    setCaptureError(null);
    setUploading(true);
    const form = new FormData();
    form.append("file", file, file.name || "photo.png");
    try {
      const result = await api<CaptureResult>(`/api/chat/${threadId}/photo`, {
        method: "POST",
        body: form,
      });
      if (result.status === "logged") {
        appendCard(result.card, `photo_${result.attachment_id}`);
        onThreadChanged();
      } else {
        appendCard(result.card, `capture_retry_${result.attachment_id}`);
      }
    } catch (caught) {
      setCaptureError(errorMessage(caught));
    } finally {
      setUploading(false);
    }
  }

  useEffect(() => {
    if (!busy && pendingPhoto.current) {
      const file = pendingPhoto.current;
      pendingPhoto.current = null;
      void uploadPhoto(file);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy]);

  async function transcribe(blob: Blob, mimeType: string) {
    setCaptureError(null);
    setTranscribing(true);
    const form = new FormData();
    const extension = mimeType.includes("ogg")
      ? "ogg"
      : mimeType.includes("mp4")
        ? "m4a"
        : "webm";
    form.append("file", blob, `voice.${extension}`);
    form.append("thread_id", threadId);
    try {
      const result = await api<TranscribeResult>("/api/transcribe", {
        method: "POST",
        body: form,
      });
      if (result.status === "transcribed") {
        voiceRef.current = result.attachment_id;
        try {
          await sendMessage({ text: result.transcript });
        } catch (caught) {
          voiceRef.current = null;
          setInput(result.transcript);
          setCaptureError(errorMessage(caught));
        }
      } else {
        appendCard(result.card, `capture_retry_${result.attachment_id}`);
      }
    } catch (caught) {
      setCaptureError(errorMessage(caught));
    } finally {
      setTranscribing(false);
    }
  }

  async function retryCapture(kind: string, attachmentId: string, messageId: string) {
    setCaptureError(null);
    try {
      if (kind === "photo") {
        const result = await api<CaptureResult>(
          `/api/chat/${encodeURIComponent(threadId)}/photo/${encodeURIComponent(attachmentId)}`,
          { method: "POST" },
        );
        if (result.status === "logged") {
          removeMessage(messageId);
          appendCard(result.card, `photo_${attachmentId}`);
          onThreadChanged();
        } else {
          setCaptureError(result.note);
        }
        return;
      }
      const result = await api<TranscribeResult>(
        `/api/transcribe/${encodeURIComponent(attachmentId)}?thread_id=${encodeURIComponent(threadId)}`,
        { method: "POST" },
      );
      if (result.status === "transcribed") {
        removeMessage(messageId);
        voiceRef.current = attachmentId;
        await sendMessage({ text: result.transcript });
      } else {
        setCaptureError(result.note);
      }
    } catch (caught) {
      setCaptureError(errorMessage(caught));
    }
  }

  async function startRecording() {
    if (busy || transcribing || recording) return;
    if (typeof MediaRecorder === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setCaptureError("This browser cannot record audio.");
      return;
    }
    setCaptureError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const preferred = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "audio/mp4",
      ].find((type) => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(
        stream,
        preferred ? { mimeType: preferred } : undefined,
      );
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        recorderRef.current = null;
        setRecording(false);
        const type = recorder.mimeType || preferred || "audio/webm";
        const blob = new Blob(chunksRef.current, { type });
        chunksRef.current = [];
        if (blob.size) void transcribe(blob, type);
      };
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
      if (!holdingRef.current) stopRecording();
    } catch {
      setCaptureError("Microphone access was refused.");
    }
  }

  function stopRecording() {
    holdingRef.current = false;
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
  }

  useEffect(() => {
    let alive = true;
    void takeSharedImage().then((file) => {
      if (alive && file) void uploadPhoto(file);
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

  function onPointerDown(event: ReactPointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    holdingRef.current = true;
    void startRecording();
  }

  function onPointerUp(event: ReactPointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    stopRecording();
  }

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
            onRetryCapture={(kind, attachmentId, messageId) =>
              void retryCapture(kind, attachmentId, messageId)
            }
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
        <div className="composer-main">
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
          <div className="composer-actions">
            <button
              type="button"
              className="ghost"
              onClick={() => fileInput.current?.click()}
              disabled={busy || uploading}
            >
              {uploading ? "Reading…" : "Photo"}
            </button>
            <input
              ref={fileInput}
              type="file"
              accept="image/*"
              hidden
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                if (file) void uploadPhoto(file);
              }}
            />
            {canRecord && (
              <button
                type="button"
                className={recording ? "ghost record live" : "ghost record"}
                disabled={busy || transcribing}
                onPointerDown={onPointerDown}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerUp}
                onLostPointerCapture={() => {
                  if (holdingRef.current) stopRecording();
                }}
              >
                {recording ? "Listening… release" : transcribing ? "Transcribing…" : "Hold to talk"}
              </button>
            )}
          </div>
          {(recording || transcribing || uploading || captureError) && (
            <p className={captureError ? "error fine" : "fine"}>
              {captureError ??
                (recording
                  ? "Recording… release to send."
                  : transcribing
                    ? "Transcribing…"
                    : "Reading the photo…")}
            </p>
          )}
        </div>
        <button type="submit" disabled={busy || !input.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}

export function ChatHome({
  focusThreadId,
  onFocusHandled,
}: {
  focusThreadId?: string | null;
  onFocusHandled?: () => void;
}) {
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
        const wanted = focusThreadId && list.some((thread) => thread.id === focusThreadId)
          ? focusThreadId
          : list[0]?.id;
        if (wanted) {
          setActiveId(wanted);
        } else {
          setLoadedFor(activeId);
        }
        if (focusThreadId) onFocusHandled?.();
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
