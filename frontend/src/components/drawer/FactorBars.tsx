import { useT } from "../../i18n";
import type { FactorBreakdown } from "../../types";
import { EM_DASH } from "../../format";
import styles from "./FactorBars.module.css";

interface Props {
  factors: FactorBreakdown | null;
  /** 부적격이면 모멘텀·RS·거래대금 막대가 통과 종목 밴드 대비 위치(음수 가능)임을 안내. */
  eligible?: boolean;
}

interface FactorRow {
  labelKey: string;
  value: number | null;
}

/** 0–1 normalized factor contribution bars (near_52w … vol_fit). */
export function FactorBars({ factors, eligible = true }: Props) {
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
        const raw = r.value;
        // 막대 폭은 0~1 클램프(부적격의 밴드 초과=만, 미달=공). 숫자는 실제값(음수·1초과 포함).
        const v = raw === null ? null : Math.max(0, Math.min(1, raw));
        const pct = v === null ? 0 : v * 100;
        const valueCls =
          raw === null || (raw >= 0 && raw <= 1)
            ? ""
            : raw < 0
              ? styles.below
              : styles.above;
        return (
          <div key={r.labelKey} className={styles.row}>
            <span className={styles.label}>{t(r.labelKey)}</span>
            <div className={styles.track}>
              <div className={styles.fill} style={{ width: `${pct}%` }} />
            </div>
            <span className={`${styles.value} ${valueCls}`}>
              {raw === null ? EM_DASH : raw.toFixed(2)}
            </span>
          </div>
        );
      })}
      {!eligible && <p className={styles.note}>{t("factor.inelig.note")}</p>}
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
