import type { AssistantClient } from "@assistant-ui/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  CHAT_JOIN_STRATEGY,
  ServerDictationAdapter,
  VoiceAttachments,
  createHistoryAdapter,
  createThreadListAdapter,
  stopChatRun,
} from "./runtime";
import type { StreamCheckpoints } from "./streams";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("stopping a run", () => {
  it("clears the resume checkpoint before asking the server to stop", async () => {
    const order: string[] = [];
    const checkpoints = {
      clear: vi.fn((threadId: string) => order.push(`clear:${threadId}`)),
    } as unknown as StreamCheckpoints;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        order.push("fetch");
        return json({ stopped: true, thread_id: "thread_1" });
      }),
    );

    await stopChatRun("thread/1", checkpoints);

    expect(checkpoints.clear).toHaveBeenCalledWith("thread/1");
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe("/api/chat/thread%2F1/stop");
    expect((vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit).method).toBe("POST");
    expect(order).toEqual(["clear:thread/1", "fetch"]);
  });
});

it("offers a retry after transcription fails and attaches only the successful recording", async () => {
  class Recorder {
    static isTypeSupported() {
      return true;
    }
    state = "inactive";
    onstart?: () => void;
    onstop?: () => void;
    ondataavailable?: (event: { data: Blob }) => void;

    start() {
      this.state = "recording";
      this.onstart?.();
    }

    stop() {
      this.ondataavailable?.({ data: new Blob(["recorded audio"]) });
      this.state = "inactive";
      this.onstop?.();
    }
  }

  const stop = vi.fn();
  vi.stubGlobal("navigator", {
    mediaDevices: { getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop }] })) },
  });
  vi.stubGlobal("MediaRecorder", Recorder);
  const card = { card: "capture-retry" };
  vi.stubGlobal(
    "fetch",
    vi.fn()
      .mockResolvedValueOnce(json({ status: "failed", attachment_id: "failed", card, note: "Try again." }))
      .mockResolvedValueOnce(json({ status: "transcribed", attachment_id: "saved", transcript: "Lunch" })),
  );

  const voice = new VoiceAttachments();
  const onError = vi.fn();
  const onRetry = vi.fn();
  const adapter = new ServerDictationAdapter(async () => "thread", voice, vi.fn(), onError, onRetry);
  const record = async () => {
    const session = adapter.listen();
    await new Promise<void>((resolve) => session.onSpeechStart(resolve));
    const transcript = vi.fn();
    session.onSpeech(transcript);
    await session.stop();
    return { status: session.status, transcript };
  };

  const failed = await record();
  expect(failed.status).toEqual({ type: "ended", reason: "error" });
  expect(onRetry).toHaveBeenCalledWith("thread", "failed", card);
  expect(onError).toHaveBeenCalledWith("thread", "Try again.");
  expect(failed.transcript).not.toHaveBeenCalled();
  expect(voice.take("thread")).toBeNull();

  const retry = await record();
  expect(retry.status).toEqual({ type: "ended", reason: "stopped" });
  expect(retry.transcript).toHaveBeenCalledWith({ transcript: "Lunch", isFinal: true });
  expect(voice.take("thread")).toBe("saved");
  expect(stop).toHaveBeenCalledTimes(2);
});

describe("reading a thread's history", () => {
  const aui = {
    threadListItem: { getState: () => ({ remoteId: "thread/1" }) },
  } as unknown as AssistantClient;
  it("treats a thread without saved history as empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ title: "Not found", status: 404 }, 404)),
    );
    const history = createHistoryAdapter(aui).withFormat!({
      format: "ai-sdk",
      encode: () => {
        throw new Error("no message should be encoded");
      },
      getId: () => "unused",
      decode: () => {
        throw new Error("no message should be decoded");
      },
    });

    await expect(history.load()).resolves.toEqual({ messages: [] });
    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe("/api/threads/thread%2F1/messages");
  });

  it("keeps a real read failure visible", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ detail: "The database is unreachable." }, 503)),
    );
    const history = createHistoryAdapter(aui).withFormat!({
      format: "ai-sdk",
      encode: () => {
        throw new Error("no message should be encoded");
      },
      getId: () => "unused",
      decode: () => {
        throw new Error("no message should be decoded");
      },
    });

    await expect(history.load()).rejects.toThrow("The database is unreachable.");
  });

  it("reports that a finished thread is not running, so a stale resume ends", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        json({
          thread: { id: "thread/1", title: "Rent", flagged: false, snapshot_at: null },
          messages: [],
          running: false,
          as_of: "2026-11-23",
        }),
      ),
    );
    const onRunning = vi.fn();
    const history = createHistoryAdapter(aui, onRunning).withFormat!({
      format: "ai-sdk",
      encode: () => {
        throw new Error("no message should be encoded");
      },
      getId: () => "unused",
      decode: () => {
        throw new Error("no message should be decoded");
      },
    });

    await history.load();

    expect(onRunning).toHaveBeenCalledWith(false);
  });
});

describe("creating a conversation", () => {
  it("returns the server's id instead of the local placeholder", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ id: "cnv_abc", title: "New chat" }, 201)),
    );
    const adapter = createThreadListAdapter({ current: "" });

    await expect(adapter.initialize("__LOCALID_123")).resolves.toEqual({
      remoteId: "cnv_abc",
      externalId: undefined,
    });
    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe("/api/threads");
    expect((vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit).method).toBe("POST");
  });
});

describe("stored assistant replies", () => {
  it("never merge with the reply that follows", () => {
    // The AI SDK merges consecutive assistant messages by default; a wake
    // turn's answer then folds into the next message's activity panel.
    expect(CHAT_JOIN_STRATEGY).toBe("none");
  });
});
