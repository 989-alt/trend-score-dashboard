"""테스트 공유 헬퍼 — OHLCV 행 생성기 + 추세 fixture.

산식 테스트는 OHLCVRow 리스트가 필요하다. ``make_rows`` 는 종가 리스트에서
open/high/low/volume/date 를 결정론으로 채워 주고, ``make_uptrend_rows`` /
``make_downtrend_rows`` 는 200일선·52주 신고가 판정을 만족하는 시계열을 만든다.

원칙: 가격·수량은 ``Decimal``, 날짜는 ``date`` (오름차순). float 금지.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from backend.schemas import OHLCVRow

_DEFAULT_START = date(2024, 1, 1)
_DEFAULT_VOLUME = Decimal("1000000")


def _as_decimal(value: object) -> Decimal:
    """int/float/str/Decimal → ``Decimal`` (float 은 문자열 경유로 정밀도 보존)."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def make_rows(
    closes: list[object],
    *,
    volumes: list[object] | None = None,
    start_date: date | None = None,
) -> list[OHLCVRow]:
    """종가 리스트 → OHLCVRow 리스트 (open/high/low/volume/date 자동).

    상승 봉(전일 대비 종가 ≥ 전일 종가)은 양봉(open<close), 하락 봉은 음봉(open>close)
    으로 만들고, high/low 는 open·close 를 감싸도록 약간의 여유(±0.5%)를 둔다. 첫 봉은
    종가를 기준으로 양봉 처리한다.

    Args:
        closes: 종가 시퀀스(길이 ≥ 1).
        volumes: 봉별 거래량. 미지정 시 모두 기본값. 길이는 ``closes`` 와 같아야 한다.
        start_date: 첫 봉 날짜. 미지정 시 ``2024-01-01``. 이후 봉은 +1일.
    """
    if not closes:
        return []
    close_decimals = [_as_decimal(c) for c in closes]
    if volumes is None:
        vol_decimals = [_DEFAULT_VOLUME for _ in close_decimals]
    else:
        if len(volumes) != len(close_decimals):
            raise ValueError("volumes length must match closes length")
        vol_decimals = [_as_decimal(v) for v in volumes]
    start = start_date or _DEFAULT_START

    rows: list[OHLCVRow] = []
    prev_close: Decimal | None = None
    for i, close in enumerate(close_decimals):
        if prev_close is None or close >= prev_close:
            # 양봉: 시가를 종가보다 1% 낮게.
            open_price = close * Decimal("0.99")
        else:
            # 음봉: 시가를 종가보다 1% 높게.
            open_price = close * Decimal("1.01")
        high = max(open_price, close) * Decimal("1.005")
        low = min(open_price, close) * Decimal("0.995")
        rows.append(
            OHLCVRow(
                date=start + timedelta(days=i),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=vol_decimals[i],
            )
        )
        prev_close = close
    return rows


def make_uptrend_rows(n: int = 260) -> list[OHLCVRow]:
    """우상향 시계열 — 200일선 위 + 52주 신고가 근접.

    종가를 완만히 단조 증가시켜 현재가가 200일 MA 위, 마지막 봉이 룩백 내 최고가가
    되도록 한다(``above_ma200`` True, ``proximity_to_52w_high`` ≈ 1).
    """
    base = Decimal("100")
    step = Decimal("0.5")
    closes = [base + step * Decimal(i) for i in range(n)]
    return make_rows(closes)


def make_downtrend_rows(n: int = 260) -> list[OHLCVRow]:
    """우하향 시계열 — 200일선 아래.

    종가를 완만히 단조 감소시켜 현재가가 200일 MA 아래가 되도록 한다
    (``above_ma200`` False). 종가는 양수를 유지한다.
    """
    base = Decimal("200")
    step = Decimal("0.5")
    closes = [base - step * Decimal(i) for i in range(n)]
    return make_rows(closes)


@pytest.fixture
def uptrend_rows() -> list[OHLCVRow]:
    """200일선 위·신고가 근접 시계열 fixture."""
    return make_uptrend_rows()


@pytest.fixture
def downtrend_rows() -> list[OHLCVRow]:
    """200일선 아래 시계열 fixture."""
    return make_downtrend_rows()
