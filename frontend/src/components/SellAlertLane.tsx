import { useT } from "../i18n";
import type { ScoreEntry } from "../types";
import { MarketBadge } from "./badges/MarketBadge";
import { SellBadge } from "./badges/SellBadge";
import styles from "./SellAlertLane.module.css";

interface Props {
  entries: ScoreEntry[];
  onSelect: (entry: ScoreEntry) => void;
}

/**
 * Red alert lane listing every sell_alert entry. Renders nothing when empty so
 * it never occupies space without a reason.
 */
export function SellAlertLane({ entries, onSelect }: Props) {
  const t = useT();
  const alerts = entries.filter((e) => e.sellAlert);
  if (alerts.length === 0) return null;

  return (
    <section className={styles.lane} aria-label={t("sellAlert.lane.title")}>
      <div className={styles.head}>
        <span className={styles.icon} aria-hidden="true">
          ⚠
        </span>
        <div>
          <h2 className={styles.title}>{t("sellAlert.lane.title")}</h2>
          <p className={styles.desc}>{t("sellAlert.lane.desc")}</p>
        </div>
        <span className={styles.count}>{alerts.length}</span>
      </div>
      <div className={styles.chips}>
        {alerts.map((e) => (
          <button
            key={`${e.market}-${e.ticker}`}
            type="button"
            className={styles.chip}
            onClick={() => onSelect(e)}
          >
            <MarketBadge market={e.market} />
            <span className={styles.name}>{e.name}</span>
            <span className={styles.ticker}>{e.ticker}</span>
            <SellBadge reason={e.sellReason} size="sm" />
          </button>
        ))}
      </div>
    </section>
  );
}
