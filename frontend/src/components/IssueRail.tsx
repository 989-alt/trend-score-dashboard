import { useT } from "../i18n";
import type { NewsIssue } from "../types";
import { GradeBadge } from "./badges/GradeBadge";
import styles from "./IssueRail.module.css";

interface Props {
  issues: NewsIssue[];
  onOpen: (key: string) => void;
}

/**
 * 긴급 이슈 Top N 빠른 접근 레일 — 테마/국장/미장 탭의 우측 사이드바.
 * 항목 클릭 → 시황 탭의 해당 이슈로 점프(App 의 onOpen 이 탭 전환 + 선택).
 */
export function IssueRail({ issues, onOpen }: Props) {
  const t = useT();
  return (
    <aside className={styles.rail} aria-label={t("news.sidebar.title")}>
      <h2 className={styles.title}>{t("news.sidebar.title")}</h2>
      {issues.length === 0 ? (
        <p className={styles.empty}>{t("news.empty")}</p>
      ) : (
        <ol className={styles.list}>
          {issues.map((issue, i) => (
            <li key={issue.key}>
              <button
                type="button"
                className={styles.item}
                onClick={() => onOpen(issue.key)}
              >
                <span className={styles.rank} data-num>
                  {i + 1}
                </span>
                <span className={styles.main}>
                  <span className={styles.itemTitle}>{issue.title}</span>
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
