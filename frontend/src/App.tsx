import { useCallback, useMemo, useState } from "react";
import { fetchIssues, fetchSnapshot, fetchThemes, fetchTicker } from "./api";
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
import { EntryLane } from "./components/EntryLane";
import { RankingTable } from "./components/RankingTable";
import { ThemeBoard } from "./components/ThemeBoard";
import { IssueBoard } from "./components/IssueBoard";
import { DetailDrawer } from "./components/drawer/DetailDrawer";
import { CountsStrip } from "./components/CountsStrip";
import { Footer } from "./components/Footer";
import { ErrorView, LoadingView } from "./components/StateViews";
import { useT } from "./i18n";
import styles from "./App.module.css";

const POLL_MS = 30_000;

function tabToMarket(tab: TabKey): Market | null {
  if (tab === "kr") return "KR";
  if (tab === "us") return "US";
  return null;
}

export function App() {
  const t = useT();
  const [tab, setTab] = useState<TabKey>("themes");
  const [selected, setSelected] = useState<ScoreEntry | null>(null);

  const market = tabToMarket(tab);
  const isMarketTab = market !== null;
  const isIssuesTab = tab === "issues";
  const isThemesTab = tab === "themes";

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
  const themesFetcher = useCallback((signal: AbortSignal) => fetchThemes(signal), []);
  const themes = usePolling(themesFetcher, ["themes"], {
    intervalMs: POLL_MS,
    enabled: isThemesTab,
  });

  // Issues poll — enabled only on the issues tab.
  const issuesFetcher = useCallback((signal: AbortSignal) => fetchIssues(signal), []);
  const issues = usePolling(issuesFetcher, ["issues"], {
    intervalMs: POLL_MS,
    enabled: isIssuesTab,
  });

  const active = isMarketTab ? snapshot : isIssuesTab ? issues : themes;

  // Header indicators — market open / next refresh apply only to KR/US snapshots.
  const marketOpen: boolean | null = useMemo(() => {
    if (isMarketTab) return snapshot.data?.marketOpen ?? null;
    return null;
  }, [isMarketTab, snapshot.data]);

  const nextRefreshAt = isMarketTab ? (snapshot.data?.nextRefreshAt ?? null) : null;

  // Buy/sell lanes feed off the market or themes view; the issues tab has none.
  const laneEntries: ScoreEntry[] = useMemo(() => {
    if (isMarketTab) return snapshot.data?.entries ?? [];
    if (isThemesTab && themes.data) {
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
    }
    return [];
  }, [isMarketTab, isThemesTab, snapshot.data, themes.data]);

  // Buy recommendations: buy / strong-buy grades, highest score first.
  const buyEntries = useMemo(
    () =>
      laneEntries
        .filter((e) => e.grade === "strong_buy" || e.grade === "buy")
        .sort((a, b) => b.score - a.score),
    [laneEntries],
  );
  // Sell alerts: entries whose trailing-stop / MA200 condition fired.
  const sellEntries = useMemo(
    () => laneEntries.filter((e) => e.sellAlert),
    [laneEntries],
  );

  // Issue → detail: a ticker row fetches its ScoreEntry and opens the drawer.
  const handleSelectTicker = useCallback(async (m: Market, code: string) => {
    const entry = await fetchTicker(m, code);
    if (entry) setSelected(entry);
  }, []);

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

        {!isIssuesTab && (
          <>
            <EntryLane
              variant="buy"
              entries={buyEntries}
              title={t("buyLane.title")}
              desc={t("buyLane.desc")}
              icon="▲"
              onSelect={setSelected}
            />
            <EntryLane
              variant="sell"
              entries={sellEntries}
              title={t("sellAlert.lane.title")}
              desc={t("sellAlert.lane.desc")}
              icon="⚠"
              onSelect={setSelected}
            />
          </>
        )}

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
          ) : isIssuesTab ? (
            <IssueBoard
              issues={issues.data?.issues ?? []}
              windowHours={issues.data?.windowHours ?? 24}
              onSelectTicker={handleSelectTicker}
              onSelectTheme={() => setTab("themes")}
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
