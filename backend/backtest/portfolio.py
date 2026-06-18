"""리스크 오버레이 포트폴리오 시뮬 — 레짐 보류·ATR 손절·사이징. 연속 dates 의 NAV 경로."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import backend.scoring as sc
from backend.backtest.panel import Panel
from backend.backtest.regime import is_risk_off
from backend.backtest.run import BacktestConfig, _score_at
from backend.config import Settings
from backend.schemas import OHLCVRow


@dataclass(frozen=True)
class OverlayResult:
    nav: list[Decimal]
    period_returns: list[Decimal]
    regime_off_dates: list[date]


def _atr20(rows: list[OHLCVRow]) -> Decimal:
    if not rows:
        return Decimal("0")
    return sc.atr20_over_price(rows) * rows[-1].close


def simulate_risk_overlay(
    panel: Panel,
    cfg: BacktestConfig,
    settings: Settings,
    dates: list[date],
    *,
    regime_on: bool = True,
    atr_on: bool = True,
    sizing_on: bool = True,
    risk_off_dates: set[date] | None = None,
) -> OverlayResult:
    """레짐 보류·ATR 손절·사이징 오버레이로 연속 dates 의 NAV 경로를 시뮬한다.

    토글(레이어2 ablation 용; 모두 기본 True = 기존 동작 불변):
      regime_on=False → is_risk_off 평가 생략(레짐 현금 미적용; regime_off_dates 빈 채 유지).
      atr_on=False    → 손절 없음(각 픽은 t_next 종가로 청산; intra-period 손절 루프 생략).
      sizing_on=False → 등가중(각 픽 weight=1 → 정규화 수익이 단순 평균).
    risk_off_dates: 외생 트리거(예: VIX·환율)로 산출한 추가 현금일 집합. 분산일 레짐과
      **OR 결합**(둘 중 하나라도 off 면 그 구간 현금). 각 T 는 ≤T 외생데이터로 산출돼야
      룩어헤드 0(호출 측 riskoff.* 가 보장).
    룩어헤드 0(≤T 슬라이스·T 이후 가격만), Decimal 전면.
    """
    nav: list[Decimal] = [Decimal("1")]
    period_returns: list[Decimal] = []
    regime_off: list[date] = []
    cost = cfg.cost_bps / Decimal("10000")
    for i in range(len(dates) - 1):
        t, t_next = dates[i], dates[i + 1]
        regime_off_today = regime_on and is_risk_off(
            panel.index_rows_asof(t),
            window=settings.regime_window,
            threshold=settings.regime_threshold,
            drop=settings.regime_drop,
        )
        injected_off = risk_off_dates is not None and t in risk_off_dates
        if regime_off_today or injected_off:
            regime_off.append(t)
            period_returns.append(Decimal("0"))
            nav.append(nav[-1])
            continue
        ranked = _score_at(panel, t, settings, cfg.preset)
        picks = [tk for tk, _ in ranked[: cfg.top_n]]
        weighted: list[tuple[Decimal, Decimal]] = []
        for tk in picks:
            rows = panel.rows_asof(tk, t)
            entry = panel.price_on_or_after(tk, t + timedelta(days=1))
            if entry is None or entry <= 0 or not rows:
                continue
            if sizing_on:
                wt = sc.suggested_weight(
                    sc.atr20_over_price(rows),
                    risk_pct=settings.risk_pct,
                    mult=settings.atr_stop_mult,
                    cap=settings.max_weight_pct,
                )
            else:
                wt = Decimal("1")
            fut = [r for r in panel.series[tk].rows if t < r.date <= t_next]
            exit_px = None
            if atr_on:
                atr = _atr20(rows)
                stop = sc.atr_stop_price(entry, atr, mult=settings.atr_stop_mult)
                for bar in fut:
                    if bar.low <= stop:
                        exit_px = stop
                        break
            if exit_px is None:
                exit_px = fut[-1].close if fut else entry
            weighted.append((wt, exit_px / entry - Decimal("1")))
        total_w = sum((w for w, _ in weighted), Decimal("0"))
        if total_w > 0:
            gross = sum((w * r for w, r in weighted), Decimal("0")) / total_w
            period_returns.append(gross - cost)
        else:
            period_returns.append(Decimal("0"))
        nav.append(nav[-1] * (Decimal("1") + period_returns[-1]))
    return OverlayResult(nav=nav, period_returns=period_returns, regime_off_dates=regime_off)


__all__ = ["OverlayResult", "simulate_risk_overlay"]
