import { createAssistantStream } from "assistant-stream";
import {
  AssistantChatTransport,
  type ResumableClientStorage,
} from "@assistant-ui/ai-sdk";
import type {
  AssistantClient,
  DictationAdapter,
  RemoteThreadListAdapter,
  ThreadHistoryAdapter,
} from "@assistant-ui/react";
import type { ChatMessage, ThreadBody, ThreadList } from "./types";
import { ApiError, api } from "../api";
import type { StreamCheckpoints } from "./streams";

/** Voice ids belong to the thread that recorded them, never to the next send. */
export class VoiceAttachments {
  private readonly byThread = new Map<string, string>();

  set(threadId: string, attachmentId: string) {
    this.byThread.set(threadId, attachmentId);
  }

  take(threadId: string): string | null {
    const attachmentId = this.byThread.get(threadId) ?? null;
    this.byThread.delete(threadId);
    return attachmentId;
  }
}

type TranscribeResult =
  | { status: "transcribed"; attachment_id: string; transcript: string }
  | { status: "failed"; attachment_id: string; note: string; card: Record<string, unknown> };

export type ChatTransportConfig = {
  voice: VoiceAttachments;
  storage: ResumableClientStorage;
};

/** The data refs stay in the UI; the model needs a prompt when the turn is only files. */
function attachmentPrompt(messages: ChatMessage[]): ChatMessage[] {
  const last = messages.at(-1);
  if (!last || last.role !== "user") return messages;
  const hasStatement = last.parts.some((part) => part.type === "data-statement");
  const hasPhoto = last.parts.some((part) => part.type === "data-photo");
  const hasText = last.parts.some((part) => part.type === "text" && part.text.trim());
  if ((!hasStatement && !hasPhoto) || hasText) return messages;
  const text =
    hasStatement && hasPhoto
      ? "Import the attached statement and log the attached receipt or payment photo."
      : hasStatement
        ? "Import the attached statement."
        : "Log the attached receipt or payment photo.";
  return [...messages.slice(0, -1), { ...last, parts: [{ type: "text", text }, ...last.parts] }];
}

/**
 * The server owns the transcript. The AI SDK transport forwards only the new
 * turn, and a reconnect is a full replay from `GET /chat/{id}/stream`. Aborts
 * from unmount, navigation, or thread switching stop the local reader only;
 * the server keeps a detached run so reopening the thread can replay it.
 */
export class ChatTransport extends AssistantChatTransport<ChatMessage> {
  constructor(private readonly config: ChatTransportConfig) {
    super({
      api: "/api/chat",
      resumable: {
        storage: config.storage,
        resumeApi: (streamId) =>
          `/api/chat/${encodeURIComponent(streamId)}/stream`,
      },
      prepareSendMessagesRequest: ({ id, messages, trigger, messageId }) => {
        const voiceAttachment = config.voice.take(id);
        return {
          body: {
            id,
            trigger,
            messageId,
            voice_attachment_id: voiceAttachment ?? undefined,
            // The server holds the thread's history; it only needs this turn's message.
            messages: attachmentPrompt(messages.slice(-1)),
          },
        };
      },
    });
  }

  override readonly resumeStreamIsReplay = true;

  override __internal_clone(): ChatTransport {
    // The clone serves the same thread runtime, so it keeps the same storage.
    return new ChatTransport(this.config);
  }
}

/**
 * An explicit Stop is the only thing that calls the server's stop endpoint.
 * The local stream is already cancelled by the library's ComposerPrimitive;
 * this only asks the detached server run to end and reports when it could not.
 */
export async function stopChatRun(
  threadId: string,
  checkpoints: StreamCheckpoints,
): Promise<void> {
  // Stop ends the server run too, so the checkpoint must not offer a resume.
  checkpoints.clear(threadId);
  await api<{ stopped: boolean; thread_id: string }>(
    `/api/chat/${encodeURIComponent(threadId)}/stop`,
    { method: "POST" },
  );
}

/**
 * Reads the app-owned transcript. `append` is deliberately empty: the server
 * persists every turn, and writing it again here would duplicate history.
 * `fmt.decode` accepts the saved AI SDK message payload directly because the
 * stored content is the message without its id.
 */
export function createHistoryAdapter(aui: AssistantClient): ThreadHistoryAdapter {
  return {
    async load() {
      return { headId: null, messages: [] };
    },
    async append() {},
    withFormat: (fmt) => ({
      async load() {
        const remoteId = aui.threadListItem.getState().remoteId;
        if (!remoteId) return { messages: [] };
        let body: ThreadBody;
        try {
          body = await api<ThreadBody>(`/api/threads/${encodeURIComponent(remoteId)}/messages`);
        } catch (error) {
          // A thread initialized by a photo or voice note may not have a row
          // until that request lands; it has no history yet, not an error.
          if (error instanceof ApiError && error.status === 404) return { messages: [] };
          throw error;
        }
        let parentId: string | null = null;
        let headId: string | null = null;
        const messages = body.messages.map((message) => {
          const { id, ...content } = message;
          const decoded = fmt.decode({
            id,
            parent_id: parentId,
            format: fmt.format,
            content: content as never,
          });
          parentId = id;
          headId = id;
          return decoded;
        });
        return { headId, messages };
      },
      async append() {},
    }),
  };
}

type AudioCapture = {
  recorder: MediaRecorder | null;
  stream: MediaStream | null;
  cancelled: boolean;
  stopRequested: boolean;
  abort: AbortController;
};

const activeDictationCancels = new Set<() => void>();

/**
 * The composer runtime does not cancel dictation when a thread runtime is
 * disposed. Navigating away or switching threads must not leave the mic on,
 * so the chat surface calls this on selection changes and unmount.
 */
export function cancelActiveDictation() {
  for (const cancel of [...activeDictationCancels]) {
    try {
      cancel();
    } catch {
      // One broken session must not keep the others recording.
    }
  }
}

function stopTracks(stream: MediaStream | null) {
  stream?.getTracks().forEach((track) => track.stop());
}

function microphoneError(error: unknown): string {
  if (!(error instanceof Error)) return "Microphone access was refused.";
  if (error.name === "NotAllowedError" || error.name === "SecurityError") {
    return "Microphone access is blocked. Allow the microphone in your browser and try again.";
  }
  if (error.name === "NotFoundError" || error.name === "OverconstrainedError") {
    return "No microphone was found.";
  }
  if (error.name === "NotReadableError") return "The microphone is already in use.";
  return "Microphone access was refused.";
}

/** The app's existing server transcription, exposed through the dictation primitive. */
export class ServerDictationAdapter implements DictationAdapter {
  disableInputDuringDictation = true;

  constructor(
    /** The stable remote id for this runtime's thread, initialized if new. */
    private readonly resolveThreadId: () => Promise<string>,
    private readonly voice: VoiceAttachments,
    private readonly onStatus: (
      threadId: string,
      status:
        | { kind: "recording" | "transcribing"; message: string }
        | { kind: "error"; message: string }
        | null,
    ) => void,
    private readonly onError: (threadId: string, message: string) => void,
    private readonly onRetryCard: (
      threadId: string,
      attachmentId: string,
      card: Record<string, unknown>,
    ) => void,
  ) {}

  listen(): ReturnType<DictationAdapter["listen"]> {
    const speechStart = new Set<() => void>();
    const speechEnd = new Set<(result: { transcript: string; isFinal?: boolean }) => void>();
    const speech = new Set<(result: { transcript: string; isFinal?: boolean }) => void>();

    const capture: AudioCapture = {
      recorder: null,
      stream: null,
      cancelled: false,
      stopRequested: false,
      abort: new AbortController(),
    };
    const chunks: BlobPart[] = [];
    // The core cleans its listeners when `stop()` settles. Settling only after
    // the transcript lands is what lets the text reach the composer.
    let cancelSession: () => void = () => {};
    let settleStop: () => void = () => {};
    let settled = false;
    const stopped = new Promise<void>((resolve) => {
      settleStop = () => {
        activeDictationCancels.delete(cancelSession);
        resolve();
      };
    });
    const settle = () => {
      if (settled) return;
      settled = true;
      settleStop();
    };

    let sessionThreadId: string | null = null;

    const fail = (threadId: string, message: string) => {
      session.status = { type: "ended", reason: "error" };
      if (threadId) this.onError(threadId, message);
      settle();
    };

    const session: ReturnType<DictationAdapter["listen"]> = {
      status: { type: "starting" },
      stop: () => {
        capture.stopRequested = true;
        if (capture.recorder && capture.recorder.state !== "inactive") {
          capture.recorder.stop();
        }
        return stopped;
      },
      cancel: () => {
        capture.cancelled = true;
        capture.abort.abort();
        if (capture.recorder && capture.recorder.state !== "inactive") {
          capture.recorder.stop();
        }
        stopTracks(capture.stream);
        session.status = { type: "ended", reason: "cancelled" };
        if (sessionThreadId) this.onStatus(sessionThreadId, null);
        settle();
      },
      onSpeechStart: (callback) => {
        speechStart.add(callback);
        return () => speechStart.delete(callback);
      },
      onSpeechEnd: (callback) => {
        speechEnd.add(callback);
        return () => speechEnd.delete(callback);
      },
      onSpeech: (callback) => {
        speech.add(callback);
        return () => speech.delete(callback);
      },
    };

    cancelSession = () => session.cancel();
    activeDictationCancels.add(cancelSession);

    void (async () => {
      try {
        sessionThreadId = await this.resolveThreadId();
      } catch {
        fail("", "Could not prepare this conversation for voice input.");
        return;
      }
      if (capture.cancelled) {
        settle();
        return;
      }
      const threadId = sessionThreadId;

      // An insecure context or an old browser has no recorder to fall back to;
      // saying so is more useful than reporting a permission refusal.
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
        fail(threadId, "Voice input is not supported in this browser.");
        return;
      }

      try {
        capture.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (error) {
        if (capture.cancelled) {
          settle();
          return;
        }
        fail(threadId, microphoneError(error));
        return;
      }
      if (capture.cancelled) {
        stopTracks(capture.stream);
        settle();
        return;
      }
      // Stop before the microphone was ready ends the session without a
      // transcript instead of recording a fraction of a second.
      if (capture.stopRequested) {
        stopTracks(capture.stream);
        session.status = { type: "ended", reason: "stopped" };
        this.onStatus(threadId, null);
        settle();
        return;
      }

      const preferred = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "audio/mp4",
      ].find((type) => MediaRecorder.isTypeSupported(type));
      let recorder: MediaRecorder;
      try {
        recorder = new MediaRecorder(
          capture.stream,
          preferred ? { mimeType: preferred } : undefined,
        );
      } catch {
        stopTracks(capture.stream);
        fail(threadId, "Recording could not start.");
        return;
      }
      capture.recorder = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      recorder.onstart = () => {
        session.status = { type: "running" };
        this.onStatus(threadId, {
          kind: "recording",
          message: "Recording… tap stop to transcribe.",
        });
        for (const callback of speechStart) callback();
      };
      recorder.onerror = () => {
        if (capture.cancelled) return;
        stopTracks(capture.stream);
        fail(threadId, "Recording failed. Try again.");
      };
      recorder.onstop = async () => {
        stopTracks(capture.stream);
        // Cancelled before or while the media was arriving: no transcript,
        // no attachment, no late callback.
        if (capture.cancelled) {
          settle();
          return;
        }
        const type = recorder.mimeType || preferred || "audio/webm";
        const blob = new Blob(chunks, { type });
        chunks.length = 0;
        if (!blob.size) {
          // A tap that lands before any audio is a stop, not a failure.
          if (capture.stopRequested) {
            session.status = { type: "ended", reason: "stopped" };
            this.onStatus(threadId, null);
            settle();
          } else {
            fail(threadId, "Nothing was recorded.");
          }
          return;
        }
        this.onStatus(threadId, { kind: "transcribing", message: "Transcribing…" });
        const extension = type.includes("ogg") ? "ogg" : type.includes("mp4") ? "m4a" : "webm";
        const form = new FormData();
        form.append("file", blob, `voice.${extension}`);
        form.append("thread_id", threadId);
        try {
          const result = await api<TranscribeResult>("/api/transcribe", {
            method: "POST",
            body: form,
            signal: capture.abort.signal,
          });
          // Stop was pressed while the server was transcribing: the result
          // belongs to no message and must not be attached to the next one.
          if (capture.cancelled) {
            settle();
            return;
          }
          if (result.status !== "transcribed") {
            this.onRetryCard(threadId, result.attachment_id, result.card);
            this.onError(threadId, result.note);
            for (const callback of speechEnd) callback({ transcript: "" });
            session.status = { type: "ended", reason: "error" };
            settle();
            return;
          }
          this.voice.set(threadId, result.attachment_id);
          const answer = { transcript: result.transcript, isFinal: true };
          for (const callback of speech) callback(answer);
          for (const callback of speechEnd) callback(answer);
          session.status = { type: "ended", reason: "stopped" };
          this.onStatus(threadId, null);
          settle();
        } catch (caught) {
          if (capture.cancelled) {
            settle();
            return;
          }
          fail(
            threadId,
            caught instanceof Error ? caught.message : "Transcription failed.",
          );
        }
      };
      try {
        recorder.start();
        // Stop may have arrived between the readiness check and this start.
        if (capture.stopRequested) recorder.stop();
      } catch {
        stopTracks(capture.stream);
        fail(threadId, "Recording could not start.");
      }
    })();

    return session;
  }
}

/**
 * Thread metadata comes from the app's existing /api/threads endpoint. Search
 * is server-side, over message bodies, so the query is read when `list` runs.
 */
export function createThreadListAdapter(query: { current: string }): RemoteThreadListAdapter {
  function asMetadata(thread: ThreadList["threads"][number]) {
    return {
      status: "regular" as const,
      remoteId: thread.id,
      title: thread.title,
      lastMessageAt: new Date(thread.updated_at),
      custom: {
        message_count: thread.message_count,
        snippet: thread.snippet,
        running: thread.running,
        flagged: thread.flagged,
      },
    };
  }

  return {
    async list() {
      const search = query.current.trim();
      const result = await api<ThreadList>(
        `/api/threads${search ? `?q=${encodeURIComponent(search)}` : ""}`,
      );
      return { threads: result.threads.map(asMetadata) };
    },
    async initialize(threadId) {
      // The server creates the row on the first chat, photo, or voice request.
      return { remoteId: threadId };
    },
    async rename(remoteId, title) {
      await api(`/api/threads/${encodeURIComponent(remoteId)}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      });
    },
    async archive() {
      throw new Error("Archiving is not available in this app.");
    },
    async delete() {
      throw new Error("Deleting conversations is not available in this app.");
    },
    async fetch(threadId) {
      const body = await api<ThreadBody>(
        `/api/threads/${encodeURIComponent(threadId)}/messages`,
      );
      return {
        status: "regular",
        remoteId: body.thread.id,
        title: body.thread.title,
        custom: { running: body.running },
      };
    },
    async generateTitle() {
      // The server titles a conversation as its first turn settles.
      return createAssistantStream(async () => {});
    },
  };
}
