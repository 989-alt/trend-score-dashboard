import { useT } from "../i18n";
import { fmtNumber, fmtPct, signClass } from "../format";
import type {
  NavPoint,
  TradingOrder,
  TradingPosition,
  TradingStatus,
} from "../types";
import styles from "./TradingView.module.css";

interface Props {
  status: TradingStatus | null;
  positions: TradingPosition[];
  orders: TradingOrder[];
  nav: NavPoint[];
}

/** ts_kst-style ISO → tz-stable "MM-DD HH:mm" (mirrors NewsView.tsLabel). */
function tsLabel(iso: string): string {
  if (iso.length < 16) return iso;
  return `${iso.slice(5, 10)} ${iso.slice(11, 16)}`;
}

/** Won amount with the localized unit suffix; em dash when null. */
function won(v: number | null, t: ReturnType<typeof useT>): string {
  if (v === null) return "—";
  return `${fmtNumber(v, 0)}${t("unit.won")}`;
}

const SPARK_W = 320;
const SPARK_H = 56;

/**
 * Cumulative-return sparkline from a NAV series, normalized to the first point.
 * Returns the polyline points + the latest cumulative return (%), or null when
 * there are fewer than 2 usable points. NO chart library — plain inline SVG.
 * TODO(P6+): benchmark overlay vs KOSPI/KOSDAQ needs an index-series endpoint
 * (out of scope here).
 */
function navSpark(
  nav: NavPoint[],
): { points: string; returnPct: number; up: boolean } | null {
  const series = nav
    .map((p) => p.totalEval)
    .filter((v): v is number => v !== null && Number.isFinite(v));
  if (series.length < 2) return null;
  const first = series[0];
  if (first === 0) return null;
  const rets = series.map((v) => (v / first - 1) * 100);
  const min = Math.min(...rets);
  const max = Math.max(...rets);
  const span = max - min || 1;
  const stepX = SPARK_W / (rets.length - 1);
  const points = rets
    .map((r, i) => {
      const x = i * stepX;
      // Invert y (SVG origin top-left); pad 2px top/bottom.
      const y = SPARK_H - 2 - ((r - min) / span) * (SPARK_H - 4);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const returnPct = rets[rets.length - 1];
  return { points, returnPct, up: returnPct >= 0 };
}

function StatusHeader({ status }: { status: TradingStatus | null }) {
  const t = useT();
  const running = status?.running ?? false;
  const pnl = status?.totalPnl ?? null;
  const pnlSign = signClass(pnl);
  return (
    <div className={styles.statusCard}>
      <div className={styles.statusTop}>
        <h2 className={styles.statusTitle}>{t("trading.title")}</h2>
        <span
          className={`${styles.runChip} ${running ? styles.run : styles.stop}`}
        >
          <span className={styles.runDot} aria-hidden="true" />
          {running ? t("trading.running") : t("trading.stopped")}
        </span>
      </div>
      <dl className={styles.metrics}>
        <div className={styles.metric}>
          <dt className={styles.metricLabel}>{t("trading.totalEval")}</dt>
          <dd className={styles.metricValue} data-num>
            {won(status?.totalEval ?? null, t)}
          </dd>
        </div>
        <div className={styles.metric}>
          <dt className={styles.metricLabel}>{t("trading.cash")}</dt>
          <dd className={styles.metricValue} data-num>
            {won(status?.cash ?? null, t)}
          </dd>
        </div>
        <div className={styles.metric}>
          <dt className={styles.metricLabel}>{t("trading.totalPnl")}</dt>
          <dd className={`${styles.metricValue} ${styles[pnlSign]}`} data-num>
            {pnl === null ? "—" : `${pnl > 0 ? "+" : ""}${won(pnl, t)}`}
          </dd>
        </div>
        <div className={styles.metric}>
          <dt className={styles.metricLabel}>{t("trading.positionCount")}</dt>
          <dd className={styles.metricValue} data-num>
            {status?.positionCount ?? 0}
          </dd>
        </div>
      </dl>
      {status?.asOf && (
        <div className={styles.asOf} data-num>
          {t("trading.asOf")} {tsLabel(status.asOf)}
        </div>
      )}
    </div>
  );
}

function NavCard({ nav }: { nav: NavPoint[] }) {
  const t = useT();
  const spark = navSpark(nav);
  return (
    <div className={styles.navCard}>
      <div className={styles.navHead}>
        <h3 className={styles.cardTitle}>{t("trading.navCurve")}</h3>
        {spark && (
          <span
            className={`${styles.navReturn} ${spark.up ? styles.up : styles.down}`}
            data-num
          >
            {fmtPct(spark.returnPct)}
          </span>
        )}
      </div>
      {spark ? (
        <svg
          className={styles.spark}
          viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`${t("trading.return")} ${fmtPct(spark.returnPct)}`}
        >
          <polyline
            points={spark.points}
            fill="none"
            stroke={spark.up ? "var(--up)" : "var(--down)"}
            strokeWidth="2"
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      ) : (
        <p className={styles.empty}>{t("trading.empty")}</p>
      )}
    </div>
  );
}

function PositionsTable({ positions }: { positions: TradingPosition[] }) {
  const t = useT();
  return (
    <div className={styles.card}>
      <h3 className={styles.cardTitle}>{t("trading.positions")}</h3>
      {positions.length === 0 ? (
        <p className={styles.empty}>{t("trading.empty")}</p>
      ) : (
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">{t("trading.col.ticker")}</th>
              <th scope="col" className={styles.numCol}>
                {t("trading.col.qty")}
              </th>
              <th scope="col" className={styles.numCol}>
                {t("trading.col.avgPrice")}
              </th>
              <th scope="col" className={styles.numCol}>
                {t("trading.col.curPrice")}
              </th>
              <th scope="col" className={styles.numCol}>
                {t("trading.col.pnl")}
              </th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => {
              const sign = signClass(p.pnlAmount);
              return (
                <tr key={p.ticker}>
                  <td>
                    <span className={styles.posName}>{p.name}</span>
                    <span className={styles.posTicker} data-num>
                      {p.ticker}
                    </span>
                  </td>
                  <td className={styles.numCol} data-num>
                    {fmtNumber(p.qty, 0)}
                  </td>
                  <td className={styles.numCol} data-num>
                    {won(p.avgPrice, t)}
                  </td>
                  <td className={styles.numCol} data-num>
                    {won(p.curPrice, t)}
                  </td>
                  <td className={`${styles.numCol} ${styles[sign]}`} data-num>
                    {p.pnlAmount === null
                      ? "—"
                      : `${p.pnlAmount > 0 ? "+" : ""}${won(p.pnlAmount, t)}`}
                    {p.pnlPct !== null && (
                      <span className={styles.pnlPct}> ({fmtPct(p.pnlPct)})</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function OrdersTimeline({ orders }: { orders: TradingOrder[] }) {
  const t = useT();
  const sideLabel = (side: string): string =>
    side.toLowerCase() === "sell" ? t("trading.side.sell") : t("trading.side.buy");
  return (
    <div className={styles.card}>
      <h3 className={styles.cardTitle}>{t("trading.orders")}</h3>
      {orders.length === 0 ? (
        <p className={styles.empty}>{t("trading.empty")}</p>
      ) : (
        <ul className={styles.timeline}>
          {orders.map((o, i) => {
            const isSell = o.side.toLowerCase() === "sell";
            return (
              <li key={`${o.ts}-${o.ticker}-${i}`} className={styles.order}>
                <span className={styles.orderTs} data-num>
                  {tsLabel(o.ts)}
                </span>
                <span
                  className={`${styles.sideChip} ${isSell ? styles.sell : styles.buy}`}
                >
                  {sideLabel(o.side)}
                </span>
                <span className={styles.orderTicker} data-num>
                  {o.ticker}
                </span>
                <span className={styles.orderQty} data-num>
                  {fmtNumber(o.qty, 0)}
                </span>
                <span className={styles.orderReason}>{o.reason}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/** 읽기전용 "매매 현황" 탭: 가동 상태 헤더 + NAV 누적수익률 + 보유 포지션 + 최근 주문. */
export function TradingView({ status, positions, orders, nav }: Props) {
  const t = useT();
  const empty =
    !status && positions.length === 0 && orders.length === 0 && nav.length === 0;

  return (
    <section className={styles.tradingView}>
      <div className={styles.note} role="note">
        <span className={styles.noteIcon} aria-hidden="true">
          ℹ
        </span>
        {t("disclaimer.text")}
      </div>

      {empty ? (
        <p className={styles.emptyBig}>{t("trading.empty")}</p>
      ) : (
        <>
          <StatusHeader status={status} />
          <NavCard nav={nav} />
          <PositionsTable positions={positions} />
          <OrdersTimeline orders={orders} />
        </>
      )}
    </section>
  );
}
