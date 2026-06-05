import { useT } from "../../i18n";
import type { FactorBreakdown } from "../../types";
import { EM_DASH } from "../../format";
import styles from "./FactorBars.module.css";

interface Props {
  factors: FactorBreakdown | null;
}

interface FactorRow {
  labelKey: string;
  value: number | null;
}

/** 0–1 normalized factor contribution bars (near_52w … vol_fit). */
export function FactorBars({ factors }: Props) {
  const t = useT();
  if (!factors) {
    return <p className={styles.empty}>{EM_DASH}</p>;
  }

  const rows: FactorRow[] = [
    { labelKey: "factor.near_52w", value: factors.near52w },
    { labelKey: "factor.pocket_pivot", value: factors.pocketPivot },
    { labelKey: "factor.momentum_norm", value: factors.momentumNorm },
    { labelKey: "factor.rs", value: factors.rsNorm },
    { labelKey: "factor.turnover_norm", value: factors.turnoverNorm },
    { labelKey: "factor.vol_fit", value: factors.volFit },
  ];

  return (
    <div className={styles.wrap}>
      {rows.map((r) => {
        const v = r.value === null ? null : Math.max(0, Math.min(1, r.value));
        const pct = v === null ? 0 : v * 100;
        return (
          <div key={r.labelKey} className={styles.row}>
            <span className={styles.label}>{t(r.labelKey)}</span>
            <div className={styles.track}>
              <div className={styles.fill} style={{ width: `${pct}%` }} />
            </div>
            <span className={styles.value}>
              {v === null ? EM_DASH : v.toFixed(2)}
            </span>
          </div>
        );
      })}
      <div className={styles.raw}>
        <span>
          {t("factor.raw.momentum")}:{" "}
          <b>{factors.momentum === null ? EM_DASH : factors.momentum.toFixed(3)}</b>
        </span>
        <span>
          {t("factor.raw.rs")}:{" "}
          <b>{factors.rs === null ? EM_DASH : factors.rs.toFixed(3)}</b>
        </span>
        <span>
          {t("factor.raw.volatility")}:{" "}
          <b>{factors.volatility === null ? EM_DASH : factors.volatility.toFixed(3)}</b>
        </span>
        <span>
          {t("field.aboveMa200")}:{" "}
          <b>{factors.aboveMa200 ? t("value.yes") : t("value.no")}</b>
        </span>
      </div>
    </div>
  );
}
