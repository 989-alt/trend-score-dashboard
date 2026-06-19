"""전진검증 리포트 순수 헬퍼 단위테스트 — 네트워크 0(yfinance 미호출)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from backend.trader.store import TradeStore
from scripts.forward_test_report import _order_stats, compute_portfolio_metrics


def _snap(store: TradeStore, at: datetime, total_eval: Decimal) -> None:
    """NAV 1점만 기록(포지션 없음) — 메트릭 산출용 최소 스냅샷."""
    store.record_snapshot(at, total_eval=total_eval, cash=total_eval, positions=[])


def test_compute_portfolio_metrics_rising_with_dip(tmp_path: Path) -> None:
    """상승+중간 하락 NAV → 총수익/CAGR/MDD(Decimal) 검증."""
    store = TradeStore(tmp_path / "trading.db")
    start = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    # 100M → 120M → 90M(하락) → 200M, 365일 구간.
    _snap(store, start, Decimal("100000000"))
    _snap(store, start + timedelta(days=120), Decimal("120000000"))
    _snap(store, start + timedelta(days=240), Decimal("90000000"))
    _snap(store, start + timedelta(days=365), Decimal("200000000"))

    pm = compute_portfolio_metrics(store.nav_series(limit=100000))
    assert pm is not None
    # 총수익률 = 200M/100M − 1 = 1.0 (정확).
    assert pm["total_return"] == Decimal("1")
    # MDD = (90M − 120M)/120M = −0.25 (정확).
    assert pm["mdd"].quantize(Decimal("0.0001")) == Decimal("-0.2500")
    # 기간·연수.
    assert pm["days"] == 365
    assert pm["years"] == Decimal("365") / Decimal("365.25")
    # CAGR 은 metrics.cagr 와 동일해야 하고, 1년에 2배 ≈ +100% 부근.
    from backend.backtest.metrics import cagr

    assert pm["cagr"] == cagr(Decimal("100000000"), Decimal("200000000"), years=pm["years"])
    assert Decimal("0.99") < pm["cagr"] < Decimal("1.01")


def test_compute_portfolio_metrics_skips_none_total_eval(tmp_path: Path) -> None:
    """total_eval=None 행은 제외하고 유효 2점으로 산출."""
    store = TradeStore(tmp_path / "t.db")
    start = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    # nav 테이블에 total_eval NULL 행을 직접 넣어 None 필터 경로 검증.(market 컬럼 포함)
    store._conn.execute(
        "INSERT INTO nav VALUES (?, ?, ?, ?)", (start.isoformat(), "KR", None, None)
    )
    store._conn.commit()
    _snap(store, start + timedelta(days=10), Decimal("100"))
    _snap(store, start + timedelta(days=20), Decimal("110"))

    pm = compute_portfolio_metrics(store.nav_series(limit=100000))
    assert pm is not None
    assert pm["first"] == Decimal("100") and pm["last"] == Decimal("110")
    assert pm["total_return"] == Decimal("110") / Decimal("100") - Decimal("1")


def test_compute_portfolio_metrics_too_few_points_returns_none(tmp_path: Path) -> None:
    """유효 NAV 2점 미만 → None(데이터 부족 sentinel, 크래시 없음)."""
    store = TradeStore(tmp_path / "t.db")
    assert compute_portfolio_metrics(store.nav_series(limit=100000)) is None
    _snap(store, datetime(2026, 6, 1, 9, 0, tzinfo=UTC), Decimal("100"))
    assert compute_portfolio_metrics(store.nav_series(limit=100000)) is None


def test_order_stats_counts_buys_and_sells() -> None:
    """매수/매도 건수 + 회전(≈매도) 집계."""
    orders = [
        {"side": "buy"},
        {"side": "buy"},
        {"side": "sell"},
        {"side": "buy"},
        {"side": "sell"},
    ]
    assert _order_stats(orders) == {"buys": 3, "sells": 2, "turnover": 2}
