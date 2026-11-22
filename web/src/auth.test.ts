import { afterEach, describe, expect, it, vi } from "vitest";
import { logout, logoutAll } from "./auth";
import { markReverified, reverifiedNow } from "./reverified";

function accept(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(null, { status: 204 })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a fresh passkey check", () => {
  it("does not survive a logout", async () => {
    accept();
    markReverified();
    expect(reverifiedNow()).toBe(true);

    await logout();

    expect(reverifiedNow()).toBe(false);
  });

  it("does not survive logging out every device", async () => {
    accept();
    markReverified();

    await logoutAll();

    expect(reverifiedNow()).toBe(false);
  });
});
