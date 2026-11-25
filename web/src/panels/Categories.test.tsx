import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Category, UncategorizedGroup } from "../types";
import { LedgerPanel } from "./Ledger";
import { SettingsPanel } from "./Settings";

function respond(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function installFetch(
  next: (url: string, init?: RequestInit) => Response | undefined,
): ReturnType<typeof vi.fn> {
  const spy = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    return next(url, init) ?? respond({ detail: `No fixture for ${url}` }, 404);
  });
  vi.stubGlobal("fetch", spy);
  return spy;
}

function makeCategories(): Category[] {
  return [
    { id: "cat_food", name: "Food & Dining", reducible: true, active: true },
    { id: "cat_archived", name: "Old Hobby", reducible: true, active: false },
  ];
}

function makeUncategorizedGroup(): UncategorizedGroup {
  return {
    key: "manas",
    kind: "merchant",
    count: 18,
    total_paise: 2597179,
    narration: "UPI/DR/533654347738/MANAS CH/PUNB/manaschand/Cook",
    hint: { category_id: null, reason: "Individual payee, purpose unclear" },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("Settings → Categories creation", () => {
  it("validates empty and duplicate names, then creates a custom category", async () => {
    let categories = makeCategories();
    const fetchMock = installFetch((url, init) => {
      if (url === "/api/settings") {
        return respond({
          base_currency: "INR",
          time_zone: "Asia/Kolkata",
          floor_paise: 2500000,
          default_horizon_days: 90,
          notification_time: "09:00",
          weekly_briefing_dow: 0,
          reminder_lead_days: 3,
          profile: { user_id: "usr_1", display_name: "You" },
          last_model_error_type: null,
          last_model_error_at: null,
          updated_at: null,
        });
      }
      if (url === "/api/backup/status") {
        return respond({
          last_success_at: null,
          last_success_age_seconds: null,
          last_attempt: null,
          due_at: null,
          due: false,
          alert_at: null,
          overdue: false,
          checked_at: "2026-11-25T00:00:00Z",
        });
      }
      if (url === "/api/preferences") {
        return respond({ items: [] });
      }
      if (url === "/api/categories" && (!init?.method || init.method === "GET")) {
        return respond({
          categories,
          rules: [],
          as_of: "2026-11-25T00:00:00Z",
        });
      }
      if (url === "/api/categories" && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as { name: string; reducible: boolean };
        const created: Category = {
          id: "cat_help",
          name: body.name,
          reducible: body.reducible,
          active: true,
        };
        categories = [...categories, created];
        return respond({ ...created, undo_id: "undo_cat_1" }, 201);
      }
      return undefined;
    });

    window.location.hash = "#settings/categories";
    render(<SettingsPanel onSignOut={() => {}} />);

    const form = await screen.findByRole("form", { name: "Add category" });
    const nameInput = within(form).getByLabelText("New category") as HTMLInputElement;
    const reducibleBox = within(form).getByLabelText("Reducible in tradeoffs") as HTMLInputElement;
    const submitBtn = within(form).getByRole("button", { name: "Add category" });

    // Empty submit surfaces accessible validation without posting.
    fireEvent.click(submitBtn);
    const emptyAlert = await within(form).findByRole("alert");
    expect(emptyAlert.textContent).toContain("Enter a category name.");
    expect(nameInput.getAttribute("aria-invalid")).toBe("true");
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => url === "/api/categories" && init?.method === "POST",
      ),
    ).toBe(false);

    // Archived duplicate gives a restore hint.
    fireEvent.change(nameInput, { target: { value: "old hobby" } });
    fireEvent.click(submitBtn);
    const dupAlert = await within(form).findByRole("alert");
    expect(dupAlert.textContent).toContain(
      "“Old Hobby” is archived below. Restore it to use it again.",
    );

    // Valid submission posts and refreshes the list.
    fireEvent.change(nameInput, { target: { value: "Household Help" } });
    fireEvent.click(reducibleBox);
    fireEvent.click(submitBtn);

    await waitFor(() => {
      const statuses = screen.getAllByRole("status");
      expect(statuses.some((node) => node.textContent?.includes("Added “Household Help”."))).toBe(
        true,
      );
    });
    const matches = await screen.findAllByText("Household Help");
    expect(matches.some((node) => node.tagName === "P")).toBe(true);
    expect(matches.some((node) => node.tagName === "OPTION")).toBe(true);

    const postCall = fetchMock.mock.calls.find(
      ([url, init]) => url === "/api/categories" && init?.method === "POST",
    );
    expect(postCall).toBeDefined();
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
      name: "Household Help",
      reducible: true,
    });
    expect(nameInput.value).toBe("");
    expect(reducibleBox.checked).toBe(false);
  });
});

describe("Activity → Ledger → Uncategorized category creation", () => {
  it("creates a category from the payee dropdown, selects it without auto-answering, then answers", async () => {
    let categories = makeCategories();
    const groups = [makeUncategorizedGroup()];
    const fetchMock = installFetch((url, init) => {
      if (url === "/api/accounts") return respond({ accounts: [] });
      if (url.startsWith("/api/movements")) return respond({ items: [], next_cursor: null });
      if (url.startsWith("/api/uncategorized?")) {
        return respond({ groups, total: groups.reduce((sum, item) => sum + item.count, 0) });
      }
      if (url === "/api/categories" && (!init?.method || init.method === "GET")) {
        return respond({
          categories,
          rules: [],
          as_of: "2026-11-25T00:00:00Z",
        });
      }
      if (url === "/api/categories" && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as { name: string; reducible: boolean };
        const created: Category = {
          id: "cat_help",
          name: body.name,
          reducible: body.reducible,
          active: true,
        };
        categories = [...categories, created];
        return respond({ ...created, undo_id: "undo_cat_2" }, 201);
      }
      if (url === "/api/uncategorized/answer" && init?.method === "POST") {
        groups.splice(0, groups.length);
        return respond({ status: "answered", labeled: 18, undo_id: "undo_ans_1" });
      }
      return undefined;
    });

    render(<LedgerPanel />);

    const select = (await screen.findByLabelText("Category for manas")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "__new_category__" } });

    const dialog = await screen.findByRole("dialog", { name: "New category" });
    const nameInput = within(dialog).getByLabelText("Category name") as HTMLInputElement;
    fireEvent.change(nameInput, { target: { value: "Household Help" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create category" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "New category" })).toBeNull();
    });
    expect(select.value).toBe("cat_help");
    expect(screen.getByRole("status").textContent).toContain(
      "Added category “Household Help” and selected it for MANAS.",
    );

    // Did not auto-answer the payee.
    expect(
      fetchMock.mock.calls.some(([url]) => url === "/api/uncategorized/answer"),
    ).toBe(false);

    // Clicking Answer sends the new category ID.
    fireEvent.click(screen.getByRole("button", { name: "Answer" }));
    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain(
        "18 rows labeled. One undo reverts it.",
      );
    });
    const answerCall = fetchMock.mock.calls.find(([url]) => url === "/api/uncategorized/answer");
    expect(answerCall).toBeDefined();
    expect(JSON.parse(String(answerCall?.[1]?.body))).toEqual({
      key: "manas",
      category_id: "cat_help",
    });
  });

  it("creates a category from the header button and removes it on undo", async () => {
    let categories = makeCategories();
    const groups = [makeUncategorizedGroup()];
    const fetchMock = installFetch((url, init) => {
      if (url === "/api/accounts") return respond({ accounts: [] });
      if (url.startsWith("/api/movements")) return respond({ items: [], next_cursor: null });
      if (url.startsWith("/api/uncategorized?")) {
        return respond({ groups, total: 18 });
      }
      if (url === "/api/categories" && (!init?.method || init.method === "GET")) {
        return respond({
          categories,
          rules: [],
          as_of: "2026-11-25T00:00:00Z",
        });
      }
      if (url === "/api/categories" && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as { name: string; reducible: boolean };
        const created: Category = {
          id: "cat_temp",
          name: body.name,
          reducible: body.reducible,
          active: true,
        };
        categories = [...categories, created];
        return respond({ ...created, undo_id: "undo_cat_3" }, 201);
      }
      if (url === "/api/undo/undo_cat_3" && init?.method === "POST") {
        categories = categories.filter((item) => item.id !== "cat_temp");
        return respond({ status: "undone" });
      }
      return undefined;
    });

    render(<LedgerPanel />);

    const newBtn = await screen.findByRole("button", { name: "New category" });
    fireEvent.click(newBtn);

    const dialog = await screen.findByRole("dialog", { name: "New category" });
    fireEvent.change(within(dialog).getByLabelText("Category name"), {
      target: { value: "Pet Care" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create category" }));

    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain("Added category “Pet Care”.");
    });

    const select = (await screen.findByLabelText("Category for manas")) as HTMLSelectElement;
    expect(within(select).getByRole("option", { name: "Pet Care" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Undo" }));

    await waitFor(() => {
      expect(screen.getByRole("status").textContent).toContain("The new category was undone.");
    });
    await waitFor(() => {
      expect(within(select).queryByRole("option", { name: "Pet Care" })).toBeNull();
    });
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => url === "/api/undo/undo_cat_3" && init?.method === "POST",
      ),
    ).toBe(true);
  });
});
