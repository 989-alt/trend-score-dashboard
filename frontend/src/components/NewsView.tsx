import type { ReactNode } from "react";
import { useT } from "../i18n";
import type { Market, NewsIssue, NewsIssuesData, WeeklyData } from "../types";
import { GradeBadge } from "./badges/GradeBadge";
import styles from "./NewsView.module.css";

interface Props {
  data: NewsIssuesData | null;
  weekly: WeeklyData | null;
  onSelectTicker: (market: Market, code: string) => void;
  /** 선택된 이슈 key (App 이 소유 — 다른 탭에서 점프 시 주입). */
  selectedKey: string | null;
  onSelectKey: (key: string) => void;
}

type LayerKey = "domestic" | "us" | "macro";
const LAYERS: { key: LayerKey; labelKey: string }[] = [
  { key: "domestic", labelKey: "news.layer.domestic" },
  { key: "us", labelKey: "news.layer.us" },
  { key: "macro", labelKey: "news.layer.macro" },
];

/** ts_kst is an ISO string with +09:00 offset — slice for a tz-stable "MM-DD HH:mm". */
function tsLabel(iso: string): string {
  if (iso.length < 16) return iso;
  return `${iso.slice(5, 10)} ${iso.slice(11, 16)}`;
}

const _PCT = "[-+]?\\d+(?:\\.\\d+)?%";
function _escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** 종목명·키워드(terms)와 %변동을 <mark> 로 강조 — React-safe 분할 렌더. */
function highlight(text: string, terms: string[]): ReactNode[] {
  const parts = [_PCT, ...terms.filter((x) => x.length >= 2).map(_escapeRe)];
  const re = new RegExp(`(${parts.join("|")})`, "g");
  const out: ReactNode[] = [];
  let last = 0;
  let i = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(
      <mark key={i++} className={styles.hl}>
        {m[0]}
      </mark>,
    );
    last = m.index + m[0].length;
    if (m.index === re.lastIndex) re.lastIndex++;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

interface ItemProps {
  issue: NewsIssue;
  rank: number;
  active: boolean;
  onSelect: () => void;
}

function IssueItem({ issue, rank, active, onSelect }: ItemProps) {
  const t = useT();
  return (
    <li>
      <button
        type="button"
        className={`${styles.issueBtn} ${active ? styles.active : ""}`}
        onClick={onSelect}
      >
        <span className={styles.rank} data-num>
          {rank}
        </span>
        <span className={styles.issueMain}>
          <span className={styles.issueTitle}>{issue.title}</span>
          {issue.headline && <span className={styles.issueHeadline}>{issue.headline}</span>}
          <span className={styles.issueMeta}>
            {t("news.issue.count").replace("{n}", String(issue.count))}
            {issue.spike >= 1.5 && (
              <span className={styles.spike} data-num>
                {" · 🔥"}
                {issue.spike.toFixed(1)}
              </span>
            )}
            {issue.score !== null && issue.grade && (
              <span className={styles.scoreChip}>
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
  );
}

/** 읽기전용 "시황" 탭: 국내/미국/종합 3-레이어 사이드바 + 상세(대표 한 줄·하이라이트) + 주간 매크로. */
export function NewsView({ data, weekly, onSelectTicker, selectedKey, onSelectKey }: Props) {
  const t = useT();
  const layers: Record<LayerKey, NewsIssue[]> = {
    domestic: data?.domestic ?? [],
    us: data?.us ?? [],
    macro: data?.macro ?? [],
  };
  const all = [...layers.domestic, ...layers.us, ...layers.macro];
  const selected = all.find((i) => i.key === selectedKey) ?? all[0] ?? null;

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
          {all.length === 0 ? (
            <p className={styles.empty}>{t("news.empty")}</p>
          ) : (
            LAYERS.map(({ key, labelKey }) => (
              <div key={key} className={styles.layer}>
                <h2 className={styles.layerTitle}>{t(labelKey)}</h2>
                {layers[key].length === 0 ? (
                  <p className={styles.layerEmpty}>{t("news.layer.empty")}</p>
                ) : (
                  <ol className={styles.issueList}>
                    {layers[key].map((issue, i) => (
                      <IssueItem
                        key={issue.key}
                        issue={issue}
                        rank={i + 1}
                        active={selected?.key === issue.key}
                        onSelect={() => onSelectKey(issue.key)}
                      />
                    ))}
                  </ol>
                )}
              </div>
            ))
          )}
        </aside>

        <div className={styles.detail}>
          {selected ? (
            <>
              <div className={styles.detailHead}>
                <h3 className={styles.detailTitle}>{selected.title}</h3>
                <div className={styles.detailMeta}>
                  {selected.ticker && selected.market && (
                    <button
                      type="button"
                      className={styles.scoreBtn}
                      onClick={() => onSelectTicker(selected.market!, selected.ticker!)}
                    >
                      {t("news.scoreDetail")}
                    </button>
                  )}
                  <span className={styles.detailUrgency} data-num>
                    {t("news.urgency")} {selected.urgency.toFixed(1)}
                  </span>
                </div>
              </div>
              {selected.headline && (
                <p className={styles.headline}>{highlight(selected.headline, [selected.title])}</p>
              )}
              <ul className={styles.msgList}>
                {selected.messages.map((m, i) => (
                  <li key={`${m.channel}-${i}`} className={styles.msg}>
                    <div className={styles.msgHead}>
                      <span className={styles.channel}>{m.channel}</span>
                      <span className={styles.ts} data-num>
                        {tsLabel(m.tsKst)}
                      </span>
                    </div>
                    <p className={styles.msgText}>{highlight(m.text, [selected.title])}</p>
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
