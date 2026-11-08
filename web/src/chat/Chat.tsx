import {
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import * as Popover from "@radix-ui/react-popover";
import {
  AssistantRuntimeProvider,
  AuiConfig,
  ComposerPrimitive,
  CompositeAttachmentAdapter,
  Suggestions,
  Tools,
  useAui,
  useAuiState,
  useRemoteThreadListRuntime,
  type AssistantClient,
} from "@assistant-ui/react";
import { FlowView } from "./FlowView";
import { useChatRuntime } from "@assistant-ui/ai-sdk";
import {
  APICallError,
  lastAssistantMessageIsCompleteWithApprovalResponses,
} from "ai";
import { Flag, History, Plus, Square, X } from "lucide-react";
import { api, errorMessage } from "../api";
import { Typing } from "../Loading";
import type { ProjectionPayload } from "../projection";
import { takeSharedImage } from "../share";
import { ConversationSkeleton } from "../Skeleton";
import { Button } from "@/components/ui/button";
import { ComposerAddAttachment } from "@/components/attachment.aui";
import { Thread, type ThreadComponents } from "@/components/thread.aui";
import {
  ThreadListItems,
  ThreadListNew,
  ThreadListRoot,
  ThreadListSearch,
} from "@/components/thread-list.aui";
import {
  ChatComposerStatus,
  ChatDataRenderers,
  ChatToolFallback,
  ChatWelcome,
  ChatWelcomeAfter,
  FIN_SUGGESTIONS,
} from "./parts";
import { ChatSurfaceContext, type ChatSurfaceState } from "./surface";
import {
  ChatTransport,
  ServerDictationAdapter,
  VoiceAttachments,
  cancelActiveDictation,
  createHistoryAdapter,
  createThreadListAdapter,
  stopChatRun,
} from "./runtime";
import { photoAttachmentAdapter } from "./photo-attachment";
import { statementAttachmentAdapter } from "./statement-attachment";
import { StreamCheckpoints, createThreadStreamStorage } from "./streams";
import { CHAT_TOOLKIT } from "./tools";
import type { ThreadList } from "./types";

const attachmentAdapter = new CompositeAttachmentAdapter([
  statementAttachmentAdapter,
  photoAttachmentAdapter,
]);

const AUI_CONFIG = AuiConfig({
  suggestions: Suggestions(FIN_SUGGESTIONS),
  tools: Tools({ toolkit: CHAT_TOOLKIT }),
});

const THREAD_STORAGE = "fin-agent-thread";

type CaptureCard = Record<string, unknown>;
type ReportCapture = (
  threadId: string,
  attachmentId: string,
  card: CaptureCard,
) => void;

function liveThreadId(aui: AssistantClient): string | null {
  // `threadListItem` can still point at the thread whose request started an
  // async action; the active thread selection is the one appends land in.
  const active = aui.threads.getState().mainThreadId;
  if (active) return active;
  const state = aui.threadListItem.getState();
  return state.remoteId ?? state.id ?? null;
}

type CaptureResult =
  | { status: "logged"; attachment_id: string; card: CaptureCard }
  | { status: "failed"; attachment_id: string; note: string; card: CaptureCard };

/** A retry replaced its own standalone card; a chat turn keeps its text and tools. */
function standaloneCaptureFailure(aui: AssistantClient, messageId: string): boolean {
  const message = aui.thread.getState().messages.find((item) => item.id === messageId);
  if (!message || message.content.length !== 1) return false;
  const [part] = message.content;
  return (
    part.type === "data" &&
    part.name === "card" &&
    (part.data as CaptureCard | undefined)?.card === "capture-retry"
  );
}

type TranscribeResult =
  | { status: "transcribed"; attachment_id: string; transcript: string }
  | { status: "failed"; attachment_id: string; note: string; card: CaptureCard };

function FlagAction() {
  const serverId = useAuiState(
    (state) => state.message.metadata.custom?.server_id as string | undefined,
  );
  const alreadyFlagged = useAuiState(
    (state) => Boolean(state.message.metadata.custom?.flagged_at),
  );
  const [state, setState] = useState<"idle" | "busy" | "done" | "error">(
    alreadyFlagged ? "done" : "idle",
  );
  const [error, setError] = useState<string | null>(null);

  if (!serverId) return null;

  return (
    <div className="aui-flag">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="aui-flag-button h-auto"
        disabled={state === "busy" || state === "done"}
        aria-pressed={state === "done"}
        onClick={async () => {
          setState("busy");
          setError(null);
          try {
            await api(`/api/messages/${encodeURIComponent(serverId)}/flag`, {
              method: "POST",
            });
            setState("done");
          } catch (caught) {
            setError(errorMessage(caught));
            setState("error");
          }
        }}
      >
        <Flag
          size={13}
          strokeWidth={1.8}
          fill={state === "done" ? "currentColor" : "none"}
          aria-hidden="true"
        />
        {state === "done" ? "Flagged" : "Flag"}
      </Button>
      {error && (
        <span role="alert" className="aui-flag-error">
          {error}
        </span>
      )}
    </div>
  );
}

/** The library owns the cancel state; this only mirrors the user's Stop to the server. */
function StopRunButton() {
  const aui = useAui();
  const surface = useContext(ChatSurfaceContext);

  return (
    <ComposerPrimitive.Cancel asChild>
      <Button
        type="button"
        variant="default"
        size="icon"
        className="aui-composer-cancel size-7 rounded-full"
        aria-label="Stop generating"
        onClick={() => {
          const threadId = liveThreadId(aui);
          if (threadId) void surface.onStop?.(threadId);
        }}
      >
        <Square size={14} strokeWidth={2} fill="currentColor" aria-hidden="true" />
      </Button>
    </ComposerPrimitive.Cancel>
  );
}

const THREAD_COMPONENTS: ThreadComponents = {
  Welcome: ChatWelcome,
  WelcomeAfter: ChatWelcomeAfter,
  ToolFallback: ChatToolFallback,
  ComposerExtra: ComposerAddAttachment,
  ComposerCancel: StopRunButton,
  ComposerStatus: ChatComposerStatus,
  AssistantActions: FlagAction,
};

/** A small, fixed rail header: title, history, new, and close. */
function ChatHeader({
  title,
  running,
  historyControl,
  viewControl,
  onNew,
  onClose,
}: {
  title: string;
  running: boolean;
  historyControl: ReactNode;
  viewControl?: ReactNode;
  onNew: () => void;
  onClose?: () => void;
}) {
  return (
    <header className="assistant-head aui-head">
      <div>
        <h2>{title}</h2>
        <p className={running ? "aui-replying" : "fine"}>
          {running && <Typing />}
          {running ? "Replying…" : "Fin-Agent"}
        </p>
      </div>
      <div className="assistant-actions">
        {viewControl}
        {historyControl}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="ghost small"
          onClick={onNew}
        >
          <Plus size={14} strokeWidth={1.8} aria-hidden="true" />
          New
        </Button>
        {onClose && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="ghost small icon-only"
            onClick={onClose}
            aria-label="Close assistant"
            title="Close assistant"
          >
            <X size={15} strokeWidth={1.8} aria-hidden="true" />
          </Button>
        )}
      </div>
    </header>
  );
}

function ChatHistory({
  query,
  onQuery,
  error,
}: {
  query: string;
  onQuery: (value: string) => void;
  error: string | null;
}) {
  return (
    <aside className="aui-history">
      <ThreadListRoot>
        <ThreadListNew>
          <Plus size={15} strokeWidth={1.8} aria-hidden="true" />
          <span>New chat</span>
        </ThreadListNew>
        <ThreadListSearch
          value={query}
          onValueChange={onQuery}
          placeholder="Search past messages"
        />
        {error && (
          <p role="alert" className="aui-history-error">
            {error}
          </p>
        )}
        <div className="aui-history-list">
          <AuiEmptyHistory query={query} />
          <ThreadListItems searchQuery="" />
        </div>
      </ThreadListRoot>
    </aside>
  );
}

function AuiEmptyHistory({ query }: { query: string }) {
  const loading = useAuiState((state) => state.threads.isLoading);
  const empty = useAuiState((state) => state.threads.threadIds.length === 0);
  if (loading || !empty) return null;
  return (
    <p className="aui-history-empty">
      {query.trim() ? "No conversations found." : "No conversations yet."}
    </p>
  );
}

function ThreadSwitchError({
  threadId,
  detail,
  onRetry,
  onNew,
}: {
  threadId: string;
  detail: string;
  onRetry: () => void;
  onNew: () => void;
}) {
  return (
    <div className="aui-switch-error" role="alert">
      <p className="aui-switch-error-title">
        This conversation could not be opened. It may have been deleted or is unavailable.
      </p>
      <p className="aui-switch-error-detail">{detail}</p>
      <div className="aui-switch-actions">
        <Button type="button" variant="ghost" size="sm" className="ghost small" onClick={onRetry}>
          Try again
        </Button>
        <Button type="button" variant="ghost" size="sm" className="ghost small" onClick={onNew}>
          Start a new chat
        </Button>
      </div>
      <span className="aui-sr-only">{threadId}</span>
    </div>
  );
}

type AssistantProps = {
  focusThreadId?: string | null;
  focusMessageId?: string | null;
  onFocusHandled?: () => void;
  seed?: { id: number; text: string } | null;
  focusSignal?: number;
  onSeedConsumed?: () => void;
  attachments?: { id: number; files: File[] } | null;
  onAttachmentsConsumed?: () => void;
  onClose?: () => void;
  onActivity?: () => void;
  onEvidence?: (payload: ProjectionPayload) => void;
  /** Each increment opens a fresh conversation (the Ask home). */
  newChatSignal?: number;
  /** Show the conversation list beside the thread instead of behind History. */
  listInline?: boolean;
  /** A reply started; Ask hands the conversation over to Chats. */
  onConversationStarted?: () => void;
};

export function Assistant(props: AssistantProps) {
  const [activeId, setActiveId] = useState<string | undefined>(
    () => props.focusThreadId ?? sessionStorage.getItem(THREAD_STORAGE) ?? undefined,
  );
  const [boot, setBoot] = useState(() => Boolean(props.focusThreadId));
  const [bootError, setBootError] = useState<string | null>(null);
  const [focusMessage, setFocusMessage] = useState<{ id: string; token: number } | null>(null);
  const focusToken = useRef(0);
  const bootStarted = useRef(false);
  const focusSeen = useRef(false);

  const { focusThreadId, focusMessageId, onFocusHandled } = props;

  useEffect(() => {
    if (bootStarted.current || focusThreadId) return;
    bootStarted.current = true;
    const saved = sessionStorage.getItem(THREAD_STORAGE);
    api<ThreadList>("/api/threads")
      .then((list) => {
        const wanted = list.threads.some((thread) => thread.id === saved)
          ? saved ?? list.threads[0]?.id
          : list.threads[0]?.id;
        if (focusSeen.current) return;
        if (wanted) setActiveId(wanted);
        setBoot(true);
      })
      .catch((caught: unknown) => {
        if (focusSeen.current) return;
        setBootError(errorMessage(caught));
        if (saved) setActiveId(saved);
        setBoot(true);
      });
    // Focus is checked once at mount; later deep links arrive through the prop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!focusThreadId) return;
    focusSeen.current = true;
    setActiveId(focusThreadId);
    setBoot(true);
    setFocusMessage(
      focusMessageId ? { id: focusMessageId, token: ++focusToken.current } : null,
    );
    onFocusHandled?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusThreadId]);
  /* eslint-enable react-hooks/set-state-in-effect */

  if (!boot) {
    return (
      <section className="assistant aui-chat dark">
        <div className="chat-loading">
          <ConversationSkeleton />
        </div>
      </section>
    );
  }

  return (
    <AssistantChat
      {...props}
      activeId={activeId}
      onActiveId={setActiveId}
      focusMessage={focusMessage}
      bootError={bootError}
    />
  );
}

function useFinAgentThreadRuntime({
  voice,
  checkpoints,
  setCapture,
  onCaptureRetry,
  onFinish,
}: {
  voice: VoiceAttachments;
  checkpoints: StreamCheckpoints;
  setCapture: (threadId: string, value: ChatSurfaceState["capture"]) => void;
  onCaptureRetry: ReportCapture;
  onFinish: () => void;
}) {
  const aui = useAui();
  const threadId = useAuiState((state) => state.threadListItem.id);
  const history = useMemo(() => createHistoryAdapter(aui), [aui]);
  // One storage per thread runtime; its key follows the thread's identity as
  // the local id is replaced by the server id.
  const storage = useMemo(
    () => createThreadStreamStorage(() => aui.threadListItem.getState()),
    [aui],
  );
  const transport = useMemo(
    () => new ChatTransport({ voice, storage }),
    [storage, voice],
  );

  useEffect(() => {
    if (!threadId) return;
    checkpoints.set(threadId, storage);
    return () => checkpoints.release(threadId, storage);
  }, [checkpoints, storage, threadId]);

  const resolveThreadId = useCallback(async () => {
    const item = aui.threadListItem;
    const current = item.getState();
    if (current.remoteId) return current.remoteId;
    const initialized = await item.initialize();
    return initialized.remoteId;
  }, [aui]);

  const dictation = useMemo(
    () =>
      new ServerDictationAdapter(
        resolveThreadId,
        voice,
        setCapture,
        (threadId, message) => setCapture(threadId, { kind: "error", message }),
        onCaptureRetry,
      ),
    [onCaptureRetry, resolveThreadId, setCapture, voice],
  );

  return useChatRuntime({
    transport,
    canResume: true,
    adapters: { history, dictation, attachments: attachmentAdapter },
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithApprovalResponses,
    onFinish,
    onResumeError: (error) => {
      if (!threadId) return;
      // 204/404 means the replay is gone. A non-retryable failure cannot replay
      // either; clearing both stops a stale Resume from being offered forever.
      if (
        APICallError.isInstance(error) &&
        (error.statusCode === 204 || error.statusCode === 404)
      ) {
        storage.clear();
        return;
      }
      if (APICallError.isInstance(error) && !error.isRetryable) storage.clear();
      // The SDK error and thread.canResume drive the single recovery message.
    },
  });
}

type AssistantChatProps = AssistantProps & {
  activeId: string | undefined;
  onActiveId: (id: string | undefined) => void;
  focusMessage: { id: string; token: number } | null;
  bootError: string | null;
};

function AssistantChat({
  activeId,
  onActiveId,
  focusMessage,
  bootError,
  seed,
  focusSignal,
  onSeedConsumed,
  attachments,
  onAttachmentsConsumed,
  onClose,
  onActivity,
  onEvidence,
  newChatSignal,
  listInline,
  onConversationStarted,
}: AssistantChatProps) {
  const queryRef = useRef("");
  const voice = useMemo(() => new VoiceAttachments(), []);
  const checkpoints = useMemo(() => new StreamCheckpoints(), []);
  const changedRef = useRef<() => void>(() => {});
  const captureRetryRef = useRef<ReportCapture | null>(null);
  const [captures, setCaptures] = useState<
    Record<string, ChatSurfaceState["capture"] | undefined>
  >({});
  const [surfaceErrors, setSurfaceErrors] = useState<
    Record<string, string | undefined>
  >({});
  const setCapture = useCallback(
    (threadId: string, value: ChatSurfaceState["capture"]) => {
      setCaptures((previous) => {
        if (previous[threadId] === value) return previous;
        const next = { ...previous };
        if (value === null) delete next[threadId];
        else next[threadId] = value;
        return next;
      });
    },
    [],
  );
  const setSurfaceError = useCallback(
    (threadId: string, message: string | null) => {
      setSurfaceErrors((previous) => {
        if (previous[threadId] === (message ?? undefined)) return previous;
        const next = { ...previous };
        if (message === null) delete next[threadId];
        else next[threadId] = message;
        return next;
      });
    },
    [],
  );
  const onCaptureRetry = useCallback<ReportCapture>(
    (threadId, attachmentId, card) => {
      captureRetryRef.current?.(threadId, attachmentId, card);
    },
    [],
  );

  // The adapter reads its mutable search text when called, never during render.
  // eslint-disable-next-line react-hooks/refs
  const adapter = useMemo(() => createThreadListAdapter(queryRef), []);
  const runtimeHook = () =>
    // eslint-disable-next-line react-hooks/rules-of-hooks
    useFinAgentThreadRuntime({
      voice,
      checkpoints,
      setCapture,
      onCaptureRetry,
      onFinish: () => changedRef.current(),
    });

  const runtime = useRemoteThreadListRuntime({
    runtimeHook,
    adapter,
    threadId: activeId,
    onThreadIdChange: (threadId) => {
      onActiveId(threadId);
      if (threadId) sessionStorage.setItem(THREAD_STORAGE, threadId);
      else sessionStorage.removeItem(THREAD_STORAGE);
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime} config={AUI_CONFIG}>
      <ChatDataRenderers />
      <ChatShell
        activeId={activeId}
        voice={voice}
        checkpoints={checkpoints}
        queryRef={queryRef}
        changedRef={changedRef}
        setCapture={setCapture}
        setSurfaceError={setSurfaceError}
        captureRetryRef={captureRetryRef}
        captures={captures}
        surfaceErrors={surfaceErrors}
        focusMessage={focusMessage}
        bootError={bootError}
        seed={seed}
        focusSignal={focusSignal}
        onSeedConsumed={onSeedConsumed}
        attachments={attachments}
        onAttachmentsConsumed={onAttachmentsConsumed}
        onClose={onClose}
        onActivity={onActivity}
        onEvidence={onEvidence}
        newChatSignal={newChatSignal}
        listInline={listInline}
        onConversationStarted={onConversationStarted}
        onNew={() => {
          onActiveId(undefined);
          sessionStorage.removeItem(THREAD_STORAGE);
        }}
      />
    </AssistantRuntimeProvider>
  );
}

type ChatShellProps = {
  activeId: string | undefined;
  voice: VoiceAttachments;
  checkpoints: StreamCheckpoints;
  queryRef: { current: string };
  changedRef: { current: () => void };
  setCapture: (threadId: string, value: ChatSurfaceState["capture"]) => void;
  setSurfaceError: (threadId: string, message: string | null) => void;
  captureRetryRef: { current: ReportCapture | null };
  captures: Record<string, ChatSurfaceState["capture"] | undefined>;
  surfaceErrors: Record<string, string | undefined>;
  focusMessage: { id: string; token: number } | null;
  bootError: string | null;
  seed?: { id: number; text: string } | null;
  focusSignal?: number;
  onSeedConsumed?: () => void;
  attachments?: { id: number; files: File[] } | null;
  onAttachmentsConsumed?: () => void;
  onClose?: () => void;
  onActivity?: () => void;
  onEvidence?: (payload: ProjectionPayload) => void;
  newChatSignal?: number;
  listInline?: boolean;
  onConversationStarted?: () => void;
  onNew: () => void;
};

function ChatShell({
  activeId,
  voice,
  checkpoints,
  queryRef,
  changedRef,
  setCapture,
  setSurfaceError,
  captureRetryRef,
  captures,
  surfaceErrors,
  focusMessage,
  bootError,
  seed,
  focusSignal,
  onSeedConsumed,
  attachments,
  onAttachmentsConsumed,
  onClose,
  onActivity,
  onEvidence,
  newChatSignal,
  listInline = false,
  onConversationStarted,
  onNew,
}: ChatShellProps) {
  const aui = useAui();
  const running = useAuiState((state) => state.thread.isRunning);
  const title = useAuiState((state) => state.threadListItem.title);
  const threadId = useAuiState(
    (state) => state.threadListItem.remoteId ?? state.threadListItem.id,
  );
  const switchError = useAuiState((state) => state.threads.switchError);
  const messageCount = useAuiState((state) => state.thread.messages.length);
  const historyLoading = useAuiState((state) => state.thread.isLoading);
  const firstThreadId = useAuiState((state) => state.threads.threadIds[0]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [view, setView] = useState<"chat" | "flow">("chat");
  const [query, setQuery] = useState("");
  const [listError, setListError] = useState<string | null>(null);
  const pendingPhoto = useRef(new Map<string, File>());
  const staleThreads = useRef(new Set<string>());
  const sharedFor = useRef<string | null>(null);
  const focusedToken = useRef<number | null>(null);
  const mounted = useRef(false);
  const focusMounted = useRef(false);
  const capture = threadId ? (captures[threadId] ?? null) : null;
  const surfaceError = threadId ? (surfaceErrors[threadId] ?? null) : null;

  useEffect(() => {
    changedRef.current = () => {
      void aui.threads.reload().catch(() => {
        // The list refresh is best effort; the turn already landed.
      });
      onActivity?.();
    };
  }, [aui, changedRef, onActivity]);

  useEffect(() => {
    // Leaving a thread or the assistant must not leave a microphone open. A
    // card the server wrote while the user was elsewhere is picked up when
    // that thread is opened again, through the library's own history reload.
    const unsubscribe = aui.on("threads.selectionChanged", ({ threadId }) => {
      cancelActiveDictation();
      if (!staleThreads.current.delete(threadId)) return;
      void aui.threads.reloadMainThread().catch(() => {
        // Opening the thread again will load it; the list is already fresh.
      });
    });
    return () => {
      unsubscribe();
      cancelActiveDictation();
    };
  }, [aui]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setHistoryOpen(false);
  }, [activeId]);

  // Ask opens a fresh conversation each time; its empty state is the agent's home.
  useEffect(() => {
    if (!newChatSignal) return;
    aui.threads.switchToNewThread();
    onNew();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newChatSignal]);

  // Chats opens on the latest conversation rather than an empty one.
  useEffect(() => {
    if (!listInline || messageCount > 0 || historyLoading || !firstThreadId) return;
    aui.threads.switchToThread(firstThreadId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listInline, firstThreadId]);

  useEffect(() => {
    if (running) onConversationStarted?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  useEffect(() => {
    queryRef.current = query;
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    const handle = window.setTimeout(() => {
      void aui.threads
        .reload()
        .then(() => setListError(null))
        .catch((caught: unknown) => {
          setListError(errorMessage(caught));
        });
    }, 250);
    return () => window.clearTimeout(handle);
  }, [aui, queryRef, query]);

  useEffect(() => {
    if (!focusMessage || historyLoading || focusedToken.current === focusMessage.token) return;
    const target = document.getElementById(`message-${focusMessage.id}`);
    if (!target) return;
    focusedToken.current = focusMessage.token;
    target.scrollIntoView({ block: "center" });
    target.focus({ preventScroll: true });
  }, [focusMessage, historyLoading, messageCount, threadId]);

  useEffect(() => {
    if (!seed?.text) return;
    aui.composer.setText(seed.text);
    onSeedConsumed?.();
    requestAnimationFrame(() => {
      document.querySelector<HTMLTextAreaElement>(".aui-composer-input")?.focus();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed?.id]);

  useEffect(() => {
    if (!attachments?.files.length) return;
    let alive = true;
    void (async () => {
      for (const file of attachments.files) {
        if (!alive) return;
        try {
          await aui.composer.addAttachment(file);
        } catch {
          // The composer's own status line reports a file it would not take.
        }
      }
      if (!alive) return;
      onAttachmentsConsumed?.();
      requestAnimationFrame(() => {
        document.querySelector<HTMLTextAreaElement>(".aui-composer-input")?.focus();
      });
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attachments?.id]);

  useEffect(() => {
    if (!focusMounted.current) {
      focusMounted.current = true;
      return;
    }
    requestAnimationFrame(() => {
      document.querySelector<HTMLTextAreaElement>(".aui-composer-input")?.focus();
    });
  }, [focusSignal]);

  /**
   * A card may arrive after the user switched threads. It belongs to the
   * thread that made the request, so only its own runtime may show it; the
   * server copy is what the other thread's next history load reads.
   */
  const appendCard = useCallback(
    (originId: string, card: CaptureCard, id: string) => {
      if (liveThreadId(aui) === originId) {
        aui.thread.append({
          role: "assistant",
          content: [{ type: "data", id, name: "card", data: card }],
        });
      } else {
        staleThreads.current.add(originId);
      }
      void aui.threads.reload().catch(() => {});
      onActivity?.();
    },
    [aui, onActivity],
  );

  useEffect(() => {
    captureRetryRef.current = (originId, attachmentId, card) =>
      appendCard(originId, card, `capture_retry_${attachmentId}`);
    return () => {
      captureRetryRef.current = null;
    };
  }, [appendCard, captureRetryRef]);

  const uploadPhoto = useCallback(
    async (file: File, originId: string) => {
      if (running && liveThreadId(aui) === originId) {
        pendingPhoto.current.set(originId, file);
        return;
      }
      setSurfaceError(originId, null);
      setCapture(originId, { kind: "uploading", message: "Reading the photo…" });
      const form = new FormData();
      form.append("file", file, file.name || "photo.png");
      try {
        const result = await api<CaptureResult>(
          `/api/chat/${encodeURIComponent(originId)}/photo`,
          { method: "POST", body: form },
        );
        if (result.status === "logged") {
          appendCard(originId, result.card, `photo_${result.attachment_id}`);
        } else {
          appendCard(originId, result.card, `capture_retry_${result.attachment_id}`);
        }
        setCapture(originId, null);
      } catch (caught) {
        const message = errorMessage(caught);
        setSurfaceError(originId, message);
        setCapture(originId, { kind: "error", message });
      }
    },
    [appendCard, aui, running, setCapture, setSurfaceError],
  );

  const uploadPhotoRef = useRef(uploadPhoto);
  useEffect(() => {
    uploadPhotoRef.current = uploadPhoto;
  }, [uploadPhoto]);

  useEffect(() => {
    if (running || !threadId) return;
    const queued = pendingPhoto.current.get(threadId);
    if (!queued) return;
    pendingPhoto.current.delete(threadId);
    void uploadPhoto(queued, threadId);
  }, [running, threadId, uploadPhoto]);

  useEffect(() => {
    if (!threadId || sharedFor.current === threadId) return;
    sharedFor.current = threadId;
    let alive = true;
    void takeSharedImage().then((file) => {
      if (alive && file) void uploadPhotoRef.current(file, threadId);
    });
    return () => {
      alive = false;
    };
  }, [threadId]);

  const retryCapture = useCallback(
    async (kind: string, attachmentId: string, messageId: string) => {
      if (running) return;
      const originId = liveThreadId(aui);
      if (!originId) return;
      setSurfaceError(originId, null);
      try {
        if (kind === "photo") {
          const result = await api<CaptureResult>(
            `/api/chat/${encodeURIComponent(originId)}/photo/${encodeURIComponent(attachmentId)}`,
            { method: "POST" },
          );
          if (liveThreadId(aui) !== originId) {
            staleThreads.current.add(originId);
            void aui.threads.reload().catch(() => {});
            return;
          }
          if (result.status === "logged") {
            if (standaloneCaptureFailure(aui, messageId)) {
              aui.thread.deleteMessage(messageId);
            }
            appendCard(originId, result.card, `photo_${attachmentId}`);
          } else {
            setSurfaceError(originId, result.note);
            setCapture(originId, { kind: "error", message: result.note });
          }
          return;
        }
        const result = await api<TranscribeResult>(
          `/api/transcribe/${encodeURIComponent(attachmentId)}?thread_id=${encodeURIComponent(originId)}`,
          { method: "POST" },
        );
        if (liveThreadId(aui) !== originId) {
          void aui.threads.reload().catch(() => {});
          return;
        }
        if (result.status === "transcribed") {
          aui.thread.deleteMessage(messageId);
          voice.set(originId, attachmentId);
          aui.thread.append({
            role: "user",
            content: [{ type: "text", text: result.transcript }],
            startRun: true,
          });
        } else {
          setSurfaceError(originId, result.note);
          setCapture(originId, { kind: "error", message: result.note });
        }
      } catch (caught) {
        const message = errorMessage(caught);
        setSurfaceError(originId, message);
        setCapture(originId, { kind: "error", message });
      }
    },
    [appendCard, aui, running, setCapture, setSurfaceError, voice],
  );

  const onStop = useCallback(
    async (originId: string) => {
      setSurfaceError(originId, null);
      try {
        await stopChatRun(originId, checkpoints);
      } catch {
        setSurfaceError(
          originId,
          "Stopped here. Fin-Agent could not confirm the server stopped, so it may keep finishing this answer.",
        );
      }
    },
    [checkpoints, setSurfaceError],
  );

  const surface = useMemo<ChatSurfaceState>(
    () => ({
      capture: surfaceError ? { kind: "error", message: surfaceError } : capture,
      onRetryCapture: (kind, attachmentId, messageId) =>
        void retryCapture(kind, attachmentId, messageId),
      onEvidence: onEvidence,
      onStop: (originId) => void onStop(originId),
    }),
    [capture, onEvidence, onStop, retryCapture, surfaceError],
  );

  const switchDetail = switchError ? errorMessage(switchError.error) : "";

  return (
    <section className={listInline ? "assistant aui-chat dark with-list" : "assistant aui-chat dark"}>
      {listInline && (
        <div className="chat-list">
          <ChatHistory query={query} onQuery={setQuery} error={listError} />
        </div>
      )}
      <div className="chat-main-col">
      <Popover.Root open={historyOpen} onOpenChange={setHistoryOpen}>
        <ChatHeader
          title={title ?? (activeId ? "Conversation" : "New conversation")}
          running={running}
          viewControl={
            messageCount > 0 ? (
              <div className="view-toggle" role="radiogroup" aria-label="Conversation view">
                <button
                  type="button"
                  role="radio"
                  aria-checked={view === "chat"}
                  className={view === "chat" ? "on" : ""}
                  onClick={() => setView("chat")}
                >
                  Chat
                </button>
                <button
                  type="button"
                  role="radio"
                  aria-checked={view === "flow"}
                  className={view === "flow" ? "on" : ""}
                  onClick={() => setView("flow")}
                >
                  Flow
                </button>
              </div>
            ) : null
          }
          historyControl={
            listInline ? null : (
            <Popover.Trigger asChild>
              <Button type="button" variant="ghost" size="sm" className="ghost small">
                <History size={14} strokeWidth={1.8} aria-hidden="true" />
                History
              </Button>
            </Popover.Trigger>
            )
          }
          onNew={() => {
            setHistoryOpen(false);
            aui.threads.switchToNewThread();
            onNew();
          }}
          onClose={onClose}
        />
        <Popover.Portal>
          <Popover.Content
            className="thread-pop aui-portal dark"
            side="bottom"
            align="end"
            sideOffset={8}
            collisionPadding={8}
            onOpenAutoFocus={(event) => event.preventDefault()}
          >
            <ChatHistory query={query} onQuery={setQuery} error={listError} />
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>

      {bootError && (
        <p className="error aui-boot-error" role="alert">
          {bootError}
        </p>
      )}

      <ChatSurfaceContext.Provider value={surface}>
        <div className="aui-thread-shell">
          {switchError ? (
            <ThreadSwitchError
              threadId={switchError.threadId}
              detail={switchDetail}
              onRetry={() => aui.threads.switchToThread(switchError.threadId)}
              onNew={() => {
                aui.threads.switchToNewThread();
                onNew();
              }}
            />
          ) : view === "flow" && messageCount > 0 ? (
            <FlowView />
          ) : (
            <Thread components={THREAD_COMPONENTS} autoFocus={false} />
          )}
        </div>
      </ChatSurfaceContext.Provider>
      </div>
    </section>
  );
}
