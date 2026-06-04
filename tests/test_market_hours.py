"""market_hours 단위 테스트 — KST/ET 경계·주말·휴일·DST 케이스.

원칙:
- 비교 대상은 모두 timezone-aware. UTC 입력을 줘서 내부 TZ 변환을 검증한다.
- 경계(개장/마감)는 반열린 구간 [open, close) 을 가정한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from backend.market_hours import is_market_open, is_trading_day, next_refresh_at

KST = ZoneInfo("Asia/Seoul")
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# is_trading_day
# ---------------------------------------------------------------------------
class TestIsTradingDay:
    def test_kr_weekday_is_trading(self) -> None:
        # 2026-06-04 목요일 (비휴일)
        assert is_trading_day("KR", datetime(2026, 6, 4).date())

    def test_kr_saturday_not_trading(self) -> None:
        assert not is_trading_day("KR", datetime(2026, 6, 6).date())  # 현충일+토

    def test_kr_sunday_not_trading(self) -> None:
        assert not is_trading_day("KR", datetime(2026, 6, 7).date())

    def test_kr_new_year_holiday(self) -> None:
        assert not is_trading_day("KR", datetime(2026, 1, 1).date())

    def test_kr_chuseok_holiday(self) -> None:
        assert not is_trading_day("KR", datetime(2026, 9, 25).date())  # 추석

    def test_us_weekday_is_trading(self) -> None:
        assert is_trading_day("US", datetime(2026, 6, 4).date())

    def test_us_independence_observed(self) -> None:
        # 7/4 토 → 7/3 금 관측 휴장
        assert not is_trading_day("US", datetime(2026, 7, 3).date())

    def test_us_thanksgiving(self) -> None:
        assert not is_trading_day("US", datetime(2026, 11, 26).date())

    def test_us_weekday_not_in_holiday_is_trading(self) -> None:
        # 7/4 자체는 토요일이라 주말 판정으로 닫힘이지만, 7/6(월)은 영업일.
        assert is_trading_day("US", datetime(2026, 7, 6).date())


# ---------------------------------------------------------------------------
# is_market_open — KR (Asia/Seoul 09:00–15:30)
# ---------------------------------------------------------------------------
class TestKRMarketOpen:
    def test_open_at_0900(self) -> None:
        assert is_market_open("KR", datetime(2026, 6, 4, 9, 0, tzinfo=KST))

    def test_open_midday(self) -> None:
        assert is_market_open("KR", datetime(2026, 6, 4, 12, 0, tzinfo=KST))

    def test_open_just_before_close(self) -> None:
        assert is_market_open("KR", datetime(2026, 6, 4, 15, 29, tzinfo=KST))

    def test_closed_at_close_boundary(self) -> None:
        # 15:30 정각은 닫힘 (반열린 구간).
        assert not is_market_open("KR", datetime(2026, 6, 4, 15, 30, tzinfo=KST))

    def test_closed_before_open(self) -> None:
        assert not is_market_open("KR", datetime(2026, 6, 4, 8, 59, tzinfo=KST))

    def test_closed_on_weekend(self) -> None:
        # 2026-06-07 일요일 정오 — 닫힘.
        assert not is_market_open("KR", datetime(2026, 6, 7, 12, 0, tzinfo=KST))

    def test_closed_on_holiday(self) -> None:
        # 신정 정오 — 닫힘.
        assert not is_market_open("KR", datetime(2026, 1, 1, 12, 0, tzinfo=KST))

    def test_tz_conversion_from_utc(self) -> None:
        # 00:30 UTC == 09:30 KST (영업일) → 열림.
        assert is_market_open("KR", datetime(2026, 6, 4, 0, 30, tzinfo=UTC))

    def test_naive_datetime_raises(self) -> None:
        with pytest.raises(ValueError):
            is_market_open("KR", datetime(2026, 6, 4, 12, 0))


# ---------------------------------------------------------------------------
# is_market_open — US (America/New_York 09:30–16:00)
# ---------------------------------------------------------------------------
class TestUSMarketOpen:
    def test_open_at_0930(self) -> None:
        assert is_market_open("US", datetime(2026, 6, 4, 9, 30, tzinfo=ET))

    def test_closed_before_0930(self) -> None:
        assert not is_market_open("US", datetime(2026, 6, 4, 9, 29, tzinfo=ET))

    def test_open_just_before_1600(self) -> None:
        assert is_market_open("US", datetime(2026, 6, 4, 15, 59, tzinfo=ET))

    def test_closed_at_1600_boundary(self) -> None:
        assert not is_market_open("US", datetime(2026, 6, 4, 16, 0, tzinfo=ET))

    def test_closed_on_holiday(self) -> None:
        # Thanksgiving 정오 — 닫힘.
        assert not is_market_open("US", datetime(2026, 11, 26, 12, 0, tzinfo=ET))


# ---------------------------------------------------------------------------
# DST — America/New_York
# ---------------------------------------------------------------------------
class TestUSDaylightSaving:
    """2026 DST: 시작 3/8(일), 종료 11/1(일).

    UTC 기준 동일 시각이 EST/EDT 에서 다른 로컬 시각으로 매핑됨을 검증한다.
    """

    def test_march_before_dst_is_est(self) -> None:
        # 3/6(금) EST(UTC-5): 09:30 ET == 14:30 UTC.
        dt_open = datetime(2026, 3, 6, 14, 30, tzinfo=UTC)
        assert is_market_open("US", dt_open)
        # EDT 였다면 13:30 UTC 가 09:30 이었을 것 — EST 이므로 13:30 UTC 는 08:30 ET(닫힘).
        assert not is_market_open("US", datetime(2026, 3, 6, 13, 30, tzinfo=UTC))

    def test_march_after_dst_is_edt(self) -> None:
        # 3/9(월) EDT(UTC-4): 09:30 ET == 13:30 UTC.
        assert is_market_open("US", datetime(2026, 3, 9, 13, 30, tzinfo=UTC))
        # 14:30 UTC == 10:30 ET (열림이지만 EST 가정과 구분).
        assert is_market_open("US", datetime(2026, 3, 9, 14, 30, tzinfo=UTC))
        # 13:00 UTC == 09:00 ET (개장 전, 닫힘) — EDT 확인.
        assert not is_market_open("US", datetime(2026, 3, 9, 13, 0, tzinfo=UTC))

    def test_november_before_dst_end_is_edt(self) -> None:
        # 10/30(금) EDT(UTC-4): 09:30 ET == 13:30 UTC.
        assert is_market_open("US", datetime(2026, 10, 30, 13, 30, tzinfo=UTC))

    def test_november_after_dst_end_is_est(self) -> None:
        # 11/2(월) EST(UTC-5): 09:30 ET == 14:30 UTC.
        assert is_market_open("US", datetime(2026, 11, 2, 14, 30, tzinfo=UTC))
        # 13:30 UTC == 08:30 ET (개장 전, 닫힘) — EST 확인.
        assert not is_market_open("US", datetime(2026, 11, 2, 13, 30, tzinfo=UTC))


# ---------------------------------------------------------------------------
# next_refresh_at
# ---------------------------------------------------------------------------
class TestNextRefreshAt:
    def test_intraday_adds_interval(self) -> None:
        at = datetime(2026, 6, 4, 10, 0, tzinfo=KST)
        nxt = next_refresh_at("KR", at, 30)
        assert nxt == datetime(2026, 6, 4, 10, 30, tzinfo=KST)

    def test_intraday_candidate_within_session(self) -> None:
        at = datetime(2026, 6, 4, 14, 0, tzinfo=KST)
        nxt = next_refresh_at("KR", at, 30)
        assert nxt == datetime(2026, 6, 4, 14, 30, tzinfo=KST)

    def test_candidate_past_close_rolls_to_next_open(self) -> None:
        # 15:20 + 30분 = 15:50 > 15:30 마감 → 다음 영업일(금) 개장.
        at = datetime(2026, 6, 4, 15, 20, tzinfo=KST)
        nxt = next_refresh_at("KR", at, 30)
        assert nxt == datetime(2026, 6, 5, 9, 0, tzinfo=KST)

    def test_after_close_rolls_to_next_open(self) -> None:
        at = datetime(2026, 6, 4, 16, 0, tzinfo=KST)
        nxt = next_refresh_at("KR", at, 30)
        assert nxt == datetime(2026, 6, 5, 9, 0, tzinfo=KST)

    def test_before_open_rolls_to_same_day_open(self) -> None:
        at = datetime(2026, 6, 4, 7, 0, tzinfo=KST)
        nxt = next_refresh_at("KR", at, 30)
        assert nxt == datetime(2026, 6, 4, 9, 0, tzinfo=KST)

    def test_friday_close_rolls_over_weekend(self) -> None:
        # 2026-06-05 금요일 마감 후 → 다음 영업일은 월요일 6/8.
        at = datetime(2026, 6, 5, 16, 0, tzinfo=KST)
        nxt = next_refresh_at("KR", at, 30)
        assert nxt == datetime(2026, 6, 8, 9, 0, tzinfo=KST)

    def test_holiday_eve_close_skips_holiday(self) -> None:
        # 2025-12-31(수) 마감 후 → 2026-01-01 신정 휴장 건너뛰어 1/2(금) 개장.
        at = datetime(2025, 12, 31, 16, 0, tzinfo=KST)
        nxt = next_refresh_at("KR", at, 30)
        assert nxt == datetime(2026, 1, 2, 9, 0, tzinfo=KST)

    def test_us_intraday_adds_interval(self) -> None:
        at = datetime(2026, 6, 4, 10, 0, tzinfo=ET)
        nxt = next_refresh_at("US", at, 30)
        assert nxt == datetime(2026, 6, 4, 10, 30, tzinfo=ET)

    def test_us_after_close_rolls_to_next_open(self) -> None:
        at = datetime(2026, 6, 4, 17, 0, tzinfo=ET)
        nxt = next_refresh_at("US", at, 30)
        assert nxt == datetime(2026, 6, 5, 9, 30, tzinfo=ET)

    def test_result_is_timezone_aware(self) -> None:
        at = datetime(2026, 6, 4, 10, 0, tzinfo=KST)
        assert next_refresh_at("KR", at, 30).tzinfo is not None

    def test_input_utc_is_converted(self) -> None:
        # 01:00 UTC == 10:00 KST 장중 → +30 = 10:30 KST.
        at = datetime(2026, 6, 4, 1, 0, tzinfo=UTC)
        nxt = next_refresh_at("KR", at, 30)
        assert nxt == datetime(2026, 6, 4, 10, 30, tzinfo=KST)

    def test_naive_datetime_raises(self) -> None:
        with pytest.raises(ValueError):
            next_refresh_at("KR", datetime(2026, 6, 4, 10, 0), 30)

    def test_nonpositive_interval_raises(self) -> None:
        with pytest.raises(ValueError):
            next_refresh_at("KR", datetime(2026, 6, 4, 10, 0, tzinfo=KST), 0)

    def test_us_dst_session_spans_interval(self) -> None:
        # 3/9(월) EDT 장중 09:30 ET + 30분 = 10:00 ET.
        at = datetime(2026, 3, 9, 9, 30, tzinfo=ET)
        nxt = next_refresh_at("US", at, 30)
        assert nxt == datetime(2026, 3, 9, 10, 0, tzinfo=ET)
        # 결과를 UTC 로 보면 EDT(UTC-4)라 14:00 UTC.
        assert nxt.astimezone(UTC) == datetime(2026, 3, 9, 14, 0, tzinfo=UTC)


def test_equivalence_across_timedelta_boundary() -> None:
    # KST 입력과 동일 순간의 UTC 입력은 같은 next_refresh_at 결과(순간)를 준다.
    at_kst = datetime(2026, 6, 4, 10, 0, tzinfo=KST)
    at_utc = at_kst.astimezone(UTC)
    assert next_refresh_at("KR", at_kst, 30) == next_refresh_at("KR", at_utc, 30)
    assert isinstance(next_refresh_at("KR", at_kst, 45) - at_kst, timedelta)
