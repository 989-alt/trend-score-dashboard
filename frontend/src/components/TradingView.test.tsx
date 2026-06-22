import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TradingView } from "./TradingView";
import { translate } from "../i18n";
import type {
  NavPoint,
  TradingOrder,
  TradingPosition,
  TradingStatus,
} from "../types";

const STATUS: TradingStatus = {
  running: true,
  totalEval: 10_500_000,
  cash: 4_500_000,
  totalPnl: 500_000,
  realizedPnl: 120_000,
  positionCount: 2,
  asOf: "2026-06-18T05:30:00+09:00",
  disclaimer: "d",
};

const POSITIONS: TradingPosition[] = [
  {
    ticker: "005930",
    name: "SamsungElec",
    qty: 10,
    avgPrice: 70_000,
    curPrice: 75_000,
    evalAmount: 750_000,
    pnlAmount: 50_000,
    pnlPct: 7.14,
  },
  {
    ticker: "000660",
    name: "SkHynix",
    qty: 5,
    avgPrice: 120_000,
    curPrice: 110_000,
    evalAmount: 550_000,
    pnlAmount: -50_000,
    pnlPct: -8.33,
  },
];

const ORDERS: TradingOrder[] = [
  {
    ts: "2026-06-18T09:05:00+09:00",
    ticker: "005930",
    side: "buy",
    qty: 10,
    filledQty: 10,
    status: "체결",
    reason: "breakout",
    message: "ok",
  },
  {
    ts: "2026-06-18T14:20:00+09:00",
    ticker: "000660",
    side: "sell",
    qty: 5,
    filledQty: 0,
    status: "접수",
    reason: "trailing_stop",
    message: "ok",
  },
];

// First→last NAV: 10,000,000 → 10,500,000 = +5.00% cumulative return.
const NAV: NavPoint[] = [
  { ts: "2026-06-16T05:30:00+09:00", totalEval: 10_000_000, cash: 5_000_000 },
  { ts: "2026-06-17T05:30:00+09:00", totalEval: 10_200_000, cash: 4_800_000 },
  { ts: "2026-06-18T05:30:00+09:00", totalEval: 10_500_000, cash: 4_500_000 },
];

describe("TradingView", () => {
  it("shows running status and summary metrics", () => {
    render(
      <TradingView status={STATUS} positions={POSITIONS} orders={ORDERS} nav={NAV} />,
    );
    expect(screen.getByText(translate("trading.running"))).toBeInTheDocument();
    // Total eval rendered with Won unit + grouping.
    expect(
      screen.getByText(`10,500,000${translate("unit.won")}`),
    ).toBeInTheDocument();
    // Position count.
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("renders positions with name, ticker and signed pnl percent", () => {
    render(
      <TradingView status={STATUS} positions={POSITIONS} orders={ORDERS} nav={NAV} />,
    );
    expect(screen.getByText("SamsungElec")).toBeInTheDocument();
    expect(screen.getByText("SkHynix")).toBeInTheDocument();
    // Winner shows a +% , loser shows a -% .
    expect(screen.getByText(/\+7\.14%/)).toBeInTheDocument();
    expect(screen.getByText(/-8\.33%/)).toBeInTheDocument();
  });

  it("renders the order timeline with side chips", () => {
    render(
      <TradingView status={STATUS} positions={POSITIONS} orders={ORDERS} nav={NAV} />,
    );
    expect(screen.getByText(translate("trading.side.buy"))).toBeInTheDocument();
    expect(screen.getByText(translate("trading.side.sell"))).toBeInTheDocument();
    expect(screen.getByText("trailing_stop")).toBeInTheDocument();
    // Fill status chips: filled buy + unfilled sell (the still-held contradiction).
    expect(screen.getByText(translate("trading.fill.filled"))).toBeInTheDocument();
    expect(screen.getByText(translate("trading.fill.none"))).toBeInTheDocument();
  });

  it("shows realized pnl distinct from unrealized total", () => {
    render(
      <TradingView status={STATUS} positions={POSITIONS} orders={ORDERS} nav={NAV} />,
    );
    expect(screen.getByText(translate("trading.realizedPnl"))).toBeInTheDocument();
    expect(screen.getByText(`+120,000${translate("unit.won")}`)).toBeInTheDocument();
  });

  it("computes the cumulative NAV return from the first point", () => {
    render(
      <TradingView status={STATUS} positions={POSITIONS} orders={ORDERS} nav={NAV} />,
    );
    // 10,000,000 → 10,500,000 = +5.00%.
    expect(screen.getByText("+5.00%")).toBeInTheDocument();
    // The sparkline svg is rendered.
    expect(
      screen.getByRole("img", { name: /\+5\.00%/ }),
    ).toBeInTheDocument();
  });

  it("shows the stopped chip when not running", () => {
    render(
      <TradingView
        status={{ ...STATUS, running: false }}
        positions={POSITIONS}
        orders={ORDERS}
        nav={NAV}
      />,
    );
    expect(screen.getByText(translate("trading.stopped"))).toBeInTheDocument();
  });

  it("shows the empty state when there is no data at all", () => {
    render(<TradingView status={null} positions={[]} orders={[]} nav={[]} />);
    // The empty message appears (big empty state).
    expect(screen.getAllByText(translate("trading.empty")).length).toBeGreaterThan(0);
    expect(screen.queryByText(translate("trading.positions"))).not.toBeInTheDocument();
  });
});
