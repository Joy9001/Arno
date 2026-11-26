import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api";
import { parseProjectionReceipt } from "../../projectionReceipt";
import { AffordabilityLab } from ".";

vi.mock("../../api", async (importOriginal) => ({
  ...await importOriginal<typeof import("../../api")>(),
  api: vi.fn(),
}));

const raw = {
  as_of: "2026-11-06", horizon_days: 90, curve: [], floor_paise: 100000,
  floor_set: true, floor_probability: 0.97, floor_threshold: 0.95,
  median_low: { date: "2026-11-20", paise: 300000 }, first_floor_crossing: null,
  data_quality: {},
};

describe("affordability lab", () => {
  beforeEach(() => vi.mocked(api).mockReset().mockResolvedValue(raw));

  it("does not add a purchase when only the spending cut changes", async () => {
    const seed = parseProjectionReceipt({ ...raw, horizon_days: 30 });
    expect(seed).not.toBeNull();
    render(<AffordabilityLab seed={seed!} />);
    fireEvent.change(screen.getByLabelText(/Reducible spending cut/), { target: { value: "10" } });
    await waitFor(() => expect(api).toHaveBeenCalled());
    const params = new URLSearchParams(String(vi.mocked(api).mock.calls[0][0]).split("?")[1]);
    expect(params.get("cut_percent")).toBe("10");
    expect(params.get("horizon_days")).toBe("30");
    expect(params.has("amount_paise")).toBe(false);
    fireEvent.change(screen.getByLabelText(/Price/), { target: { value: "6000000" } });
    await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
    const purchase = new URLSearchParams(String(vi.mocked(api).mock.calls[1][0]).split("?")[1]);
    expect(purchase.get("amount_paise")).toBe("6000000");
    expect(purchase.get("cut_percent")).toBe("10");
  });

  it("preserves an off-step purchase until the price slider moves", async () => {
    const seed = parseProjectionReceipt({ ...raw, request: { hypothetical: { amount_paise: 5123456 } } });
    expect(seed).not.toBeNull();
    render(<AffordabilityLab seed={seed!} />);
    const price = screen.getByLabelText(/Price/);
    expect(price.getAttribute("step")).toBe("50000");
    expect(price.getAttribute("aria-valuetext")).toContain("51,234.56");
    fireEvent.change(screen.getByLabelText(/Reducible spending cut/), { target: { value: "10" } });
    await waitFor(() => expect(api).toHaveBeenCalled());
    const params = new URLSearchParams(String(vi.mocked(api).mock.calls[0][0]).split("?")[1]);
    expect(params.get("amount_paise")).toBe("5123456");
    fireEvent.change(price, { target: { value: "5173456" } });
    await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
    const updated = new URLSearchParams(String(vi.mocked(api).mock.calls[1][0]).split("?")[1]);
    expect(updated.get("amount_paise")).toBe("5173456");
  });

  it("keeps the slider positive for a low-priced purchase", async () => {
    const seed = parseProjectionReceipt({ ...raw, request: { hypothetical: { amount_paise: 12345 } } });
    expect(seed).not.toBeNull();
    render(<AffordabilityLab seed={seed!} />);
    const price = screen.getByLabelText(/Price/) as HTMLInputElement;
    expect(price.min).toBe("12345");
    expect(price.value).toBe("12345");
    fireEvent.change(price, { target: { value: "62345" } });
    await waitFor(() => expect(api).toHaveBeenCalled());
    const params = new URLSearchParams(String(vi.mocked(api).mock.calls[0][0]).split("?")[1]);
    expect(params.get("amount_paise")).toBe("62345");
  });
});
