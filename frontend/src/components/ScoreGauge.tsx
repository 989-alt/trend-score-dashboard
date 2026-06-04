import styles from "./ScoreGauge.module.css";

interface Props {
  score: number; // 0..100
  /** Render a compact bar without the numeric label (for dense table cells). */
  compact?: boolean;
  /**
   * Force the warning (sell) color regardless of score band — used when the
   * entry is graded "sell" or has an active sell alert, so a high raw score
   * never paints the bar green for a position that should be exited.
   */
  alert?: boolean;
}

function band(score: number): "high" | "mid" | "low" | "weak" {
  if (score >= 75) return "high";
  if (score >= 60) return "mid";
  if (score >= 45) return "low";
  return "weak";
}

/** Horizontal 0–100 score bar. Numeric value is always shown for AA. */
export function ScoreGauge({ score, compact = false, alert = false }: Props) {
  const clamped = Math.max(0, Math.min(100, score));
  const pct = `${clamped}%`;
  const fillClass = alert ? styles.alert : styles[band(clamped)];
  return (
    <div
      className={`${styles.wrap} ${compact ? styles.compact : ""}`}
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped)}
    >
      <div className={styles.track}>
        <div className={`${styles.fill} ${fillClass}`} style={{ width: pct }} />
      </div>
      <span className={styles.value}>{clamped.toFixed(0)}</span>
    </div>
  );
}
