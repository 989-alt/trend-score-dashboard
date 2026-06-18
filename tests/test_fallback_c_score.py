from datetime import date
from decimal import Decimal

from backend.backtest.run import _score_at
from backend.config import get_settings
from tests.fixtures.backtest_synth import make_panel


def test_fallback_c_differs_from_baseline_and_reweights_52w() -> None:
    panel = make_panel()
    settings = get_settings()
    t = date(2023, 12, 1)
    base = dict(_score_at(panel, t, settings, preset="baseline"))
    fc = dict(_score_at(panel, t, settings, preset="fallback_c"))
    assert set(fc) == set(base)  # 동일 후보군(eligible)
    assert fc != base  # 재가중으로 점수 변화
    assert all(v >= 0 for v in fc.values())


def test_fallback_c_weight_sweep_changes_scores(monkeypatch) -> None:
    panel = make_panel()
    t = date(2023, 12, 1)
    s_low = get_settings()
    monkeypatch.setattr(s_low, "weight_52w_fallback", Decimal("0.12"))
    s_high = get_settings()
    monkeypatch.setattr(s_high, "weight_52w_fallback", Decimal("0.30"))
    fc_low = dict(_score_at(panel, t, s_low, preset="fallback_c"))
    fc_high = dict(_score_at(panel, t, s_high, preset="fallback_c"))
    assert fc_low != fc_high  # near_52w 가중치 스윕이 점수를 바꾼다
