import { useEffect } from "react";
import { useT } from "../../i18n";
import type { ScoreEntry } from "../../types";
import {
  EM_DASH,
  fmtCompact,
  fmtMoney,
  fmtNumber,
  fmtPct,
  fmtPctPlain,
  fmtPrice,
  signClass,
} from "../../format";
import { GradeBadge } from "../badges/GradeBadge";
import { MarketBadge } from "../badges/MarketBadge";
import { SellBadge } from "../badges/SellBadge";
import { ScoreGauge } from "../ScoreGauge";
import { FactorBars } from "./FactorBars";
import { InvestorFlowBars } from "./InvestorFlowBars";
import styles from "./DetailDrawer.module.css";

interface Props {
  entry: ScoreEntry | null;
  onClose: () => void;
}

interface FieldProps {
  label: string;
  value: React.ReactNode;
  sign?: "up" | "down" | "flat";
  mono?: boolean;
}

function Field({ label, value, sign, mono }: FieldProps) {
  return (
    <div className={styles.field}>
      <dt className={styles.fieldLabel}>{label}</dt>
      <dd
        className={`${styles.fieldValue} ${mono ? styles.mono : ""} ${
          sign ? styles[sign] : ""
        }`}
      >
        {value}
      </dd>
    </div>
  );
}

export function DetailDrawer({ entry, onClose }: Props) {
  const t = useT();
  const open = entry !== null;

  // Close on Escape; lock body scroll while open.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!entry) return null;

  const changeOpenSign = signClass(entry.changeFromOpenPct);
  const changeSign = signClass(entry.changePct);
  const returnSign = signClass(entry.return1yPct);

  return (
    <div className={styles.overlay} onClick={onClose}>
      <aside
        className={styles.drawer}
        role="dialog"
        aria-modal="true"
        aria-label={t("drawer.title")}
        onClick={(e) => e.stopPropagation()}
      >
        <header className={styles.header}>
          <div className={styles.titleArea}>
            <div className={styles.nameRow}>
              <MarketBadge market={entry.market} />
              <h2 className={styles.name}>{entry.name}</h2>
              <span className={styles.code}>{entry.ticker}</span>
            </div>
            <div className={styles.badges}>
              <GradeBadge grade={entry.grade} />
              {entry.sellAlert && <SellBadge reason={entry.sellReason} />}
            </div>
            {entry.themes.length > 0 && (
              <div className={styles.themes}>
                {entry.themes.map((th) => (
                  <span key={th} className={styles.themeChip}>
                    {th}
                  </span>
                ))}
              </div>
            )}
          </div>
          <button
            type="button"
            className={styles.close}
            onClick={onClose}
            aria-label={t("drawer.close")}
          >
            ✕
          </button>
        </header>

        <div className={styles.scoreBlock}>
          <span className={styles.scoreLabel}>{t("field.score")}</span>
          <ScoreGauge
            score={entry.score}
            alert={entry.grade === "sell" || entry.sellAlert}
          />
          <span
            className={`${styles.eligible} ${
              entry.eligible ? styles.elig : styles.inelig
            }`}
          >
            {entry.eligible ? t("value.eligible.yes") : t("value.eligible.no")}
          </span>
        </div>

        <div className={styles.body}>
          <Section title={t("drawer.section.quote")}>
            <dl className={styles.grid}>
              <Field
                label={t("field.price")}
                value={fmtPrice(entry.price, entry.market)}
                mono
              />
              <Field
                label={t("field.openPrice")}
                value={fmtPrice(entry.openPrice, entry.market)}
                mono
              />
              <Field
                label={t("field.changeFromOpen")}
                value={fmtPct(entry.changeFromOpenPct)}
                sign={changeOpenSign}
                mono
              />
              <Field
                label={t("field.changePct")}
                value={fmtPct(entry.changePct)}
                sign={changeSign}
                mono
              />
              <Field
                label={t("field.volume")}
                value={entry.volume === null ? EM_DASH : fmtCompact(entry.volume)}
                mono
              />
              <Field
                label={t("field.turnover")}
                value={
                  entry.turnover === null
                    ? EM_DASH
                    : fmtMoney(entry.turnover, entry.market)
                }
                mono
              />
              <Field
                label={t("field.marketCap")}
                value={
                  entry.marketCap === null
                    ? EM_DASH
                    : fmtMoney(entry.marketCap, entry.market)
                }
                mono
              />
            </dl>
          </Section>

          <Section title={t("drawer.section.stats")}>
            <dl className={styles.grid}>
              <Field
                label={t("field.w52High")}
                value={fmtPrice(entry.w52High, entry.market)}
                mono
              />
              <Field
                label={t("field.w52Low")}
                value={fmtPrice(entry.w52Low, entry.market)}
                mono
              />
              <Field
                label={t("field.near52w")}
                value={fmtPctPlain(entry.near52wPct)}
                mono
              />
              <Field
                label={t("field.return1y")}
                value={fmtPct(entry.return1yPct)}
                sign={returnSign}
                mono
              />
            </dl>
          </Section>

          <Section title={t("drawer.section.fundamentals")}>
            <dl className={styles.grid}>
              <Field label={t("field.per")} value={fmtNumber(entry.per, 2)} mono />
              <Field label={t("field.pbr")} value={fmtNumber(entry.pbr, 2)} mono />
              <Field label={t("field.eps")} value={fmtNumber(entry.eps, 2)} mono />
            </dl>
          </Section>

          <Section title={t("drawer.section.classification")}>
            <dl className={styles.grid}>
              <Field label={t("field.sector")} value={entry.sector ?? EM_DASH} />
              <Field label={t("field.industry")} value={entry.industry ?? EM_DASH} />
            </dl>
          </Section>

          <Section title={t("drawer.section.investorFlow")}>
            <InvestorFlowBars flow={entry.investorFlow} market={entry.market} />
          </Section>

          <Section title={t("drawer.section.factors")}>
            <FactorBars factors={entry.factors} />
          </Section>

          <Section title={t("drawer.section.stops")}>
            <dl className={styles.grid}>
              <Field
                label={t("field.stopPrice")}
                value={
                  entry.stopPrice === null ? (
                    <span
                      className={styles.stopNotSet}
                      title={t("stop.notSet.hint")}
                    >
                      {t("stop.notSet")}
                    </span>
                  ) : (
                    fmtPrice(entry.stopPrice, entry.market)
                  )
                }
                mono={entry.stopPrice !== null}
              />
              <Field
                label={t("field.trailingPeak")}
                value={fmtPrice(entry.trailingPeak, entry.market)}
                mono
              />
              <Field
                label={t("field.ma200")}
                value={fmtPrice(entry.ma200, entry.market)}
                mono
              />
              <Field
                label={t("field.aboveMa200")}
                value={
                  entry.factors
                    ? entry.factors.aboveMa200
                      ? t("value.yes")
                      : t("value.no")
                    : EM_DASH
                }
              />
            </dl>
          </Section>

          {entry.rationale && (
            <Section title={t("drawer.section.rationale")}>
              <p className={styles.rationale}>{entry.rationale}</p>
            </Section>
          )}

          <p className={styles.disclaimer}>{t("disclaimer.text")}</p>
        </div>
      </aside>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className={styles.section}>
      <h3 className={styles.sectionTitle}>{title}</h3>
      {children}
    </section>
  );
}
