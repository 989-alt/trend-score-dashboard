import { useT } from "../../i18n";
import { GRADE_ICON } from "../../format";
import type { Grade } from "../../types";
import styles from "./GradeBadge.module.css";

interface Props {
  grade: Grade;
  size?: "sm" | "md";
}

/**
 * Grade pill. WCAG AA: meaning is carried by an icon glyph + a text label, not
 * color alone. Color is decorative reinforcement only.
 */
export function GradeBadge({ grade, size = "md" }: Props) {
  const t = useT();
  const label = t(`grade.${grade}`);
  return (
    <span
      className={`${styles.badge} ${styles[grade]} ${size === "sm" ? styles.sm : ""}`}
    >
      <span className={styles.icon} aria-hidden="true">
        {GRADE_ICON[grade]}
      </span>
      <span className={styles.label}>{label}</span>
    </span>
  );
}
