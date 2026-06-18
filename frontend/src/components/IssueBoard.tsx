import { useT } from "../i18n";
import type { IssueEntry, Market } from "../types";
import { fmtClock } from "../format";
import { GradeBadge } from "./badges/GradeBadge";
import { MarketBadge } from "./badges/MarketBadge";
import styles from "./IssueBoard.module.css";

interface Props {
  issues: IssueEntry[];
  windowHours: number;
  onSelectTicker: (market: Market, code: string) => void;
  onSelectTheme: () => void;
}

interface RowProps {
  rank: number;
  issue: IssueEntry;
  onSelectTicker: (market: Market, code: string) => void;
  onSelectTheme: () => void;
}

/** One ranked issue row — name + badges + sample headlines + spike/mention metrics. */
function IssueRow({ rank, issue, onSelectTicker, onSelectTheme }: RowProps) {
  const t = useT();
  const isTicker = issue.kind === "ticker";
  const open = () => {
    if (isTicker && issue.market) onSelectTicker(issue.market, issue.key);
    else if (!isTicker) onSelectTheme();
  };
  return (
    <li className={styles.row}>
      <span className={styles.rank} data-num aria-hidden="true">
        {rank}
      </span>
      <div className={styles.body}>
        <div className={styles.titleRow}>
          <button type="button" className={styles.name} onClick={open}>
            {issue.name}
          </button>
          {isTicker && issue.market ? (
            <MarketBadge market={issue.market} />
          ) : (
            <span className={styles.themeTag}>{t("issues.kind.theme")}</span>
          )}
          {isTicker && issue.grade && <GradeBadge grade={issue.grade} size="sm" />}
        </div>
        {issue.headlines.length > 0 && (
          <ul className={styles.headlines}>
            {issue.headlines.map((h) => (
              <li key={h.url ?? h.title} className={styles.headlineItem}>
                {h.url ? (
                  <a
                    className={styles.headline}
                    href={h.url}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    {h.title}
                  </a>
                ) : (
                  <span className={styles.headline}>{h.title}</span>
                )}
                <span className={styles.src}>
                  {h.source}
                  {h.publishedAt ? ` · ${fmtClock(h.publishedAt)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className={styles.metrics}>
        <span className={styles.spike} data-num title={t("issues.spike.hint")}>
          ×{issue.spike.toFixed(2)}
        </span>
        <span className={styles.mentions} data-num>
          {t("issues.mentions", { n: issue.mentionCount })}
        </span>
      </div>
    </li>
  );
}

/**
 * Real-time issue ranking — tickers/themes sorted by mention surge (spike).
 * Ticker rows open the detail drawer; theme rows jump to the themes tab.
 */
export function IssueBoard({ issues, windowHours, onSelectTicker, onSelectTheme }: Props) {
  const t = useT();
  if (issues.length === 0) {
    return <div className={styles.empty}>{t("issues.empty")}</div>;
  }
  return (
    <section>
      <p className={styles.caption}>{t("issues.caption", { hours: windowHours })}</p>
      <ol className={styles.list}>
        {issues.map((issue, i) => (
          <IssueRow
            key={`${issue.kind}-${issue.key}`}
            rank={i + 1}
            issue={issue}
            onSelectTicker={onSelectTicker}
            onSelectTheme={onSelectTheme}
          />
        ))}
      </ol>
    </section>
  );
}
