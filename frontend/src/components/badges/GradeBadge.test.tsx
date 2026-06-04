import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { GradeBadge } from "./GradeBadge";
import { GRADE_ICON } from "../../format";
import { translate } from "../../i18n";
import type { Grade } from "../../types";

const GRADES: Grade[] = ["strong_buy", "buy", "hold", "avoid", "sell"];

// (b) GradeBadge must convey meaning via a Korean text label + icon glyph,
// never color alone (WCAG AA). Assert label text and icon presence per grade.
describe("GradeBadge", () => {
  it.each(GRADES)("renders the Korean label and icon for grade %s", (grade) => {
    const { container } = render(<GradeBadge grade={grade} />);
    const label = translate(`grade.${grade}`);

    // Korean label is present and is a real translation (not the raw key).
    expect(label).not.toBe(`grade.${grade}`);
    expect(screen.getByText(label)).toBeInTheDocument();

    // Icon glyph is present (decorative, aria-hidden), proving meaning is not
    // carried by color alone.
    const icon = container.querySelector('[aria-hidden="true"]');
    expect(icon).not.toBeNull();
    expect(icon?.textContent).toBe(GRADE_ICON[grade]);
  });

  it("renders distinct labels across grades", () => {
    const labels = GRADES.map((g) => translate(`grade.${g}`));
    expect(new Set(labels).size).toBe(GRADES.length);
  });

  it("supports the small size variant", () => {
    const { container } = render(<GradeBadge grade="buy" size="sm" />);
    // Still shows the label in compact form.
    expect(screen.getByText(translate("grade.buy"))).toBeInTheDocument();
    expect(container.firstChild).toBeTruthy();
  });
});
