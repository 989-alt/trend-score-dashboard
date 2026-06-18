import { useState } from "react";
import { useT } from "../i18n";
import type { NewsIssue, NewsIssuesData } from "../types";
import { GradeBadge } from "./badges/GradeBadge";
import styles from "./IssueRail.module.css";

interface Props {
  data: NewsIssuesData | null;
  onOpen: (key: string) => void;
}

type LayerKey = "domestic" | "us" | "macro";
const TABS: { key: LayerKey; labelKey: string }[] = [
  { key: "domestic", labelKey: "news.layer.domestic" },
  { key: "us", labelKey: "news.layer.us" },
  { key: "macro", labelKey: "news.layer.macro" },
];

/**
 * 긴급 이슈 레일 — 테마/국장/미장 탭의 우측 고정 사이드바. 3-탭(국내/미국/종합),
 * **종합 기본 활성**. 항목 클릭 → 시황 탭의 해당 이슈로 점프(App 의 onOpen).
 */
export function IssueRail({ data, onOpen }: Props) {
  const t = useT();
  const [active, setActive] = useState<LayerKey>("macro");
  const issues: NewsIssue[] = data ? data[active] : [];
  return (
    <aside className={styles.rail} aria-label={t("news.sidebar.title")}>
      <h2 className={styles.title}>{t("news.sidebar.title")}</h2>
      <div className={styles.tabs} role="tablist" aria-label={t("news.sidebar.title")}>
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={tab.key === active}
            className={`${styles.tab} ${tab.key === active ? styles.tabActive : ""}`}
            onClick={() => setActive(tab.key)}
          >
            {t(tab.labelKey)}
          </button>
        ))}
      </div>
      {issues.length === 0 ? (
        <p className={styles.empty}>{t("news.empty")}</p>
      ) : (
        <ol className={styles.list}>
          {issues.map((issue, i) => (
            <li key={issue.key}>
              <button type="button" className={styles.item} onClick={() => onOpen(issue.key)}>
                <span className={styles.rank} data-num>
                  {i + 1}
                </span>
                <span className={styles.main}>
                  <span className={styles.itemTitle}>{issue.title}</span>
                  {issue.headline && <span className={styles.itemHeadline}>{issue.headline}</span>}
                  <span className={styles.meta}>
                    {t("news.issue.count").replace("{n}", String(issue.count))}
                    {issue.spike >= 1.5 && (
                      <span className={styles.spike} data-num>
                        {" · 🔥"}
                        {issue.spike.toFixed(1)}
                      </span>
                    )}
                    {issue.score !== null && issue.grade && (
                      <span className={styles.score}>
                        {" · "}
                        <GradeBadge grade={issue.grade} size="sm" />
                      </span>
                    )}
                  </span>
                </span>
                <span className={styles.urgency} data-num>
                  {issue.urgency.toFixed(1)}
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </aside>
  );
}
