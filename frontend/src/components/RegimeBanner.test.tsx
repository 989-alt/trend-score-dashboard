import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { RegimeBanner } from "./RegimeBanner";
import { translate } from "../i18n";
import type { RegimeInfo } from "../types";

const MARKETS: RegimeInfo[] = [
  { market: "KR", regime: "UP_TREND", indexClose: 2500, ma200: 2400, adx: 31.7, aboveMa200: true },
  { market: "US", regime: "DOWN", indexClose: 4000, ma200: 4200, adx: 28.2, aboveMa200: false },
];

describe("RegimeBanner", () => {
  it("renders a chip per market with the regime label", () => {
    render(<RegimeBanner markets={MARKETS} />);
    expect(screen.getByText(translate("regime.title"))).toBeInTheDocument();
    expect(screen.getByText(translate("regime.UP_TREND"))).toBeInTheDocument();
    expect(screen.getByText(translate("regime.DOWN"))).toBeInTheDocument();
    // ADX rounded to integer.
    expect(screen.getByText(/32/)).toBeInTheDocument();
  });

  it("renders nothing when there is no regime data (static demo)", () => {
    const { container } = render(<RegimeBanner markets={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
