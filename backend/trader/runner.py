"""모의 매매봇 독립 러너 — APScheduler ``BlockingScheduler`` 로 ``TraderLoop`` 주기 구동.

대시보드(FastAPI)와 **별도 프로세스**(systemd ``trend-trader.service``)로 돈다. FastAPI 의
``AsyncIOScheduler`` 가 아니라 standalone 데몬용 ``BlockingScheduler`` 를 쓴다.

원칙(``scheduler.py`` 와 동일):
- 스케줄러 timezone = ``Asia/Seoul``, 모든 ``now`` 는 tz-aware.
- 한 사이클 실패가 스케줄러를 죽이면 안 됨 → 잡 래퍼가 모든 예외를 잡아 로깅(warning+exc_info)만.
- 장중 게이트·킬스위치·멱등은 ``TraderLoop.run_once`` 내부 책임(러너는 주기 호출만).
- 국장(시장가)·미장(지정가)을 각각 1잡으로 돌린다. 둘은 같은 모의 앱키 → **토큰 1개 공유**
  (``KisToken``, 앱키당 1토큰 정책 + 토큰 thrash 방지).
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
from backend.trader.gemini_decider import GeminiDecider
from backend.trader.kis_auth import KisToken, token_from_settings
from backend.trader.kis_order import KisOrderClient
from backend.trader.kis_overseas import KisOverseasOrderClient
from backend.trader.loop import OrderClient, TraderLoop
from backend.trader.positions import PositionManager
from backend.trader.store import TradeStore
from backend.trader.strategy import StrategyEngine

logger = logging.getLogger(__name__)

#: 스케줄러·잡 기준 시간대. 모든 트리거·``now`` 가 이 TZ 를 따른다.
_SEOUL = ZoneInfo("Asia/Seoul")

#: 모의 도메인 — 국내·해외 클라이언트가 공유(토큰도 이 도메인 기준 1개).
_MOCK_DOMAIN = "https://openapivts.koreainvestment.com:29443"


def _build_decider(settings: Settings) -> GeminiDecider | None:
    """``trader_use_llm`` 이면 Gemini 결정기 1개 생성(네트워크 0 — 클라이언트는 지연 생성).

    KR·US 가 한 인스턴스를 공유한다(캐시는 시장별로 분리). 키 없으면 첫 호출 시 실패→폴백.
    """
    return GeminiDecider(settings) if settings.trader_use_llm else None


def build_loop(
    settings: Settings,
    market: Market,
    *,
    token: KisToken | None = None,
    decider: GeminiDecider | None = None,
) -> TraderLoop:
    """``market`` 매매 루프 1개를 의존성 주입해 조립(네트워크 0 — 객체 생성만).

    - 주문: 국장=``KisOrderClient``(시장가) / 미장=``KisOverseasOrderClient``(지정가). 둘 다
      모의 도메인. ``token`` 주입 시 그 ``KisToken`` 을 공유(국장·미장 토큰 thrash 방지).
    - 스냅샷 읽기: ``Store`` (대시보드가 쓰는 ``db_path`` 동일 파일 — 봇은 읽기만).
    - 매매 기록: ``TradeStore`` (``trader_db_path``, 봇이 쓰고 API 가 읽음).
    - 매수 결정: ``decider`` 주입 + ``trader_use_llm`` 면 Gemini, 아니면 결정론(StrategyEngine).
    """
    tok = token or token_from_settings(settings, _MOCK_DOMAIN)
    order_client: OrderClient
    if market == "US":
        order_client = KisOverseasOrderClient(settings, mode="mock", token=tok)
    else:
        order_client = KisOrderClient(settings, mode="mock", token=tok)
    store = Store(settings.db_path)
    trade_store = TradeStore(settings.trader_db_path)
    dec = decider if decider is not None else _build_decider(settings)
    engine = StrategyEngine(settings, decider=dec)
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


def run(settings: Settings | None = None, *, markets: tuple[Market, ...] = ("KR", "US")) -> None:
    """``BlockingScheduler`` 로 시장별 매매 루프를 ``trader_loop_sec`` 주기로 구동(블로킹).

    국장·미장은 각자 1잡(자기 ``TraderLoop``)으로 돌되, **토큰 1개를 공유**한다(같은 모의 앱키).
    이 함수는 ``scheduler.start()`` 에서 블로킹된다(데몬). ``KeyboardInterrupt``/``SystemExit``
    시 ``scheduler.shutdown()`` 으로 정상 종료한다. 로깅 설정은 호출 측(엔트리포인트) 책임.
    """
    settings = settings or get_settings()
    token = token_from_settings(settings, _MOCK_DOMAIN)
    decider = _build_decider(settings)  # KR·US 공유(시장별 캐시 분리). 키 없으면 폴백.
    scheduler = BlockingScheduler(timezone=_SEOUL)
    for market in markets:
        loop = build_loop(settings, market, token=token, decider=decider)
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
