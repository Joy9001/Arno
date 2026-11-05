import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import * as Popover from "@radix-ui/react-popover";
import {
  ArrowUp,
  Calculator,
  Camera,
  History,
  LogIn,
  Mic,
  Plus,
  ReceiptText,
  RotateCcw,
  Search,
  Square,
  Wallet,
  X,
} from "lucide-react";
import { APICallError, DefaultChatTransport, isToolUIPart, lastAssistantMessageIsCompleteWithApprovalResponses } from "ai";
import { useChat } from "@ai-sdk/react";
import { Streamdown } from "streamdown";
import { api, errorMessage } from "../api";
import { Busy, Typing } from "../Loading";
import type { ProjectionPayload } from "../projection";
import { takeSharedImage } from "../share";
import { ConversationSkeleton } from "../Skeleton";
import { Card, NumberFlags } from "./cards";
import { ToolPart } from "./ToolPart";
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

const STARTERS = [
  { Icon: ReceiptText, text: "How much did I spend on food last month?" },
  { Icon: Calculator, text: "Can I afford a ₹40,000 phone in December?" },
  { Icon: Wallet, text: "What is due in the next 30 days?" },
];

function newThreadId(): string {
  return `cnv_${crypto.randomUUID()}`;
}

/** One short sentence for the user; provider payloads stay out of the transcript. */
function chatErrorMessage(error: Error): string {
  if (APICallError.isInstance(error)) {
    if (error.statusCode === 401) {
      return "Your session expired. Reload the page and sign in again.";
    }
    if (error.isRetryable) {
      return "Fin-Agent could not be reached or is temporarily unavailable. Try again.";
    }
  }
  return "Fin-Agent could not finish that answer. Your earlier messages are still here. Try again.";
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
        <Plus size={15} strokeWidth={1.8} aria-hidden="true" />
        New chat
      </button>
      <label className="search-field">
        <Search size={14} strokeWidth={1.8} aria-hidden="true" />
        <input
          className="thread-search"
          value={query}
          placeholder="Search past messages"
          aria-label="Search past messages"
          onChange={(event) => onQuery(event.target.value)}
        />
      </label>
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
              {thread.running ? (
                <>
                  <Typing />
                  replying…
                </>
              ) : (
                `${thread.message_count} ${thread.message_count === 1 ? "message" : "messages"}`
              )}
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
  onEvidence,
  hideLegacyBadge,
}: {
  part: ChatMessage["parts"][number];
  onRetryCapture?: (kind: string, attachmentId: string) => void;
  onEvidence?: (payload: ProjectionPayload) => void;
  hideLegacyBadge?: boolean;
}) {
  if (part.type === "text") {
    return (
      <Streamdown components={LINKED} mode="static" className="markdown">
        {part.text}
      </Streamdown>
    );
  }
  if (part.type === "data-card") {
    return (
      <Card
        data={part.data}
        onRetryCapture={onRetryCapture}
        onEvidence={onEvidence}
      />
    );
  }
  if (part.type === "data-number-flags") {
    return <NumberFlags amounts={part.data.amounts} />;
  }
  if (part.type === "data-model-badge") {
    // New saves carry data-reply-model with the model that really answered; the old badge is
    // only kept for transcripts saved before that part existed.
    if (hideLegacyBadge) return null;
    return <p className="note">Answered by the backup model ({part.data.model}).</p>;
  }
  if (part.type === "data-reply-model") {
    return (
      <p className="fine">
        Model: {part.data.model}
        {part.data.fallback ? " (fallback)" : ""}
      </p>
    );
  }
  if (part.type === "data-ending") {
    return part.data.note ? <p className="note">{part.data.note}</p> : null;
  }
  return null;
}

function Message({
  message,
  busy,
  onApprove,
  onFlag,
  onRetryCapture,
  onEvidence,
}: {
  message: ChatMessage;
  busy: boolean;
  onApprove: (approvalId: string, approved: boolean) => void;
  onFlag: (message: ChatMessage) => void;
  onRetryCapture: (kind: string, attachmentId: string, messageId: string) => void;
  onEvidence?: (payload: ProjectionPayload) => void;
}) {
  const [flagged, setFlagged] = useState(Boolean(message.metadata?.flagged_at));
  const role = message.role;
  const serverId = message.metadata?.server_id;
  const hasReplyModel = message.parts.some((part) => part.type === "data-reply-model");
  // "assistant" is the panel's layout class; a bubble wearing it became a flex item and
  // overflowed its own height. Use a role class the panel never matches.
  return (
    <article
      id={serverId ? `message-${serverId}` : undefined}
      tabIndex={serverId ? -1 : undefined}
      className={`bubble ${role === "assistant" ? "agent" : role}`}
    >
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
          return (
            <ToolPart
              key={index}
              part={part}
              busy={busy}
              onApprove={onApprove}
            />
          );
        }
        return (
          <Part
            key={index}
            part={part}
            onEvidence={onEvidence}
            hideLegacyBadge={hasReplyModel}
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
  seed = null,
  focusSignal,
  onSeedConsumed,
  onEvidence,
  focusMessage = null,
}: {
  threadId: string;
  initialMessages: ChatMessage[];
  onThreadChanged: () => void;
  seed?: { id: number; text: string } | null;
  focusSignal?: number;
  onSeedConsumed?: () => void;
  onEvidence?: (payload: ProjectionPayload) => void;
  focusMessage?: { id: string; token: number } | null;
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
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const focusMounted = useRef(false);

  useLayoutEffect(() => {
    const textarea = inputRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 144)}px`;
  }, [input]);

  useEffect(() => {
    if (seed?.text) {
      // A quick action outside React hands the composer its text; there is no render to fold it
      // into because the seed arrives after the mount.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setInput(seed.text);
      onSeedConsumed?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed?.id]);

  useEffect(() => {
    if (!focusMounted.current) {
      focusMounted.current = true;
      return;
    }
    inputRef.current?.focus();
  }, [focusSignal]);
  const bottom = useRef<HTMLDivElement>(null);
  // A source link opens an article, not the end of the thread; the first paint keeps its place,
  // and later streamed turns scroll as before.
  const pendingFocus = useRef<string | null>(focusMessage?.id ?? null);
  const firstScroll = useRef(true);
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
  const needsSignIn = APICallError.isInstance(error) && error.statusCode === 401;
  const visibleMessages = useMemo(() => groupMessages(messages), [messages]);

  useEffect(() => {
    return () => {
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      streamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  useEffect(() => {
    if (firstScroll.current) {
      firstScroll.current = false;
      if (pendingFocus.current) return;
    }
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  useEffect(() => {
    const id = pendingFocus.current;
    if (!id) return;
    const article = document.getElementById(`message-${id}`);
    pendingFocus.current = null;
    if (!article) return;
    article.scrollIntoView({ block: "center" });
    article.focus({ preventScroll: true });
  }, [focusMessage?.token]);

  function appendCard(card: Record<string, unknown>, id: string) {
    setMessages((current) => [
      ...current,
      {
        id,
        role: "assistant",
        parts: [{ type: "data-card", id, data: card }],
      },
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
    <section className="conversation compact">
      {busy && (
        <header className="conversation-head">
          <h2 className="replying">
            <Typing />
            Replying…
          </h2>
          <button type="button" className="ghost" onClick={() => void stopTurn()}>
            <Square size={13} strokeWidth={1.8} aria-hidden="true" />
            Stop
          </button>
        </header>
      )}

      <div className="messages" aria-live="polite">
        {messages.length === 0 && (
          <div className="empty-chat">
            <h3>Ask anything about your money.</h3>
            <p className="muted">
              Answers use your ledger, plan, and projection. Every figure comes with its sources.
            </p>
            <div className="starter-list">
              {STARTERS.map(({ Icon, text }) => (
                <button
                  key={text}
                  type="button"
                  className="starter"
                  onClick={() => {
                    setInput(text);
                    inputRef.current?.focus();
                  }}
                >
                  <Icon size={16} strokeWidth={1.8} aria-hidden="true" />
                  <span>{text}</span>
                </button>
              ))}
            </div>
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
            onEvidence={onEvidence}
            onRetryCapture={(kind, attachmentId, messageId) =>
              void retryCapture(kind, attachmentId, messageId)
            }
          />
        ))}
        {status === "submitted" && <p className="muted fine">Thinking…</p>}
        {error && (
          <div className="chat-card">
            <p className="error">{chatErrorMessage(error)}</p>
            <button
              type="button"
              className="ghost small"
              disabled={busy}
              onClick={() => {
                if (needsSignIn) window.location.reload();
                else void sendMessage({ text: "Try again" });
              }}
            >
              {needsSignIn ? (
                <LogIn size={13} strokeWidth={1.8} aria-hidden="true" />
              ) : (
                <RotateCcw size={13} strokeWidth={1.8} aria-hidden="true" />
              )}
              {needsSignIn ? "Sign in again" : "Retry"}
            </button>
          </div>
        )}
        <div ref={bottom} />
      </div>

      <form className="composer" onSubmit={(event) => void submit(event)}>
        <div className="composer-main">
          <div className="composer-row">
            <textarea
              ref={inputRef}
              value={input}
              rows={1}
              aria-label="Message Fin-Agent"
              placeholder="Message Fin-Agent…"
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
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
                {uploading ? (
                  <>
                    <Busy />
                    Reading…
                  </>
                ) : (
                  <>
                    <Camera size={15} strokeWidth={1.8} aria-hidden="true" />
                    Photo
                  </>
                )}
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
                  {recording ? (
                    <>
                      <Mic size={15} strokeWidth={1.8} aria-hidden="true" />
                      Listening… release
                    </>
                  ) : transcribing ? (
                    <>
                      <Busy />
                      Transcribing…
                    </>
                  ) : (
                    <>
                      <Mic size={15} strokeWidth={1.8} aria-hidden="true" />
                      Hold to talk
                    </>
                  )}
                </button>
              )}
              <button className="composer-send" type="submit" disabled={busy || !input.trim()}>
                <ArrowUp size={16} strokeWidth={2} aria-hidden="true" />
                Send
              </button>
            </div>
          </div>
          {(recording || transcribing || uploading || captureError) && (
            <p className={captureError ? "error fine" : "fine"} role={captureError ? "alert" : "status"}>
              {captureError ??
                (recording
                  ? "Recording… release to send."
                  : transcribing
                    ? "Transcribing…"
                    : "Reading the photo…")}
            </p>
          )}
        </div>
      </form>
    </section>
  );
}

export function Assistant({
  focusThreadId,
  focusMessageId,
  onFocusHandled,
  seed,
  focusSignal,
  onSeedConsumed,
  onClose,
  onActivity,
  onEvidence,
}: {
  focusThreadId?: string | null;
  focusMessageId?: string | null;
  onFocusHandled?: () => void;
  seed?: { id: number; text: string } | null;
  focusSignal?: number;
  onSeedConsumed?: () => void;
  onClose?: () => void;
  onActivity?: () => void;
  onEvidence?: (payload: ProjectionPayload) => void;
}) {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [activeId, setActiveId] = useState<string>(() => {
    const saved = sessionStorage.getItem("fin-agent-thread");
    return saved || newThreadId();
  });
  const [initial, setInitial] = useState<ChatMessage[]>([]);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  // A validated source thread can be older than the first /threads page; load it directly.
  const [directId, setDirectId] = useState<string | null>(null);
  const [directTitle, setDirectTitle] = useState<string | null>(null);
  const [focusMessage, setFocusMessage] = useState<{ id: string; token: number } | null>(null);
  const [focusError, setFocusError] = useState<string | null>(null);
  const [focusAttempt, setFocusAttempt] = useState(0);
  const focusToken = useRef(0);
  const mounted = useRef(false);

  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    if (!focusThreadId) return;
    const nextFocus = focusMessageId
      ? { id: focusMessageId, token: ++focusToken.current }
      : null;
    if (focusThreadId === activeId && loadedFor === activeId) {
      if (nextFocus) setFocusMessage(nextFocus);
      onFocusHandled?.();
      return;
    }
    // Shell has already asked the server whether this thread exists, so do not fall back to the
    // newest thread when it is missing from the loaded history page.
    setError(null);
    setFocusError(null);
    setDirectTitle(null);
    setInitial([]);
    setLoadedFor(null);
    setDirectId(focusThreadId);
    setActiveId(focusThreadId);
    if (nextFocus) setFocusMessage(nextFocus);
    setFocusAttempt((value) => value + 1);
    onFocusHandled?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusThreadId]);

  function startNew() {
    const id = newThreadId();
    setInitial([]);
    setLoadedFor(id);
    setActiveId(id);
    setDirectId(null);
    setDirectTitle(null);
    setFocusMessage(null);
    setFocusError(null);
    setHistoryOpen(false);
  }

  function selectThread(id: string) {
    setInitial([]);
    setLoadedFor(null);
    setActiveId(id);
    setDirectId(null);
    setDirectTitle(null);
    setFocusMessage(null);
    setFocusError(null);
    setHistoryOpen(false);
  }

  function retryFocus() {
    setFocusError(null);
    setFocusAttempt((value) => value + 1);
  }

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
        const saved = sessionStorage.getItem("fin-agent-thread");
        const wanted =
          focusThreadId ??
          (list.some((thread) => thread.id === saved) ? saved : list[0]?.id);
        if (wanted) {
          setActiveId(wanted);
        } else {
          setLoadedFor(activeId);
        }
        if (focusThreadId) {
          setDirectId(focusThreadId);
          if (focusMessageId) {
            setFocusMessage({ id: focusMessageId, token: ++focusToken.current });
          }
          onFocusHandled?.();
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
    if (activeId) sessionStorage.setItem("fin-agent-thread", activeId);
  }, [activeId]);

  useEffect(() => {
    let alive = true;
    if (!threads.some((thread) => thread.id === activeId) && activeId !== directId) {
      return () => {
        alive = false;
      };
    }
    api<ThreadBody>(`/api/threads/${activeId}/messages`)
      .then((body) => {
        if (alive) {
          setInitial(body.messages);
          setLoadedFor(activeId);
          setFocusError(null);
          if (activeId === directId) setDirectTitle(body.thread.title);
        }
      })
      .catch((caught: unknown) => {
        if (!alive) return;
        if (activeId === directId) setFocusError(errorMessage(caught));
        else setError(errorMessage(caught));
      });
    return () => {
      alive = false;
    };
  }, [activeId, threads, directId, focusAttempt]);

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
    return (
      <section className="assistant">
        <header className="assistant-head">
          <div>
            <h2>Conversation</h2>
            <p className="fine">Fin-Agent</p>
          </div>
        </header>
        <div className="chat-loading">
          <ConversationSkeleton />
        </div>
      </section>
    );
  }

  const title =
    threads.find((thread) => thread.id === activeId)?.title ??
    directTitle ??
    (focusError ? "Conversation unavailable" : "New conversation");

  return (
    <section className="assistant">
      <header className="assistant-head">
        <div>
          <h2>{title}</h2>
          <p className="fine">Fin-Agent</p>
        </div>
        <div className="assistant-actions">
          <Popover.Root open={historyOpen} onOpenChange={setHistoryOpen}>
            <Popover.Trigger asChild>
              <button type="button" className="ghost small">
                <History size={14} strokeWidth={1.8} aria-hidden="true" />
                History
              </button>
            </Popover.Trigger>
            <Popover.Portal>
              <Popover.Content
                className="thread-pop"
                side="bottom"
                align="end"
                sideOffset={8}
                collisionPadding={8}
              >
                <ThreadSidebar
                  threads={threads}
                  activeId={activeId}
                  query={query}
                  onQuery={setQuery}
                  onSelect={selectThread}
                  onNew={startNew}
                />
              </Popover.Content>
            </Popover.Portal>
          </Popover.Root>
          <button type="button" className="ghost small" onClick={startNew}>
            <Plus size={14} strokeWidth={1.8} aria-hidden="true" />
            New
          </button>
          {onClose && (
            <button
              type="button"
              className="ghost small icon-only"
              onClick={onClose}
              aria-label="Close assistant"
              title="Close assistant"
            >
              <X size={15} strokeWidth={1.8} aria-hidden="true" />
            </button>
          )}
        </div>
      </header>

      {error && <p className="error chat-error">{error}</p>}
      {focusError ? (
        <div className="chat-card" role="alert">
          <p className="error">
            This conversation could not be opened. It may have been deleted or is unavailable.
          </p>
          <p className="fine">{focusError}</p>
          <div className="button-row">
            <button type="button" className="ghost small" onClick={retryFocus}>
              Try again
            </button>
            <button type="button" className="ghost small" onClick={startNew}>
              Start a new chat
            </button>
          </div>
        </div>
      ) : loadedFor === activeId ? (
        <Conversation
          key={activeId}
          threadId={activeId}
          initialMessages={initial}
          onThreadChanged={() => {
            void refresh();
            onActivity?.();
          }}
          seed={seed}
          focusSignal={focusSignal}
          onSeedConsumed={onSeedConsumed}
          onEvidence={onEvidence}
          focusMessage={focusMessage}
        />
      ) : (
        <div className="chat-loading">
          <ConversationSkeleton />
        </div>
      )}
    </section>
  );
}
