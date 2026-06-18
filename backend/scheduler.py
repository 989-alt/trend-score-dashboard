"""APScheduler 잡 — 일일 prep + intraday 갱신 (market_hours 게이트).

원칙:
- 스케줄러 timezone 은 ``Asia/Seoul`` 기준(표시·트리거 일관성). 모든 ``now`` 는 tz-aware.
- intraday 잡은 ``market_hours.is_market_open`` 으로 게이트해 폐장·휴장 시 산출을 건너뛴다.
- 일일 prep 은 provider 캐시 워밍 겸 ``score_market`` 1회(장 개장 전 갱신).
- ``refresh_now`` 는 스케줄러와 무관한 동기 1회 산출(초기 부팅·수동 호출용).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend import market_hours
from backend.config import Settings
from backend.engine import score_market
from backend.market_data import get_provider
from backend.news.collector import collect_once
from backend.news.factset import collect_factset
from backend.news.rss import collect_rss_once
from backend.news.store import NewsStore
from backend.news.summary import summarize_week
from backend.schemas import Market, Snapshot
from backend.store import Store
from backend.themes import load_themes

#: 스케줄러·잡 기준 시간대. 모든 트리거·``now`` 가 이 TZ 를 따른다.
_SEOUL = ZoneInfo("Asia/Seoul")

#: 일일 prep 시각(KST). US 는 개장 30분 전을 단순화해 22:00 KST 로 둔다.
_PREP_HOUR: dict[Market, int] = {"KR": 8, "US": 22}
_PREP_MINUTE: dict[Market, int] = {"KR": 30, "US": 0}


def refresh_now(market: Market, store: Store, settings: Settings) -> Snapshot:
    """``market`` 을 동기로 1회 산출·저장하고 ``Snapshot`` 을 반환(초기/수동).

    ``now`` 는 ``Asia/Seoul`` tz-aware 로 고정한다. ``score_market`` 내부에서
    ``store.save_snapshot`` 으로 영속되므로 별도 저장은 하지 않는다.
    """
    now = datetime.now(tz=_SEOUL)
    themes = load_themes(settings.themes_path)
    provider = get_provider(settings)
    return score_market(market, provider, store, settings, now, themes=themes)


def _run_intraday(market: Market, store: Store, settings: Settings) -> None:
    """intraday 잡 본체 — 장중일 때만 산출. 폐장·휴장이면 조용히 건너뛴다.

    일봉·펀더멘털은 prep 가 채운 일1회 캐시를 재사용(FIX-C) → 시세만 신선하게 받아 빠르고
    Yahoo 부하 최소. (캐시 미스 종목은 ``get_daily_ohlcv`` 가 per-ticker 폴백으로 흡수.)
    """
    now = datetime.now(tz=_SEOUL)
    if not market_hours.is_market_open(market, now):
        return
    refresh_now(market, store, settings)


def prep_now(market: Market, store: Store, settings: Settings) -> Snapshot:
    """일봉·펀더멘털 캐시 워밍(배치) 후 1회 산출·저장(FIX-C). 초기 부팅·prep 잡 공용.

    ``provider.prepare_daily`` 가 US 는 ``yf.download`` 배치로 일봉 캐시를 일괄 채워 이후
    intraday 가 Yahoo 를 거의 안 타게 한다. 그 뒤 ``score_market`` 으로 prep 스냅샷 산출.
    """
    provider = get_provider(settings)
    universe = provider.list_universe(market)
    provider.prepare_daily(universe, market)
    now = datetime.now(tz=_SEOUL)
    themes = load_themes(settings.themes_path)
    return score_market(market, provider, store, settings, now, themes=themes)


def _run_prep(market: Market, store: Store, settings: Settings) -> None:
    """일일 prep 잡 본체 — ``prep_now`` 위임(배치 워밍 후 산출)."""
    prep_now(market, store, settings)


async def _run_news_poll(news_store: NewsStore, settings: Settings) -> None:
    """텔레그램 catch-up 폴링(주기=news_poll_interval_min, fail-open 은 collect_once 내부)."""
    await collect_once(news_store, settings)


async def _run_news_rss(news_store: NewsStore, settings: Settings) -> None:
    """RSS 크롤링(동기 feedparser → to_thread). 키 불필요·피드별 fail-open."""
    await asyncio.to_thread(collect_rss_once, news_store, settings)


async def _run_news_weekly(news_store: NewsStore, settings: Settings) -> None:
    """토요일 — FactSet RSS 수집 후 Gemini 주간요약(둘 다 동기→to_thread)."""
    await asyncio.to_thread(collect_factset, news_store, settings)
    await asyncio.to_thread(summarize_week, news_store, settings)


def build_scheduler(
    store: Store, settings: Settings, news_store: NewsStore | None = None
) -> AsyncIOScheduler:
    """``AsyncIOScheduler`` 를 만들어 잡을 등록한 뒤 반환(미시작 상태).

    등록 잡(시장별):
    - 일일 prep: KR 08:30 / US 22:00 KST 에 ``score_market`` 1회(캐시 워밍).
    - intraday: ``settings.refresh_interval_min`` (기본 30분) 주기. ``market_hours``
      로 장중에만 실제 산출.

    호출 측은 반환된 스케줄러의 ``.start()`` 만 호출하면 된다.
    """
    scheduler = AsyncIOScheduler(timezone=_SEOUL)
    markets: tuple[Market, ...] = ("KR", "US")

    for market in markets:
        scheduler.add_job(
            _run_prep,
            CronTrigger(
                hour=_PREP_HOUR[market],
                minute=_PREP_MINUTE[market],
                timezone=_SEOUL,
            ),
            args=(market, store, settings),
            id=f"prep-{market}",
        )
        scheduler.add_job(
            _run_intraday,
            IntervalTrigger(minutes=settings.refresh_interval_min, timezone=_SEOUL),
            args=(market, store, settings),
            id=f"intraday-{market}",
        )

    # ── 뉴스 잡 (news_store 주입 시) ───────────────────────────────────
    if news_store is not None:
        # RSS 크롤링 — 키 불필요, 항상 등록.
        scheduler.add_job(
            _run_news_rss,
            IntervalTrigger(minutes=settings.news_rss_interval_min, timezone=_SEOUL),
            args=(news_store, settings),
            id="news-rss",
        )
        # 텔레그램 폴링·주간요약 — APP_API 키 있을 때만.
        if settings.app_api_id and settings.app_api_hash:
            scheduler.add_job(
                _run_news_poll,
                IntervalTrigger(minutes=settings.news_poll_interval_min, timezone=_SEOUL),
                args=(news_store, settings),
                id="news-poll",
            )
            scheduler.add_job(
                _run_news_weekly,
                CronTrigger(day_of_week="sat", hour=8, minute=0, timezone=_SEOUL),
                args=(news_store, settings),
                id="news-weekly",
            )

    return scheduler


__all__ = ["build_scheduler", "prep_now", "refresh_now"]
