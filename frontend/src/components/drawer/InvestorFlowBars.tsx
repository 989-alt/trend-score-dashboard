import { useT } from "../../i18n";
import type { InvestorFlow, Market } from "../../types";
import { fmtMoney } from "../../format";
import styles from "./InvestorFlowBars.module.css";

interface Props {
  flow: InvestorFlow | null;
  market: Market;
}

interface Row {
  labelKey: string;
  net: number | null;
  buy: number | null;
  sell: number | null;
}

/**
 * Per-actor investor trading (foreign/institution/individual). Shows buy, sell
 * and net amounts; the diverging bar visualizes net (green/right = net buy,
 * red/left = net sell). Buy/sell amounts are shown when the backend provides
 * them, otherwise the row falls back to net only. KR only — US (no
 * investor_flow) shows an explicit "not provided" note.
 */
export function InvestorFlowBars({ flow, market }: Props) {
  const t = useT();

  if (market === "US" || !flow) {
    return (
      <p className={styles.notProvided}>
        <span className={styles.naDash}>{t("investor.notProvided")}</span>
        <span className={styles.naHint}>{t("investor.notProvided.us")}</span>
      </p>
    );
  }

  const rows: Row[] = [
    {
      labelKey: "investor.foreign",
      net: flow.foreignNet,
      buy: flow.foreignBuy,
      sell: flow.foreignSell,
    },
    {
      labelKey: "investor.institution",
      net: flow.institutionNet,
      buy: flow.institutionBuy,
      sell: flow.institutionSell,
    },
    {
      labelKey: "investor.individual",
      net: flow.individualNet,
      buy: flow.individualBuy,
      sell: flow.individualSell,
    },
  ];

  const maxAbs = Math.max(
    1,
    ...rows.map((r) => (r.net === null ? 0 : Math.abs(r.net))),
  );

  return (
    <div className={styles.wrap}>
      <div className={styles.meta}>
        <span>
          {t("investor.asOf")}: {flow.date}
        </span>
        <span>{t("investor.unit")}</span>
      </div>
      <div className={styles.bars}>
        {rows.map((r) => {
          const v = r.net ?? 0;
          const pct = (Math.abs(v) / maxAbs) * 100;
          const positive = v >= 0;
          const hasGross = r.buy !== null || r.sell !== null;
          return (
            <div key={r.labelKey} className={styles.barRow}>
              <span className={styles.label}>{t(r.labelKey)}</span>
              <div className={styles.axis}>
                <div className={styles.center} aria-hidden="true" />
                <div className={styles.half}>
                  {!positive && (
                    <div
                      className={`${styles.bar} ${styles.neg}`}
                      style={{ width: `${pct}%` }}
                    />
                  )}
                </div>
                <div className={styles.half}>
                  {positive && (
                    <div
                      className={`${styles.bar} ${styles.pos}`}
                      style={{ width: `${pct}%` }}
                    />
                  )}
                </div>
              </div>
              <div className={styles.amounts}>
                {hasGross && (
                  <div className={styles.gross}>
                    <span className={styles.gLabel}>{t("investor.buy")}</span>
                    <span className={`${styles.gValue} ${styles.up}`}>
                      {fmtMoney(r.buy, market)}
                    </span>
                    <span className={styles.gLabel}>{t("investor.sell")}</span>
                    <span className={`${styles.gValue} ${styles.down}`}>
                      {fmtMoney(r.sell, market)}
                    </span>
                  </div>
                )}
                <div className={styles.netLine}>
                  <span className={styles.gLabel}>{t("investor.net")}</span>
                  <span
                    className={`${styles.value} ${positive ? styles.up : styles.down}`}
                  >
                    {r.net === null ? t("value.na") : fmtMoney(v, market)}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
