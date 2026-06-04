import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { ScoreGauge } from "./ScoreGauge";

// FE-4: a "sell" entry must not paint the bar with a high-score (green) color.
describe("ScoreGauge", () => {
  it("uses a band color by default", () => {
    const { container } = render(<ScoreGauge score={90} />);
    const fill = container.querySelector<HTMLElement>("[style]");
    // 90 is a "high" band; class list should not include the alert modifier.
    expect(fill?.className ?? "").not.toContain("alert");
    expect(fill?.style.width).toBe("90%");
  });

  it("overrides to the alert color when alert is set, even at a high score", () => {
    const { container } = render(<ScoreGauge score={90} alert />);
    const fill = container.querySelector<HTMLElement>("[style]");
    expect(fill?.className ?? "").toContain("alert");
  });

  it("always exposes the numeric value and meter role for AA", () => {
    const { container, getByText } = render(<ScoreGauge score={42} />);
    expect(container.querySelector('[role="meter"]')).toBeInTheDocument();
    expect(getByText("42")).toBeInTheDocument();
  });
});
