import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { RefreshCountdown } from "./RefreshCountdown";
import { translate } from "../i18n";

// FE-3: closed market / abnormal (>1h) remaining must not render a live mm:ss.
describe("RefreshCountdown", () => {
  const noop = () => {};

  it("shows 'closed' when the market is closed", () => {
    render(
      <RefreshCountdown
        lastUpdated={null}
        nextRefreshAt={null}
        marketOpen={false}
        refreshing={false}
        onRefresh={noop}
      />,
    );
    // The "closed" label is shown (label + value both read closed); no mm:ss.
    expect(
      screen.getAllByText(translate("market.closed")).length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/^\d{2}:\d{2}$/)).not.toBeInTheDocument();
  });

  it("shows 'next open HH:MM' when closed with a known next-open time", () => {
    // 09:00 local for a stable HH:MM regardless of the test machine TZ.
    const next = new Date();
    next.setHours(9, 0, 0, 0);
    render(
      <RefreshCountdown
        lastUpdated={null}
        nextRefreshAt={next.toISOString()}
        marketOpen={false}
        refreshing={false}
        onRefresh={noop}
      />,
    );
    expect(
      screen.getByText(translate("refresh.nextOpen", { time: "09:00" })),
    ).toBeInTheDocument();
  });

  it("does not render a runaway countdown when remaining exceeds an hour", () => {
    // 5 hours out with the market nominally open: not a real poll interval.
    const far = new Date(Date.now() + 5 * 60 * 60 * 1000).toISOString();
    render(
      <RefreshCountdown
        lastUpdated={null}
        nextRefreshAt={far}
        marketOpen={true}
        refreshing={false}
        onRefresh={noop}
      />,
    );
    // No "300:00"-style runaway value.
    expect(screen.queryByText(/\d{3,}:\d{2}/)).not.toBeInTheDocument();
    expect(screen.getByText(translate("market.closed"))).toBeInTheDocument();
  });
});
