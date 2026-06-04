import { describe, expect, it } from "vitest";
import {
  fmtCompact,
  fmtCountdown,
  fmtMoney,
  fmtPct,
  fmtPctPlain,
  fmtPrice,
  signClass,
} from "./format";
import { translate } from "./i18n";

describe("formatters", () => {
  it("formats signed percent", () => {
    expect(fmtPct(1.5)).toBe("+1.50%");
    expect(fmtPct(-0.5)).toBe("-0.50%");
    expect(fmtPct(null)).toBe("—");
  });

  it("formats plain percent", () => {
    expect(fmtPctPlain(93.27)).toBe("93.3%");
  });

  it("formats price per market", () => {
    expect(fmtPrice(70000, "KR")).toBe("70,000");
    expect(fmtPrice(123.456, "US")).toBe("123.46");
    expect(fmtPrice(null, "KR")).toBe("—");
  });

  it("computes sign class", () => {
    expect(signClass(1)).toBe("up");
    expect(signClass(-1)).toBe("down");
    expect(signClass(0)).toBe("flat");
    expect(signClass(null)).toBe("flat");
  });

  it("formats countdown mm:ss and clamps at 0", () => {
    expect(fmtCountdown(75)).toBe("01:15");
    expect(fmtCountdown(-5)).toBe("00:00");
  });

  it("caps countdown at 59:59 for abnormal (>1h) durations", () => {
    // FE-3: a runaway value must never render as e.g. "1320:00".
    expect(fmtCountdown(1320 * 60)).toBe("59:59");
    expect(fmtCountdown(3600)).toBe("59:59");
  });

  it("formats KR currency in eok/jo units, not USD K/M/B (FE-1)", () => {
    const eok = translate("unit.eok");
    const jo = translate("unit.jo");
    const won = translate("unit.won");
    // 5e9 KRW must be "50.0" + eok suffix, never "5.00B".
    expect(fmtMoney(5_000_000_000, "KR")).toBe(`50.0${eok}`);
    expect(fmtMoney(5_000_000_000, "KR")).not.toContain("B");
    expect(fmtMoney(-2_000_000_000, "KR")).toBe(`-20.0${eok}`);
    expect(fmtMoney(5_000_000_000_000, "KR")).toBe(`5.00${jo}`);
    expect(fmtMoney(70_000_000, "KR")).toBe(`70,000,000${won}`);
    expect(fmtMoney(null, "KR")).toBe("—");
  });

  it("formats US currency with a dollar sign and K/M/B/T (FE-1)", () => {
    expect(fmtMoney(5_000_000_000, "US")).toBe("$5.00B");
    expect(fmtMoney(1_500_000, "US")).toBe("$1.50M");
    expect(fmtMoney(2_000_000_000_000, "US")).toBe("$2.00T");
    expect(fmtMoney(null, "US")).toBe("—");
  });

  it("keeps fmtCompact currency-free for share counts", () => {
    // Volume is a share count, not money — no currency sign or eok/jo unit.
    const out = fmtCompact(1_000_000);
    expect(out).toBe("1.00M");
    expect(out).not.toContain("$");
  });
});

describe("i18n", () => {
  it("translates known grade keys to non-empty distinct labels", () => {
    // Avoid Hangul literals in source: assert structure, not exact glyphs.
    const strong = translate("grade.strong_buy");
    const sell = translate("grade.sell");
    expect(strong).not.toBe("grade.strong_buy");
    expect(sell).not.toBe("grade.sell");
    expect(strong).not.toBe(sell);
    expect(strong.length).toBeGreaterThan(0);
  });

  it("interpolates variables", () => {
    expect(translate("ranking.count", { n: 7 })).toContain("7");
  });

  it("falls back to the key when unknown", () => {
    expect(translate("does.not.exist")).toBe("does.not.exist");
  });
});
