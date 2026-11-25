import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Card } from "./cards";

const photoFailure = {
  card: "capture-retry",
  kind: "photo",
  attachment_id: "att_1",
  title: "Photo not logged",
  note: "The photo couldn't be read. Retry it.",
};

describe("a photo that could not be read", () => {
  it("offers Retry by default", () => {
    render(<Card data={photoFailure} />);

    expect(screen.getByRole("button", { name: /Retry/ })).toBeTruthy();
  });

  it("hides Retry when reading photos is not configured", () => {
    render(<Card data={{ ...photoFailure, retryable: false }} />);

    expect(screen.queryByRole("button", { name: /Retry/ })).toBeNull();
    expect(screen.getByText("The photo couldn't be read. Retry it.")).toBeTruthy();
  });
});

describe("movements_batch card", () => {
  it("renders multi-row movements with a single header total and one Undo button", () => {
    render(
      <Card
        data={{
          card: "movements_batch",
          title: "Logged 3 movements",
          count: 3,
          total_outflow: { paise: 101_200, text: "₹1,012" },
          total_inflow: null,
          undo_id: "und_batch_1",
          items: [
            {
              movement_id: "mov_1",
              date: "2026-11-01",
              kind: "expense",
              payee: "Google",
              description: "Play Store",
              account: "HDFC Bank",
              category: "Software",
              amount: { paise: 200, text: "₹2" },
              summary: "Google ₹2",
            },
            {
              movement_id: "mov_2",
              date: "2026-11-01",
              kind: "expense",
              payee: "Opencode San Francisc Us",
              description: "API",
              account: "Credit Card",
              category: "Dev",
              amount: { paise: 98_700, text: "₹987" },
              summary: "Opencode ₹987",
            },
            {
              movement_id: "mov_3",
              date: "2026-11-01",
              kind: "split",
              payee: "Chai Point",
              description: "Snacks",
              account: "HDFC Bank",
              category: "Food",
              amount: { paise: 2_300, text: "₹23" },
              summary: "Chai Point ₹23",
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("Logged 3 movements")).toBeTruthy();
    expect(screen.getByText("₹1,012")).toBeTruthy();
    expect(screen.getByText("Google")).toBeTruthy();
    expect(screen.getByText("Opencode San Francisc Us")).toBeTruthy();
    expect(screen.getByText("Chai Point")).toBeTruthy();
    expect(screen.getByText("split")).toBeTruthy();
    expect(screen.getByText("2026-11-01 · HDFC Bank · Software")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /Undo/ })).toHaveLength(1);
  });

  it("renders separate Spent and Received totals when a batch includes both inflows and outflows", () => {
    render(
      <Card
        data={{
          card: "movements_batch",
          title: "Logged 2 movements",
          count: 2,
          total_outflow: { paise: 50_000, text: "₹500" },
          total_inflow: { paise: 120_000, text: "₹1,200" },
          undo_id: "und_batch_2",
          items: [
            {
              movement_id: "mov_out",
              date: "2026-11-02",
              kind: "expense",
              payee: "Metro",
              description: "",
              account: "Cash",
              category: "Transport",
              amount: { paise: 50_000, text: "₹500" },
              summary: "Metro ₹500",
            },
            {
              movement_id: "mov_in",
              date: "2026-11-02",
              kind: "refund",
              payee: "Amazon",
              description: "",
              account: "HDFC Bank",
              category: "Shopping",
              amount: { paise: 120_000, text: "₹1,200" },
              summary: "Amazon refund ₹1,200",
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("Spent ₹500")).toBeTruthy();
    expect(screen.getByText("Received ₹1,200")).toBeTruthy();
    expect(screen.getByText("refund")).toBeTruthy();
  });
});
