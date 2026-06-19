"""StrategyEngine 단위테스트 — 진입/청산 결정·이력관성·청산사유. 네트워크 0."""

from __future__ import annotations

from decimal import Decimal

from backend.config import Settings
from backend.schemas import Grade, ScoreEntry, SellReason
from backend.trader.models import Balance, HoldingPosition
from backend.trader.positions import PositionManager
from backend.trader.strategy import StrategyEngine


def _entry(
    ticker: str,
    score: str,
    *,
    grade: Grade = Grade.BUY,
    eligible: bool = True,
    sell_alert: bool = False,
    sell_reason: SellReason | None = None,
) -> ScoreEntry:
    """테스트용 최소 ScoreEntry."""
    return ScoreEntry(
        ticker=ticker,
        name=ticker,
        market="KR",
        price=Decimal("10000"),
        score=Decimal(score),
        grade=grade,
        eligible=eligible,
        sell_alert=sell_alert,
        sell_reason=sell_reason,
    )


def _pm(*held: str) -> PositionManager:
    """주어진 종목을 1주씩 보유한 PositionManager."""
    pm = PositionManager()
    pm.sync(
        Balance(
            cash=Decimal("0"),
            total_eval=Decimal("0"),
            positions=[HoldingPosition(ticker=t, qty=1, avg_price=Decimal("10000")) for t in held],
        )
    )
    return pm


def test_buys_top_n_excluding_held() -> None:
    """상위 top_n 중 미보유만 매수. 매도 없음."""
    engine = StrategyEngine(Settings())
    entries = [
        _entry("A", "90"),
        _entry("B", "80"),
        _entry("C", "70"),
    ]
    decisions = engine.decide(entries, _pm("A"), top_n=3)
    assert decisions.buys == ["B", "C"]
    assert decisions.sells == []


def test_buy_order_is_score_desc() -> None:
    """매수 순서는 점수 내림차순(순서안정)."""
    engine = StrategyEngine(Settings())
    entries = [_entry("C", "70"), _entry("A", "90"), _entry("B", "80")]
    decisions = engine.decide(entries, _pm(), top_n=3)
    assert decisions.buys == ["A", "B", "C"]


def test_ineligible_and_nonbuy_grade_excluded_from_target() -> None:
    """부적격·관망/회피 등급은 진입 후보에서 제외."""
    engine = StrategyEngine(Settings())
    entries = [
        _entry("A", "90", eligible=False),
        _entry("B", "85", grade=Grade.HOLD),
        _entry("C", "80", grade=Grade.AVOID),
        _entry("D", "75", grade=Grade.STRONG_BUY),
    ]
    decisions = engine.decide(entries, _pm(), top_n=5)
    assert decisions.buys == ["D"]


def test_hysteresis_keeps_held_within_top_n_times_1_5() -> None:
    """보유 종목이 top_n*1.5 안이면 순위이탈로 팔지 않음(채터링 방지)."""
    engine = StrategyEngine(Settings())
    # top_n=2 → target=[A,B], keep_set=상위 3개=[A,B,C]. C 보유는 유지.
    entries = [_entry("A", "90"), _entry("B", "80"), _entry("C", "70")]
    decisions = engine.decide(entries, _pm("C"), top_n=2)
    # C 는 순위이탈(target 밖)이지만 keep_set(상위3) 안이라 매도하지 않음.
    assert decisions.sells == []
    # 미보유 상위 A·B 는 신규 매수(C 는 target 밖이라 매수 대상 아님).
    assert decisions.buys == ["A", "B"]


def test_rank_drop_outside_keep_set_is_sold() -> None:
    """보유 종목이 top_n*1.5 밖이면 순위이탈로 매도."""
    engine = StrategyEngine(Settings())
    # top_n=2 → keep_set=상위3=[A,B,C]. D(4위) 보유는 이탈.
    entries = [_entry("A", "90"), _entry("B", "80"), _entry("C", "70"), _entry("D", "60")]
    decisions = engine.decide(entries, _pm("D"), top_n=2)
    assert ("D", "청산:순위이탈") in decisions.sells
    assert decisions.buys == ["A", "B"]


def test_sell_alert_trailing_stop_reason() -> None:
    """sell_alert + trailing_stop → 트레일링손절 사유."""
    engine = StrategyEngine(Settings())
    entries = [_entry("A", "90", sell_alert=True, sell_reason="trailing_stop")]
    decisions = engine.decide(entries, _pm("A"), top_n=5)
    assert decisions.sells == [("A", "청산:트레일링손절")]
    assert decisions.buys == []


def test_sell_alert_ma200_break_reason() -> None:
    """sell_alert + ma200_break → 200일선이탈 사유."""
    engine = StrategyEngine(Settings())
    entries = [_entry("A", "90", sell_alert=True, sell_reason="ma200_break")]
    decisions = engine.decide(entries, _pm("A"), top_n=5)
    assert decisions.sells == [("A", "청산:200일선이탈")]


def test_sell_alert_without_reason_generic() -> None:
    """sell_alert 인데 사유 None → 일반 손절."""
    engine = StrategyEngine(Settings())
    entries = [_entry("A", "90", sell_alert=True, sell_reason=None)]
    decisions = engine.decide(entries, _pm("A"), top_n=5)
    assert decisions.sells == [("A", "청산:손절")]


def test_held_not_in_snapshot_is_sold() -> None:
    """보유 중인데 스냅샷에 없으면 안전상 매도(스냅샷이탈)."""
    engine = StrategyEngine(Settings())
    entries = [_entry("A", "90")]
    decisions = engine.decide(entries, _pm("Z"), top_n=5)
    assert decisions.sells == [("Z", "청산:스냅샷이탈")]
    assert decisions.buys == ["A"]


def test_sell_alert_takes_precedence_over_rank() -> None:
    """손절 발동은 순위 안에 있어도 우선 매도."""
    engine = StrategyEngine(Settings())
    entries = [_entry("A", "90", sell_alert=True, sell_reason="trailing_stop")]
    decisions = engine.decide(entries, _pm("A"), top_n=5)
    assert decisions.sells == [("A", "청산:트레일링손절")]
