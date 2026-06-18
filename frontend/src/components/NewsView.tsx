import { useState } from "react";
import { useT } from "../i18n";
import type { NewsIssuesData, WeeklyData } from "../types";
import styles from "./NewsView.module.css";

interface Props {
  data: NewsIssuesData | null;
  weekly: WeeklyData | null;
}

/** ts_kst is an ISO string with +09:00 offset — slice for a tz-stable "MM-DD HH:mm". */
function tsLabel(iso: string): string {
  if (iso.length < 16) return iso;
  return `${iso.slice(5, 10)} ${iso.slice(11, 16)}`;
}

/** Read-only "situation" tab: urgency-ranked issue sidebar + raw detail + weekly macro. */
export function NewsView({ data, weekly }: Props) {
  const t = useT();
  const issues = data?.issues ?? [];
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selected = issues.find((i) => i.key === selectedKey) ?? issues[0] ?? null;

  return (
    <section className={styles.newsView}>
      <div className={styles.note} role="note">
        <span className={styles.noteIcon} aria-hidden="true">
          ℹ
        </span>
        {t("news.disclaimer")}
      </div>

      <div className={styles.grid}>
        <aside className={styles.sidebar} aria-label={t("news.sidebar.title")}>
          <h2 className={styles.sidebarTitle}>{t("news.sidebar.title")}</h2>
          {issues.length === 0 ? (
            <p className={styles.empty}>{t("news.empty")}</p>
          ) : (
            <ol className={styles.issueList}>
              {issues.map((issue, i) => {
                const active = selected?.key === issue.key;
                return (
                  <li key={issue.key}>
                    <button
                      type="button"
                      className={`${styles.issueBtn} ${active ? styles.active : ""}`}
                      onClick={() => setSelectedKey(issue.key)}
                    >
                      <span className={styles.rank} data-num>
                        {i + 1}
                      </span>
                      <span className={styles.issueMain}>
                        <span className={styles.issueTitle}>{issue.title}</span>
                        <span className={styles.issueMeta}>
                          {t("news.issue.count").replace("{n}", String(issue.count))}
                          {" · "}
                          {t("news.issue.channels").replace(
                            "{n}",
                            String(issue.channels.length),
                          )}
                          {issue.severity > 0 && (
                            <span className={styles.sevDot} aria-hidden="true" />
                          )}
                        </span>
                      </span>
                      <span className={styles.urgency} data-num>
                        {issue.urgency.toFixed(1)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
        </aside>

        <div className={styles.detail}>
          {selected ? (
            <>
              <div className={styles.detailHead}>
                <h3 className={styles.detailTitle}>{selected.title}</h3>
                <span className={styles.detailUrgency} data-num>
                  {t("news.urgency")} {selected.urgency.toFixed(1)}
                </span>
              </div>
              <ul className={styles.msgList}>
                {selected.messages.map((m, i) => (
                  <li key={`${m.channel}-${i}`} className={styles.msg}>
                    <div className={styles.msgHead}>
                      <span className={styles.channel}>{m.channel}</span>
                      <span className={styles.ts} data-num>
                        {tsLabel(m.tsKst)}
                      </span>
                    </div>
                    <p className={styles.msgText}>{m.text}</p>
                    {m.urls.length > 0 && (
                      <div className={styles.links}>
                        {m.urls.map((u, j) => (
                          <a
                            key={j}
                            href={u}
                            target="_blank"
                            rel="noopener noreferrer"
                            className={styles.link}
                          >
                            {t("news.openLink")} ↗
                          </a>
                        ))}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className={styles.empty}>{t("news.detail.hint")}</p>
          )}
        </div>
      </div>

      <div className={styles.weekly}>
        <div className={styles.weeklyHead}>
          <h3 className={styles.weeklyTitle}>{t("news.weekly.title")}</h3>
          <span className={styles.weeklySource}>{t("news.weekly.source")}</span>
        </div>
        {weekly?.krMarkdown ? (
          <div className={styles.weeklyBody}>{weekly.krMarkdown}</div>
        ) : (
          <p className={styles.empty}>{t("news.weekly.empty")}</p>
        )}
      </div>
    </section>
  );
}
