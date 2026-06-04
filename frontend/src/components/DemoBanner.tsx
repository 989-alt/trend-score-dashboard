import { STATIC_DEMO } from "../api";
import { useT } from "../i18n";
import styles from "./DemoBanner.module.css";

const INFO = "ⓘ";

/**
 * Static-demo notice — only shown on the GitHub Pages build (VITE_STATIC=1),
 * where the data is bundled synthetic sample data, not a live feed. Keeps the
 * public page honest about real tickers showing example numbers.
 */
export function DemoBanner() {
  const t = useT();
  if (!STATIC_DEMO) return null;
  return (
    <div className={styles.banner} role="note">
      <span aria-hidden>{INFO}</span>
      <span>{t("demo.banner")}</span>
    </div>
  );
}
