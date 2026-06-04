import type {
  FactorBreakdown,
  InvestorFlow,
  Market,
  MergedTheme,
  RawFactorBreakdown,
  RawInvestorFlow,
  RawScoreEntry,
  RawSnapshot,
  RawThemesResponse,
  ScoreEntry,
  Snapshot,
  ThemesData,
} from "./types";

/**
 * Static-demo build (GitHub Pages): no backend, so fetch bundled sample JSON
 * under the Vite base path instead of the live `/api/*` endpoints.
 */
export const STATIC_DEMO = import.meta.env.VITE_STATIC === "1";
const BASE = import.meta.env.BASE_URL;

/** Parse a Decimal-as-string|number into a number, or null if absent/invalid. */
export function toNum(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : parseFloat(String(v));
  return Number.isFinite(n) ? n : null;
}

function parseInvestorFlow(r: RawInvestorFlow | null | undefined): InvestorFlow | null {
  if (!r) return null;
  return {
    date: r.date,
    foreignNet: toNum(r.foreign_net),
    institutionNet: toNum(r.institution_net),
    individualNet: toNum(r.individual_net),
    foreignBuy: toNum(r.foreign_buy),
    foreignSell: toNum(r.foreign_sell),
    institutionBuy: toNum(r.institution_buy),
    institutionSell: toNum(r.institution_sell),
    individualBuy: toNum(r.individual_buy),
    individualSell: toNum(r.individual_sell),
  };
}

function parseFactors(r: RawFactorBreakdown | null | undefined): FactorBreakdown | null {
  if (!r) return null;
  return {
    near52w: toNum(r.near_52w),
    pocketPivot: toNum(r.pocket_pivot),
    momentumNorm: toNum(r.momentum_norm),
    turnoverNorm: toNum(r.turnover_norm),
    volFit: toNum(r.vol_fit),
    momentum: toNum(r.momentum),
    volatility: toNum(r.volatility),
    aboveMa200: Boolean(r.above_ma200),
  };
}

export function parseEntry(r: RawScoreEntry): ScoreEntry {
  return {
    ticker: r.ticker,
    name: r.name,
    market: r.market,
    themes: Array.isArray(r.themes) ? r.themes : [],

    price: toNum(r.price),
    openPrice: toNum(r.open_price),
    changeFromOpenPct: toNum(r.change_from_open_pct),
    changePct: toNum(r.change_pct),
    volume: toNum(r.volume),
    turnover: toNum(r.turnover),

    marketCap: toNum(r.market_cap),
    w52High: toNum(r.w52_high),
    w52Low: toNum(r.w52_low),
    near52wPct: toNum(r.near_52w_pct),
    return1yPct: toNum(r.return_1y_pct),
    per: toNum(r.per),
    pbr: toNum(r.pbr),
    eps: toNum(r.eps),
    sector: r.sector ?? null,
    industry: r.industry ?? null,

    score: toNum(r.score) ?? 0,
    grade: r.grade,
    eligible: Boolean(r.eligible),
    factors: parseFactors(r.factors),
    ma200: toNum(r.ma200),

    stopPrice: toNum(r.stop_price),
    trailingPeak: toNum(r.trailing_peak),
    sellAlert: Boolean(r.sell_alert),
    sellReason: r.sell_reason ?? null,
    rationale: r.rationale ?? null,

    investorFlow: parseInvestorFlow(r.investor_flow),
  };
}

function parseSnapshot(r: RawSnapshot): Snapshot {
  return {
    market: r.market,
    generatedAt: r.generated_at,
    nextRefreshAt: r.next_refresh_at ?? null,
    marketOpen: Boolean(r.market_open),
    disclaimer: r.disclaimer,
    counts: {
      scanned: r.counts?.scanned ?? 0,
      eligible: r.counts?.eligible ?? 0,
      scored: r.counts?.scored ?? 0,
      failed: r.counts?.failed ?? 0,
    },
    entries: (r.entries ?? []).map(parseEntry),
  };
}

/** Merge theme groups by theme name; KR/US collapse under one theme. */
function parseThemes(r: RawThemesResponse): ThemesData {
  const byName = new Map<string, MergedTheme>();
  // Preserve first-seen order of theme names.
  for (const g of r.groups ?? []) {
    const leaders = (g.leaders ?? []).map(parseEntry);
    let merged = byName.get(g.theme);
    if (!merged) {
      merged = { theme: g.theme, leaders: [], markets: [], sellCount: 0 };
      byName.set(g.theme, merged);
    }
    merged.leaders.push(...leaders);
    if (!merged.markets.includes(g.market)) merged.markets.push(g.market);
  }
  for (const merged of byName.values()) {
    merged.leaders.sort((a, b) => b.score - a.score);
    merged.sellCount = merged.leaders.filter((e) => e.sellAlert).length;
  }
  const marketOpen = r.market_open ?? {};
  return {
    generatedAt: r.generated_at,
    marketOpen: {
      KR: Boolean(marketOpen.KR),
      US: Boolean(marketOpen.US),
    },
    disclaimer: r.disclaimer,
    themes: Array.from(byName.values()),
  };
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal, headers: { Accept: "application/json" } });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} for ${url}`);
  }
  return (await res.json()) as T;
}

export async function fetchSnapshot(
  market: Market,
  signal?: AbortSignal,
): Promise<Snapshot> {
  const m = market.toLowerCase();
  const url = STATIC_DEMO ? `${BASE}data/snapshot-${m}.json` : `/api/snapshot?market=${m}`;
  const raw = await getJson<RawSnapshot>(url, signal);
  return parseSnapshot(raw);
}

export async function fetchThemes(signal?: AbortSignal): Promise<ThemesData> {
  const url = STATIC_DEMO ? `${BASE}data/themes.json` : "/api/themes";
  const raw = await getJson<RawThemesResponse>(url, signal);
  return parseThemes(raw);
}
