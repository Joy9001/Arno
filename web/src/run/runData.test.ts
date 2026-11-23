import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ImportTask } from "../types";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function task(overrides: Partial<ImportTask> = {}): ImportTask {
  return {
    task_id: "task_1",
    agent_name: "parser",
    state: "finished",
    outcome: "ok",
    started_at: 0,
    finished_at: 1,
    report: null,
    question: null,
    plan: [],
    imports: [],
    messages: [],
    model: null,
    conversation_id: "cnv_1",
    follow_up: null,
    run: { stages: [], steps: [], segments: [], used: 0, limit: 120, stop: null },
    ...overrides,
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("how a run ended", () => {
  it("does not repeat a model service error as the card's sentence", async () => {
    const { runEnding } = await import("./runData");
    const ending = runEnding(
      task({
        outcome: "error",
        imports: [
          {
            import_id: "imp_1",
            filename: "statement.pdf",
            status: "failed",
            reason:
              "The parser stopped with an error: ModelRetry: Sub-agent 'parser' crashed: ModelServerError: status_code: 500, model_name: hy4-flash-free",
            progress: null,
            counts: null,
          },
        ],
      }),
    );

    expect(ending.tone).toBe("failed");
    expect(ending.body).toBe(
      "The model service failed before the statement was read. Nothing was saved.",
    );
  });

  it("does not repeat an internal tool error as the card's sentence", async () => {
    const { runEnding } = await import("./runData");
    const ending = runEnding(
      task({
        report: { status: "gave_up", reason: "open_imports returned no import_id for the file" },
      }),
    );

    expect(ending.body).toBe(
      "Something stopped it before it could read the statement. Nothing was saved.",
    );
  });

  it("keeps a blocked run's plain reason", async () => {
    const { runEnding } = await import("./runData");
    const ending = runEnding(
      task({
        outcome: "error",
        report: { status: "blocked", blocking: "No statement file was attached." },
      }),
    );

    expect(ending.body).toBe("No statement file was attached. Nothing was saved.");
  });

  it("names a file that is not a statement", async () => {
    const { runEnding } = await import("./runData");
    const ending = runEnding(
      task({
        report: { status: "not_a_statement", reason: "payslip" },
        imports: [
          {
            import_id: "imp_1",
            filename: "payslip.pdf",
            status: "not_a_statement",
            reason: "This file isn't a statement, so nothing was imported.",
            progress: null,
            counts: null,
          },
        ],
      }),
    );

    expect(ending.body).toBe(
      "This file isn't a statement, so nothing was imported. Nothing was saved.",
    );
  });
});

describe("the shared run store", () => {
  it("polls once for every card that mounts the same task", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(task({ task_id: "poll_once", state: "running" }))),
    );
    const { useRun } = await import("./runData");
    const { result } = renderHook(() => ({
      first: useRun("poll_once"),
      second: useRun("poll_once"),
    }));
    await act(async () => {});

    expect(result.current.first.task?.task_id).toBe("poll_once");
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  });

  it("reads a finished task once and only refetches on an explicit reload", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json(task({ task_id: "read_once", state: "finished" }))),
    );
    const { useRun } = await import("./runData");
    const first = renderHook(() => useRun("read_once"));
    await act(async () => {});
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
    first.unmount();

    const second = renderHook(() => useRun("read_once"));
    await act(async () => {});
    expect(second.result.current.task?.task_id).toBe("read_once");
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);

    await act(async () => {
      second.result.current.reload();
    });
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);  });

  it("keeps polling a running task after a failed read, with backoff", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockRejectedValueOnce(new Error("network down"))
        .mockImplementation(async () => json(task({ task_id: "retry_running", state: "running" }))),
    );
    const { useRun } = await import("./runData");
    const { result } = renderHook(() => useRun("retry_running"));
    await act(async () => {});

    expect(result.current.error).toBe("network down");
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
    expect(result.current.task?.task_id).toBe("retry_running");
    expect(result.current.error).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(3);
  });
});
