from decimal import Decimal

from backend.config import get_settings


def test_fallback_c_params_present_with_defaults() -> None:
    s = get_settings()
    assert s.weight_52w_fallback == Decimal("0.18")  # near_52w 후보 기본값
    assert s.regime_window == 25 and s.regime_threshold == 5
    assert s.regime_drop == Decimal("0.998")
    assert s.atr_stop_mult == Decimal("2")
    assert s.risk_pct == Decimal("0.01") and s.max_weight_pct == Decimal("0.10")
