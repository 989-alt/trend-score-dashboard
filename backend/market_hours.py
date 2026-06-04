"""장시간·영업일 게이트 — 스냅샷 신선도·다음 갱신 시각 판정.

원칙:
- 모든 비교는 timezone-aware (``zoneinfo`` 기반). 서버 TZ 에 의존하지 않는다.
- KR 09:00–15:30 ``Asia/Seoul``, US 09:30–16:00 ``America/New_York`` (DST 자동).
- 주말·휴일 제외. 휴일 목록은 2026년 근사 집합(상수)으로 둔다.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.schemas import Market

_TZ: dict[Market, ZoneInfo] = {
    "KR": ZoneInfo("Asia/Seoul"),
    "US": ZoneInfo("America/New_York"),
}

# 정규장 개장/마감 (해당 시장 로컬 시각).
_OPEN: dict[Market, time] = {
    "KR": time(9, 0),
    "US": time(9, 30),
}
_CLOSE: dict[Market, time] = {
    "KR": time(15, 30),
    "US": time(16, 0),
}

# 2026 공휴일 근사 집합 (정규장 휴장일). 주말은 별도 판정하므로 평일 휴일만 의미가 있다.
_KR_HOLIDAYS_2026: frozenset[date] = frozenset(
    {
        date(2026, 1, 1),  # 신정
        date(2026, 2, 16),  # 설 연휴
        date(2026, 2, 17),  # 설날
        date(2026, 2, 18),  # 설 연휴
        date(2026, 3, 1),  # 삼일절 (일요일)
        date(2026, 3, 2),  # 삼일절 대체공휴일
        date(2026, 5, 5),  # 어린이날
        date(2026, 5, 24),  # 부처님오신날 (일요일)
        date(2026, 5, 25),  # 부처님오신날 대체공휴일
        date(2026, 6, 6),  # 현충일 (토요일)
        date(2026, 8, 15),  # 광복절 (토요일)
        date(2026, 9, 24),  # 추석 연휴
        date(2026, 9, 25),  # 추석
        date(2026, 9, 26),  # 추석 연휴 (토요일)
        date(2026, 10, 3),  # 개천절 (토요일)
        date(2026, 10, 9),  # 한글날
        date(2026, 12, 25),  # 성탄절
    }
)

_US_HOLIDAYS_2026: frozenset[date] = frozenset(
    {
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 19),  # MLK Jr. Day (3rd Mon Jan)
        date(2026, 2, 16),  # Presidents' Day (3rd Mon Feb)
        date(2026, 4, 3),  # Good Friday
        date(2026, 5, 25),  # Memorial Day (last Mon May)
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),  # Independence Day 관측 (7/4 토 → 7/3 금)
        date(2026, 9, 7),  # Labor Day (1st Mon Sep)
        date(2026, 11, 26),  # Thanksgiving (4th Thu Nov)
        date(2026, 12, 25),  # Christmas
    }
)

_HOLIDAYS: dict[Market, frozenset[date]] = {
    "KR": _KR_HOLIDAYS_2026,
    "US": _US_HOLIDAYS_2026,
}


def is_trading_day(market: Market, d: date) -> bool:
    """``market`` 기준 ``d`` 가 영업일인가 (주말·휴장일 제외)."""
    if d.weekday() >= 5:  # 5=토, 6=일
        return False
    return d not in _HOLIDAYS[market]


def is_market_open(market: Market, at: datetime) -> bool:
    """``market`` 의 정규장이 ``at`` 시점에 열려 있는가.

    - KR: ``Asia/Seoul`` 09:00–15:30.
    - US: ``America/New_York`` 09:30–16:00 (DST 자동).
    - 영업일이 아니면 항상 ``False``.

    ``at`` 은 timezone-aware 여야 한다(naive 금지).
    """
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    local = at.astimezone(_TZ[market])
    if not is_trading_day(market, local.date()):
        return False
    return _OPEN[market] <= local.timetz().replace(tzinfo=None) < _CLOSE[market]


def _open_dt(market: Market, d: date) -> datetime:
    """``market`` 의 ``d`` 일 개장 시각 (해당 TZ aware)."""
    return datetime.combine(d, _OPEN[market], tzinfo=_TZ[market])


def _next_open(market: Market, after: datetime) -> datetime:
    """``after`` (해당 TZ local) 이후 가장 가까운 다음 영업일 개장 시각.

    같은 날이라도 아직 개장 전이면 그 날 개장을 반환한다.
    """
    candidate_day = after.date()
    # 오늘이 영업일이고 아직 개장 전이면 오늘 개장.
    if is_trading_day(market, candidate_day):
        today_open = _open_dt(market, candidate_day)
        if after < today_open:
            return today_open
    # 그 외에는 다음 영업일을 탐색.
    d = candidate_day
    for _ in range(1, 366):
        d = date.fromordinal(d.toordinal() + 1)
        if is_trading_day(market, d):
            return _open_dt(market, d)
    raise RuntimeError("no trading day found within a year")


def next_refresh_at(market: Market, at: datetime, interval_min: int) -> datetime:
    """``at`` 이후 다음 갱신 시각(timezone-aware).

    - 장중이면 다음 갱신 후보(``at + interval_min`` 분)가 여전히 같은 세션 안이면 그대로.
    - 그 후보가 마감을 넘기거나, 애초에 장 마감/휴장 상태이면 다음 영업일 개장 시각.

    반환은 ``market`` 의 로컬 TZ(aware) 기준. ``at`` 은 timezone-aware 여야 한다.
    """
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    if interval_min <= 0:
        raise ValueError("interval_min must be positive")

    local = at.astimezone(_TZ[market])
    if is_market_open(market, local):
        candidate = local + timedelta(minutes=interval_min)
        # 후보가 같은 세션 마감 전이면 채택, 아니면 다음 개장.
        close_dt = datetime.combine(local.date(), _CLOSE[market], tzinfo=_TZ[market])
        if candidate < close_dt:
            return candidate
        return _next_open(market, candidate)
    return _next_open(market, local)


__all__ = ["is_market_open", "is_trading_day", "next_refresh_at"]
