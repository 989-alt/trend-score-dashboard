import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { DisclaimerBanner } from "./DisclaimerBanner";
import { translate } from "../i18n";

// (a) The disclaimer text must always render.
describe("DisclaimerBanner", () => {
  it("renders the disclaimer text from i18n", () => {
    render(<DisclaimerBanner />);
    expect(screen.getByText(translate("disclaimer.text"))).toBeInTheDocument();
  });

  it("exposes a labelled note region for screen readers", () => {
    render(<DisclaimerBanner />);
    const note = screen.getByRole("note", {
      name: translate("disclaimer.label"),
    });
    expect(note).toBeInTheDocument();
  });
});
