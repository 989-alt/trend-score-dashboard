import { useT } from "../i18n";
import type { MergedTheme, ScoreEntry } from "../types";
import { fmtPct, signClass } from "../format";
import { GradeBadge } from "./badges/GradeBadge";
import { MarketBadge } from "./badges/MarketBadge";
import { ScoreGauge } from "./ScoreGauge";
import styles from "./ThemeCard.module.css";

interface Props {
  theme: MergedTheme;
  onSelect: (entry: ScoreEntry) => void;
}

const MAX_VISIBLE = 5;

export function ThemeCard({ theme, onSelect }: Props) {
  const t = useT();
  const visible = theme.leaders.slice(0, MAX_VISIBLE);
  const moreCount = theme.leaders.length - visible.length;

  return (
    <article className={styles.card}>
      <header className={styles.head}>
        <div className={styles.titleRow}>
          <h3 className={styles.title}>{theme.theme}</h3>
          <div className={styles.markets}>
            {theme.markets.map((m) => (
              <MarketBadge key={m} market={m} />
            ))}
          </div>
        </div>
        {theme.sellCount > 0 && (
          <span className={styles.sellCount}>
            <span aria-hidden="true">⚠</span>
            {t("theme.sellCount", { n: theme.sellCount })}
          </span>
        )}
      </header>

      <ul className={styles.leaders}>
        {visible.map((e) => {
          const cs = signClass(e.changeFromOpenPct);
          return (
            <li key={`${e.market}-${e.ticker}`}>
              <button
                type="button"
                className={`${styles.leader} ${e.sellAlert ? styles.alert : ""}`}
                onClick={() => onSelect(e)}
              >
                <MarketBadge market={e.market} />
                <span className={styles.name}>{e.name}</span>
                <span className={`${styles.change} ${styles[cs]}`}>
                  {fmtPct(e.changeFromOpenPct)}
                </span>
                <span className={styles.gauge}>
                  <ScoreGauge score={e.score} compact />
                </span>
                <GradeBadge grade={e.grade} size="sm" />
              </button>
            </li>
          );
        })}
      </ul>

      {moreCount > 0 && (
        <p className={styles.more}>{t("theme.moreCount", { n: moreCount })}</p>
      )}
    </article>
  );
}
