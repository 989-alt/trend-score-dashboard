// Mock data builders for component tests. Built from RAW API shapes (mirrors
// backend/schemas.py) and run through the real api.ts parsers so the fixtures
// exercise the exact normalization the app uses at runtime — no network needed.

import { parseEntry } from "../api";
import type {
  Grade,
  Market,
  RawInvestorFlow,
  RawScoreEntry,
  ScoreEntry,
  Snapshot,
  ThemesData,
} from "../types";

const KR_FLOW: RawInvestorFlow = {
  date: "2026-06-04",
  foreign_net: "5000000000",
  institution_net: "-2000000000",
  individual_net: "-3000000000",
};

/** A complete RAW entry; override any field per test. */
export function rawEntry(over: Partial<RawScoreEntry> = {}): RawScoreEntry {
  return {
    ticker: "005930",
    name: "TestCorp",
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
      turnover_norm: "0.8",
      vol_fit: "0.6",
      momentum: "0.12",
      volatility: "0.35",
      above_ma200: true,
    },
    ma200: "65000",
    stop_price: "64000",
    trailing_peak: "72000",
    sell_alert: false,
    sell_reason: null,
    rationale: "trend ok",
    investor_flow: KR_FLOW,
    ...over,
  };
}

/** A normalized ScoreEntry (parsed). Override raw fields via `over`. */
export function makeEntry(over: Partial<RawScoreEntry> = {}): ScoreEntry {
  return parseEntry(rawEntry(over));
}

/** Convenience: a unique-ish entry with given ticker/name/grade. */
export function entryWith(opts: {
  ticker: string;
  name: string;
  grade?: Grade;
  market?: Market;
  score?: number;
  sellAlert?: boolean;
  sellReason?: RawScoreEntry["sell_reason"];
  eligible?: boolean;
  near52w?: number;
  price?: number;
}): ScoreEntry {
  return makeEntry({
    ticker: opts.ticker,
    name: opts.name,
    grade: opts.grade ?? "buy",
    market: opts.market ?? "KR",
    score: opts.score ?? 50,
    sell_alert: opts.sellAlert ?? false,
    sell_reason: opts.sellReason ?? null,
    eligible: opts.eligible ?? true,
    near_52w_pct: opts.near52w ?? 80,
    price: opts.price ?? 10000,
  });
}

/** A KR snapshot with a few mixed entries. */
export function makeSnapshot(over: Partial<Snapshot> = {}): Snapshot {
  const entries: ScoreEntry[] = [
    entryWith({ ticker: "A001", name: "AlphaCorp", grade: "strong_buy", score: 91 }),
    entryWith({ ticker: "B002", name: "BetaInc", grade: "buy", score: 70 }),
    entryWith({ ticker: "C003", name: "GammaLtd", grade: "hold", score: 45, eligible: false }),
    entryWith({
      ticker: "D004",
      name: "DeltaCo",
      grade: "sell",
      score: 20,
      sellAlert: true,
      sellReason: "trailing_stop",
    }),
  ];
  return {
    market: "KR",
    generatedAt: "2026-06-04T05:00:00Z",
    nextRefreshAt: "2026-06-04T05:30:00Z",
    marketOpen: true,
    disclaimer: "raw disclaimer from server",
    counts: { scanned: 100, eligible: 40, scored: 35, failed: 2 },
    entries,
    ...over,
  };
}

/** A merged ThemesData payload with KR+US leaders under one theme. */
export function makeThemes(): ThemesData {
  const krLeader = entryWith({
    ticker: "KR01",
    name: "KoreaLeader",
    grade: "strong_buy",
    market: "KR",
    score: 88,
  });
  const usLeader = entryWith({
    ticker: "AAPL",
    name: "UsLeader",
    grade: "buy",
    market: "US",
    score: 75,
  });
  const sellLeader = entryWith({
    ticker: "KR99",
    name: "FallingStar",
    grade: "sell",
    market: "KR",
    score: 22,
    sellAlert: true,
    sellReason: "ma200_break",
  });
  return {
    generatedAt: "2026-06-04T05:00:00Z",
    marketOpen: { KR: true, US: false },
    disclaimer: "raw disclaimer",
    themes: [
      {
        theme: "Semiconductors",
        leaders: [krLeader, usLeader, sellLeader],
        markets: ["KR", "US"],
        sellCount: 1,
      },
    ],
  };
}
