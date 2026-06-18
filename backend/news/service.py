"""이슈 랭킹 오케스트레이션 — 수집 → 적재 → 렉시콘 → 랭킹 → 스냅샷 저장.

``score_market`` 의 뉴스판: 외부 수집/영속(``NewsStore``)과 순수 산출(``extract``/
``rank``)을 묶어 ``IssuesResponse`` 를 만들고 저장한다. 렉시콘은 ``Store`` 의 최신 스냅샷
entries 에서 구성하므로 별도 네트워크 호출이 없다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from backend.config import Settings
from backend.news.collect import collect_all
from backend.news.extract import build_lexicon
from backend.news.rank import rank_issues
from backend.news.sources import load_sources
from backend.news.store import NewsStore
from backend.schemas import IssueCounts, IssuesResponse, Market, ScoreEntry
from backend.store import Store
from backend.themes import load_themes

_MARKETS: tuple[Market, ...] = ("KR", "US")


def refresh_issues(
    store: Store, news_store: NewsStore, settings: Settings, now: datetime
) -> IssuesResponse:
    """1회 수집·산출해 ``IssuesResponse`` 를 만들고 ``news_store`` 에 저장.

    렉시콘은 최신 KR/US 스냅샷 entries(종목명→코드) + ``themes.yml`` 에서 구성한다.
    스냅샷이 아직 없으면(부팅 직후) 종목 매칭은 비고 테마 키워드만 작동한다(비차단).
    """
    sources = load_sources(settings.news_sources_path)
    items, sources_ok, sources_failed = collect_all(sources, settings, now)
    inserted = news_store.add_items(items)

    since = now - timedelta(days=settings.news_baseline_days)
    window_items = news_store.recent(since)

    entries_by_market: dict[Market, list[ScoreEntry]] = {}
    by_ticker: dict[str, ScoreEntry] = {}
    for market in _MARKETS:
        snap = store.load_snapshot(market)
        entries = snap.entries if snap is not None else []
        entries_by_market[market] = entries
        for entry in entries:
            by_ticker[entry.ticker] = entry

    lexicon = build_lexicon(entries_by_market, load_themes(settings.themes_path))
    issues, items_recent = rank_issues(window_items, lexicon, by_ticker, settings, now)

    response = IssuesResponse(
        generated_at=now,
        window_hours=settings.news_recent_hours,
        counts=IssueCounts(
            collected=inserted,
            items_recent=items_recent,
            sources_ok=sources_ok,
            sources_failed=sources_failed,
        ),
        issues=issues,
    )
    news_store.save_issues(response)
    return response


def empty_issues(settings: Settings, now: datetime) -> IssuesResponse:
    """아직 산출 전(초기/비활성) '준비 중' 빈 이슈 응답(비차단)."""
    return IssuesResponse(generated_at=now, window_hours=settings.news_recent_hours, issues=[])


__all__ = ["empty_issues", "refresh_issues"]
