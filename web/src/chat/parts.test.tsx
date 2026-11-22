import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import { ChatToolFallback } from "./parts";

type Props = ComponentProps<typeof ChatToolFallback>;

function pending(respondToApproval: (answer: { approved: boolean }) => Promise<void>): Props {
  return {
    type: "tool-call",
    toolCallId: "call_1",
    toolName: "preference",
    args: { action: "remember", text: "Never show rent reminders" },
    argsText: "{}",
    status: { type: "complete", reason: "stop" },
    approval: { id: "call_1", prompt: "Remember this preference?" },
    respondToApproval,
  } as unknown as Props;
}

describe("a pending confirm card", () => {
  it("sends a denial without running the tool", async () => {
    const respond = vi.fn(async () => {});
    render(<ChatToolFallback {...pending(respond)} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(respond).toHaveBeenCalledTimes(1);
    expect(respond).toHaveBeenCalledWith({ approved: false });
  });

  it("sends one continuation when the confirm button is clicked twice", () => {
    const respond = vi.fn(() => new Promise<void>(() => {}));
    render(<ChatToolFallback {...pending(respond)} />);

    const confirm = screen.getByRole("button", { name: "Confirm" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    expect(respond).toHaveBeenCalledTimes(1);
    expect(respond).toHaveBeenCalledWith({ approved: true });
  });
});
