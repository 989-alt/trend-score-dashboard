import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { DetailDrawer } from "./DetailDrawer";
import { translate } from "../../i18n";
import { makeEntry } from "../../test/fixtures";

describe("DetailDrawer", () => {
  it("renders nothing when no entry is selected", () => {
    const { container } = render(
      <DetailDrawer entry={null} onClose={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  // (d) KR entry: investor flow shown as bars (width-driven elements).
  it("shows KR investor flow as bars", () => {
    const entry = makeEntry({ market: "KR" });
    const { container } = render(
      <DetailDrawer entry={entry} onClose={() => {}} />,
    );

    // Investor-flow section header present.
    expect(
      screen.getByText(translate("drawer.section.investorFlow")),
    ).toBeInTheDocument();
    // Investor labels (bars) rendered.
    expect(screen.getByText(translate("investor.foreign"))).toBeInTheDocument();

    const bars = Array.from(
      container.querySelectorAll<HTMLElement>("[style]"),
    ).filter((el) => el.style.width !== "");
    expect(bars.length).toBeGreaterThanOrEqual(3);

    // US "not provided" note must NOT appear for KR.
    expect(
      screen.queryByText(translate("investor.notProvided.us")),
    ).not.toBeInTheDocument();
  });

  // (d) US entry: investor flow shown as the "not provided" note, not bars.
  it("shows US investor flow as a 'not provided' note", () => {
    const entry = makeEntry({ market: "US", investor_flow: null });
    render(<DetailDrawer entry={entry} onClose={() => {}} />);

    expect(
      screen.getByText(translate("investor.notProvided")),
    ).toBeInTheDocument();
    expect(
      screen.getByText(translate("investor.notProvided.us")),
    ).toBeInTheDocument();
    // No bar labels for US flow.
    expect(
      screen.queryByText(translate("investor.foreign")),
    ).not.toBeInTheDocument();
  });

  it("always shows the disclaimer inside the drawer", () => {
    const entry = makeEntry();
    render(<DetailDrawer entry={entry} onClose={() => {}} />);
    expect(
      screen.getByText(translate("disclaimer.text")),
    ).toBeInTheDocument();
  });

  it("closes on the close button and on Escape", () => {
    const onClose = vi.fn();
    const entry = makeEntry();
    render(<DetailDrawer entry={entry} onClose={onClose} />);

    fireEvent.click(
      screen.getByRole("button", { name: translate("drawer.close") }),
    );
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
