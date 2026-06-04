import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { translate as t } from "../../i18n";
import { fmtPrice } from "../../format";
import { makeEntry } from "../../test/fixtures";
import { RationaleSection } from "./RationaleSection";

describe("RationaleSection", () => {
  it("renders grade conclusion + criteria checklist for an eligible strong_buy", () => {
    render(<RationaleSection entry={makeEntry({ grade: "strong_buy", eligible: true })} />);
    expect(screen.getByText(t("drawer.section.recommendation"))).toBeInTheDocument();
    expect(screen.getByText(t("grade.strong_buy"))).toBeInTheDocument();
    expect(screen.getByText(t("rec.checklist"))).toBeInTheDocument();
    expect(screen.getByText(t("crit.trend"))).toBeInTheDocument();
    expect(screen.getByText(t("crit.leader"))).toBeInTheDocument();
    // no sell warning for a healthy buy
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the trailing-stop sell warning with the actual stop price", () => {
    render(
      <RationaleSection
        entry={makeEntry({
          grade: "sell",
          sell_alert: true,
          sell_reason: "trailing_stop",
          trailing_peak: "72000",
          stop_price: "66240",
          price: "65000",
        })}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toBeInTheDocument();
    expect(alert.textContent ?? "").toContain(fmtPrice(66240, "KR"));
  });

  it("lists hard-filter rejection reasons when ineligible (below MA200, negative momentum)", () => {
    render(
      <RationaleSection
        entry={makeEntry({
          eligible: false,
          grade: "avoid",
          score: "0",
          factors: {
            near_52w: "0.4",
            pocket_pivot: "0",
            momentum_norm: "0",
            turnover_norm: "0",
            vol_fit: "0",
            momentum: "-0.05",
            volatility: "0.35",
            above_ma200: false,
          },
        })}
      />,
    );
    expect(screen.getByText(t("rec.inelig.title"))).toBeInTheDocument();
    expect(screen.getByText(t("rec.inelig.ma200"))).toBeInTheDocument();
    expect(screen.getByText(t("rec.inelig.momentum"))).toBeInTheDocument();
  });
});
