import { useT } from "../../i18n";
import type { ScoreEntry } from "../../types";
import { EM_DASH, fmtPct, fmtPctPlain, fmtPrice } from "../../format";
import styles from "./RationaleSection.module.css";

type Status = "pass" | "warn" | "fail";

// Non-Hangul glyphs (check / triangle / cross / warning) — keeps .tsx Hangul-free.
const ICON: Record<Status, string> = {
  pass: "✓", // checkmark
  warn: "△", // white up-triangle
  fail: "✗", // ballot X
};
const WARN_SIGN = "⚠"; // warning sign

interface Crit {
  key: string;
  label: string;
  status: Status;
  detail: string;
}

/** Grade a value against pass/warn thresholds. null → warn (unknown). */
function band(v: number | null, pass: number, warn: number): Status {
  if (v === null) return "warn";
  if (v >= pass) return "pass";
  if (v >= warn) return "warn";
  return "fail";
}

/**
 * "Why this grade" — explains the recommendation grade in plain language.
 * Everything is derived from the already-fetched ScoreEntry (no extra backend call).
 */
export function RationaleSection({ entry }: { entry: ScoreEntry }) {
  const t = useT();
  const f = entry.factors;
  const score = Math.round(entry.score);

  // Sell-alert trigger, with the actual numbers.
  let sell: string | null = null;
  if (entry.sellAlert) {
    sell =
      entry.sellReason === "ma200_break"
        ? t("rec.sell.ma200", { ma200: fmtPrice(entry.ma200, entry.market) })
        : t("rec.sell.trailing", {
            peak: fmtPrice(entry.trailingPeak, entry.market),
            stop: fmtPrice(entry.stopPrice, entry.market),
            price: fmtPrice(entry.price, entry.market),
          });
  }

  // When ineligible (avoid / score 0), infer which hard filter rejected it.
  const inelig: string[] = [];
  if (!entry.eligible && f) {
    if (!f.aboveMa200) inelig.push(t("rec.inelig.ma200"));
    if (f.momentum !== null && f.momentum <= 0) inelig.push(t("rec.inelig.momentum"));
    if (f.volFit !== null && f.volFit <= 0) inelig.push(t("rec.inelig.vol"));
    if (inelig.length === 0) inelig.push(t("rec.inelig.other"));
  }

  // Trend-following criteria checklist (the 5 weighted factors).
  const crits: Crit[] = f
    ? [
        {
          key: "trend",
          label: t("crit.trend"),
          status: f.aboveMa200 ? "pass" : "fail",
          detail: f.aboveMa200 ? t("crit.trend.pass") : t("crit.trend.fail"),
        },
        {
          key: "leader",
          label: t("crit.leader"),
          status: band(entry.near52wPct, 90, 75),
          detail:
            entry.near52wPct === null
              ? EM_DASH
              : t("crit.leader.detail", { pct: fmtPctPlain(entry.near52wPct) }),
        },
        {
          key: "momentum",
          label: t("crit.momentum"),
          status: f.momentum === null ? "warn" : f.momentum > 0 ? "pass" : "fail",
          detail: f.momentum === null ? EM_DASH : fmtPct(f.momentum * 100),
        },
        {
          key: "pocket",
          label: t("crit.pocket"),
          status: (f.pocketPivot ?? 0) >= 1 ? "pass" : "fail",
          detail: (f.pocketPivot ?? 0) >= 1 ? t("crit.pocket.pass") : t("crit.pocket.fail"),
        },
        {
          key: "vol",
          label: t("crit.vol"),
          status: band(f.volFit, 0.5, 0.01),
          detail: f.volFit === null ? EM_DASH : f.volFit.toFixed(2),
        },
      ]
    : [];

  return (
    <section className={`${styles.rec} ${styles[`g_${entry.grade}`]}`}>
      <h3 className={styles.title}>{t("drawer.section.recommendation")}</h3>

      <div className={styles.conclusion}>
        <span className={styles.grade}>{t(`grade.${entry.grade}`)}</span>
        <span className={styles.score}>{t("rec.scoreOf", { score })}</span>
        <span className={styles.meaning}>{t(`recmeaning.${entry.grade}`)}</span>
      </div>

      {sell && (
        <p className={styles.sell} role="alert">
          <span aria-hidden>{WARN_SIGN}</span> {sell}
        </p>
      )}

      {inelig.length > 0 && (
        <div className={styles.inelig}>
          <span className={styles.ineligTitle}>{t("rec.inelig.title")}</span>
          <ul>
            {inelig.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      {crits.length > 0 && (
        <>
          <p className={styles.checklistTitle}>{t("rec.checklist")}</p>
          <ul className={styles.checklist}>
            {crits.map((c) => (
              <li key={c.key} className={styles[c.status]}>
                <span className={styles.icon} aria-hidden>
                  {ICON[c.status]}
                </span>
                <span className={styles.label}>{c.label}</span>
                <span className={styles.detail}>{c.detail}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {entry.rationale && <p className={styles.summary}>{entry.rationale}</p>}
    </section>
  );
}
