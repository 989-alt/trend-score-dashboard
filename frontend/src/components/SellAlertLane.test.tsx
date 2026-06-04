import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { SellAlertLane } from "./SellAlertLane";
import { translate } from "../i18n";
import { entryWith } from "../test/fixtures";

// (c) sell_alert entries must surface in the lane (with the sell badge); rows
// without sell_alert must be excluded; an empty lane renders nothing.
describe("SellAlertLane", () => {
  it("lists only sell_alert entries and shows the sell badge label", () => {
    const entries = [
      entryWith({ ticker: "OK01", name: "SafeCorp", grade: "buy" }),
      entryWith({
        ticker: "AL01",
        name: "AlertCorp",
        grade: "sell",
        sellAlert: true,
        sellReason: "trailing_stop",
      }),
    ];
    render(<SellAlertLane entries={entries} onSelect={() => {}} />);

    // Alert entry shown.
    expect(screen.getByText("AlertCorp")).toBeInTheDocument();
    // Non-alert entry not shown.
    expect(screen.queryByText("SafeCorp")).not.toBeInTheDocument();

    // Sell badge (label + reason) is present.
    expect(screen.getByText(translate("sellAlert.badge"))).toBeInTheDocument();
    expect(
      screen.getByText(translate("sellReason.trailing_stop")),
    ).toBeInTheDocument();
  });

  it("renders the lane title and a count of alerts", () => {
    const entries = [
      entryWith({ ticker: "A1", name: "A", grade: "sell", sellAlert: true }),
      entryWith({ ticker: "A2", name: "B", grade: "sell", sellAlert: true }),
    ];
    render(<SellAlertLane entries={entries} onSelect={() => {}} />);
    expect(
      screen.getByRole("region", { name: translate("sellAlert.lane.title") }),
    ).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("renders nothing when there are no sell alerts", () => {
    const { container } = render(
      <SellAlertLane
        entries={[entryWith({ ticker: "X", name: "X", grade: "buy" })]}
        onSelect={() => {}}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("invokes onSelect with the clicked alert entry", () => {
    const onSelect = vi.fn();
    const alert = entryWith({
      ticker: "AL01",
      name: "AlertCorp",
      grade: "sell",
      sellAlert: true,
      sellReason: "ma200_break",
    });
    render(<SellAlertLane entries={[alert]} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("AlertCorp"));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(alert);
  });
});
