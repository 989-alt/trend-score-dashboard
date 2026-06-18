import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MarketTabs, TABPANEL_ID, tabId } from "./MarketTabs";

// FE-5: tabs expose stable ids + aria-controls so the content panel can be a
// role="tabpanel" labelled by the active tab.
describe("MarketTabs", () => {
  it("gives each tab a stable id and points it at the shared tabpanel", () => {
    render(<MarketTabs active="kr" onChange={() => {}} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(4);
    for (const tab of tabs) {
      expect(tab.id).toMatch(/^tab-(themes|kr|us|news)$/);
      expect(tab).toHaveAttribute("aria-controls", TABPANEL_ID);
    }
  });

  it("marks the active tab as selected", () => {
    render(<MarketTabs active="us" onChange={() => {}} />);
    const active = document.getElementById(tabId("us"));
    expect(active).toHaveAttribute("aria-selected", "true");
    const inactive = document.getElementById(tabId("kr"));
    expect(inactive).toHaveAttribute("aria-selected", "false");
  });
});
