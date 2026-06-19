"""매매 의사결정 엔진 — 점수 스냅샷 + 현재 보유 → 진입/청산 종목.

순수 함수(네트워크 0·결정론·순서안정). 실제 주문은 ``loop.TraderLoop`` 가 수행한다.

전략:
- 진입 후보 = 적격(``eligible``) AND 등급 매수/적극매수, 점수 내림차순 정렬.
- 목표 = 상위 ``top_n``. 이력관성(hysteresis)으로 보유 종목은 ``top_n*1.5`` 위면 유지
  (경계 근처에서 사고팔고 반복하는 채터링 방지).
- 청산 = 스냅샷 이탈 / 손절 발동(``sell_alert``) / 순위 이탈.

매수 종목 선정(P10): ``GeminiDecider`` 주입 + ``trader_use_llm`` 면 적격 후보 안에서 Gemini 2.5 Pro
가 매수/재량매도를 고른다. **안전 게이트(적격 필터·강제 손절 매도)는 LLM 무관하게 항상 강제**하고,
LLM 이 ``None``(실패/오류) 을 주면 결정론 점수상위로 폴백한다(매매 연속성).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.config import Settings
from backend.schemas import Grade, ScoreEntry
from backend.trader.positions import PositionManager

if TYPE_CHECKING:
    from backend.trader.gemini_decider import GeminiDecider

logger = logging.getLogger(__name__)

_BUY_GRADES = (Grade.STRONG_BUY, Grade.BUY)


@dataclass(frozen=True)
class Decisions:
    """한 사이클 결정 — 매수 종목, 매도 종목+사유."""

    buys: list[str]
    sells: list[tuple[str, str]]


class StrategyEngine:
    """점수 스냅샷·보유 상태로 진입/청산을 결정(순수·결정론).

    ``decider`` 주입 시 매수 선정만 Gemini 로 위임하고, 안전 게이트는 그대로 강제한다.
    """

    def __init__(self, settings: Settings, *, decider: GeminiDecider | None = None) -> None:
        self._s = settings
        self._decider = decider

    def decide(self, entries: list[ScoreEntry], pm: PositionManager, *, top_n: int) -> Decisions:
        """``entries``(점수 스냅샷) + ``pm``(보유) → 진입/청산 결정.

        매도를 먼저 산정한 뒤, 매도 대상이 아닌 종목만 신규 매수한다. 강제 매도(스냅샷이탈·손절)는
        LLM 경로와 무관하게 항상 포함된다.
        """
        ranked = sorted(
            (e for e in entries if e.eligible and e.grade in _BUY_GRADES),
            key=lambda e: e.score,
            reverse=True,
        )
        by_ticker = {e.ticker: e for e in entries}
        held = pm.held_tickers()

        # ── 강제 매도(안전 게이트) — 항상 적용, LLM 이 건너뛸 수 없음 ──────────────
        forced_sells: list[tuple[str, str]] = []
        for t in held:
            entry = by_ticker.get(t)
            if entry is None:
                forced_sells.append((t, "청산:스냅샷이탈"))
            elif entry.sell_alert:
                forced_sells.append((t, _sell_reason(entry)))
        forced_set = {t for t, _ in forced_sells}

        # ── 매수(+재량 매도) 선정 — LLM 우선, 실패 시 결정론 폴백 ────────────────
        llm = self._llm_decision(ranked, pm, top_n) if self._use_llm() else None
        if llm is not None:
            buys, disc_sells = self._apply_llm(llm, ranked=ranked, held=held, forced=forced_set)
        else:
            buys, disc_sells = self._deterministic(ranked, by_ticker, pm, top_n, forced_set)

        sells = forced_sells + disc_sells
        return Decisions(buys=buys, sells=sells)

    def _use_llm(self) -> bool:
        """LLM 결정 경로 사용 여부 — 설정 ON + decider 주입 시에만."""
        return self._s.trader_use_llm and self._decider is not None

    def _llm_decision(
        self, ranked: list[ScoreEntry], pm: PositionManager, top_n: int
    ) -> Decisions | None:
        """적격 후보·보유·현금을 Gemini 에 넘겨 결정 받기. 실패 시 ``None``(엔진이 폴백)."""
        assert self._decider is not None  # _use_llm 가 보장
        positions = [p for p in (pm.position(t) for t in pm.held_tickers()) if p is not None]
        market = ranked[0].market if ranked else "KR"
        return self._decider.decide(market, ranked, positions, pm.cash, top_n)

    def _apply_llm(
        self,
        llm: Decisions,
        *,
        ranked: list[ScoreEntry],
        held: set[str],
        forced: set[str],
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """LLM 결정에 안전 제약 강제 — 적격·미보유만 매수, 강제매도 중복 제거.

        매수: 적격 후보(``ranked``) 이면서 미보유·강제매도 대상 아님. (반-환각은 decider 가 1차
        필터하지만 여기서 다시 적격/보유 기준으로 교차검증.)
        재량 매도: 보유 종목이면서 강제매도에 이미 없는 것만(중복 방지).
        """
        eligible = {e.ticker for e in ranked}
        disc_sells = [(t, r) for t, r in llm.sells if t in held and t not in forced]
        blocked = held | forced | {t for t, _ in disc_sells}
        buys = [t for t in llm.buys if t in eligible and t not in blocked]
        return buys, disc_sells

    def _deterministic(
        self,
        ranked: list[ScoreEntry],
        by_ticker: dict[str, ScoreEntry],
        pm: PositionManager,
        top_n: int,
        forced: set[str],
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """결정론 폴백 — 점수상위 top_n 매수 + 이력관성 기반 순위이탈 매도."""
        target = [e.ticker for e in ranked[:top_n]]
        keep_set = {e.ticker for e in ranked[: int(top_n * 1.5)]}
        held = pm.held_tickers()

        rank_sells = [(t, "청산:순위이탈") for t in held if t not in forced and t not in keep_set]
        sold = forced | {t for t, _ in rank_sells}
        buys = [t for t in target if t not in held and t not in sold]
        return buys, rank_sells


def _sell_reason(entry: ScoreEntry) -> str:
    """``sell_alert`` 종목의 한국어 청산 사유."""
    if entry.sell_reason == "trailing_stop":
        return "청산:트레일링손절"
    if entry.sell_reason == "ma200_break":
        return "청산:200일선이탈"
    return "청산:손절"


__all__ = ["Decisions", "StrategyEngine"]
