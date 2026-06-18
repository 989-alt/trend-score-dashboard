"""리스크 오버레이 시뮬 검증 — NAV 경로 형태 + 레짐 보류(현금) 경로."""

from datetime import date, timedelta
from decimal import Decimal

from backend.backtest.panel import Panel, TickerSeries, Valuation
from backend.backtest.portfolio import simulate_risk_overlay
from backend.backtest.run import BacktestConfig
from backend.config import Settings, get_settings
from backend.schemas import OHLCVRow
from tests.fixtures.backtest_synth import make_panel


def test_simulate_risk_overlay_returns_nav_and_period_returns() -> None:
    panel = make_panel()
    cfg = BacktestConfig(
        start=panel.index_rows[0].date,
        end=panel.index_rows[-1].date,
        rebalance="weekly",
        top_n=2,
        preset="fallback_c",
    )
    res = simulate_risk_overlay(
        panel, cfg, get_settings(), dates=[r.date for r in panel.index_rows][::5]
    )
    assert res.nav[0] == Decimal("1")
    assert len(res.nav) == len(res.period_returns) + 1
    assert all(isinstance(r, Decimal) for r in res.period_returns)


def _idx_row(d: date, close: Decimal, vol: int) -> OHLCVRow:
    return OHLCVRow(
        date=d,
        open=close,
        high=close * Decimal("1.001"),
        low=close * Decimal("0.999"),
        close=close,
        volume=Decimal(str(vol)),
    )


def _flat_series(ticker: str, start: date, closes: list[int]) -> TickerSeries:
    rows = [
        OHLCVRow(
            date=start + timedelta(days=i),
            open=Decimal(c),
            high=Decimal(c) * Decimal("1.025"),
            low=Decimal(c) * Decimal("0.975"),
            close=Decimal(c),
            volume=Decimal("1000000"),
        )
        for i, c in enumerate(closes)
    ]
    return TickerSeries(
        ticker=ticker,
        rows=rows,
        turnover_by_date={r.date: Decimal("20000000000") for r in rows},
        valuation_by_date={r.date: Valuation(per=Decimal("10"), pbr=Decimal("1.2")) for r in rows},
    )


def test_simulate_risk_overlay_regime_off_forces_cash() -> None:
    """첫 리밸런스일에 지수 분산일 패턴을 심어 is_risk_off=True → 현금(기간수익 0)."""
    start = date(2023, 1, 2)
    # 지수 KS11: 6봉을 '하락 + 거래량 증가'로 만들어 window=5 안에서 분산일 5개 생성.
    #   분산일 = 종가 ≤ 전일종가×0.998 AND 거래량 > 전일거래량.
    #   첫 봉은 전일이 없어 평가 제외 → 6봉 ⇒ 분산일 5개 ⇒ threshold=5 충족.
    idx_rows: list[OHLCVRow] = []
    close = Decimal("2000")
    vol = 1_000_000
    for i in range(6):
        idx_rows.append(_idx_row(start + timedelta(days=i), close, vol))
        close *= Decimal("0.99")  # 1% 하락 (≤ ×0.998)
        vol += 100_000  # 거래량 증가
    # 이후 봉(평평) — 시뮬에 t_next 가 존재하도록 구간 연장.
    for i in range(6, 12):
        idx_rows.append(_idx_row(start + timedelta(days=i), close, vol))

    # 종목 시리즈는 레짐-오프(현금) 경로에서 사용되지 않지만 Panel 구성을 위해 1개 둔다.
    series = _flat_series("000001", start, list(range(100, 130)))
    panel = Panel(
        series={"000001": series},
        fundamentals={"000001": []},
        listings={"000001": (start, None)},
        index_rows=idx_rows,
    )

    settings = Settings(regime_window=5, regime_threshold=5)
    cfg = BacktestConfig(
        start=start, end=idx_rows[-1].date, rebalance="weekly", top_n=2, preset="fallback_c"
    )
    dates = [date(2023, 1, 7), date(2023, 1, 12)]  # dates[0] = 6번째 봉(분산일 패턴 직후)

    res = simulate_risk_overlay(panel, cfg, settings, dates=dates)

    assert res.period_returns[0] == Decimal("0")  # 레짐-오프 → 현금
    assert dates[0] in res.regime_off_dates
    assert res.nav[1] == res.nav[0]  # 현금 보유 → NAV 불변
