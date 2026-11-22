import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { HistoryPanel } from "./History";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("undoes the selected group once and reloads its state", async () => {
  const item = {
    undo_id: "move_undo",
    created_at: "2026-11-21T12:00:00Z",
    actor: "user",
    message_id: null,
    conversation_id: null,
    summary: "Recorded a movement",
    records: [],
    undoable: true,
    reason: null,
  };
  const page = (entry: object) =>
    Response.json({ items: [entry], next_cursor: null });
  let completeUndo = () => {};
  const pending = new Promise<Response>((resolve) => {
    completeUndo = () => resolve(new Response(null, { status: 204 }));
  });
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(page(item))
    .mockReturnValueOnce(pending)
    .mockResolvedValueOnce(page({ ...item, undoable: false, reason: "Already undone" }));
  vi.stubGlobal("fetch", fetchMock);
  render(<HistoryPanel />);

  const undo = await screen.findByRole("button", { name: "Undo" });
  fireEvent.click(undo);
  expect(undo).toHaveProperty("disabled", true);
  fireEvent.click(undo);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock).toHaveBeenLastCalledWith(
    "/api/undo/move_undo",
    expect.objectContaining({ method: "POST" }),
  );

  completeUndo();
  expect(await screen.findByText("Already undone")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
});
