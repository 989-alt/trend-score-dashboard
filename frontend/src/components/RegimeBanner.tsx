import { useT } from "../i18n";
import { fmtNumber } from "../format";
import type { RegimeInfo } from "../types";
import styles from "./RegimeBanner.module.css";

/** 레짐 라벨 → 색상 클래스(상승=녹/하락=적/횡보=옐로/판정중=중립). */
const REGIME_CLASS: Record<string, string> = {
  UP_TREND: "up",
  DOWN: "down",
  CHOP_VOL: "chop",
  UNKNOWN: "unknown",
};

/** 시황(레짐) 배너 — 국장·미장 장세를 색칩으로. 데이터 없으면 렌더 안 함(정적 데모). */
export function RegimeBanner({ markets }: { markets: RegimeInfo[] }) {
  const t = useT();
  if (markets.length === 0) return null;
  return (
    <div className={styles.banner} role="note" title={t("regime.hint")}>
      <span className={styles.title}>{t("regime.title")}</span>
      <div className={styles.chips}>
        {markets.map((m) => {
          const cls = REGIME_CLASS[m.regime] ?? "unknown";
          return (
            <span key={m.market} className={styles.item}>
              <span className={styles.mkt}>
                {m.market === "US" ? t("regime.us") : t("regime.kr")}
              </span>
              <span className={`${styles.chip} ${styles[cls]}`}>
                {t(`regime.${m.regime}`)}
              </span>
              {m.adx !== null && (
                <span className={styles.adx} data-num>
                  {t("regime.adx")} {fmtNumber(m.adx, 0)}
                </span>
              )}
            </span>
          );
        })}
      </div>
    </div>
  );
}
