import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LedgerPanel } from "./Ledger";

function respond(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
}

afterEach(() => vi.unstubAllGlobals());

it("assigns an existing category only after confirmation and offers undo", async () => {
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/accounts") return respond({ accounts: [] });
    if (url.startsWith("/api/movements")) return respond({ items: [], next_cursor: null });
    if (url.startsWith("/api/uncategorized?")) return respond({ total: 1, groups: [{ key: "shop", kind: "merchant", count: 1, total_paise: 500, narration: "Shop", hint: null }] });
    if (url === "/api/categories") return respond({ categories: [{ id: "food", name: "Food", active: true, reducible: true }], rules: [] });
    if (url === "/api/uncategorized/answer") return respond({ labeled: 1, undo_id: "undo-1" });
    if (url === "/api/undo/undo-1") return respond({});
    throw new Error(`Unexpected request ${url} ${init?.method ?? "GET"}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<LedgerPanel />);
  fireEvent.change(await screen.findByLabelText("Category for shop"), { target: { value: "food" } });
  expect(fetchMock.mock.calls.some(([url]) => url === "/api/uncategorized/answer")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Answer" }));
  await screen.findByText(/1 row labeled/);
  const answer = fetchMock.mock.calls.find(([url]) => url === "/api/uncategorized/answer");
  expect(JSON.parse(String(answer?.[1]?.body))).toEqual({ key: "shop", category_id: "food" });
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === "/api/undo/undo-1")).toBe(true));
});

it("opens category creation in Ask without sending a message", async () => {
  const listener = vi.fn();
  window.addEventListener("arno:ask", listener);
  vi.stubGlobal("fetch", vi.fn(async () => respond({ accounts: [], items: [], categories: [], groups: [], total: 0 })));
  render(<LedgerPanel />);
  fireEvent.click(screen.getByRole("button", { name: "Ask to create a category" }));
  expect(listener.mock.calls[0][0].detail).toEqual({ text: "Help me create a category.", send: false });
  window.removeEventListener("arno:ask", listener);
});
