import { useT } from "../../i18n";
import type { SellReason } from "../../types";
import styles from "./SellBadge.module.css";

interface Props {
  reason?: SellReason | null;
  size?: "sm" | "md";
}

/**
 * Sell-alert chip. AA: warning triangle glyph + explicit text label, never a
 * bare red color. Shows the specific reason when available.
 */
export function SellBadge({ reason, size = "md" }: Props) {
  const t = useT();
  const reasonLabel = reason ? t(`sellReason.${reason}`) : t("sellReason.unknown");
  return (
    <span className={`${styles.badge} ${size === "sm" ? styles.sm : ""}`}>
      <span className={styles.icon} aria-hidden="true">
        ⚠
      </span>
      <span className={styles.label}>{t("sellAlert.badge")}</span>
      <span className={styles.reason}>{reasonLabel}</span>
    </span>
  );
}
