import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { InvestorFlowBars } from "./InvestorFlowBars";
import { fmtMoney } from "../../format";
import { translate } from "../../i18n";
import type { InvestorFlow } from "../../types";

const KR_FLOW: InvestorFlow = {
  date: "2026-06-04",
  foreignNet: 5_000_000_000,
  institutionNet: -2_000_000_000,
  individualNet: -3_000_000_000,
  foreignBuy: null,
  foreignSell: null,
  institutionBuy: null,
  institutionSell: null,
  individualBuy: null,
  individualSell: null,
};

// Flow with gross buy/sell amounts present (foreign actor).
const KR_FLOW_GROSS: InvestorFlow = {
  ...KR_FLOW,
  foreignBuy: 12_000_000_000,
  foreignSell: 7_000_000_000,
};

// (d) KR investor_flow renders as diverging bars (width-driven elements + the
// three investor rows). US renders the explicit "not provided" note instead.
describe("InvestorFlowBars", () => {
  it("renders KR net-buy bars with investor labels and values", () => {
    const { container } = render(
      <InvestorFlowBars flow={KR_FLOW} market="KR" />,
    );

    // Three investor rows present by Korean label.
    expect(screen.getByText(translate("investor.foreign"))).toBeInTheDocument();
    expect(
      screen.getByText(translate("investor.institution")),
    ).toBeInTheDocument();
    expect(
      screen.getByText(translate("investor.individual")),
    ).toBeInTheDocument();

    // As-of date and unit shown.
    expect(
      screen.getByText(new RegExp(KR_FLOW.date)),
    ).toBeInTheDocument();
    expect(screen.getByText(translate("investor.unit"))).toBeInTheDocument();

    // Bars are real width-driven elements (CSS bars), not text only.
    const bars = Array.from(
      container.querySelectorAll<HTMLElement>("[style]"),
    ).filter((el) => el.style.width !== "");
    expect(bars.length).toBeGreaterThanOrEqual(3);
    // Largest magnitude (foreign 5B) is the widest at 100%.
    expect(bars.some((b) => b.style.width === "100%")).toBe(true);

    // Net values rendered via the KR money formatter (eok/jo), not USD K/M/B.
    expect(screen.getByText(fmtMoney(5_000_000_000, "KR"))).toBeInTheDocument();
    expect(screen.getByText(fmtMoney(-2_000_000_000, "KR"))).toBeInTheDocument();
  });

  it("shows buy/sell amounts when the backend provides them", () => {
    render(<InvestorFlowBars flow={KR_FLOW_GROSS} market="KR" />);

    // Buy / sell labels present for the gross-amount row.
    expect(screen.getByText(translate("investor.buy"))).toBeInTheDocument();
    expect(screen.getByText(translate("investor.sell"))).toBeInTheDocument();

    // Gross amounts formatted in KR money units.
    expect(screen.getByText(fmtMoney(12_000_000_000, "KR"))).toBeInTheDocument();
    expect(screen.getByText(fmtMoney(7_000_000_000, "KR"))).toBeInTheDocument();
  });

  it("renders the 'not provided' note for US markets", () => {
    const { container } = render(
      <InvestorFlowBars flow={null} market="US" />,
    );
    expect(
      screen.getByText(translate("investor.notProvided")),
    ).toBeInTheDocument();
    expect(
      screen.getByText(translate("investor.notProvided.us")),
    ).toBeInTheDocument();
    // No width-driven bars in the not-provided state.
    const bars = Array.from(
      container.querySelectorAll<HTMLElement>("[style]"),
    ).filter((el) => el.style.width !== "");
    expect(bars.length).toBe(0);
  });

  it("shows the not-provided note when KR flow is missing", () => {
    render(<InvestorFlowBars flow={null} market="KR" />);
    expect(
      screen.getByText(translate("investor.notProvided")),
    ).toBeInTheDocument();
  });
});
