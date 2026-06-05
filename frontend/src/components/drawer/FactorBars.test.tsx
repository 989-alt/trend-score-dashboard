import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { FactorBars } from "./FactorBars";
import { translate } from "../../i18n";
import type { FactorBreakdown } from "../../types";

function factors(overrides: Partial<FactorBreakdown> = {}): FactorBreakdown {
  return {
    near52w: 0.8,
    pocketPivot: 0,
    momentumNorm: 0.5,
    rsNorm: 0.5,
    turnoverNorm: 0.5,
    volFit: 0.6,
    momentum: 0.1,
    rs: 0.02,
    volatility: 0.35,
    aboveMa200: true,
    ...overrides,
  };
}

describe("FactorBars", () => {
  it("ineligible: shows band-relative values (negative / >1) and a note", () => {
    render(
      <FactorBars
        eligible={false}
        factors={factors({ momentumNorm: -0.3, turnoverNorm: 1.8 })}
      />,
    );
    expect(screen.getByText("-0.30")).toBeInTheDocument(); // 밴드 미달(음수)
    expect(screen.getByText("1.80")).toBeInTheDocument(); // 밴드 초과(>1)
    expect(
      screen.getByText(translate("factor.inelig.note")),
    ).toBeInTheDocument();
  });

  it("eligible: no ineligible note", () => {
    render(<FactorBars eligible factors={factors()} />);
    expect(
      screen.queryByText(translate("factor.inelig.note")),
    ).not.toBeInTheDocument();
  });

  it("renders a dash when factors is null", () => {
    const { container } = render(<FactorBars factors={null} />);
    expect(container.textContent).toContain("—");
  });
});
