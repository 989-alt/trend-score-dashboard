import { useState } from "react";
import { useT } from "../i18n";
import type { ScoreEntry } from "../types";
import { MarketBadge } from "./badges/MarketBadge";
import { GradeBadge } from "./badges/GradeBadge";
import { SellBadge } from "./badges/SellBadge";
import styles from "./EntryLane.module.css";

const PREVIEW_COUNT = 6;

interface Props {
  /** Pre-filtered + sorted entries for this lane (caller decides the set). */
  entries: ScoreEntry[];
  /** "buy" = green recommendation lane · "sell" = red alert lane. */
  variant: "buy" | "sell";
  title: string;
  desc: string;
  icon: string;
  onSelect: (entry: ScoreEntry) => void;
}

/**
 * Collapsible chip lane shared by the buy-recommendation and sell-alert rails.
 * Shows a {@link PREVIEW_COUNT}-chip preview with the total count in the header;
 * a toggle reveals the rest. Renders nothing when empty so it never occupies
 * space without a reason.
 */
export function EntryLane({ entries, variant, title, desc, icon, onSelect }: Props) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  if (entries.length === 0) return null;

  const visible = expanded ? entries : entries.slice(0, PREVIEW_COUNT);
  const hidden = entries.length - PREVIEW_COUNT;

  return (
    <section className={`${styles.lane} ${styles[variant]}`} aria-label={title}>
      <div className={styles.head}>
        <span className={styles.icon} aria-hidden="true">
          {icon}
        </span>
        <div>
          <h2 className={styles.title}>{title}</h2>
          <p className={styles.desc}>{desc}</p>
        </div>
        <span className={styles.count}>{entries.length}</span>
      </div>
      <div className={styles.chips}>
        {visible.map((e) => (
          <button
            key={`${e.market}-${e.ticker}`}
            type="button"
            className={styles.chip}
            onClick={() => onSelect(e)}
          >
            <MarketBadge market={e.market} />
            <span className={styles.name}>{e.name}</span>
            <span className={styles.ticker}>{e.ticker}</span>
            {variant === "buy" ? (
              <GradeBadge grade={e.grade} size="sm" />
            ) : (
              <SellBadge reason={e.sellReason} size="sm" />
            )}
          </button>
        ))}
      </div>
      {entries.length > PREVIEW_COUNT && (
        <button
          type="button"
          className={styles.toggle}
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
        >
          {expanded ? t("lane.collapse") : t("lane.expand").replace("{n}", String(hidden))}
        </button>
      )}
    </section>
  );
}
