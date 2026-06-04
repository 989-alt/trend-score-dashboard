"""``backend.stops`` 트레일링 손절(무상태) 판정 단위 테스트.

검증 대상:
- ``trailing_stop_price`` = peak*(1-pct/100).
- ``evaluate_sell`` 트레일링 우선 규칙 (trailing → ma200_break → None) + 양수 가드.
- ``compute_trailing_stop`` — 가격이력에서 무상태로 (peak, stop) 산출.

원칙: 금액은 ``Decimal`` (float 금지).
"""

from __future__ import annotations

from decimal import Decimal

from backend.stops import compute_trailing_stop, evaluate_sell, trailing_stop_price
from tests.conftest import make_rows

_PCT = Decimal("8")


# ---------------------------------------------------------------------------
# trailing_stop_price
# ---------------------------------------------------------------------------


def test_trailing_stop_price_is_peak_times_one_minus_pct() -> None:
    # peak 100, 8% → 92.
    assert trailing_stop_price(Decimal("100"), Decimal("8")) == Decimal("92")


def test_trailing_stop_price_zero_pct_equals_peak() -> None:
    assert trailing_stop_price(Decimal("123.45"), Decimal("0")) == Decimal("123.45")


# ---------------------------------------------------------------------------
# evaluate_sell — 트레일링 발동
# ---------------------------------------------------------------------------


def test_evaluate_sell_trailing_triggers_at_threshold() -> None:
    # stop = 100*0.92 = 92. current==92 → 경계 포함(<=) → trailing_stop.
    assert (
        evaluate_sell(current=Decimal("92"), peak=Decimal("100"), ma200=None, pct=_PCT)
        == "trailing_stop"
    )


def test_evaluate_sell_trailing_triggers_below_threshold() -> None:
    assert (
        evaluate_sell(current=Decimal("90"), peak=Decimal("100"), ma200=None, pct=_PCT)
        == "trailing_stop"
    )


def test_evaluate_sell_above_threshold_holds() -> None:
    # current 95 > stop 92 → 보유 유지.
    assert evaluate_sell(current=Decimal("95"), peak=Decimal("100"), ma200=None, pct=_PCT) is None


# ---------------------------------------------------------------------------
# evaluate_sell — 200일선 이탈
# ---------------------------------------------------------------------------


def test_evaluate_sell_ma200_break_when_below_ma200() -> None:
    # 트레일링 미발동(peak 없음) + current < ma200 → ma200_break.
    assert (
        evaluate_sell(current=Decimal("95"), peak=None, ma200=Decimal("100"), pct=_PCT)
        == "ma200_break"
    )


def test_evaluate_sell_ma200_equal_holds() -> None:
    # current == ma200 은 이탈 아님(< 만 발동) → None.
    assert evaluate_sell(current=Decimal("100"), peak=None, ma200=Decimal("100"), pct=_PCT) is None


# ---------------------------------------------------------------------------
# evaluate_sell — 양수 가드 (exit_manager: peak>0 · ma200>0)
# ---------------------------------------------------------------------------


def test_evaluate_sell_nonpositive_peak_skips_trailing() -> None:
    # peak<=0 은 미관측으로 보고 트레일링 검사를 건너뛴다 (0이면 stop 0이라 오발동 방지).
    assert evaluate_sell(current=Decimal("0"), peak=Decimal("0"), ma200=None, pct=_PCT) is None


def test_evaluate_sell_nonpositive_ma200_skips_ma200_break() -> None:
    # ma200<=0 은 미관측으로 보고 200일선 검사를 건너뛴다.
    assert evaluate_sell(current=Decimal("50"), peak=None, ma200=Decimal("0"), pct=_PCT) is None


# ---------------------------------------------------------------------------
# evaluate_sell — 우선순위 (트레일링 > ma200)
# ---------------------------------------------------------------------------


def test_evaluate_sell_trailing_takes_priority_over_ma200() -> None:
    # 둘 다 발동 조건이지만 트레일링이 우선 → trailing_stop.
    # stop = 92, current 90 <= 92 (트레일링 O), 90 < ma200 95 (ma200 도 O).
    assert (
        evaluate_sell(current=Decimal("90"), peak=Decimal("100"), ma200=Decimal("95"), pct=_PCT)
        == "trailing_stop"
    )


def test_evaluate_sell_ma200_when_trailing_not_triggered() -> None:
    # 트레일링 미발동(current 95 > stop 92)이지만 ma200(98) 아래 → ma200_break.
    assert (
        evaluate_sell(current=Decimal("95"), peak=Decimal("100"), ma200=Decimal("98"), pct=_PCT)
        == "ma200_break"
    )


def test_evaluate_sell_both_none_inputs_holds() -> None:
    assert evaluate_sell(current=Decimal("100"), peak=None, ma200=None, pct=_PCT) is None


# ---------------------------------------------------------------------------
# compute_trailing_stop — 무상태 (peak, stop) 산출
# ---------------------------------------------------------------------------


def test_compute_trailing_stop_peak_from_recent_high() -> None:
    # 최근 window 봉 종가 최고가가 peak (current 가 더 낮으면 그 고점 유지).
    rows = make_rows([100, 120, 110, 105, 95])
    peak, stop = compute_trailing_stop(rows, Decimal("95"), window=60, pct=_PCT)
    assert peak == Decimal("120")  # max(최근 종가들, current)
    assert stop == Decimal("110.4")  # 120*0.92
    # current 95 <= stop 110.4 → 트레일링 발동.
    assert evaluate_sell(current=Decimal("95"), peak=peak, ma200=None, pct=_PCT) == "trailing_stop"


def test_compute_trailing_stop_current_above_history_uses_current() -> None:
    # 현재가가 이력 최고가보다 높으면(신고가 경신) peak = current.
    rows = make_rows([100, 110, 105])
    peak, stop = compute_trailing_stop(rows, Decimal("130"), window=60, pct=_PCT)
    assert peak == Decimal("130")
    assert stop == Decimal("119.6")  # 130*0.92


def test_compute_trailing_stop_window_limits_lookback() -> None:
    # window 가 작으면 그 봉 수만큼만 본다 — 윈도 밖의 옛 고점은 무시.
    rows = make_rows([200, 100, 101, 102])
    # window=2 → 최근 2봉(101,102) ∪ current(102) → peak 102 (옛 고점 200 무시).
    peak, _ = compute_trailing_stop(rows, Decimal("102"), window=2, pct=_PCT)
    assert peak == Decimal("102")


def test_compute_trailing_stop_empty_rows_uses_current() -> None:
    # rows 가 비면 current 만으로 peak.
    peak, stop = compute_trailing_stop([], Decimal("50"), window=60, pct=_PCT)
    assert peak == Decimal("50")
    assert stop == Decimal("46")  # 50*0.92


def test_compute_trailing_stop_zero_window_uses_full_history() -> None:
    # window<=0 이면 전체 이력으로 peak.
    rows = make_rows([100, 150, 120])
    peak, _ = compute_trailing_stop(rows, Decimal("120"), window=0, pct=_PCT)
    assert peak == Decimal("150")
