import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SettingsPanel } from "./Settings";

afterEach(() => { vi.unstubAllGlobals(); window.location.hash = ""; });

it("adds, promotes, and deletes merchant rules without changing match semantics", async () => {
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    void init;
    const url = String(input);
    const body = url === "/api/categories" ? {
      categories: [{ id: "food", name: "Food", active: true, reducible: true }],
      rules: [{ id: "r1", pattern: "shop", match: "contains", priority: 7, category_id: "food", category_name: "Food", source: "llm", hits: 2 }],
    } : {};
    return new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  window.location.hash = "#settings/categories";
  render(<SettingsPanel onSignOut={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Save as your rule" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => url === "/api/category-rules" && init?.method === "POST")).toBe(true));
  const promote = fetchMock.mock.calls.find(([url, init]) => url === "/api/category-rules" && init?.method === "POST");
  expect(JSON.parse(String(promote?.[1]?.body))).toEqual({ pattern: "shop", match: "contains", priority: 7, category_id: "food", source: "user" });
  fireEvent.change(screen.getByLabelText("Merchant"), { target: { value: "bakery" } });
  fireEvent.change(screen.getByLabelText("Category"), { target: { value: "food" } });
  fireEvent.click(screen.getByRole("button", { name: "Add rule" }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([url, init]) => url === "/api/category-rules" && init?.method === "POST")).toHaveLength(2));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  fireEvent.click(await screen.findByRole("button", { name: "Delete rule" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => url === "/api/category-rules/r1" && init?.method === "DELETE")).toBe(true));
});
