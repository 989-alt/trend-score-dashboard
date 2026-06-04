import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { RankingTable } from "./RankingTable";
import { translate } from "../i18n";
import { entryWith, makeEntry } from "../test/fixtures";
import type { ScoreEntry } from "../types";

function makeEntries(): ScoreEntry[] {
  return [
    entryWith({ ticker: "A001", name: "AlphaCorp", grade: "strong_buy", score: 91, near52w: 99 }),
    entryWith({ ticker: "B002", name: "BetaInc", grade: "buy", score: 70, near52w: 60 }),
    entryWith({ ticker: "C003", name: "GammaLtd", grade: "hold", score: 45, near52w: 80, eligible: false }),
    entryWith({
      ticker: "D004",
      name: "DeltaCo",
      grade: "sell",
      score: 20,
      near52w: 30,
      sellAlert: true,
      sellReason: "trailing_stop",
    }),
  ];
}

/** Names of entries in body order (skips the header row). */
function bodyNames(): string[] {
  const rows = screen.getAllByRole("button").filter((el) => el.tagName === "TR");
  return rows
    .map((r) => within(r).queryByText(/Corp|Inc|Ltd|Co$/)?.textContent ?? "")
    .filter(Boolean);
}

// (e) RankingTable: sorting + filtering behaviour.
describe("RankingTable", () => {
  it("defaults to score-descending order", () => {
    render(<RankingTable entries={makeEntries()} onSelect={() => {}} />);
    expect(bodyNames()).toEqual(["AlphaCorp", "BetaInc", "GammaLtd", "DeltaCo"]);
  });

  it("filters by free-text query over name and ticker", () => {
    render(<RankingTable entries={makeEntries()} onSelect={() => {}} />);
    const search = screen.getByLabelText(translate("ranking.search.placeholder"));

    fireEvent.change(search, { target: { value: "Beta" } });
    expect(screen.getByText("BetaInc")).toBeInTheDocument();
    expect(screen.queryByText("AlphaCorp")).not.toBeInTheDocument();

    // Ticker search also works.
    fireEvent.change(search, { target: { value: "C003" } });
    expect(screen.getByText("GammaLtd")).toBeInTheDocument();
    expect(screen.queryByText("BetaInc")).not.toBeInTheDocument();
  });

  it("filters by grade", () => {
    render(<RankingTable entries={makeEntries()} onSelect={() => {}} />);
    const select = screen.getByLabelText(translate("ranking.filter.grade"));
    fireEvent.change(select, { target: { value: "buy" } });
    expect(screen.getByText("BetaInc")).toBeInTheDocument();
    expect(screen.queryByText("AlphaCorp")).not.toBeInTheDocument();
    expect(screen.queryByText("GammaLtd")).not.toBeInTheDocument();
  });

  it("filters to eligible-only entries", () => {
    render(<RankingTable entries={makeEntries()} onSelect={() => {}} />);
    const checkbox = screen.getByRole("checkbox");
    fireEvent.click(checkbox);
    // GammaLtd is the only non-eligible entry; it disappears.
    expect(screen.queryByText("GammaLtd")).not.toBeInTheDocument();
    expect(screen.getByText("AlphaCorp")).toBeInTheDocument();
  });

  it("sorts ascending then descending when a column header is clicked", () => {
    render(<RankingTable entries={makeEntries()} onSelect={() => {}} />);

    // Click the score column header. Default direction for score is desc, so the
    // first click on the already-active sort key flips it to ascending.
    const scoreHeader = screen
      .getByText(translate("col.score"))
      .closest("button")!;

    fireEvent.click(scoreHeader);
    // Ascending by score: Delta(20) < Gamma(45) < Beta(70) < Alpha(91).
    expect(bodyNames()).toEqual(["DeltaCo", "GammaLtd", "BetaInc", "AlphaCorp"]);

    fireEvent.click(scoreHeader);
    // Back to descending.
    expect(bodyNames()).toEqual(["AlphaCorp", "BetaInc", "GammaLtd", "DeltaCo"]);
  });

  it("sorts by name ascending", () => {
    render(<RankingTable entries={makeEntries()} onSelect={() => {}} />);
    const nameHeader = screen
      .getByText(translate("col.ticker"))
      .closest("button")!;
    fireEvent.click(nameHeader);
    expect(bodyNames()).toEqual(["AlphaCorp", "BetaInc", "DeltaCo", "GammaLtd"]);
  });

  it("shows the filtered-empty message when nothing matches", () => {
    render(<RankingTable entries={makeEntries()} onSelect={() => {}} />);
    const search = screen.getByLabelText(translate("ranking.search.placeholder"));
    fireEvent.change(search, { target: { value: "zzzNoMatch" } });
    expect(
      screen.getByText(translate("ranking.empty.filtered")),
    ).toBeInTheDocument();
  });

  it("shows the empty message when there are no entries", () => {
    render(<RankingTable entries={[]} onSelect={() => {}} />);
    expect(screen.getByText(translate("ranking.empty"))).toBeInTheDocument();
  });

  it("invokes onSelect when a row is clicked", () => {
    const onSelect = vi.fn();
    const entries = makeEntries();
    render(<RankingTable entries={entries} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("AlphaCorp"));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0].ticker).toBe("A001");
  });

  // FE-6: a null stop price must read as "not computed", never a bare dash that
  // could be mistaken for "safe".
  it("labels a missing stop price instead of showing a bare dash", () => {
    const entry = makeEntry({
      ticker: "Z999",
      name: "NoStopCo",
      stop_price: null,
    });
    render(<RankingTable entries={[entry]} onSelect={() => {}} />);
    const label = screen.getByText(translate("stop.notSet"));
    expect(label).toBeInTheDocument();
    // Has an explanatory tooltip clarifying it is not "safe".
    expect(label).toHaveAttribute("title", translate("stop.notSet.hint"));
  });

  // FE-1: KR turnover shows eok/jo units, not USD-style B.
  it("formats KR turnover in Korean money units", () => {
    const entry = makeEntry({
      ticker: "T100",
      name: "TurnoverCo",
      market: "KR",
      turnover: "5000000000",
    });
    render(<RankingTable entries={[entry]} onSelect={() => {}} />);
    expect(
      screen.getByText(`50.0${translate("unit.eok")}`),
    ).toBeInTheDocument();
    expect(screen.queryByText("5.00B")).not.toBeInTheDocument();
  });
});
