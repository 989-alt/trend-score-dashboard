import { describe, expect, it } from "vitest";
import { parseEntry, parseIssues, toNum } from "./api";
import type { RawIssuesResponse, RawScoreEntry } from "./types";

describe("toNum", () => {
  it("parses Decimal-as-string into a number", () => {
    expect(toNum("123.45")).toBe(123.45);
  });
  it("passes through numbers", () => {
    expect(toNum(7)).toBe(7);
  });
  it("returns null for null/undefined/empty", () => {
    expect(toNum(null)).toBeNull();
    expect(toNum(undefined)).toBeNull();
    expect(toNum("")).toBeNull();
  });
  it("returns null for non-numeric junk", () => {
    expect(toNum("abc")).toBeNull();
  });
});

describe("parseEntry", () => {
  const raw: RawScoreEntry = {
    ticker: "005930",
    name: "Samsung",
    market: "KR",
    themes: ["AI"],
    price: "70000",
    open_price: "69000",
    change_from_open_pct: "1.45",
    change_pct: "2.00",
    volume: "1000000",
    turnover: "70000000000",
    market_cap: "400000000000000",
    w52_high: "75000",
    w52_low: "60000",
    near_52w_pct: "93.3",
    return_1y_pct: "12.5",
    per: "11.2",
    pbr: "1.3",
    eps: "6200",
    sector: "Tech",
    industry: "Semiconductors",
    score: "82.4",
    grade: "strong_buy",
    eligible: true,
    factors: {
      near_52w: "0.93",
      pocket_pivot: "1",
      momentum_norm: "0.7",
      rs_norm: "0.65",
      turnover_norm: "0.8",
      vol_fit: "0.6",
      momentum: "0.12",
      rs: "0.04",
      volatility: "0.35",
      above_ma200: true,
    },
    ma200: "65000",
    stop_price: "64000",
    trailing_peak: "72000",
    sell_alert: false,
    sell_reason: null,
    rationale: "trend ok",
    investor_flow: {
      date: "2026-06-04",
      foreign_net: "5000000000",
      institution_net: "-2000000000",
      individual_net: "-3000000000",
    },
  };

  it("normalizes string decimals to numbers", () => {
    const e = parseEntry(raw);
    expect(e.price).toBe(70000);
    expect(e.score).toBe(82.4);
    expect(e.changeFromOpenPct).toBe(1.45);
    expect(e.factors?.pocketPivot).toBe(1);
    expect(e.investorFlow?.foreignNet).toBe(5_000_000_000);
    expect(e.investorFlow?.institutionNet).toBe(-2_000_000_000);
  });

  it("defaults missing score to 0 and missing investor flow to null", () => {
    const e = parseEntry({
      ...raw,
      score: null,
      investor_flow: null,
    });
    expect(e.score).toBe(0);
    expect(e.investorFlow).toBeNull();
  });

  it("keeps US investor_flow null", () => {
    const e = parseEntry({ ...raw, market: "US", investor_flow: null });
    expect(e.market).toBe("US");
    expect(e.investorFlow).toBeNull();
  });
});

describe("parseIssues", () => {
  const raw: RawIssuesResponse = {
    generated_at: "2026-06-18T09:00:00+09:00",
    window_hours: 24,
    disclaimer: "x",
    counts: { collected: 5, items_recent: 50, sources_ok: 8, sources_failed: 1 },
    issues: [
      {
        kind: "ticker",
        key: "005930",
        name: "Samsung",
        market: "KR",
        mention_count: 18,
        baseline_count: 4,
        spike: "3.60",
        score: "82.5",
        grade: "strong_buy",
        headlines: [
          {
            title: "h1",
            url: "https://x",
            source: "MK",
            published_at: "2026-06-18T08:40:00+09:00",
          },
        ],
        sources: ["MK"],
      },
      {
        kind: "theme",
        key: "semis",
        name: "Semis",
        mention_count: 31,
        baseline_count: 13,
        spike: "2.38",
      },
    ],
  };

  it("normalizes spike/score strings and maps headlines", () => {
    const d = parseIssues(raw);
    expect(d.windowHours).toBe(24);
    expect(d.counts.itemsRecent).toBe(50);
    expect(d.issues[0].spike).toBe(3.6);
    expect(d.issues[0].score).toBe(82.5);
    expect(d.issues[0].market).toBe("KR");
    expect(d.issues[0].headlines[0].publishedAt).toBe("2026-06-18T08:40:00+09:00");
  });

  it("defaults missing market/score/grade/headlines for a theme issue", () => {
    const d = parseIssues(raw);
    expect(d.issues[1].market).toBeNull();
    expect(d.issues[1].score).toBeNull();
    expect(d.issues[1].grade).toBeNull();
    expect(d.issues[1].headlines).toEqual([]);
    expect(d.issues[1].sources).toEqual([]);
  });
});
