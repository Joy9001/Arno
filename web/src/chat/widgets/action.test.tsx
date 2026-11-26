import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ActionReceipt } from "./action";

afterEach(() => vi.unstubAllGlobals());

it("keeps undo available when the server rejects it and confirms only after success", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Cannot undo yet" }), { status: 409 }))
    .mockResolvedValueOnce(new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  render(<ActionReceipt data={{ title: "Logged", undo_id: "group_1" }} />);
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect((await screen.findByRole("alert")).textContent).toContain("Cannot undo yet");
  expect(screen.queryByText("Undone.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  expect(await screen.findByText("Undone.")).toBeTruthy();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
