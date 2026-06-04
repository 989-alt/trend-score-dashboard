import { useCallback, useMemo, useState } from "react";
import { fetchSnapshot, fetchThemes } from "./api";
import type { Market, ScoreEntry } from "./types";
import { usePolling } from "./hooks/usePolling";
import { DemoBanner } from "./components/DemoBanner";
import { Header } from "./components/Header";
import {
  MarketTabs,
  TABPANEL_ID,
  tabId,
  type TabKey,
} from "./components/MarketTabs";
import { SellAlertLane } from "./components/SellAlertLane";
import { RankingTable } from "./components/RankingTable";
import { ThemeBoard } from "./components/ThemeBoard";
import { DetailDrawer } from "./components/drawer/DetailDrawer";
import { CountsStrip } from "./components/CountsStrip";
import { Footer } from "./components/Footer";
import { ErrorView, LoadingView } from "./components/StateViews";
import styles from "./App.module.css";

const POLL_MS = 30_000;

function tabToMarket(tab: TabKey): Market | null {
  if (tab === "kr") return "KR";
  if (tab === "us") return "US";
  return null;
}

export function App() {
  const [tab, setTab] = useState<TabKey>("themes");
  const [selected, setSelected] = useState<ScoreEntry | null>(null);

  const market = tabToMarket(tab);
  const isMarketTab = market !== null;

  // Snapshot poll — enabled only on KR/US tabs, keyed by market.
  const snapshotFetcher = useCallback(
    (signal: AbortSignal) => fetchSnapshot(market ?? "KR", signal),
    [market],
  );
  const snapshot = usePolling(snapshotFetcher, [market ?? "none"], {
    intervalMs: POLL_MS,
    enabled: isMarketTab,
  });

  // Themes poll — enabled only on the themes tab.
  const themesFetcher = useCallback(
    (signal: AbortSignal) => fetchThemes(signal),
    [],
  );
  const themes = usePolling(themesFetcher, ["themes"], {
    intervalMs: POLL_MS,
    enabled: !isMarketTab,
  });

  const active = isMarketTab ? snapshot : themes;

  // Header indicators derived from whichever resource is active.
  const marketOpen: boolean | null = useMemo(() => {
    if (isMarketTab) return snapshot.data?.marketOpen ?? null;
    if (themes.data && market === null) {
      // Themes view: no single market. Hide the indicator.
      return null;
    }
    return null;
  }, [isMarketTab, snapshot.data, themes.data, market]);

  const nextRefreshAt = isMarketTab ? (snapshot.data?.nextRefreshAt ?? null) : null;

  // All entries flowing through the active view, for the sell-alert lane.
  const sellAlertEntries: ScoreEntry[] = useMemo(() => {
    if (isMarketTab) return snapshot.data?.entries ?? [];
    if (!themes.data) return [];
    const seen = new Set<string>();
    const out: ScoreEntry[] = [];
    for (const th of themes.data.themes) {
      for (const e of th.leaders) {
        const key = `${e.market}-${e.ticker}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push(e);
      }
    }
    return out;
  }, [isMarketTab, snapshot.data, themes.data]);

  const showInitialLoading = active.loading && !active.data;
  const showError = !!active.error && !active.data;

  return (
    <div className={styles.app}>
      <DemoBanner />
      <div className={styles.container}>
        <Header
          marketOpen={marketOpen}
          lastUpdated={active.lastUpdated}
          nextRefreshAt={nextRefreshAt}
          refreshing={active.refreshing}
          onRefresh={active.refresh}
        />

        <MarketTabs active={tab} onChange={setTab} />

        <SellAlertLane entries={sellAlertEntries} onSelect={setSelected} />

        {isMarketTab && snapshot.data && (
          <CountsStrip counts={snapshot.data.counts} />
        )}

        <main
          className={styles.main}
          id={TABPANEL_ID}
          role="tabpanel"
          aria-labelledby={tabId(tab)}
        >
          {showInitialLoading ? (
            <LoadingView />
          ) : showError ? (
            <ErrorView onRetry={active.refresh} />
          ) : isMarketTab ? (
            <RankingTable
              entries={snapshot.data?.entries ?? []}
              onSelect={setSelected}
            />
          ) : (
            <ThemeBoard
              themes={themes.data?.themes ?? []}
              onSelect={setSelected}
            />
          )}
        </main>

        <Footer />
      </div>

      <DetailDrawer entry={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
