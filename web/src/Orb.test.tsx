import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Orb } from "./Orb";

class FakeObserver {
  static latest: FakeObserver | undefined;
  constructor(private readonly callback: IntersectionObserverCallback) {
    FakeObserver.latest = this;
  }
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
  trigger(isIntersecting: boolean) {
    this.callback(
      [{ isIntersecting } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    );
  }
}

afterEach(() => {
  FakeObserver.latest = undefined;
  vi.unstubAllGlobals();
});

describe("an orb", () => {
  it("stops animating while it is not visible", () => {
    vi.stubGlobal("IntersectionObserver", FakeObserver);
    const { container } = render(<Orb live />);
    const orb = container.querySelector(".orb");

    expect(orb?.classList.contains("live")).toBe(true);

    act(() => FakeObserver.latest!.trigger(false));
    expect(orb?.classList.contains("live")).toBe(false);

    act(() => FakeObserver.latest!.trigger(true));
    expect(orb?.classList.contains("live")).toBe(true);
  });

  it("stays still unless asked to animate", () => {
    vi.stubGlobal("IntersectionObserver", FakeObserver);
    const { container } = render(<Orb />);

    expect(container.querySelector(".orb")?.classList.contains("live")).toBe(false);
  });
});
