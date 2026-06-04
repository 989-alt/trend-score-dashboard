import { useT } from "../../i18n";
import type { Market } from "../../types";
import styles from "./MarketBadge.module.css";

interface Props {
  market: Market;
}

/** Small KR/US market tag. Text label (KR/US) carries the meaning. */
export function MarketBadge({ market }: Props) {
  const t = useT();
  return (
    <span className={`${styles.badge} ${styles[market]}`}>
      {t(`market.badge.${market}`)}
    </span>
  );
}
