"""``backend.backtest.riskoff`` — 객관 트리거(as-of) · 위기 커버리지 · 포트폴리오 주입."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from backend.backtest.panel import Panel, TickerSeries, Valuation
from backend.backtest.portfolio import simulate_risk_overlay
from backend.backtest.riskoff import (
    CrisisEvent,
    coverage,
    fx_risk_off_dates,
    level_jump_risk_off_dates,
    load_crisis_events,
    vix_risk_off_dates,
)
from backend.backtest.run import BacktestConfig
from backend.config import get_settings
from backend.schemas import OHLCVRow


def _series(vals: list[tuple[int, str]]) -> list[tuple[date, Decimal]]:
    return [(date(2024, 1, d), Decimal(v)) for d, v in vals]


# ── 객관 트리거 ──────────────────────────────────────────────────────────────


def test_vix_level_trigger() -> None:
    s = _series([(1, "15"), (2, "16"), (3, "32")])  # day3 VIX≥30
    off = vix_risk_off_dates(s, [date(2024, 1, 2), date(2024, 1, 3)])
    assert date(2024, 1, 3) in off
    assert date(2024, 1, 2) not in off


def test_vix_jump_trigger_below_level() -> None:
    s = _series([(1, "20"), (2, "23")])  # +15% < level 30 이지만 급등 트리거
    assert date(2024, 1, 2) in vix_risk_off_dates(s, [date(2024, 1, 2)])


def test_asof_no_lookahead() -> None:
    # day3 스파이크가 day2 를 risk-off 로 만들면 안 된다(룩어헤드 0).
    s = _series([(1, "15"), (2, "15"), (3, "40")])
    assert vix_risk_off_dates(s, [date(2024, 1, 2)]) == set()


def test_fx_jump_only_no_level() -> None:
    calm = _series([(1, "1300"), (2, "1305")])  # +0.38% < 1.5%
    assert fx_risk_off_dates(calm, [date(2024, 1, 2)]) == set()
    spike = _series([(1, "1300"), (2, "1325")])  # +1.92% > 1.5%
    assert date(2024, 1, 2) in fx_risk_off_dates(spike, [date(2024, 1, 2)])


def test_empty_series_no_triggers() -> None:
    assert (
        level_jump_risk_off_dates(
            [], [date(2024, 1, 2)], level=Decimal("30"), jump_pct=Decimal("0.1")
        )
        == set()
    )


# ── 위기 이벤트 / 커버리지 ───────────────────────────────────────────────────


def test_load_crisis_events(tmp_path: Path) -> None:
    p = tmp_path / "c.yml"
    p.write_text(
        "events:\n"
        '  - {name: "covid", start: "2020-02-20", end: "2020-03-23", scope: global}\n'
        '  - {name: "kr", start: "2024-12-03", end: "2024-12-13", scope: kr}\n',
        encoding="utf-8",
    )
    assert load_crisis_events(p) == [
        CrisisEvent("covid", date(2020, 2, 20), date(2020, 3, 23), "global"),
        CrisisEvent("kr", date(2024, 12, 3), date(2024, 12, 13), "kr"),
    ]


def test_load_crisis_events_missing(tmp_path: Path) -> None:
    assert load_crisis_events(tmp_path / "none.yml") == []


def test_coverage_counts_and_kr_gap() -> None:
    events = [
        CrisisEvent("global1", date(2024, 8, 1), date(2024, 8, 7), "global"),
        CrisisEvent("kr1", date(2024, 12, 3), date(2024, 12, 13), "kr"),
        CrisisEvent("nomatch", date(2099, 1, 1), date(2099, 1, 2), "global"),  # 평가 제외
    ]
    rebals = [date(2024, 8, 5), date(2024, 12, 9), date(2025, 1, 1)]
    triggers = {date(2024, 8, 5)}  # global1 catch, kr1 miss
    cov = coverage(events, triggers, rebals)
    assert cov.total == 2  # nomatch 는 구간 내 리밸런스일 없어 제외
    assert cov.caught == 1
    assert cov.missed == ["kr1"]
    assert cov.kr_total == 1
    assert cov.kr_caught == 0


# ── 포트폴리오 주입 (risk_off_dates → 현금) ──────────────────────────────────


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


def test_injected_risk_off_forces_cash() -> None:
    """평평한 지수(레짐 항상 off 아님) → 주입된 risk_off_dates 만이 현금을 강제한다."""
    start = date(2023, 1, 2)
    idx = [_idx_row(start + timedelta(days=i), Decimal("2000"), 1_000_000) for i in range(12)]
    panel = Panel(
        series={"000001": _flat_series("000001", start, list(range(100, 130)))},
        fundamentals={"000001": []},
        listings={"000001": (start, None)},
        index_rows=idx,
    )
    cfg = BacktestConfig(
        start=start, end=idx[-1].date, rebalance="weekly", top_n=2, preset="fallback_c"
    )
    dates = [date(2023, 1, 7), date(2023, 1, 12)]
    settings = get_settings()

    base = simulate_risk_overlay(
        panel, cfg, settings, dates=dates, regime_on=True, atr_on=False, sizing_on=False
    )
    assert dates[0] not in base.regime_off_dates  # 레짐만으론 현금 아님

    injected = simulate_risk_overlay(
        panel,
        cfg,
        settings,
        dates=dates,
        regime_on=True,
        atr_on=False,
        sizing_on=False,
        risk_off_dates={dates[0]},
    )
    assert dates[0] in injected.regime_off_dates  # 주입 → 현금 경로
    assert injected.period_returns[0] == Decimal("0")
    assert injected.nav[1] == injected.nav[0]
