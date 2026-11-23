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
