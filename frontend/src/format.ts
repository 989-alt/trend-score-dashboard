import type { Grade, Market } from "./types";
import { translate } from "./i18n";

const DASH = "—"; // em dash for "missing" values

/** Localized number with grouping; falls back to em dash for null. */
export function fmtNumber(v: number | null, digits = 0): string {
  if (v === null || !Number.isFinite(v)) return DASH;
  return v.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** Price formatting: KR shows whole numbers, US shows 2 decimals. */
export function fmtPrice(v: number | null, market: Market): string {
  if (v === null || !Number.isFinite(v)) return DASH;
  return market === "US" ? fmtNumber(v, 2) : fmtNumber(v, 0);
}

/** Signed percent, e.g. +2.34% / -0.50%. */
export function fmtPct(v: number | null, digits = 2): string {
  if (v === null || !Number.isFinite(v)) return DASH;
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(digits)}%`;
}

/** Plain percent without forced sign (e.g. 52w proximity). */
export function fmtPctPlain(v: number | null, digits = 1): string {
  if (v === null || !Number.isFinite(v)) return DASH;
  return `${v.toFixed(digits)}%`;
}

/**
 * Compact large count (NOT currency). Plain USD-style K/M/B/T scaling with no
 * currency unit — used for share counts (e.g. trading volume in shares), which
 * are dimensionless quantities, not money.
 */
export function fmtCompact(v: number | null): string {
  if (v === null || !Number.isFinite(v)) return DASH;
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (abs >= 1e12) return `${sign}${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}${(abs / 1e3).toFixed(1)}K`;
  return `${sign}${abs.toFixed(0)}`;
}

/**
 * Compact currency amount, branched by market. KR uses the Korean myriad scale
 * (1e8 = "eok", 1e12 = "jo") with the Won sign; US uses dollar-prefixed K/M/B/T.
 * The KR unit suffixes come from i18n (`unit.eok` / `unit.jo` / `unit.won`) so no
 * Hangul is inlined here. Used for turnover, market cap and investor amounts —
 * never for share counts (use `fmtCompact`).
 */
export function fmtMoney(v: number | null, market: Market): string {
  if (v === null || !Number.isFinite(v)) return DASH;
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (market === "US") {
    if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)}T`;
    if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
    if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(2)}K`;
    return `${sign}$${abs.toFixed(0)}`;
  }
  // KR: jo (1e12) and eok (1e8). Below 1e8, show whole Won with grouping.
  const won = translate("unit.won");
  if (abs >= 1e12) {
    const jo = abs / 1e12;
    return `${sign}${jo.toFixed(jo >= 100 ? 0 : 2)}${translate("unit.jo")}`;
  }
  if (abs >= 1e8) {
    const eok = abs / 1e8;
    return `${sign}${eok.toFixed(eok >= 100 ? 0 : 1)}${translate("unit.eok")}`;
  }
  return `${sign}${abs.toLocaleString("en-US", { maximumFractionDigits: 0 })}${won}`;
}

/** Sign class for coloring deltas: "up" | "down" | "flat". */
export function signClass(v: number | null): "up" | "down" | "flat" {
  if (v === null || !Number.isFinite(v) || v === 0) return "flat";
  return v > 0 ? "up" : "down";
}

/** HH:MM in local time from an ISO string; em dash if missing/invalid. */
export function fmtClock(iso: string | null): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

/**
 * mm:ss from a number of seconds (clamped to [0, 59:59]). A real poll countdown
 * is sub-hour; values beyond an hour are abnormal (e.g. market closed) and are
 * capped at "59:59" so the display never reads a runaway value like "1320:00".
 * Callers that detect a closed market should show a "closed" label instead.
 */
export function fmtCountdown(totalSeconds: number): string {
  const s = Math.min(60 * 60 - 1, Math.max(0, Math.floor(totalSeconds)));
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

/** Icon glyph per grade (paired with text label for AA, never color alone). */
export const GRADE_ICON: Record<Grade, string> = {
  strong_buy: "▲▲", // ▲▲
  buy: "▲", // ▲
  hold: "●", // ●
  avoid: "▽", // ▽
  sell: "■", // ■
};

export const EM_DASH = DASH;
