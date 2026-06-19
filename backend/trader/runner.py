"""모의 매매봇 독립 러너 — APScheduler ``BlockingScheduler`` 로 ``TraderLoop`` 주기 구동.

대시보드(FastAPI)와 **별도 프로세스**(systemd ``trend-trader.service``)로 돈다. FastAPI 의
``AsyncIOScheduler`` 가 아니라 standalone 데몬용 ``BlockingScheduler`` 를 쓴다.

원칙(``scheduler.py`` 와 동일):
- 스케줄러 timezone = ``Asia/Seoul``, 모든 ``now`` 는 tz-aware.
- 한 사이클 실패가 스케줄러를 죽이면 안 됨 → 잡 래퍼가 모든 예외를 잡아 로깅(warning+exc_info)만.
- 장중 게이트·킬스위치·멱등은 ``TraderLoop.run_once`` 내부 책임(러너는 주기 호출만).
- US 는 P8 로 유보 → 기본 ``markets=("KR",)``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from backend.config import Settings, get_settings
from backend.schemas import Market
from backend.store import Store
from backend.trader.kis_order import KisOrderClient
from backend.trader.loop import TraderLoop
from backend.trader.positions import PositionManager
from backend.trader.store import TradeStore
from backend.trader.strategy import StrategyEngine

logger = logging.getLogger(__name__)

#: 스케줄러·잡 기준 시간대. 모든 트리거·``now`` 가 이 TZ 를 따른다.
_SEOUL = ZoneInfo("Asia/Seoul")


def build_loop(settings: Settings, market: Market) -> TraderLoop:
    """``market`` 매매 루프 1개를 의존성 주입해 조립(네트워크 0 — 객체 생성만).

    - 주문: ``KisOrderClient`` (모의 도메인, ``mode="mock"``).
    - 스냅샷 읽기: ``Store`` (대시보드가 쓰는 ``db_path`` 동일 파일 — 봇은 읽기만).
    - 매매 기록: ``TradeStore`` (``trader_db_path``, 봇이 쓰고 API 가 읽음).
    """
    order_client = KisOrderClient(settings, mode="mock")
    store = Store(settings.db_path)
    trade_store = TradeStore(settings.trader_db_path)
    engine = StrategyEngine(settings)
    pm = PositionManager()
    return TraderLoop(
        settings,
        market,
        order_client=order_client,
        store=store,
        trade_store=trade_store,
        engine=engine,
        position_manager=pm,
    )


def _make_job(loop: TraderLoop, market: Market) -> Callable[[], None]:
    """``loop.run_once`` 를 감싼 잡 — 모든 예외를 흡수해 스케줄러 생존을 보장한다."""

    def _job() -> None:
        try:
            loop.run_once(datetime.now(tz=_SEOUL))
        except Exception:
            # 한 사이클 실패가 데몬을 죽이면 안 됨 → 모든 예외를 흡수하고 다음 주기 재시도.
            logger.warning("매매 사이클 실패 — 다음 주기 재시도 (market=%s)", market, exc_info=True)

    return _job


def run(settings: Settings | None = None, *, markets: tuple[Market, ...] = ("KR",)) -> None:
    """``BlockingScheduler`` 로 시장별 매매 루프를 ``trader_loop_sec`` 주기로 구동(블로킹).

    이 함수는 ``scheduler.start()`` 에서 블로킹된다(데몬). ``KeyboardInterrupt``/``SystemExit``
    시 ``scheduler.shutdown()`` 으로 정상 종료한다. 로깅 설정은 호출 측(엔트리포인트) 책임.
    """
    settings = settings or get_settings()
    scheduler = BlockingScheduler(timezone=_SEOUL)
    for market in markets:
        loop = build_loop(settings, market)
        scheduler.add_job(
            _make_job(loop, market),
            IntervalTrigger(seconds=settings.trader_loop_sec, timezone=_SEOUL),
            id=f"trader-{market}",
        )
    logger.info(
        "매매봇 시작 — markets=%s, 주기=%ds (TZ=Asia/Seoul)", markets, settings.trader_loop_sec
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("매매봇 종료 신호 — 스케줄러 정상 종료")
        scheduler.shutdown()


if __name__ == "__main__":
    run(get_settings())


__all__ = ["build_loop", "run"]
