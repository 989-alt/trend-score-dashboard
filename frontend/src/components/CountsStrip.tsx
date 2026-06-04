import { useT } from "../i18n";
import type { SnapshotCounts } from "../types";
import styles from "./CountsStrip.module.css";

interface Props {
  counts: SnapshotCounts;
}

/** Observability strip: scanned / eligible / scored / failed. */
export function CountsStrip({ counts }: Props) {
  const t = useT();
  const items: { key: string; value: number; alert?: boolean }[] = [
    { key: "counts.scanned", value: counts.scanned },
    { key: "counts.eligible", value: counts.eligible },
    { key: "counts.scored", value: counts.scored },
    { key: "counts.failed", value: counts.failed, alert: counts.failed > 0 },
  ];
  return (
    <div className={styles.strip}>
      {items.map((it) => (
        <span key={it.key} className={styles.item}>
          <span className={styles.label}>{t(it.key)}</span>
          <span className={`${styles.value} ${it.alert ? styles.alert : ""}`}>
            {it.value.toLocaleString("en-US")}
          </span>
        </span>
      ))}
    </div>
  );
}
