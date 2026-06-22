import type {
  FactorBreakdown,
  Grade,
  InvestorFlow,
  Market,
  MergedTheme,
  NavPoint,
  NewsIssue,
  NewsIssuesData,
  RawFactorBreakdown,
  RawInvestorFlow,
  RawNavPoint,
  RawNewsIssue,
  RawNewsIssuesResponse,
  RawScoreEntry,
  RawSnapshot,
  RawThemesResponse,
  RawTradingNavResponse,
  RawTradingOrder,
  RawTradingOrdersResponse,
  RawTradingPosition,
  RawTradingPositionsResponse,
  RawTradingStatus,
  RawWeeklyResponse,
  ScoreEntry,
  Snapshot,
  ThemesData,
  TradingNavData,
  TradingOrder,
  TradingOrdersData,
  TradingPosition,
  TradingPositionsData,
  TradingStatus,
  WeeklyData,
} from "./types";

/**
 * Data source resolution for the deployed frontend:
 * - VITE_API_BASE set (e.g. OCI backend URL) → fetch LIVE from that origin (CORS).
 * - else VITE_STATIC=1 (GitHub Pages demo) → fetch bundled sample JSON under base path.
 * - else → same-origin /api (OCI single-process serving, or local dev proxy).
 */
export const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/+$/, "");
export const STATIC_DEMO = import.meta.env.VITE_STATIC === "1";
/** True when live data comes from a remote backend (OCI), not bundled sample. */
export const LIVE_REMOTE = API_BASE !== "";
const BASE = import.meta.env.BASE_URL;

function snapshotUrl(market: string): string {
  if (API_BASE) return `${API_BASE}/api/snapshot?market=${market}`;
  if (STATIC_DEMO) return `${BASE}data/snapshot-${market}.json`;
  return `/api/snapshot?market=${market}`;
}

function themesUrl(): string {
  if (API_BASE) return `${API_BASE}/api/themes`;
  if (STATIC_DEMO) return `${BASE}data/themes.json`;
  return "/api/themes";
}

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
    rsNorm: toNum(r.rs_norm),
    turnoverNorm: toNum(r.turnover_norm),
    volFit: toNum(r.vol_fit),
    momentum: toNum(r.momentum),
    rs: toNum(r.rs),
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
  const raw = await getJson<RawSnapshot>(snapshotUrl(market.toLowerCase()), signal);
  return parseSnapshot(raw);
}

export async function fetchThemes(signal?: AbortSignal): Promise<ThemesData> {
  const raw = await getJson<RawThemesResponse>(themesUrl(), signal);
  return parseThemes(raw);
}

function newsIssuesUrl(): string {
  if (API_BASE) return `${API_BASE}/api/news/issues`;
  if (STATIC_DEMO) return `${BASE}data/news-issues.json`;
  return "/api/news/issues";
}

function newsWeeklyUrl(): string {
  if (API_BASE) return `${API_BASE}/api/news/weekly`;
  if (STATIC_DEMO) return `${BASE}data/news-weekly.json`;
  return "/api/news/weekly";
}

function tickerUrl(market: Market, code: string): string | null {
  const m = market.toLowerCase();
  if (API_BASE) return `${API_BASE}/api/ticker/${m}/${code}`;
  if (STATIC_DEMO) return null; // 정적 데모엔 종목 상세 엔드포인트 없음
  return `/api/ticker/${m}/${code}`;
}

/** Fetch one ticker's full detail (for opening the drawer from a news issue). */
export async function fetchTicker(
  market: Market,
  code: string,
  signal?: AbortSignal,
): Promise<ScoreEntry | null> {
  const url = tickerUrl(market, code);
  if (!url) return null;
  try {
    const raw = await getJson<RawScoreEntry>(url, signal);
    return parseEntry(raw);
  } catch {
    return null;
  }
}

function parseNewsIssue(r: RawNewsIssue): NewsIssue {
  return {
    key: r.key,
    title: r.title,
    urgency: toNum(r.urgency) ?? 0,
    channels: Array.isArray(r.channels) ? r.channels : [],
    severity: toNum(r.severity) ?? 0,
    count: r.count,
    lastTs: r.last_ts,
    messages: (r.messages ?? []).map((m) => ({
      channel: m.channel,
      tsKst: m.ts_kst,
      text: m.text,
      urls: Array.isArray(m.urls) ? m.urls : [],
    })),
    spike: toNum(r.spike) ?? 0,
    ticker: r.ticker ?? null,
    score: toNum(r.score),
    grade: (r.grade ?? null) as Grade | null,
    market: (r.market ?? null) as Market | null,
    headline: r.headline ?? "",
  };
}

export async function fetchNewsIssues(signal?: AbortSignal): Promise<NewsIssuesData> {
  const raw = await getJson<RawNewsIssuesResponse>(newsIssuesUrl(), signal);
  return {
    generatedAt: raw.generated_at,
    disclaimer: raw.disclaimer,
    domestic: (raw.domestic ?? []).map(parseNewsIssue),
    us: (raw.us ?? []).map(parseNewsIssue),
    macro: (raw.macro ?? []).map(parseNewsIssue),
  };
}

export async function fetchNewsWeekly(signal?: AbortSignal): Promise<WeeklyData> {
  const raw = await getJson<RawWeeklyResponse>(newsWeeklyUrl(), signal);
  return {
    weekStart: raw.week_start,
    krMarkdown: raw.kr_markdown,
    generatedAt: raw.generated_at,
    disclaimer: raw.disclaimer,
  };
}

// ── 매매 현황(trading) ──────────────────────────────────────────────────────
function tradingStatusUrl(): string {
  if (API_BASE) return `${API_BASE}/api/trading/status`;
  if (STATIC_DEMO) return `${BASE}data/trading-status.json`;
  return "/api/trading/status";
}

function tradingPositionsUrl(): string {
  if (API_BASE) return `${API_BASE}/api/trading/positions`;
  if (STATIC_DEMO) return `${BASE}data/trading-positions.json`;
  return "/api/trading/positions";
}

function tradingHistoryUrl(limit: number): string {
  if (API_BASE) return `${API_BASE}/api/trading/history?limit=${limit}`;
  if (STATIC_DEMO) return `${BASE}data/trading-history.json`;
  return `/api/trading/history?limit=${limit}`;
}

function tradingNavUrl(limit: number): string {
  if (API_BASE) return `${API_BASE}/api/trading/nav?limit=${limit}`;
  if (STATIC_DEMO) return `${BASE}data/trading-nav.json`;
  return `/api/trading/nav?limit=${limit}`;
}

function parseTradingPosition(r: RawTradingPosition): TradingPosition {
  return {
    ticker: r.ticker,
    name: r.name,
    qty: r.qty,
    avgPrice: toNum(r.avg_price),
    curPrice: toNum(r.cur_price),
    evalAmount: toNum(r.eval_amount),
    pnlAmount: toNum(r.pnl_amount),
    pnlPct: toNum(r.pnl_pct),
  };
}

function parseNavPoint(r: RawNavPoint): NavPoint {
  return {
    ts: r.ts,
    totalEval: toNum(r.total_eval),
    cash: toNum(r.cash),
  };
}

export async function fetchTradingStatus(signal?: AbortSignal): Promise<TradingStatus> {
  const raw = await getJson<RawTradingStatus>(tradingStatusUrl(), signal);
  return {
    running: Boolean(raw.running),
    totalEval: toNum(raw.total_eval),
    cash: toNum(raw.cash),
    positionCount: raw.position_count ?? 0,
    totalPnl: toNum(raw.total_pnl),
    realizedPnl: toNum(raw.realized_pnl),
    asOf: raw.as_of ?? null,
    disclaimer: raw.disclaimer,
  };
}

export async function fetchTradingPositions(
  signal?: AbortSignal,
): Promise<TradingPositionsData> {
  const raw = await getJson<RawTradingPositionsResponse>(tradingPositionsUrl(), signal);
  return {
    positions: (raw.positions ?? []).map(parseTradingPosition),
    disclaimer: raw.disclaimer,
  };
}

export async function fetchTradingHistory(
  limit = 50,
  signal?: AbortSignal,
): Promise<TradingOrdersData> {
  const raw = await getJson<RawTradingOrdersResponse>(tradingHistoryUrl(limit), signal);
  return {
    orders: (raw.orders ?? []).map(
      (o: RawTradingOrder): TradingOrder => ({
        ts: o.ts,
        ticker: o.ticker,
        name: o.name ?? "",
        side: o.side,
        qty: o.qty,
        filledQty: o.filled_qty ?? 0,
        status: o.status ?? "",
        reason: o.reason,
        message: o.message,
      }),
    ),
    disclaimer: raw.disclaimer,
  };
}

export async function fetchTradingNav(
  limit = 2000,
  signal?: AbortSignal,
): Promise<TradingNavData> {
  const raw = await getJson<RawTradingNavResponse>(tradingNavUrl(limit), signal);
  return {
    nav: (raw.nav ?? []).map(parseNavPoint),
    disclaimer: raw.disclaimer,
  };
}
