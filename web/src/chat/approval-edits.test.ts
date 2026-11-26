import { describe, expect, it } from "vitest";
import { ApprovalEdits } from "./runtime";
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
    expect(edits.approved("thread-b", messages)).toEqual({});
    messages[0].parts[0] = {
      ...messages[0].parts[0],
      approval: { id: "approval", approved: false },
    } as (typeof messages)[0]["parts"][0];
    expect(edits.approved("thread-a", messages)).toEqual({});
  });
});
