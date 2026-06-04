import { useMemo, useState } from "react";
import { useT } from "../i18n";
import type { Grade, ScoreEntry } from "../types";
import {
  EM_DASH,
  fmtMoney,
  fmtPct,
  fmtPctPlain,
  fmtPrice,
  signClass,
} from "../format";
import { GradeBadge } from "./badges/GradeBadge";
import { MarketBadge } from "./badges/MarketBadge";
import { SellBadge } from "./badges/SellBadge";
import { ScoreGauge } from "./ScoreGauge";
import styles from "./RankingTable.module.css";

type SortKey =
  | "rank"
  | "name"
  | "price"
  | "changeFromOpenPct"
  | "score"
  | "stopPrice"
  | "near52wPct"
  | "turnover";

type SortDir = "asc" | "desc";

interface Props {
  entries: ScoreEntry[];
  onSelect: (entry: ScoreEntry) => void;
}

const GRADE_OPTIONS: Grade[] = ["strong_buy", "buy", "hold", "avoid", "sell"];

function nullableCompare(a: number | null, b: number | null): number {
  const av = a ?? Number.NEGATIVE_INFINITY;
  const bv = b ?? Number.NEGATIVE_INFINITY;
  return av - bv;
}

export function RankingTable({ entries, onSelect }: Props) {
  const t = useT();
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [query, setQuery] = useState("");
  const [gradeFilter, setGradeFilter] = useState<Grade | "all">("all");
  const [eligibleOnly, setEligibleOnly] = useState(false);

  // Baseline rank = score-descending position (stable, independent of sort).
  const ranked = useMemo(() => {
    const sorted = [...entries].sort((a, b) => b.score - a.score);
    return new Map(sorted.map((e, i) => [`${e.market}-${e.ticker}`, i + 1]));
  }, [entries]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return entries.filter((e) => {
      if (eligibleOnly && !e.eligible) return false;
      if (gradeFilter !== "all" && e.grade !== gradeFilter) return false;
      if (q) {
        const hay = `${e.name} ${e.ticker}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [entries, query, gradeFilter, eligibleOnly]);

  const sorted = useMemo(() => {
    const dir = sortDir === "asc" ? 1 : -1;
    const arr = [...filtered];
    arr.sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case "rank": {
          const ra = ranked.get(`${a.market}-${a.ticker}`) ?? 0;
          const rb = ranked.get(`${b.market}-${b.ticker}`) ?? 0;
          cmp = ra - rb;
          break;
        }
        case "name":
          cmp = a.name.localeCompare(b.name);
          break;
        case "price":
          cmp = nullableCompare(a.price, b.price);
          break;
        case "changeFromOpenPct":
          cmp = nullableCompare(a.changeFromOpenPct, b.changeFromOpenPct);
          break;
        case "score":
          cmp = a.score - b.score;
          break;
        case "stopPrice":
          cmp = nullableCompare(a.stopPrice, b.stopPrice);
          break;
        case "near52wPct":
          cmp = nullableCompare(a.near52wPct, b.near52wPct);
          break;
        case "turnover":
          cmp = nullableCompare(a.turnover, b.turnover);
          break;
      }
      return cmp * dir;
    });
    return arr;
  }, [filtered, sortKey, sortDir, ranked]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      // Sensible default direction per column.
      setSortDir(key === "name" || key === "rank" ? "asc" : "desc");
    }
  }

  const columns: { key: SortKey; labelKey: string; align?: "right" }[] = [
    { key: "rank", labelKey: "col.rank" },
    { key: "name", labelKey: "col.ticker" },
    { key: "price", labelKey: "col.price", align: "right" },
    { key: "changeFromOpenPct", labelKey: "col.changeFromOpen", align: "right" },
    { key: "score", labelKey: "col.score" },
    { key: "stopPrice", labelKey: "col.stop", align: "right" },
    { key: "near52wPct", labelKey: "col.near52w", align: "right" },
    { key: "turnover", labelKey: "col.turnover", align: "right" },
  ];

  return (
    <div className={styles.panel}>
      <div className={styles.toolbar}>
        <div className={styles.search}>
          <span className={styles.searchIcon} aria-hidden="true">
            ⌕
          </span>
          <input
            type="search"
            className={styles.searchInput}
            placeholder={t("ranking.search.placeholder")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label={t("ranking.search.placeholder")}
          />
        </div>
        <div className={styles.filters}>
          <label className={styles.filterLabel} htmlFor="grade-filter">
            {t("ranking.filter.grade")}
          </label>
          <select
            id="grade-filter"
            className={styles.select}
            value={gradeFilter}
            onChange={(e) => setGradeFilter(e.target.value as Grade | "all")}
          >
            <option value="all">{t("ranking.filter.all")}</option>
            {GRADE_OPTIONS.map((g) => (
              <option key={g} value={g}>
                {t(`grade.${g}`)}
              </option>
            ))}
          </select>
          <label className={styles.checkbox}>
            <input
              type="checkbox"
              checked={eligibleOnly}
              onChange={(e) => setEligibleOnly(e.target.checked)}
            />
            {t("ranking.filter.eligibleOnly")}
          </label>
          <span className={styles.resultCount}>
            {t("ranking.count", { n: sorted.length })}
          </span>
        </div>
      </div>

      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              {columns.map((col) => {
                const isSorted = sortKey === col.key;
                return (
                  <th
                    key={col.key}
                    className={`${col.align === "right" ? styles.right : ""} ${
                      isSorted ? styles.sortedHead : ""
                    }`}
                    aria-sort={
                      isSorted
                        ? sortDir === "asc"
                          ? "ascending"
                          : "descending"
                        : "none"
                    }
                  >
                    <button
                      type="button"
                      className={styles.sortBtn}
                      onClick={() => toggleSort(col.key)}
                      aria-label={
                        isSorted && sortDir === "asc"
                          ? t("col.sortDesc")
                          : t("col.sortAsc")
                      }
                    >
                      <span>{t(col.labelKey)}</span>
                      <span className={styles.sortArrow} aria-hidden="true">
                        {isSorted ? (sortDir === "asc" ? "▲" : "▼") : "↕"}
                      </span>
                    </button>
                  </th>
                );
              })}
              <th aria-label={t("col.grade")}>{t("col.grade")}</th>
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={columns.length + 1} className={styles.empty}>
                  {entries.length === 0
                    ? t("ranking.empty")
                    : t("ranking.empty.filtered")}
                </td>
              </tr>
            ) : (
              sorted.map((e) => {
                const rank = ranked.get(`${e.market}-${e.ticker}`) ?? 0;
                const cs = signClass(e.changeFromOpenPct);
                return (
                  <tr
                    key={`${e.market}-${e.ticker}`}
                    className={`${styles.row} ${e.sellAlert ? styles.alertRow : ""}`}
                    onClick={() => onSelect(e)}
                    tabIndex={0}
                    role="button"
                    onKeyDown={(ev) => {
                      if (ev.key === "Enter" || ev.key === " ") {
                        ev.preventDefault();
                        onSelect(e);
                      }
                    }}
                  >
                    <td className={styles.rankCell}>{rank}</td>
                    <td>
                      <div className={styles.tickerCell}>
                        <MarketBadge market={e.market} />
                        <div className={styles.tickerText}>
                          <span className={styles.name}>{e.name}</span>
                          <span className={styles.code}>{e.ticker}</span>
                        </div>
                        {e.sellAlert && <SellBadge reason={e.sellReason} size="sm" />}
                      </div>
                    </td>
                    <td className={styles.right}>
                      <span className={styles.price}>
                        {fmtPrice(e.price, e.market)}
                      </span>
                    </td>
                    <td className={`${styles.right} ${styles[cs]}`}>
                      {fmtPct(e.changeFromOpenPct)}
                    </td>
                    <td>
                      <ScoreGauge
                        score={e.score}
                        compact
                        alert={e.grade === "sell" || e.sellAlert}
                      />
                    </td>
                    <td className={`${styles.right} ${styles.mono}`}>
                      {e.stopPrice === null ? (
                        <span
                          className={styles.stopNotSet}
                          title={t("stop.notSet.hint")}
                        >
                          {t("stop.notSet")}
                        </span>
                      ) : (
                        fmtPrice(e.stopPrice, e.market)
                      )}
                    </td>
                    <td className={`${styles.right} ${styles.mono}`}>
                      {fmtPctPlain(e.near52wPct)}
                    </td>
                    <td className={`${styles.right} ${styles.mono}`}>
                      {e.turnover === null ? EM_DASH : fmtMoney(e.turnover, e.market)}
                    </td>
                    <td>
                      <GradeBadge grade={e.grade} size="sm" />
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
