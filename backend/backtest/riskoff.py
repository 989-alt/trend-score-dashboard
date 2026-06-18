"""뉴스 리스크오프 — 객관·외생 트리거(VIX·환율) → risk_off 날짜 집합 + 위기 커버리지.

설계: docs/superpowers/specs/2026-06-18-news-riskoff-filter-design.md
- v1 은 **LLM·뉴스 0**. 객관·시점별 가용 트리거(VIX·원달러)로 '리스크오프 액션'의 가치만
  fail-fast 백테스트한다. (뉴스 탐지기는 전향(forward) 검증이 따로 필요해 v1 범위 밖.)
- **룩어헤드 0**: 각 리밸런스일 T 의 risk_off 여부는 ≤T 외생 시계열로만 판정(as-of).
- 큐레이션 위기 리스트(``crisis_events.yml``)는 트리거가 아니라 **커버리지 측정자**(§3):
  객관 트리거가 한국 고유 충격을 얼마나 놓치는지 = 뉴스 탐지기가 메울 가치의 정량화.
- 과최적화 금지(§9): 임계는 표준값 소수만(아래 상수). Decimal 전면.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

#: 표준 임계(소수만 — 과최적화 방지). VIX 절대수준 + 1일 급등률.
VIX_LEVEL = Decimal("30")
VIX_JUMP_PCT = Decimal("0.10")
#: 원/달러는 절대수준보다 1일 급등(외국인 이탈 스트레스)이 신호 → level 미사용.
FX_JUMP_PCT = Decimal("0.015")

ExogSeries = list[tuple[date, Decimal]]


@dataclass(frozen=True)
class CrisisEvent:
    """큐레이션 위기구간(폐구간). ``scope`` = ``global`` | ``kr``."""

    name: str
    start: date
    end: date
    scope: str


@dataclass(frozen=True)
class Coverage:
    """위기 커버리지 측정 — 객관 트리거가 큐레이션 위기를 얼마나 잡았나."""

    total: int
    caught: int
    missed: list[str]
    kr_total: int
    kr_caught: int


def _as_date(value: Any) -> date:
    """``date`` 또는 ISO 문자열 → ``date``."""
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def load_crisis_events(path: Path) -> list[CrisisEvent]:
    """``crisis_events.yml`` 파싱. 없거나 항목이 깨졌으면 그 항목만 건너뛴다(fail-open)."""
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = raw.get("events", []) if isinstance(raw, dict) else []
    out: list[CrisisEvent] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                CrisisEvent(
                    name=str(item["name"]),
                    start=_as_date(item["start"]),
                    end=_as_date(item["end"]),
                    scope=str(item.get("scope", "global")),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _fetch_exog(symbol: str, start: date, end: date) -> ExogSeries:
    """yfinance 일별 종가(예: ^VIX, KRW=X). 실패/빈 결과면 빈 리스트(fail-open)."""
    try:
        import yfinance as yf

        hist = yf.Ticker(symbol).history(
            start=start.isoformat(), end=end.isoformat(), auto_adjust=False
        )
        if hist is None or hist.empty:
            return []
        out: ExogSeries = []
        for ts, rec in hist.iterrows():
            try:
                out.append((ts.date(), Decimal(str(rec["Close"]))))
            except (ArithmeticError, ValueError, TypeError, KeyError):
                continue
        return sorted(out, key=lambda x: x[0])
    except Exception:
        logging.warning("exog fetch failed for %s — risk-off triggers disabled for it", symbol)
        return []


def load_exog_series(symbol: str, start: date, end: date, cache_dir: Path) -> ExogSeries:
    """외생 시계열(종가) — 디스크 캐시·fail-open. ``(date, close)`` 오름차순."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace("^", "_").replace("=", "_")
    cache_path = cache_dir / f"exog_{safe}_{start.isoformat()}_{end.isoformat()}.json"
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            return [(date.fromisoformat(str(d)), Decimal(str(c))) for d, c in data]
        except Exception:
            logging.warning("exog cache read failed for %s — refetching", symbol)
    series = _fetch_exog(symbol, start, end)
    if series:
        try:
            cache_path.write_text(
                json.dumps([[d.isoformat(), str(c)] for d, c in series]), encoding="utf-8"
            )
        except Exception:
            logging.warning("exog cache write failed for %s — continuing", symbol)
    return series


def level_jump_risk_off_dates(
    series: ExogSeries,
    rebalance_dates: list[date],
    *,
    level: Decimal | None,
    jump_pct: Decimal,
) -> set[date]:
    """각 리밸런스일 T 의 risk_off 판정(≤T 데이터만, 룩어헤드 0).

    risk_off(T) = (``level`` 지정 AND 최신 종가 ≥ level) OR (최신 1일 변화율 > ``jump_pct``).
    T 이하 표본이 없으면 평가 제외, 1개뿐이면 급등은 평가 불가(보수적: off 아님).
    """
    if not series:
        return set()
    ordered = sorted(series, key=lambda x: x[0])
    out: set[date] = set()
    for t in rebalance_dates:
        asof = [(d, c) for d, c in ordered if d <= t]
        if not asof:
            continue
        last_close = asof[-1][1]
        triggered = level is not None and last_close >= level
        if not triggered and len(asof) >= 2:
            prev_close = asof[-2][1]
            if prev_close > 0 and (last_close / prev_close - Decimal("1")) > jump_pct:
                triggered = True
        if triggered:
            out.add(t)
    return out


def vix_risk_off_dates(
    series: ExogSeries,
    rebalance_dates: list[date],
    *,
    level: Decimal = VIX_LEVEL,
    jump_pct: Decimal = VIX_JUMP_PCT,
) -> set[date]:
    """VIX 트리거 — 절대수준(공포 임계) OR 1일 급등."""
    return level_jump_risk_off_dates(series, rebalance_dates, level=level, jump_pct=jump_pct)


def fx_risk_off_dates(
    series: ExogSeries,
    rebalance_dates: list[date],
    *,
    jump_pct: Decimal = FX_JUMP_PCT,
) -> set[date]:
    """원/달러 트리거 — 1일 급등만(절대수준 미사용)."""
    return level_jump_risk_off_dates(series, rebalance_dates, level=None, jump_pct=jump_pct)


def _rebals_in_window(rebalance_dates: list[date], event: CrisisEvent) -> list[date]:
    """위기 구간[start, end] 안의 리밸런스일."""
    return [t for t in rebalance_dates if event.start <= t <= event.end]


def coverage(
    events: list[CrisisEvent], trigger_dates: set[date], rebalance_dates: list[date]
) -> Coverage:
    """객관 트리거가 큐레이션 위기를 몇 % 잡는지(커버리지 측정자, §3).

    한 위기 = 'caught' ⟺ 그 구간 안 리밸런스일 중 ``trigger_dates`` 에 든 게 하나라도 있음.
    구간 안에 리밸런스일이 하나도 없는 위기는 **평가 제외**(측정 불가). ``missed`` 는 평가
    대상 중 못 잡은 위기 이름(특히 ``kr`` 스코프 미스가 뉴스 탐지기의 가치 신호).
    """
    evaluable = [ev for ev in events if _rebals_in_window(rebalance_dates, ev)]
    caught = [
        ev
        for ev in evaluable
        if any(t in trigger_dates for t in _rebals_in_window(rebalance_dates, ev))
    ]
    caught_names = {ev.name for ev in caught}
    missed = [ev.name for ev in evaluable if ev.name not in caught_names]
    kr = [ev for ev in evaluable if ev.scope == "kr"]
    kr_caught = [ev for ev in kr if ev.name in caught_names]
    return Coverage(
        total=len(evaluable),
        caught=len(caught),
        missed=missed,
        kr_total=len(kr),
        kr_caught=len(kr_caught),
    )


__all__ = [
    "FX_JUMP_PCT",
    "VIX_JUMP_PCT",
    "VIX_LEVEL",
    "Coverage",
    "CrisisEvent",
    "ExogSeries",
    "coverage",
    "fx_risk_off_dates",
    "level_jump_risk_off_dates",
    "load_crisis_events",
    "load_exog_series",
    "vix_risk_off_dates",
]
