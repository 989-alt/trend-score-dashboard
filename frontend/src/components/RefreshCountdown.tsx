import { useT } from "../i18n";
import { fmtClock, fmtCountdown } from "../format";
import { useCountdown } from "../hooks/usePolling";
import styles from "./RefreshCountdown.module.css";

interface Props {
  lastUpdated: Date | null;
  nextRefreshAt: string | null;
  /** Active market open state; when false the countdown reads as "closed". */
  marketOpen?: boolean | null;
  refreshing: boolean;
  onRefresh: () => void;
}

// A live poll cadence is sub-hour; anything beyond this is not a real
// countdown (market closed / next session far away), so we don't render mm:ss.
const MAX_COUNTDOWN_SECONDS = 60 * 60;

/**
 * Shows "last updated HH:MM · next refresh in mm:ss" plus a manual refresh
 * button. The countdown ticks every second toward `nextRefreshAt`. When the
 * market is closed (or the remaining time exceeds an hour, which is not a
 * normal poll interval) it shows a "closed / next open HH:MM" label instead of
 * a runaway mm:ss value.
 */
export function RefreshCountdown({
  lastUpdated,
  nextRefreshAt,
  marketOpen,
  refreshing,
  onRefresh,
}: Props) {
  const t = useT();
  const remaining = useCountdown(nextRefreshAt);
  const lastIso = lastUpdated ? lastUpdated.toISOString() : null;

  // Closed when the market is explicitly closed, or the remaining time is not a
  // plausible poll interval (> 1h) — both mean "no live tick".
  const closed =
    marketOpen === false ||
    (remaining !== null && remaining > MAX_COUNTDOWN_SECONDS);

  let countdownLabel: string;
  if (closed) {
    const openClock = fmtClock(nextRefreshAt);
    countdownLabel =
      openClock === "—"
        ? t("refresh.closed")
        : t("refresh.nextOpen", { time: openClock });
  } else if (remaining === null) {
    countdownLabel = t("refresh.waiting");
  } else {
    countdownLabel = fmtCountdown(remaining);
  }

  return (
    <div className={styles.wrap}>
      <div className={styles.info}>
        <span className={styles.line}>
          <span className={styles.label}>{t("refresh.lastUpdated")}</span>
          <span className={styles.value}>{fmtClock(lastIso)}</span>
        </span>
        <span className={styles.sep} aria-hidden="true">
          ·
        </span>
        <span className={styles.line}>
          <span className={styles.label}>
            {closed ? t("market.closed") : t("refresh.nextIn")}
          </span>
          <span className={styles.value}>{countdownLabel}</span>
        </span>
      </div>
      <button
        type="button"
        className={styles.btn}
        onClick={onRefresh}
        disabled={refreshing}
        aria-label={t("refresh.now")}
      >
        <span
          className={`${styles.spin} ${refreshing ? styles.spinning : ""}`}
          aria-hidden="true"
        >
          ⟳
        </span>
        <span className={styles.btnText}>
          {refreshing ? t("refresh.refreshing") : t("refresh.now")}
        </span>
      </button>
    </div>
  );
}
