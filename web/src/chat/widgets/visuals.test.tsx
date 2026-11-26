import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ComparisonMatrix } from "./visuals";
import { ToolWidgets } from ".";
import { renderWidget } from "./registry";

describe("scenario comparison", () => {
  it("shows only calculated combinations and leaves missing combinations explicit", () => {
    render(<ComparisonMatrix data={{ scenarios: [
      { label: "Offer A", tenure_months: 12, prepayment_paise: 0, monthly_paise: 10000, interest_paise: 20000, total_paise: 120000 },
      { label: "Offer B", tenure_months: 24, prepayment_paise: 5000, monthly_paise: 5000, interest_paise: 30000, total_paise: 130000 },
    ] }} />);
    expect(screen.getByText("Offer A")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Prepayment"), { target: { value: "5000" } });
    expect(screen.getByText("No calculation was returned for this combination.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Tenure"), { target: { value: "24" } });
    expect(screen.getByText("Offer B")).toBeTruthy();
  });
});

it("renders a ledger metric through the allowed registry", () => {
  render(<ToolWidgets toolName="run_query" args={{}} result={{ columns: ["label", "metric_paise"], rows: [["Rent", 120000]], truncated: false }} />);
  expect(screen.getAllByText("Rent").length).toBeGreaterThan(0);
  expect(screen.getAllByText("₹1,200").length).toBeGreaterThan(0);
});

it("does not render a component outside the widget allowlist", () => {
  const { container } = render(<>{renderWidget({ _type: "Unapproved", amount: 1 })}</>);
  expect(container.textContent).not.toContain("Unapproved");
});
