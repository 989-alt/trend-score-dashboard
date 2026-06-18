import { useT } from "../i18n";
import styles from "./MarketTabs.module.css";

export type TabKey = "themes" | "issues" | "kr" | "us";

interface Props {
  active: TabKey;
  onChange: (tab: TabKey) => void;
}

const TABS: { key: TabKey; labelKey: string }[] = [
  { key: "themes", labelKey: "tab.themes" },
  { key: "issues", labelKey: "tab.issues" },
  { key: "kr", labelKey: "tab.kr" },
  { key: "us", labelKey: "tab.us" },
];

/** DOM id for a tab button — referenced by the panel's `aria-labelledby`. */
export const tabId = (key: TabKey): string => `tab-${key}`;
/** DOM id for the single tabpanel — referenced by each tab's `aria-controls`. */
export const TABPANEL_ID = "tabpanel-main";

export function MarketTabs({ active, onChange }: Props) {
  const t = useT();
  return (
    <div className={styles.tabs} role="tablist" aria-label={t("app.title")}>
      {TABS.map((tab) => {
        const selected = tab.key === active;
        return (
          <button
            key={tab.key}
            id={tabId(tab.key)}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={TABPANEL_ID}
            className={`${styles.tab} ${selected ? styles.active : ""}`}
            onClick={() => onChange(tab.key)}
          >
            {t(tab.labelKey)}
          </button>
        );
      })}
    </div>
  );
}
