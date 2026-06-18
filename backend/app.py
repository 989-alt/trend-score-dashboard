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
from datetime import datetime
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
from backend.news.store import NewsStore
from backend.schemas import (
    DISCLAIMER,
    HealthResponse,
    Market,
    ScoreEntry,
    Snapshot,
    SnapshotCounts,
    ThemesResponse,
)
from backend.store import Store
from backend.themes import ThemeDef, load_themes

#: 정적 SPA 산출물 경로(존재 시 마운트).
_FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"

#: 응답 ``generated_at`` 기준 TZ (스케줄러와 동일).
_SEOUL = ZoneInfo("Asia/Seoul")

#: 데이터 갱신 대상 시장.
_MARKETS: tuple[Market, ...] = ("KR", "US")


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

    @application.get("/api/ticker/{market}/{code}", response_model=ScoreEntry)
    async def ticker(market: str, code: str) -> ScoreEntry:
        """단일 종목 상세 — 저장된 스냅샷에서 조회(없으면 404)."""
        resolved = _resolve_market(market)
        store: Store = application.state.store
        entry = ticker_detail(resolved, code, store)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"ticker not found: {code}")
        return entry

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
