import { useCallback, useMemo, useState } from "react";
import {
  fetchNewsIssues,
  fetchNewsWeekly,
  fetchSnapshot,
  fetchThemes,
  fetchTicker,
} from "./api";
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
import { NewsView } from "./components/NewsView";
import { IssueRail } from "./components/IssueRail";
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
  const [newsIssueKey, setNewsIssueKey] = useState<string | null>(null);

  const market = tabToMarket(tab);
  const isMarketTab = market !== null;
  const isNewsTab = tab === "news";

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
    enabled: tab === "themes",
  });

  // News polls — enabled only on the situation tab. Weekly summary polls slowly.
  const newsFetcher = useCallback((signal: AbortSignal) => fetchNewsIssues(signal), []);
  // Polled on every tab: the 긴급 이슈 rail appears on themes/kr/us too (not just 시황).
  const news = usePolling(newsFetcher, ["news"], {
    intervalMs: POLL_MS,
    enabled: true,
  });
  const weeklyFetcher = useCallback((signal: AbortSignal) => fetchNewsWeekly(signal), []);
  const weekly = usePolling(weeklyFetcher, ["weekly"], {
    intervalMs: 5 * 60_000,
    enabled: isNewsTab,
  });

  const active = isMarketTab ? snapshot : isNewsTab ? news : themes;

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

  // All entries flowing through the active view, feeding the buy + sell lanes.
  const laneEntries: ScoreEntry[] = useMemo(() => {
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

  // News issue → detail: a stock-keyed issue fetches its ScoreEntry → opens drawer.
  const handleSelectTicker = useCallback(async (m: Market, code: string) => {
    const entry = await fetchTicker(m, code);
    if (entry) setSelected(entry);
  }, []);

  // 긴급 이슈 레일(다른 탭) 클릭 → 시황 탭으로 점프 + 해당 이슈 선택.
  const openIssue = useCallback((key: string) => {
    setNewsIssueKey(key);
    setTab("news");
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

        {!isNewsTab && (
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
          {isNewsTab ? (
            showInitialLoading ? (
              <LoadingView />
            ) : showError ? (
              <ErrorView onRetry={active.refresh} />
            ) : (
              <NewsView
                data={news.data}
                weekly={weekly.data}
                onSelectTicker={handleSelectTicker}
                selectedKey={newsIssueKey}
                onSelectKey={setNewsIssueKey}
              />
            )
          ) : showInitialLoading ? (
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

      {!isNewsTab && (
        <IssueRail issues={news.data?.issues ?? []} onOpen={openIssue} />
      )}

      <DetailDrawer entry={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
