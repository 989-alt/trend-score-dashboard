import { LIVE_REMOTE, STATIC_DEMO } from "../api";
import { useT } from "../i18n";
import styles from "./DemoBanner.module.css";

const INFO = "ⓘ";

/**
 * Top notice strip:
 * - LIVE_REMOTE (Pages fetching a remote OCI backend) → "30-min auto-refreshed live data".
 * - STATIC_DEMO (Pages bundled sample) → "sample demo, not live" (real tickers, example numbers).
 * - neither (same-origin live) → no banner (header already shows last-updated).
 */
export function DemoBanner() {
  const t = useT();
  if (LIVE_REMOTE) {
    return (
      <div className={`${styles.banner} ${styles.live}`} role="note">
        <span aria-hidden>{INFO}</span>
        <span>{t("live.banner")}</span>
      </div>
    );
  }
  if (!STATIC_DEMO) return null;
  return (
    <div className={styles.banner} role="note">
      <span aria-hidden>{INFO}</span>
      <span>{t("demo.banner")}</span>
    </div>
  );
}
