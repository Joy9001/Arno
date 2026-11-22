import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ImportRecord } from "../types";
import { ImportsPanel } from "./Imports";

function record(id: string, status: ImportRecord["status"]): ImportRecord {
  return {
    import_id: id,
    status,
    attachment: {
      id: `att_${id}`,
      filename: "statement.pdf",
      content_type: "application/pdf",
      size_bytes: 1024,
      sha256: "0".repeat(64),
      purged_at: null,
    },
    account: null,
    period: { start: "2026-10-01", end: "2026-10-31" },
    opening_paise: null,
    closing_paise: null,
    check: null,
    check_detail: null,
    opening_source: null,
    opening_evidenced: false,
    opening_required: false,
    closing_adjustment_paise: 0,
    closing_adjustment_reason: null,
    stated_balance_paise: null,
    stated_balance_as_of: null,
    stated_balance_reason: null,
    closing_source_note: null,
    counts: null,
    review: null,
    reason: "password required",
    recovery_action: null,
    can_retry_parse: false,
    parser_saved: false,
    categorize: null,
    created_at: "2026-11-01T10:00:00Z",
    committed_at: null,
    rejected_at: null,
    undone_at: null,
    lines: [],
  };
}

function respond(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function installFetch(next: (url: string, init?: RequestInit) => Response | undefined): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      return next(url, init) ?? respond({ detail: `No fixture for ${url}` }, 404);
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the statement password card", () => {
  it("forgets a typed password when the user opens another import", async () => {
    installFetch((url) => {
      if (url.startsWith("/api/import-history")) return respond({ items: [] });
      if (url === "/api/accounts") return respond({ accounts: [] });
      if (url === "/api/layouts") return respond({ items: [] });
      if (url === "/api/imports/imp_1") return respond(record("imp_1", "needs_password"));
      if (url === "/api/imports/imp_2") return respond(record("imp_2", "needs_password"));
      return undefined;
    });

    const view = render(<ImportsPanel importId="imp_1" />);
    const input = await screen.findByPlaceholderText("Statement password");
    fireEvent.change(input, { target: { value: "hunter2" } });
    expect((input as HTMLInputElement).value).toBe("hunter2");

    view.rerender(<ImportsPanel importId="imp_2" />);

    await waitFor(() =>
      expect(
        (screen.getByPlaceholderText("Statement password") as HTMLInputElement).value,
      ).toBe(""),
    );
  });

  it("clears the password after the server accepts it", async () => {
    let unlocked = false;
    installFetch((url) => {
      if (url.startsWith("/api/import-history")) return respond({ items: [] });
      if (url === "/api/accounts") return respond({ accounts: [] });
      if (url === "/api/layouts") return respond({ items: [] });
      if (url === "/api/imports/imp_1/password") {
        unlocked = true;
        return respond(record("imp_1", "parsing"));
      }
      if (url === "/api/imports/imp_1") {
        return respond(record("imp_1", unlocked ? "parsing" : "needs_password"));
      }
      return undefined;
    });

    render(<ImportsPanel importId="imp_1" />);
    const input = await screen.findByPlaceholderText("Statement password");
    fireEvent.change(input, { target: { value: "hunter2" } });
    fireEvent.click(screen.getByRole("button", { name: "Unlock" }));

    await waitFor(() => expect(screen.queryByPlaceholderText("Statement password")).toBeNull());
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith("/password"))).toBe(true);
  });
});
