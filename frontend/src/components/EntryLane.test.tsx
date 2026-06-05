import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { EntryLane } from "./EntryLane";
import { translate } from "../i18n";
import { entryWith } from "../test/fixtures";

const buyProps = {
  variant: "buy" as const,
  title: translate("buyLane.title"),
  desc: translate("buyLane.desc"),
  icon: "▲",
};
const sellProps = {
  variant: "sell" as const,
  title: translate("sellAlert.lane.title"),
  desc: translate("sellAlert.lane.desc"),
  icon: "⚠",
};

describe("EntryLane", () => {
  it("renders nothing when there are no entries", () => {
    const { container } = render(
      <EntryLane {...buyProps} entries={[]} onSelect={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("buy lane shows the grade badge, region title and total count", () => {
    const entries = [
      entryWith({ ticker: "B1", name: "BuyCorp", grade: "strong_buy" }),
      entryWith({ ticker: "B2", name: "AltBuy", grade: "buy" }),
    ];
    render(<EntryLane {...buyProps} entries={entries} onSelect={() => {}} />);
    expect(
      screen.getByRole("region", { name: translate("buyLane.title") }),
    ).toBeInTheDocument();
    expect(screen.getByText("BuyCorp")).toBeInTheDocument();
    expect(screen.getByText(translate("grade.strong_buy"))).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("sell lane shows the sell badge", () => {
    const entries = [
      entryWith({
        ticker: "S1",
        name: "AlertCorp",
        grade: "sell",
        sellAlert: true,
        sellReason: "trailing_stop",
      }),
    ];
    render(<EntryLane {...sellProps} entries={entries} onSelect={() => {}} />);
    expect(screen.getByText(translate("sellAlert.badge"))).toBeInTheDocument();
  });

  it("previews 6 entries and toggles the rest while keeping the total count", () => {
    const entries = Array.from({ length: 8 }, (_, i) =>
      entryWith({ ticker: `T${i}`, name: `Name${i}`, grade: "buy" }),
    );
    render(<EntryLane {...buyProps} entries={entries} onSelect={() => {}} />);

    // Total count always reflects all entries, even when collapsed.
    expect(screen.getByText("8")).toBeInTheDocument();
    // Preview shows the first 6, hides the rest.
    expect(screen.getByText("Name5")).toBeInTheDocument();
    expect(screen.queryByText("Name6")).not.toBeInTheDocument();
    expect(screen.queryByText("Name7")).not.toBeInTheDocument();

    // Expand reveals the remaining 2.
    fireEvent.click(
      screen.getByText(translate("lane.expand").replace("{n}", "2")),
    );
    expect(screen.getByText("Name6")).toBeInTheDocument();
    expect(screen.getByText("Name7")).toBeInTheDocument();

    // Collapse hides them again.
    fireEvent.click(screen.getByText(translate("lane.collapse")));
    expect(screen.queryByText("Name7")).not.toBeInTheDocument();
  });

  it("invokes onSelect with the clicked entry", () => {
    const onSelect = vi.fn();
    const entry = entryWith({ ticker: "B1", name: "BuyCorp", grade: "buy" });
    render(<EntryLane {...buyProps} entries={[entry]} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("BuyCorp"));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(entry);
  });
});
