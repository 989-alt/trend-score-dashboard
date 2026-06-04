"""FastAPI 앱 — 스냅샷·테마·종목 상세 API + 정적 SPA 서빙.

라우트:
- ``GET /healthz`` → ``HealthResponse`` (데이터 모드·최신 KR/US 스냅샷 시각).
- ``GET /api/snapshot?market=kr|us`` → ``Snapshot`` (없으면 즉시 산출 후 반환).
- ``GET /api/themes`` → ``ThemesResponse`` (두 시장 스냅샷으로 테마별 주도주).
- ``GET /api/ticker/{market}/{code}`` → ``ScoreEntry`` (저장 스냅샷에서 조회, 없으면 404).

lifespan(startup) 에서 ``Store`` 초기화·KR/US 초기 스냅샷 산출·스케줄러 시작,
shutdown 에서 스케줄러 종료. ``frontend/dist`` 가 존재하면 SPA 로 정적 마운트
(없으면 루트 JSON 안내 — API 전용으로 동작).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import cast, get_args
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend import scheduler as sched
from backend.config import ROOT_DIR, Settings, get_settings
from backend.engine import build_themes_response, ticker_detail
from backend.schemas import (
    DISCLAIMER,
    HealthResponse,
    Market,
    ScoreEntry,
    Snapshot,
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


def _ensure_snapshot(market: Market, store: Store, settings: Settings) -> Snapshot:
    """저장된 스냅샷이 있으면 그대로, 없으면 즉시 산출(동기)해 반환한다."""
    existing = store.load_snapshot(market)
    if existing is not None:
        return existing
    return sched.refresh_now(market, store, settings)


def create_app(settings: Settings | None = None) -> FastAPI:
    """앱 팩토리 — CORS·라우트·정적 마운트·lifespan 훅을 구성해 반환한다."""
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """startup: Store 초기화·KR/US 초기 스냅샷·스케줄러 시작 / shutdown: 스케줄러 종료."""
        store = Store(cfg.db_path)
        themes: list[ThemeDef] = load_themes(cfg.themes_path)
        for market in _MARKETS:
            sched.refresh_now(market, store, cfg)
        scheduler: AsyncIOScheduler = sched.build_scheduler(store, cfg)
        scheduler.start()

        application.state.settings = cfg
        application.state.store = store
        application.state.themes = themes
        application.state.scheduler = scheduler
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
        """``market`` 의 최신 랭킹 스냅샷 (없으면 즉시 산출)."""
        resolved = _resolve_market(market)
        store: Store = application.state.store
        return _ensure_snapshot(resolved, store, cfg)

    @application.get("/api/themes", response_model=ThemesResponse)
    async def themes() -> ThemesResponse:
        """테마별 주도주 — 두 시장 스냅샷을 합쳐 구성."""
        store: Store = application.state.store
        theme_defs: list[ThemeDef] = application.state.themes
        snapshots: dict[Market, Snapshot] = {
            market: _ensure_snapshot(market, store, cfg) for market in _MARKETS
        }
        now = datetime.now(tz=_SEOUL)
        return build_themes_response(snapshots, theme_defs, cfg, now)

    @application.get("/api/ticker/{market}/{code}", response_model=ScoreEntry)
    async def ticker(market: str, code: str) -> ScoreEntry:
        """단일 종목 상세 — 저장된 스냅샷에서 조회(없으면 404)."""
        resolved = _resolve_market(market)
        store: Store = application.state.store
        _ensure_snapshot(resolved, store, cfg)
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
