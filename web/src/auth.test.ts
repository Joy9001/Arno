import { afterEach, describe, expect, it, vi } from "vitest";
import { login, logout } from "./auth";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("sign-in calls", () => {
  it("posts the passphrase as JSON and signs out with a POST", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await login("open sesame");
    await logout();

    const [loginCall, logoutCall] = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(loginCall[0]).toBe("/api/auth/login");
    expect(loginCall[1].body).toBe(JSON.stringify({ passphrase: "open sesame" }));
    expect(logoutCall[0]).toBe("/api/auth/logout");
    expect(logoutCall[1].method).toBe("POST");
  });
});
