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

// ---------------------------------------------------------------------------
// Issues (실시간 이슈 랭킹 — see backend/schemas.py IssuesResponse).
// ---------------------------------------------------------------------------

export type IssueKind = "ticker" | "theme";

export interface RawIssueHeadline {
  title: string;
  url?: string | null;
  source: string;
  published_at?: string | null;
}

export interface RawIssueEntry {
  kind: IssueKind;
  key: string;
  name: string;
  market?: Market | null;
  mention_count: number;
  baseline_count: number;
  spike: Num;
  score?: Num;
  grade?: Grade | null;
  headlines?: RawIssueHeadline[];
  sources?: string[];
}

export interface RawIssueCounts {
  collected: number;
  items_recent: number;
  sources_ok: number;
  sources_failed: number;
}

export interface RawIssuesResponse {
  generated_at: string;
  window_hours: number;
  disclaimer: string;
  counts: RawIssueCounts;
  issues: RawIssueEntry[];
}

export interface IssueHeadline {
  title: string;
  url: string | null;
  source: string;
  publishedAt: string | null;
}

export interface IssueEntry {
  kind: IssueKind;
  key: string;
  name: string;
  market: Market | null;
  mentionCount: number;
  baselineCount: number;
  spike: number;
  score: number | null;
  grade: Grade | null;
  headlines: IssueHeadline[];
  sources: string[];
}

export interface IssueCounts {
  collected: number;
  itemsRecent: number;
  sourcesOk: number;
  sourcesFailed: number;
}

export interface IssuesData {
  generatedAt: string;
  windowHours: number;
  disclaimer: string;
  counts: IssueCounts;
  issues: IssueEntry[];
}
