"""매매 의사결정 엔진 — 점수 스냅샷 + 현재 보유 → 진입/청산 종목.

순수 함수(네트워크 0·결정론·순서안정). 실제 주문은 ``loop.TraderLoop`` 가 수행한다.

전략:
- 진입 후보 = 적격(``eligible``) AND 등급 매수/적극매수, 점수 내림차순 정렬.
- 목표 = 상위 ``top_n``. 이력관성(hysteresis)으로 보유 종목은 ``top_n*1.5`` 위면 유지
  (경계 근처에서 사고팔고 반복하는 채터링 방지).
- 청산 = 스냅샷 이탈 / 손절 발동(``sell_alert``) / 순위 이탈.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.config import Settings
from backend.schemas import Grade, ScoreEntry
from backend.trader.positions import PositionManager

_BUY_GRADES = (Grade.STRONG_BUY, Grade.BUY)


@dataclass(frozen=True)
class Decisions:
    """한 사이클 결정 — 매수 종목, 매도 종목+사유."""

    buys: list[str]
    sells: list[tuple[str, str]]


class StrategyEngine:
    """점수 스냅샷·보유 상태로 진입/청산을 결정(순수·결정론)."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def decide(self, entries: list[ScoreEntry], pm: PositionManager, *, top_n: int) -> Decisions:
        """``entries``(점수 스냅샷) + ``pm``(보유) → 진입/청산 결정.

        매도를 먼저 산정한 뒤, 매도 대상이 아닌 상위 종목만 신규 매수한다.
        """
        ranked = sorted(
            (e for e in entries if e.eligible and e.grade in _BUY_GRADES),
            key=lambda e: e.score,
            reverse=True,
        )
        target = [e.ticker for e in ranked[:top_n]]
        keep_set = {e.ticker for e in ranked[: int(top_n * 1.5)]}
        by_ticker = {e.ticker: e for e in entries}

        sells: list[tuple[str, str]] = []
        for t in pm.held_tickers():
            entry = by_ticker.get(t)
            if entry is None:
                sells.append((t, "청산:스냅샷이탈"))
            elif entry.sell_alert:
                sells.append((t, _sell_reason(entry)))
            elif t not in keep_set:
                sells.append((t, "청산:순위이탈"))

        sell_tickers = {t for t, _ in sells}
        held = pm.held_tickers()
        buys = [t for t in target if t not in held and t not in sell_tickers]

        return Decisions(buys=buys, sells=sells)


def _sell_reason(entry: ScoreEntry) -> str:
    """``sell_alert`` 종목의 한국어 청산 사유."""
    if entry.sell_reason == "trailing_stop":
        return "청산:트레일링손절"
    if entry.sell_reason == "ma200_break":
        return "청산:200일선이탈"
    return "청산:손절"


__all__ = ["Decisions", "StrategyEngine"]
