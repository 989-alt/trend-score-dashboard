"""레짐 게이트 — 지수 분산일(distribution day) 기반 risk_off 판정. 결정론·Decimal."""

from __future__ import annotations

from decimal import Decimal

from backend.schemas import OHLCVRow


def count_distribution_days(
    rows: list[OHLCVRow], *, window: int, drop: Decimal = Decimal("0.998")
) -> int:
    """직전 ``window`` 거래일의 분산일 수.

    분산일 = 당일 종가 ≤ 전일 종가 × ``drop`` AND 당일 거래량 > 전일 거래량.
    첫 봉은 전일이 없어 평가 제외. 표본 부족(<2)이면 0.
    """
    if len(rows) < 2:
        return 0
    recent = rows[-window:] if window > 0 else rows
    # recent[0] 의 전일은 그 앞 봉. recent 가 전체의 suffix 이므로 전일 인덱스 보정.
    start = len(rows) - len(recent)
    count = 0
    for i in range(start, len(rows)):
        if i == 0:
            continue
        today, prev = rows[i], rows[i - 1]
        if today.close <= prev.close * drop and today.volume > prev.volume:
            count += 1
    return count


def is_risk_off(
    rows: list[OHLCVRow], *, window: int = 25, threshold: int = 5, drop: Decimal = Decimal("0.998")
) -> bool:
    """직전 ``window`` 거래일 분산일 ≥ ``threshold`` 면 risk_off.

    표본 부족이면 False(보수적: 진입 허용).
    """
    if len(rows) < window:
        return False
    return count_distribution_days(rows, window=window, drop=drop) >= threshold


__all__ = ["count_distribution_days", "is_risk_off"]
