import { useT } from "../i18n";
import styles from "./DisclaimerBanner.module.css";

/** Always-visible disclaimer. Required on header, footer, and drawer. */
export function DisclaimerBanner() {
  const t = useT();
  return (
    <div className={styles.banner} role="note" aria-label={t("disclaimer.label")}>
      <span className={styles.icon} aria-hidden="true">
        ℹ
      </span>
      <span className={styles.text}>{t("disclaimer.text")}</span>
    </div>
  );
}
