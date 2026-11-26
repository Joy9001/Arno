import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { InlineCitation } from "./inline-citation";
import { Sources } from "./sources";

vi.mock("@assistant-ui/react", () => ({
  useAuiState: (select: (state: unknown) => unknown) => select({ message: { parts: [
    { type: "source", sourceType: "url", id: "1", url: "https://example.com", title: "Example", providerMetadata: { arno: { snippet: "Evidence" } } },
    { type: "source", sourceType: "url", id: "2", url: "javascript:alert(1)", title: "Unsafe" },
  ] } }),
}));

describe("source access", () => {
  it("offers safe keyboard reachable links and their snippets", () => {
    render(<Sources />);
    const link = screen.getByRole("link", { name: "Example", hidden: true });
    expect(link.getAttribute("href")).toBe("https://example.com");
    expect(screen.getByText("Evidence")).toBeTruthy();
    expect(screen.queryByText("Unsafe")).toBeNull();
  });

  it("describes an inline citation and closes it with Escape", () => {
    render(<InlineCitation source={{ id: "1", url: "https://example.com", title: "Example", snippet: "Evidence" }}>[1]</InlineCitation>);
    const button = screen.getByRole("button", { name: "[1]" });
    expect(button.getAttribute("aria-describedby")).toBeTruthy();
    fireEvent.click(button);
    fireEvent.keyDown(button, { key: "Escape" });
    expect(button.getAttribute("aria-expanded")).toBe("false");
  });
});
