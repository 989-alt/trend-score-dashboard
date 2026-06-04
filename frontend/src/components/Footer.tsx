import { useT } from "../i18n";
import styles from "./Footer.module.css";

export function Footer() {
  const t = useT();
  return (
    <footer className={styles.footer}>
      <p className={styles.disclaimer}>{t("footer.disclaimer")}</p>
      <p className={styles.method}>{t("footer.method")}</p>
    </footer>
  );
}
