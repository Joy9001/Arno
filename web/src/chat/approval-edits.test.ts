import { describe, expect, it, vi } from "vitest";
import { ApprovalEdits, ChatTransport, VoiceAttachments } from "./runtime";
import type { ChatMessage } from "./types";

describe("approval edits", () => {
  it("sends edits only for approved calls in the current thread", () => {
    const edits = new ApprovalEdits();
    edits.set("thread-a", "call-1", { category: "Food" });
    const messages = [
      {
        role: "assistant",
        id: "message",
        parts: [
          {
            type: "tool-correct_movement",
            toolCallId: "call-1",
            input: {},
            state: "approval-responded",
            approval: { id: "approval", approved: true },
          },
        ],
      },
    ] as ChatMessage[];
    expect(edits.approved("thread-a", messages)).toEqual({
      "call-1": { category: "Food" },
    });
    expect(edits.approved("thread-b", messages)).toBeUndefined();
    messages[0].parts[0] = {
      ...messages[0].parts[0],
      approval: { id: "approval", approved: false },
    } as (typeof messages)[0]["parts"][0];
    expect(edits.approved("thread-a", messages)).toBeUndefined();
  });
  it("serializes ordinary and approval sends, consuming edits once", async () => {
    const bodies: Record<string, unknown>[] = [];
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      bodies.push(JSON.parse(String(init?.body)));
      return new Response("data: [DONE]\n\n");
    });
    try {
      const edits = new ApprovalEdits();
      const storage = { getStreamId: () => null, setStreamId: () => {}, clear: () => {} };
      const transport = new ChatTransport({ voice: new VoiceAttachments(), approvalEdits: edits, storage });
      const user: ChatMessage = { id: "user", role: "user", parts: [{ type: "text", text: "Hi" }] };
      const approval = {
        id: "assistant", role: "assistant", parts: [{ type: "tool-correct_movement",
          toolCallId: "call-1", input: {}, state: "approval-responded",
          approval: { id: "approval", approved: true } }],
      } as ChatMessage;
      const send = (messages: ChatMessage[]) => transport.sendMessages({
        chatId: "thread-a", messages, trigger: "submit-message", messageId: undefined, abortSignal: undefined,
      });
      await send([user]);
      await send([approval]);
      edits.set("thread-a", "call-1", { category: "Food" });
      await send([approval]);
      await send([approval]);
      edits.set("thread-a", "call-1", { category: "Travel" });
      await send([approval, user]);
      await send([approval]);
      expect(bodies[0]).not.toHaveProperty("approval_edits");
      expect(bodies[1]).not.toHaveProperty("approval_edits");
      expect(bodies[2].approval_edits).toEqual({ "call-1": { category: "Food" } });
      expect(bodies[3]).not.toHaveProperty("approval_edits");
      expect(bodies[4]).not.toHaveProperty("approval_edits");
      expect(bodies[5].approval_edits).toEqual({ "call-1": { category: "Travel" } });
    } finally {
      fetch.mockRestore();
    }
  });

});
