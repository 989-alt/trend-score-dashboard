import { useT } from "../i18n";
import styles from "./StateViews.module.css";

export function LoadingView() {
  const t = useT();
  return (
    <div className={styles.state} role="status" aria-live="polite">
      <span className={styles.spinner} aria-hidden="true" />
      <span className={styles.text}>{t("state.loading")}</span>
    </div>
  );
}

export function ErrorView({ onRetry }: { onRetry: () => void }) {
  const t = useT();
  return (
    <div className={styles.state} role="alert">
      <span className={styles.errorIcon} aria-hidden="true">
        ⚠
      </span>
      <h3 className={styles.title}>{t("state.error.title")}</h3>
      <p className={styles.hint}>{t("state.error.hint")}</p>
      <button type="button" className={styles.retry} onClick={onRetry}>
        {t("state.retry")}
      </button>
    </div>
  );
}

export function EmptyView() {
  const t = useT();
  return (
    <div className={styles.state}>
      <span className={styles.text}>{t("state.empty")}</span>
    </div>
  );
}
