import { useT } from "../i18n";
import { DisclaimerBanner } from "./DisclaimerBanner";
import { RefreshCountdown } from "./RefreshCountdown";
import styles from "./Header.module.css";

interface Props {
  /** Whether the currently active market is open; null hides the indicator. */
  marketOpen: boolean | null;
  lastUpdated: Date | null;
  nextRefreshAt: string | null;
  refreshing: boolean;
  onRefresh: () => void;
}

export function Header({
  marketOpen,
  lastUpdated,
  nextRefreshAt,
  refreshing,
  onRefresh,
}: Props) {
  const t = useT();
  return (
    <header className={styles.header}>
      <div className={styles.top}>
        <div className={styles.brand}>
          <div className={styles.logo} aria-hidden="true">
            <svg viewBox="0 0 32 32" width="28" height="28">
              <rect width="32" height="32" rx="7" fill="#0f172a" />
              <path
                d="M6 22 L13 14 L18 18 L26 8"
                fill="none"
                stroke="#22c55e"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <circle cx="26" cy="8" r="2.4" fill="#22c55e" />
            </svg>
          </div>
          <div className={styles.titles}>
            <h1 className={styles.title}>{t("app.title")}</h1>
            <p className={styles.subtitle}>{t("app.subtitle")}</p>
          </div>
        </div>

        <div className={styles.controls}>
          {marketOpen !== null && (
            <span
              className={`${styles.marketStatus} ${
                marketOpen ? styles.open : styles.closed
              }`}
            >
              <span className={styles.dot} aria-hidden="true" />
              {marketOpen ? t("market.open") : t("market.closed")}
            </span>
          )}
          <RefreshCountdown
            lastUpdated={lastUpdated}
            nextRefreshAt={nextRefreshAt}
            marketOpen={marketOpen}
            refreshing={refreshing}
            onRefresh={onRefresh}
          />
        </div>
      </div>

      <DisclaimerBanner />
    </header>
  );
}
