"""FastAPI 앱 — 스냅샷·테마·종목 상세 API + 정적 SPA 서빙.

라우트:
- ``GET /healthz`` → ``HealthResponse`` (데이터 모드·최신 KR/US 스냅샷 시각).
- ``GET /api/snapshot?market=kr|us`` → ``Snapshot`` (없으면 '준비 중' 빈 스냅샷, 비차단).
- ``GET /api/themes`` → ``ThemesResponse`` (두 시장 스냅샷으로 테마별 주도주, 비차단).
- ``GET /api/ticker/{market}/{code}`` → ``ScoreEntry`` (저장 스냅샷에서 조회, 없으면 404).

lifespan(startup) 에서 ``Store`` 초기화·스케줄러 시작 후, 초기 KR/US 스캔은 **백그라운드
daemon 스레드**로 던지고 즉시 서빙을 시작한다(서버가 곧바로 응답). shutdown 에서 스케줄러
종료. ``frontend/dist`` 가 존재하면 SPA 로 정적 마운트(없으면 루트 JSON 안내 — API 전용).
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from typing import cast, get_args
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend import market_hours
from backend import scheduler as sched
from backend.config import ROOT_DIR, Settings, get_settings
from backend.engine import build_themes_response, ticker_detail
from backend.market_data import get_provider
from backend.news.api_models import (
    NewsIssue,
    NewsIssuesResponse,
    NewsMessage,
    WeeklyResponse,
)
from backend.news.issues import (
    Issue,
    StockMeta,
    build_issues,
    clean_text,
    group_by_layer,
    load_severity,
)
from backend.news.store import NewsStore
from backend.regime import assess_regime
from backend.schemas import (
    DISCLAIMER,
    HealthResponse,
    Market,
    RegimeInfo,
    RegimeResponse,
    ScoreEntry,
    Snapshot,
    SnapshotCounts,
    ThemesResponse,
)
from backend.store import Store
from backend.themes import ThemeDef, load_themes
from backend.trader.api_models import (
    NavPoint,
    TradingNavResponse,
    TradingOrder,
    TradingOrdersResponse,
    TradingPosition,
    TradingPositionsResponse,
    TradingStatus,
)
from backend.trader.store import TradeStore

#: 정적 SPA 산출물 경로(존재 시 마운트).
_FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"

#: 응답 ``generated_at`` 기준 TZ (스케줄러와 동일).
_SEOUL = ZoneInfo("Asia/Seoul")

#: 데이터 갱신 대상 시장.
_MARKETS: tuple[Market, ...] = ("KR", "US")

#: 매매봇 '가동 중' 판정 윈도 — 최신 NAV ts 가 현재로부터 이 시간 이내면 running.
_TRADER_FRESH = timedelta(minutes=10)

#: ``GET /api/trading/history`` limit 상한.
_HISTORY_LIMIT_MAX = 200


def _resolve_market(value: str) -> Market:
    """``"kr"``/``"us"`` (대소문자 무관) → ``Market``. 미지원이면 400."""
    upper = value.upper()
    if upper not in get_args(Market):
        raise HTTPException(status_code=400, detail=f"unsupported market: {value}")
    return cast(Market, upper)


def _empty_snapshot(market: Market) -> Snapshot:
    """아직 산출 전(초기 스캔 진행 중) '준비 중' 스냅샷 — 빈 entries·scanned=0.

    프론트는 ``counts.scanned == 0`` + 빈 entries 를 로딩/빈 상태로 처리한다.
    """
    now = datetime.now(tz=_SEOUL)
    return Snapshot(
        market=market,
        generated_at=now,
        next_refresh_at=None,
        market_open=market_hours.is_market_open(market, now),
        counts=SnapshotCounts(scanned=0, eligible=0, scored=0, failed=0),
        entries=[],
    )


def _get_snapshot(market: Market, store: Store) -> Snapshot:
    """저장된 스냅샷이 있으면 그대로, 없으면 '준비 중' 빈 스냅샷을 **즉시** 반환(비차단).

    초기 스캔은 lifespan 의 백그라운드 스레드가 채운다 — 여기서 동기 refresh 를 호출하지
    않는다(서버가 즉시 응답하도록).
    """
    existing = store.load_snapshot(market)
    return existing if existing is not None else _empty_snapshot(market)


def _trader_running(nav: list[dict[str, object]], positions_count: int, now: datetime) -> bool:
    """가동 여부 — 최신 NAV ts 가 ``_TRADER_FRESH`` 이내면 True.

    NAV 가 비었으면 (봇 미가동/막 시작) 보유 포지션 존재로 폴백한다. ts 파싱 실패나
    naive datetime 은 보수적으로 처리(naive → Seoul 가정).
    """
    if nav:
        raw = nav[-1].get("ts")
        if isinstance(raw, str):
            try:
                last = datetime.fromisoformat(raw)
            except ValueError:
                return positions_count > 0
            if last.tzinfo is None:
                last = last.replace(tzinfo=_SEOUL)
            return now - last <= _TRADER_FRESH
    return positions_count > 0


def create_app(settings: Settings | None = None) -> FastAPI:
    """앱 팩토리 — CORS·라우트·정적 마운트·lifespan 훅을 구성해 반환한다."""
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """startup: Store 초기화·스케줄러 시작·**초기 스캔은 백그라운드** / shutdown: 종료.

        초기 KR/US 스캔(라이브는 수백 종목 × 외부 호출로 수십 초 이상)을 동기로 돌리면
        서버가 그동안 안 뜬다 → daemon 스레드로 던지고 즉시 서빙을 시작한다.
        """
        store = Store(cfg.db_path)
        news_store = NewsStore(cfg.news_db_path)
        # 매매봇 TradeStore(읽기전용 표시). 테이블을 생성하므로 봇 미가동 시엔 빈 읽기 = 안전.
        trade_store = TradeStore(cfg.trader_db_path)
        severity = load_severity(cfg.news_severity_path)
        themes: list[ThemeDef] = load_themes(cfg.themes_path)
        scheduler: AsyncIOScheduler = sched.build_scheduler(store, cfg, news_store)
        scheduler.start()

        def _initial_scan() -> None:
            # 부팅 초기 스캔은 prep 경로(일봉 캐시 배치 워밍 후 산출)로 — 장중 부팅 시에도
            # US ~300종목을 종목당 호출하지 않고 yf.download 배치로 받아 Yahoo 429 회피(FIX-C).
            for market in _MARKETS:
                sched.prep_now(market, store, cfg)

        initial_thread = threading.Thread(target=_initial_scan, name="initial-scan", daemon=True)
        initial_thread.start()

        application.state.settings = cfg
        application.state.store = store
        application.state.news_store = news_store
        application.state.trade_store = trade_store
        application.state.severity = severity
        application.state.themes = themes
        application.state.scheduler = scheduler
        application.state.initial_thread = initial_thread
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)

    application = FastAPI(
        title="Trend Score Dashboard",
        description="추세추종 매수-추천 스코어 대시보드 (KR/US). 투자 자문 아님.",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @application.get("/healthz", response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        """헬스체크 — 데이터 모드·최신 KR/US 스냅샷 시각."""
        store: Store = application.state.store
        kr = store.load_snapshot("KR")
        us = store.load_snapshot("US")
        return HealthResponse(
            status="ok",
            data_mode=cfg.data_mode,
            last_kr_snapshot=kr.generated_at if kr is not None else None,
            last_us_snapshot=us.generated_at if us is not None else None,
        )

    @application.get("/api/snapshot", response_model=Snapshot)
    async def snapshot(market: str = Query(..., description="kr|us")) -> Snapshot:
        """``market`` 의 최신 랭킹 스냅샷 (없으면 '준비 중' 빈 스냅샷, 비차단)."""
        resolved = _resolve_market(market)
        store: Store = application.state.store
        return _get_snapshot(resolved, store)

    @application.get("/api/themes", response_model=ThemesResponse)
    async def themes() -> ThemesResponse:
        """테마별 주도주 — 두 시장 스냅샷을 합쳐 구성(비차단)."""
        store: Store = application.state.store
        theme_defs: list[ThemeDef] = application.state.themes
        snapshots: dict[Market, Snapshot] = {
            market: _get_snapshot(market, store) for market in _MARKETS
        }
        now = datetime.now(tz=_SEOUL)
        return build_themes_response(snapshots, theme_defs, cfg, now)

    @application.get("/api/regime", response_model=RegimeResponse)
    def regime() -> RegimeResponse:
        """시장별 레짐(장세) — 지수 방향(MA200)×강도(ADX). 읽기전용.

        sync 핸들러라 FastAPI 가 스레드풀에서 실행(provider 의 동기 지수조회가 이벤트루프를
        막지 않게). 지수 일봉은 일1회 캐시라 폴링마다 재조회하지 않는다. 실패는 UNKNOWN 흡수.
        """
        provider = get_provider(cfg)
        markets = [
            RegimeInfo(
                market=market,
                regime=(r := assess_regime(provider, market, cfg)).regime,
                index_close=r.index_close,
                ma200=r.ma200,
                adx=r.adx,
                above_ma200=r.above_ma200,
            )
            for market in _MARKETS
        ]
        return RegimeResponse(markets=markets, disclaimer=DISCLAIMER)

    @application.get("/api/ticker/{market}/{code}", response_model=ScoreEntry)
    async def ticker(market: str, code: str) -> ScoreEntry:
        """단일 종목 상세 — 저장된 스냅샷에서 조회(없으면 404)."""
        resolved = _resolve_market(market)
        store: Store = application.state.store
        entry = ticker_detail(resolved, code, store)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"ticker not found: {code}")
        return entry

    @application.get("/api/news/issues", response_model=NewsIssuesResponse)
    async def news_issues() -> NewsIssuesResponse:
        """긴급도순 Top10 이슈(최근 48h) + 구성 원문. 점수 무반영·면책 포함."""
        news_store: NewsStore = application.state.news_store
        store: Store = application.state.store
        severity: dict[str, Decimal] = application.state.severity
        # 종목명 → 라이브 스냅샷 메타(점수연결). 같은 이름이 양 시장에 있으면 먼저 본 것 유지.
        stock_meta: dict[str, StockMeta] = {}
        for market in _MARKETS:
            snap = store.load_snapshot(market)
            if snap is None:
                continue
            for entry in snap.entries:
                stock_meta.setdefault(
                    entry.name,
                    StockMeta(
                        ticker=entry.ticker,
                        score=entry.score,
                        grade=entry.grade.value,
                        market=entry.market,
                    ),
                )
        names = set(stock_meta)
        now = datetime.now(tz=_SEOUL)
        # 최근 48h = 이슈 구성, 직전 48~96h = spike(언급 급등) 베이스라인.
        window = news_store.recent_raw(now - timedelta(hours=96))
        recent_cut = now - timedelta(hours=48)
        recent = [it for it in window if it.ts_utc >= recent_cut]
        baseline = [it for it in window if it.ts_utc < recent_cut]
        issues = build_issues(
            recent,
            names,
            severity,
            now=now,
            top_n=200,
            baseline_items=baseline,
            stock_meta=stock_meta,
        )
        layers = group_by_layer(issues, cfg.news_top_n_per_layer)

        def _to_news_issue(issue: Issue) -> NewsIssue:
            return NewsIssue(
                key=issue.key,
                title=issue.title,
                urgency=issue.urgency,
                channels=list(issue.channels),
                severity=issue.severity,
                count=issue.count,
                last_ts=issue.last_ts,
                spike=issue.spike,
                ticker=issue.ticker,
                score=issue.score,
                grade=issue.grade,
                market=issue.market,
                headline=issue.headline,
                # 표시 가독성: 원문을 clean_text 로 정리하고, 비는 메시지는 제외.
                messages=[
                    NewsMessage(
                        channel=it.channel,
                        ts_kst=it.ts_kst,
                        text=ct,
                        urls=list(it.urls),
                    )
                    for it in issue.items
                    if (ct := clean_text(it.text))
                ],
            )

        return NewsIssuesResponse(
            generated_at=now,
            disclaimer=DISCLAIMER,
            domestic=[_to_news_issue(i) for i in layers["domestic"]],
            us=[_to_news_issue(i) for i in layers["us"]],
            macro=[_to_news_issue(i) for i in layers["macro"]],
        )

    @application.get("/api/news/weekly", response_model=WeeklyResponse)
    async def news_weekly() -> WeeklyResponse:
        """최신 주간 한국어 요약(없으면 None) + 면책."""
        news_store: NewsStore = application.state.news_store
        weekly = news_store.latest_weekly()
        return WeeklyResponse(
            week_start=weekly.week_start if weekly else None,
            kr_markdown=weekly.kr_markdown if weekly else None,
            generated_at=weekly.generated_at if weekly else None,
            disclaimer=DISCLAIMER,
        )

    @application.get("/api/trading/status", response_model=TradingStatus)
    async def trading_status() -> TradingStatus:
        """모의 매매봇 현황 — 가동 여부 + 최신 NAV·포지션 요약(읽기전용). 면책 포함.

        봇이 아직 안 돌았으면 빈 TradeStore 라 모든 필드 None/0 · running=False.
        """
        trade_store: TradeStore = application.state.trade_store
        positions = trade_store.latest_positions()
        # 헤드라인 NAV(현금·총평가)는 자금이 든 KR(KRW) 계좌만 본다 — 미장(USD)은 현재 $0 이라
        # 통화가 다른 두 NAV 를 합치면 KRW 헤드라인이 0 으로 오염된다. TODO: KR+US 통합 평가는
        # USD→KRW 환산(FX)이 필요하며 미장에 실자금이 들어오면 그때 합산한다.
        nav = trade_store.nav_series(market="KR")
        now = datetime.now(tz=_SEOUL)
        latest_nav = nav[-1] if nav else None
        pnls = [p["pnl_amount"] for p in positions if p["pnl_amount"] is not None]
        total_pnl = sum(pnls, Decimal("0")) if pnls else None
        return TradingStatus(
            running=_trader_running(nav, len(positions), now),
            total_eval=latest_nav["total_eval"] if latest_nav else None,
            cash=latest_nav["cash"] if latest_nav else None,
            position_count=len(positions),
            total_pnl=total_pnl,
            realized_pnl=trade_store.realized_pnl_total(),
            as_of=latest_nav["ts"] if latest_nav else None,
            disclaimer=DISCLAIMER,
        )

    @application.get("/api/trading/positions", response_model=TradingPositionsResponse)
    async def trading_positions() -> TradingPositionsResponse:
        """최신 스냅샷의 보유 종목(읽기전용). 면책 포함."""
        trade_store: TradeStore = application.state.trade_store
        positions = [TradingPosition(**p) for p in trade_store.latest_positions()]
        return TradingPositionsResponse(positions=positions, disclaimer=DISCLAIMER)

    @application.get("/api/trading/history", response_model=TradingOrdersResponse)
    async def trading_history(
        limit: int = Query(50, ge=1, le=_HISTORY_LIMIT_MAX),
    ) -> TradingOrdersResponse:
        """최근 주문 접수 기록(최신순, 읽기전용). 면책 포함."""
        trade_store: TradeStore = application.state.trade_store
        # recent_orders 는 order_no 도 주지만 표시 모델엔 불필요 → 필요한 키만 추린다.
        orders = [
            TradingOrder(
                ts=o["ts"],
                ticker=o["ticker"],
                name=o["name"],
                side=o["side"],
                qty=o["qty"],
                filled_qty=o["filled_qty"],
                status=o["status"],
                reason=o["reason"],
                message=o["message"],
            )
            for o in trade_store.recent_orders(limit)
        ]
        return TradingOrdersResponse(orders=orders, disclaimer=DISCLAIMER)

    @application.get("/api/trading/nav", response_model=TradingNavResponse)
    async def trading_nav(limit: int = Query(2000, ge=1, le=5000)) -> TradingNavResponse:
        """NAV(총평가) 시계열(오래된→최신, 읽기전용). 면책 포함.

        헤드라인과 동일하게 자금이 든 KR(KRW) 계좌만 — 미장(USD)은 현재 $0(통화 혼입 방지).
        """
        trade_store: TradeStore = application.state.trade_store
        nav = [NavPoint(**n) for n in trade_store.nav_series(limit, market="KR")]
        return TradingNavResponse(nav=nav, disclaimer=DISCLAIMER)

    # 정적 SPA — 산출물이 있으면 마운트, 없으면 루트 JSON 안내를 노출(API 전용).
    if _FRONTEND_DIST.is_dir():
        application.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="spa")
    else:

        @application.get("/")
        async def root() -> dict[str, str]:
            """정적 SPA 산출물이 없을 때의 루트 안내(JSON)."""
            return {
                "service": "trend-score-dashboard",
                "docs": "/docs",
                "disclaimer": DISCLAIMER,
            }

    return application


#: ASGI 진입점 (``uvicorn backend.app:app``).
app = create_app()


__all__ = ["app", "create_app"]
