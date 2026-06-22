// API contract mirror (see backend/schemas.py). FIXED — do not diverge.
// Decimal fields may arrive as JSON strings; raw types accept string|number and
// the api layer normalizes them to numbers via Number()/parseFloat.

export type Market = "KR" | "US";

export type Grade = "strong_buy" | "buy" | "hold" | "avoid" | "sell";

export type SellReason = "trailing_stop" | "ma200_break";

/** A numeric field that the backend may serialize as a string. */
export type Num = number | string | null | undefined;

export interface RawInvestorFlow {
  date: string;
  foreign_net: Num;
  institution_net: Num;
  individual_net: Num;
  foreign_buy?: Num;
  foreign_sell?: Num;
  institution_buy?: Num;
  institution_sell?: Num;
  individual_buy?: Num;
  individual_sell?: Num;
}

export interface RawFactorBreakdown {
  near_52w: Num;
  pocket_pivot: Num;
  momentum_norm: Num;
  rs_norm: Num;
  turnover_norm: Num;
  vol_fit: Num;
  momentum: Num;
  rs: Num;
  volatility: Num;
  above_ma200: boolean;
}

export interface RawScoreEntry {
  ticker: string;
  name: string;
  market: Market;
  themes: string[];

  price: Num;
  open_price?: Num;
  change_from_open_pct?: Num;
  change_pct?: Num;
  volume?: Num;
  turnover?: Num;

  market_cap?: Num;
  w52_high?: Num;
  w52_low?: Num;
  near_52w_pct?: Num;
  return_1y_pct?: Num;
  per?: Num;
  pbr?: Num;
  eps?: Num;
  sector?: string | null;
  industry?: string | null;

  score: Num;
  grade: Grade;
  eligible: boolean;
  factors?: RawFactorBreakdown | null;
  ma200?: Num;

  stop_price?: Num;
  trailing_peak?: Num;
  sell_alert?: boolean;
  sell_reason?: SellReason | null;
  rationale?: string | null;

  investor_flow?: RawInvestorFlow | null;
}

export interface RawSnapshotCounts {
  scanned: number;
  eligible: number;
  scored: number;
  failed: number;
}

export interface RawSnapshot {
  market: Market;
  generated_at: string;
  next_refresh_at?: string | null;
  market_open: boolean;
  disclaimer: string;
  counts: RawSnapshotCounts;
  entries: RawScoreEntry[];
}

export interface RawThemeGroup {
  theme: string;
  market: Market;
  leaders: RawScoreEntry[];
}

export interface RawThemesResponse {
  generated_at: string;
  market_open: Record<string, boolean>;
  disclaimer: string;
  groups: RawThemeGroup[];
}

export interface RawHealth {
  status: "ok" | "degraded";
  data_mode: string;
  last_kr_snapshot?: string | null;
  last_us_snapshot?: string | null;
}

// ---------------------------------------------------------------------------
// Normalized (parsed) shapes used throughout the UI. Numbers are real numbers
// or null; never strings.
// ---------------------------------------------------------------------------

export interface InvestorFlow {
  date: string;
  foreignNet: number | null;
  institutionNet: number | null;
  individualNet: number | null;
  foreignBuy: number | null;
  foreignSell: number | null;
  institutionBuy: number | null;
  institutionSell: number | null;
  individualBuy: number | null;
  individualSell: number | null;
}

export interface FactorBreakdown {
  near52w: number | null;
  pocketPivot: number | null;
  momentumNorm: number | null;
  rsNorm: number | null;
  turnoverNorm: number | null;
  volFit: number | null;
  momentum: number | null;
  rs: number | null;
  volatility: number | null;
  aboveMa200: boolean;
}

export interface ScoreEntry {
  ticker: string;
  name: string;
  market: Market;
  themes: string[];

  price: number | null;
  openPrice: number | null;
  changeFromOpenPct: number | null;
  changePct: number | null;
  volume: number | null;
  turnover: number | null;

  marketCap: number | null;
  w52High: number | null;
  w52Low: number | null;
  near52wPct: number | null;
  return1yPct: number | null;
  per: number | null;
  pbr: number | null;
  eps: number | null;
  sector: string | null;
  industry: string | null;

  score: number;
  grade: Grade;
  eligible: boolean;
  factors: FactorBreakdown | null;
  ma200: number | null;

  stopPrice: number | null;
  trailingPeak: number | null;
  sellAlert: boolean;
  sellReason: SellReason | null;
  rationale: string | null;

  investorFlow: InvestorFlow | null;
}

export interface SnapshotCounts {
  scanned: number;
  eligible: number;
  scored: number;
  failed: number;
}

export interface Snapshot {
  market: Market;
  generatedAt: string;
  nextRefreshAt: string | null;
  marketOpen: boolean;
  disclaimer: string;
  counts: SnapshotCounts;
  entries: ScoreEntry[];
}

export interface ThemeGroup {
  theme: string;
  market: Market;
  leaders: ScoreEntry[];
}

/** Themes grouped by name across markets (KR/US merged under one theme). */
export interface MergedTheme {
  theme: string;
  leaders: ScoreEntry[];
  markets: Market[];
  sellCount: number;
}

export interface ThemesData {
  generatedAt: string;
  marketOpen: { KR: boolean; US: boolean };
  disclaimer: string;
  themes: MergedTheme[];
}

// ── 시황(뉴스) 탭 ────────────────────────────────────────────────────────
// Raw shapes (API JSON; Decimal 은 string 으로 올 수 있음).
export interface RawNewsMessage {
  channel: string;
  ts_kst: string;
  text: string;
  urls: string[];
}

export interface RawNewsIssue {
  key: string;
  title: string;
  urgency: number | string;
  channels: string[];
  severity: number | string;
  count: number;
  last_ts: string;
  messages: RawNewsMessage[];
  spike?: number | string | null;
  ticker?: string | null;
  score?: number | string | null;
  grade?: string | null;
  market?: string | null;
  headline?: string | null;
}

export interface RawNewsIssuesResponse {
  generated_at: string;
  disclaimer: string;
  domestic: RawNewsIssue[];
  us: RawNewsIssue[];
  macro: RawNewsIssue[];
}

export interface RawWeeklyResponse {
  week_start: string | null;
  kr_markdown: string | null;
  generated_at: string | null;
  disclaimer: string;
}

// Normalized shapes (consumed by components).
export interface NewsMessage {
  channel: string;
  tsKst: string;
  text: string;
  urls: string[];
}

export interface NewsIssue {
  key: string;
  title: string;
  urgency: number;
  channels: string[];
  severity: number;
  count: number;
  lastTs: string;
  messages: NewsMessage[];
  spike: number;
  ticker: string | null;
  score: number | null;
  grade: Grade | null;
  market: Market | null;
  headline: string;
}

/** 3 레이어(국내/미국/종합) 각 urgency 순. */
export interface NewsIssuesData {
  generatedAt: string;
  disclaimer: string;
  domestic: NewsIssue[];
  us: NewsIssue[];
  macro: NewsIssue[];
}

export interface WeeklyData {
  weekStart: string | null;
  krMarkdown: string | null;
  generatedAt: string | null;
  disclaimer: string;
}

// ── 매매 현황(trading) 탭 ──────────────────────────────────────────────────
// Raw shapes (API JSON; Decimal 은 string 으로 올 수 있음 — see backend/trader/api_models.py).
export interface RawTradingStatus {
  running: boolean;
  total_eval?: Num;
  cash?: Num;
  position_count?: number;
  total_pnl?: Num;
  realized_pnl?: Num;
  as_of?: string | null;
  disclaimer: string;
}

export interface RawTradingPosition {
  ticker: string;
  name: string;
  qty: number;
  avg_price: Num;
  cur_price?: Num;
  eval_amount?: Num;
  pnl_amount?: Num;
  pnl_pct?: Num;
}

export interface RawTradingPositionsResponse {
  positions: RawTradingPosition[];
  disclaimer: string;
}

export interface RawTradingOrder {
  ts: string;
  ticker: string;
  name?: string;
  side: string;
  qty: number;
  filled_qty?: number;
  status?: string;
  reason: string;
  message: string;
}

export interface RawTradingOrdersResponse {
  orders: RawTradingOrder[];
  disclaimer: string;
}

export interface RawNavPoint {
  ts: string;
  total_eval?: Num;
  cash?: Num;
}

export interface RawTradingNavResponse {
  nav: RawNavPoint[];
  disclaimer: string;
}

// Normalized shapes (consumed by components). Decimals are number|null.
export interface TradingStatus {
  running: boolean;
  totalEval: number | null;
  cash: number | null;
  positionCount: number;
  totalPnl: number | null;
  realizedPnl: number | null;
  asOf: string | null;
  disclaimer: string;
}

export interface TradingPosition {
  ticker: string;
  name: string;
  qty: number;
  avgPrice: number | null;
  curPrice: number | null;
  evalAmount: number | null;
  pnlAmount: number | null;
  pnlPct: number | null;
}

export interface TradingOrder {
  ts: string;
  ticker: string;
  name: string;
  side: string;
  qty: number;
  filledQty: number;
  status: string;
  reason: string;
  message: string;
}

export interface NavPoint {
  ts: string;
  totalEval: number | null;
  cash: number | null;
}

export interface TradingPositionsData {
  positions: TradingPosition[];
  disclaimer: string;
}

export interface TradingOrdersData {
  orders: TradingOrder[];
  disclaimer: string;
}

export interface TradingNavData {
  nav: NavPoint[];
  disclaimer: string;
}
