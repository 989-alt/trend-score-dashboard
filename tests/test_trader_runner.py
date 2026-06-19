"""runner.build_loop 배선 단위테스트 — 컴포넌트 타입·시장 확인. 네트워크 0(run() 미호출)."""

from __future__ import annotations

from pathlib import Path

from backend.config import Settings
from backend.store import Store
from backend.trader.kis_auth import token_from_settings
from backend.trader.kis_order import KisOrderClient
from backend.trader.kis_overseas import KisOverseasOrderClient
from backend.trader.loop import TraderLoop
from backend.trader.positions import PositionManager
from backend.trader.runner import build_loop
from backend.trader.store import TradeStore
from backend.trader.strategy import StrategyEngine


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        kis_appkey="k",
        kis_appsecret="s",
        kis_account="50190719",
        db_path=tmp_path / "dashboard.db",
        trader_db_path=tmp_path / "trading.db",
    )


def test_build_loop_wires_components(tmp_path: Path) -> None:
    """build_loop 가 올바른 타입의 컴포넌트를 주입한 TraderLoop(market=KR)을 반환한다."""
    loop = build_loop(_settings(tmp_path), "KR")

    assert isinstance(loop, TraderLoop)
    assert loop._market == "KR"
    assert isinstance(loop._oc, KisOrderClient)
    assert isinstance(loop._store, Store)
    assert isinstance(loop._ts, TradeStore)
    assert isinstance(loop._engine, StrategyEngine)
    assert isinstance(loop._pm, PositionManager)


def test_build_loop_us_uses_overseas_client(tmp_path: Path) -> None:
    """build_loop('US') 는 해외 주문 클라이언트를, KR 은 국내 클라이언트를 주입한다."""
    settings = _settings(tmp_path)

    us = build_loop(settings, "US")
    kr = build_loop(settings, "KR")

    assert us._market == "US"
    assert isinstance(us._oc, KisOverseasOrderClient)
    assert isinstance(kr._oc, KisOrderClient)


def test_build_loop_shares_token(tmp_path: Path) -> None:
    """주입한 KisToken 을 KR·US 클라이언트가 동일 인스턴스로 공유(토큰 thrash 방지)."""
    settings = _settings(tmp_path)
    token = token_from_settings(settings, "https://openapivts.koreainvestment.com:29443")

    kr = build_loop(settings, "KR", token=token)
    us = build_loop(settings, "US", token=token)

    assert kr._oc._token is token
    assert us._oc._token is token
