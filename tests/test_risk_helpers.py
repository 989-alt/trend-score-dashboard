from decimal import Decimal

from backend.scoring import atr_stop_price, suggested_weight


def test_atr_stop_price() -> None:
    # 진입 100, ATR 4, 배수 2 → 손절 100 - 8 = 92
    assert atr_stop_price(Decimal("100"), Decimal("4"), mult=Decimal("2")) == Decimal("92")


def test_atr_stop_price_floors_at_zero() -> None:
    assert atr_stop_price(Decimal("5"), Decimal("4"), mult=Decimal("2")) == Decimal("0")


def test_suggested_weight_caps_and_scales() -> None:
    # weight = risk_pct / (mult × atr/price), 상한 cap.
    # atr/price=0.04, mult=2 → 분모 0.08; risk 0.01 → 0.125 → cap 0.10
    w = suggested_weight(
        Decimal("0.04"), risk_pct=Decimal("0.01"), mult=Decimal("2"), cap=Decimal("0.10")
    )
    assert w == Decimal("0.10")


def test_suggested_weight_zero_atr_returns_zero() -> None:
    assert suggested_weight(
        Decimal("0"), risk_pct=Decimal("0.01"), mult=Decimal("2"), cap=Decimal("0.10")
    ) == Decimal("0")
