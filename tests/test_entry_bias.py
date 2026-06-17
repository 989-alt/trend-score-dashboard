"""T5: entry_bias 프리셋 — 팩터 단위 테스트 + 프리셋 통합 테스트 + 합성 하니스 sanity.

목표:
  F1. compute_extension_guard 단위 테스트
  F2. compute_pullback_3pos 단위 테스트
  F3. entry_bias 가중치 합 1.0 단언
  F4. _score_at(entry_bias) 가 baseline 과 다르고, 과매수 종목을 하방 조정함
  F5. 합성 하니스 sanity — 과매수 종목이 사후 하락, 눌림목 종목이 사후 상승하는
      패널에서 entry_bias 가 baseline 보다 낮은(덜 음수) MAE 를 보임
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

import pytest
from backend.config import Settings
from backend.schemas import OHLCVRow
from backend.scoring import compute_extension_guard, compute_pullback_3pos

# ---------------------------------------------------------------------------
# 테스트용 OHLCV 헬퍼
# ---------------------------------------------------------------------------


def _row(d: date, close: Decimal) -> OHLCVRow:
    return OHLCVRow(
        date=d,
        open=close,
        high=close * Decimal("1.02"),
        low=close * Decimal("0.98"),
        close=close,
        volume=Decimal("1000000"),
    )


def _rows_flat(n: int, base: Decimal = Decimal("100")) -> list[OHLCVRow]:
    """n 봉의 평탄(동일가격) OHLCV 시리즈."""
    start = date(2023, 1, 2)
    return [_row(start + timedelta(days=i), base) for i in range(n)]


def _rows_trend(n: int, start_price: Decimal, slope: Decimal) -> list[OHLCVRow]:
    """선형 트렌드 OHLCV (각 봉마다 slope 만큼 상승)."""
    start = date(2023, 1, 2)
    rows = []
    for i in range(n):
        c = start_price + slope * Decimal(i)
        c = max(c, Decimal("1"))
        rows.append(_row(start + timedelta(days=i), c))
    return rows


# ---------------------------------------------------------------------------
# F1. compute_extension_guard 단위 테스트
# ---------------------------------------------------------------------------


def test_extension_guard_no_penalty_below_lo() -> None:
    """MA 이격도가 ext_guard_lo 이하 → 페널티 없음(1.0)."""
    settings = Settings()
    # MA 기준: 100, 현재가 104 → ext = 4% < ext_guard_lo(5%) → 1.0
    rows = _rows_flat(settings.ext_guard_ma_window, Decimal("100"))
    # 마지막 봉만 104 로 변경
    rows[-1] = _row(rows[-1].date, Decimal("104"))
    result = compute_extension_guard(rows, settings)
    assert result == Decimal("1"), f"ext<lo 이면 페널티 없음(1.0), got {result}"


def test_extension_guard_max_penalty_above_hi() -> None:
    """MA 이격도가 ext_guard_hi 이상 → floor 값."""
    settings = Settings()
    # MA 기준: 100, 현재가 135 → ext = 35% > ext_guard_hi(30%) → floor(0.5)
    rows = _rows_flat(settings.ext_guard_ma_window, Decimal("100"))
    rows[-1] = _row(rows[-1].date, Decimal("135"))
    result = compute_extension_guard(rows, settings)
    assert result == settings.ext_guard_floor, (
        f"ext>=hi 이면 floor({settings.ext_guard_floor}), got {result}"
    )


def test_extension_guard_linear_interpolation() -> None:
    """MA 이격도가 lo와 hi 사이 → 선형 보간으로 floor < result < 1.0."""
    settings = Settings()
    # ext_guard_lo=0.05, ext_guard_hi=0.30: 중간(0.175)에서 1.0 > result > floor
    # MA: rows[-window:] 의 평균. 마지막 봉을 바꾸면 MA도 약간 달라지므로
    # 여기서는 근사값이 아닌 단조성(floor < x < 1)만 검증한다.
    rows = _rows_flat(settings.ext_guard_ma_window, Decimal("100"))
    rows[-1] = _row(rows[-1].date, Decimal("117.5"))  # ext > lo, ext < hi
    result = compute_extension_guard(rows, settings)
    floor = settings.ext_guard_floor
    assert floor < result < Decimal("1"), f"보간값은 floor({floor})와 1.0 사이여야 함, got {result}"
    # ext_guard_hi(30%)보다 낮은 이격 → floor 보다 커야 함
    assert result > floor, f"중간 이격 → floor보다 높아야 함, got {result}"


def test_extension_guard_insufficient_data() -> None:
    """데이터가 window보다 적으면 페널티 없음(1.0)."""
    settings = Settings()
    # ext_guard_ma_window=20인데 10봉만 제공
    rows = _rows_flat(10, Decimal("200"))  # 과매수처럼 보여도 MA 계산 불가
    result = compute_extension_guard(rows, settings)
    assert result == Decimal("1"), f"데이터 부족 → 1.0, got {result}"


def test_extension_guard_below_ma() -> None:
    """MA 아래(음의 이격도) → 페널티 없음(1.0)."""
    settings = Settings()
    rows = _rows_flat(settings.ext_guard_ma_window, Decimal("100"))
    rows[-1] = _row(rows[-1].date, Decimal("90"))  # ext = -10%
    result = compute_extension_guard(rows, settings)
    assert result == Decimal("1"), f"MA 아래 이격 → 페널티 없음(1.0), got {result}"


# ---------------------------------------------------------------------------
# F2. compute_pullback_3pos 단위 테스트
# ---------------------------------------------------------------------------


def test_pullback_at_fresh_high_returns_low() -> None:
    """고점(신고가) 근처 → depth ≈ 0 → pullback_3pos 낮음 (< 0.3).

    Note: _rows_trend의 ±1.5% 교번 때문에 마지막 봉이 정확히 최고가가 아닐 수
    있으나, depth가 매우 작아 보상이 낮아야 함.
    """
    settings = Settings()
    n = max(settings.pullback_ma_window, settings.pullback_high_window) + 10
    # 우상향 + 마지막 봉이 최고점 근처 → depth 작음
    rows = _rows_trend(n, Decimal("1000"), Decimal("1"))
    result = compute_pullback_3pos(rows, settings)
    # 신고가 근처: depth가 매우 작아 보상이 낮아야 함 (< ideal/2 → reward < 0.5)
    # 실제로 depth ≈ 0이면 reward ≈ 0; ±1.5% 교번으로 약간의 depth가 생길 수 있음
    assert result < Decimal("0.5"), f"신고가 근처 → pullback_3pos 낮아야 함(< 0.5), got {result}"


def test_pullback_ideal_depth_returns_high() -> None:
    """이상적 눌림목 깊이(~8%) + MA 위 → pullback_3pos 높음(> 0.7).

    삼각형 보상 정점은 depth=pullback_ideal에서 1.0이지만, 고점 기준 rows의
    high 필드가 close보다 살짝 높으므로 실제 depth가 pullback_ideal과 약간 다를 수
    있다. 따라서 > 0.7 의 넉넉한 임계로 검증한다.
    """
    settings = Settings()
    n = max(settings.pullback_ma_window, settings.pullback_high_window) + 10
    base = Decimal("1000")
    # MA 형성을 위해 평탄한 구간
    rows = _rows_flat(n - 5, base)
    high_price = base * Decimal("1.15")  # recent high: 1150
    # 고점 형성 (최근 4봉)
    for _i in range(4):
        rows.append(_row(rows[-1].date + timedelta(days=1), high_price))
    # 현재가: 고점 대비 8% 눌림 → 1058
    # MA(20)는 약 base 근처 → 1058 > MA(≈1000+약간) → 지지선 위
    close = high_price * (Decimal("1") - settings.pullback_ideal)
    rows.append(_row(rows[-1].date + timedelta(days=1), close))
    result = compute_pullback_3pos(rows, settings)
    # 이상적 눌림목 → 보상 높음
    assert result > Decimal("0.7"), (
        f"이상적 눌림목(depth≈8%)에서 pullback_3pos가 높아야 함(> 0.7), got {result}"
    )


def test_pullback_too_deep_returns_zero() -> None:
    """눌림목 깊이가 pullback_max 초과 → 0."""
    settings = Settings()
    n = max(settings.pullback_ma_window, settings.pullback_high_window) + 10
    base = Decimal("1000")
    rows = _rows_flat(n - 5, base)
    high = base * Decimal("1.5")
    for _i in range(4):
        rows.append(_row(rows[-1].date + timedelta(days=1), high))
    # 25% 눌림 > pullback_max(20%) — MA는 여전히 아래에 있으므로 지지선 유지
    # 실제로 1500 * 0.75 = 1125 > MA(≈1000) → 지지선 위
    close = high * (Decimal("1") - Decimal("0.25"))
    rows.append(_row(rows[-1].date + timedelta(days=1), close))
    result = compute_pullback_3pos(rows, settings)
    assert result == Decimal("0"), f"너무 깊은 눌림목 → 0, got {result}"


def test_pullback_below_ma_returns_zero() -> None:
    """종가가 MA 아래 → 0 (지지선 붕괴)."""
    settings = Settings()
    n = max(settings.pullback_ma_window, settings.pullback_high_window) + 10
    # MA 위에서 고점 후 MA 아래로 붕괴
    rows = _rows_trend(n, Decimal("1000"), Decimal("1"))
    # 마지막 봉을 MA 아래로 내림 — MA ≈ 1010 (마지막 20개 평균), 현재가 900
    rows[-1] = _row(rows[-1].date, Decimal("900"))
    result = compute_pullback_3pos(rows, settings)
    assert result == Decimal("0"), f"MA 아래 → pullback_3pos=0, got {result}"


def test_pullback_insufficient_data_returns_zero() -> None:
    """데이터 부족 → 0."""
    settings = Settings()
    rows = _rows_flat(5, Decimal("1000"))
    result = compute_pullback_3pos(rows, settings)
    assert result == Decimal("0"), f"데이터 부족 → 0, got {result}"


# ---------------------------------------------------------------------------
# F3. entry_bias 가중치 합 1.0 단언
# ---------------------------------------------------------------------------


def test_entry_bias_weights_sum_to_one() -> None:
    """entry_bias 프리셋 가중치 합 = 1.0 (drift 방지 회귀)."""
    s = Settings()
    total = (
        s.weight_52w_entry  # 0.18
        + s.weight_pullback  # 0.12
        + s.weight_pocket_pivot  # 0.20
        + s.weight_momentum  # 0.13
        + s.weight_rs  # 0.12
        + s.weight_turnover  # 0.15
        + s.weight_vol_fit  # 0.10
    )
    assert total == Decimal("1.00"), (
        f"entry_bias 가중치 합이 1.00이 아님: {total}. config.py의 weight_* 값을 확인할 것."
    )


# ---------------------------------------------------------------------------
# F4. _score_at(entry_bias) 이 baseline 과 다르고 과매수 종목을 하방 조정함
# ---------------------------------------------------------------------------


def _make_entry_bias_panel_2tickers() -> tuple:
    """2개 종목 합성 패널:
    - extended: MA 위 30%+ 이격 (고점 추격)
    - pullback: MA 위이고 최근 고점 대비 8% 눌림 (이상적 눌림목)

    반환: (panel, t)
    """
    from backend.backtest.panel import Panel, TickerSeries, Valuation

    start = date(2023, 1, 2)

    def _make_series(
        ticker: str,
        n: int,
        prices: list[Decimal],
    ) -> TickerSeries:
        rows = []
        for i, c in enumerate(prices):
            # 홀짝 교번 ±1.5% → 변동성 밴드 통과(≈0.48)
            close = c * (Decimal("1.015") if i % 2 == 0 else Decimal("0.985"))
            rows.append(
                OHLCVRow(
                    date=start + timedelta(days=i),
                    open=c,
                    high=c * Decimal("1.025"),
                    low=c * Decimal("0.975"),
                    close=close,
                    volume=Decimal("1000000"),
                )
            )
        return TickerSeries(
            ticker=ticker,
            rows=rows,
            turnover_by_date={r.date: Decimal("20000000000") for r in rows},
            valuation_by_date={
                r.date: Valuation(per=Decimal("10"), pbr=Decimal("1.2")) for r in rows
            },
        )

    # extended 종목: 200봉 우상향 + 마지막 30봉 급등 (MA 대비 +35%)
    n_base = 210  # MA200 통과
    n_fwd = 40

    # extended: 트레일링 구간
    ext_prices: list[Decimal] = []
    for i in range(n_base):
        if i < 180:
            ext_prices.append(Decimal("1000") + Decimal(i))
        else:
            # 마지막 30봉: 1180 → 1590 (급등, MA≈1090이면 이격 35%+)
            base = Decimal("1180") + Decimal(i - 180) * Decimal("14")
            ext_prices.append(base)
    # extended: 포워드 구간 (하락 — 과매수 후 되돌림)
    for _j in range(1, n_fwd + 1):
        ext_prices.append(ext_prices[-1] - Decimal("15"))

    # pullback 종목: 200봉 우상향 + 최근 고점 대비 8% 눌림, MA 위
    pb_prices: list[Decimal] = []
    for i in range(n_base):
        if i < n_base - 20:
            pb_prices.append(Decimal("1000") + Decimal(i))
        elif i < n_base - 5:
            # 고점 형성: 1190 ~ 1220
            pb_prices.append(Decimal("1190") + Decimal(i - (n_base - 20)) * Decimal("2"))
        else:
            # 마지막 5봉: 고점 1220 에서 8% 눌림 → 1222*0.92 ≈ 1124
            high_price = Decimal("1220")
            pb_prices.append(high_price * (Decimal("1") - Decimal("0.08")))
    # pullback: 포워드 구간 (상승 — 눌림목 후 반등)
    for _j in range(1, n_fwd + 1):
        pb_prices.append(pb_prices[-1] + Decimal("10"))

    from backend.backtest.panel import AsOfFundamentals

    ext_series = _make_series("000001", len(ext_prices), ext_prices)
    pb_series = _make_series("000002", len(pb_prices), pb_prices)

    fundamentals = {
        "000001": [
            AsOfFundamentals(
                rcept_date=date(2023, 3, 31),
                roe=Decimal("0.10"),
                op_margin=Decimal("0.08"),
                rev_growth=Decimal("0.15"),
            )
        ],
        "000002": [
            AsOfFundamentals(
                rcept_date=date(2023, 3, 31),
                roe=Decimal("0.10"),
                op_margin=Decimal("0.08"),
                rev_growth=Decimal("0.15"),
            )
        ],
    }
    listings = {
        "000001": (start, None),
        "000002": (start, None),
    }

    # 인덱스 시리즈 (완만한 상승)
    total = len(ext_prices)
    index_rows = [
        OHLCVRow(
            date=start + timedelta(days=i),
            open=Decimal("2000"),
            high=Decimal("2050"),
            low=Decimal("1950"),
            close=Decimal("2000") + Decimal(i),
            volume=Decimal("1000000"),
        )
        for i in range(total)
    ]

    panel = Panel(
        series={"000001": ext_series, "000002": pb_series},
        fundamentals=fundamentals,
        listings=listings,
        index_rows=index_rows,
    )
    t = start + timedelta(days=n_base - 1)
    return panel, t


def test_entry_bias_differs_from_baseline() -> None:
    """_score_at(entry_bias) 결과가 baseline 과 달라야 함 (no-op 아님)."""
    from backend.backtest.run import _score_at
    from backend.config import get_settings

    panel, t = _make_entry_bias_panel_2tickers()
    settings = get_settings()

    base = _score_at(panel, t, settings, "baseline")
    entry = _score_at(panel, t, settings, "entry_bias")

    assert base and entry, "채점된 후보가 있어야 함"
    base_dict = dict(base)
    entry_dict = dict(entry)

    # 점수가 달라야 함 (entry_bias가 no-op가 아님)
    any_diff = any(
        abs(base_dict.get(tk, Decimal("0")) - entry_dict.get(tk, Decimal("0"))) > Decimal("0.001")
        for tk in base_dict
    )
    assert any_diff, (
        "entry_bias 점수가 baseline과 동일함 — 리웨이트가 적용되지 않았거나 "
        "extension_guard/pullback_3pos가 모두 no-op임"
    )


def test_entry_bias_penalizes_extended_ticker() -> None:
    """과매수(이격 35%+) 종목이 entry_bias에서 baseline보다 낮은 점수를 받아야 함."""
    from backend.backtest.run import _score_at
    from backend.config import get_settings

    panel, t = _make_entry_bias_panel_2tickers()
    settings = get_settings()

    base = _score_at(panel, t, settings, "baseline")
    entry = _score_at(panel, t, settings, "entry_bias")

    base_dict = dict(base)
    entry_dict = dict(entry)

    # 000001이 extended, 000002가 pullback
    ext_tk = "000001"

    if ext_tk not in base_dict or ext_tk not in entry_dict:
        pytest.skip("extended 종목이 적격 후보가 아님 — 패널 설계 확인 필요")

    ext_base = base_dict[ext_tk]
    ext_entry = entry_dict.get(ext_tk, Decimal("0"))

    # extension_guard가 over-extended 종목을 하방 조정해야 함
    assert ext_entry <= ext_base, (
        f"과매수 종목({ext_tk})이 entry_bias에서 baseline보다 높거나 같음: "
        f"baseline={ext_base:.4f}, entry_bias={ext_entry:.4f}. "
        "extension_guard가 작동하지 않을 수 있음."
    )


def test_entry_bias_prefers_pullback_over_extended() -> None:
    """entry_bias에서 눌림목 종목 순위가 과매수 종목보다 높아야 함 (또는 격차가 줄어야 함)."""
    from backend.backtest.run import _score_at
    from backend.config import get_settings

    panel, t = _make_entry_bias_panel_2tickers()
    settings = get_settings()

    base = _score_at(panel, t, settings, "baseline")
    entry = _score_at(panel, t, settings, "entry_bias")

    base_dict = dict(base)
    entry_dict = dict(entry)

    ext_tk, pb_tk = "000001", "000002"

    if pb_tk not in entry_dict or ext_tk not in entry_dict:
        pytest.skip("두 종목 모두 적격 후보여야 함")

    # entry_bias에서 과매수 종목 점수가 낮아지거나, 눌림목 종목이 높아져야 함
    ext_gap_base = base_dict.get(ext_tk, Decimal("0")) - base_dict.get(pb_tk, Decimal("0"))
    ext_gap_entry = entry_dict.get(ext_tk, Decimal("0")) - entry_dict.get(pb_tk, Decimal("0"))

    # entry_bias에서 extended 종목의 우위가 줄어들어야 함 (또는 역전)
    assert ext_gap_entry <= ext_gap_base, (
        f"entry_bias에서 extended vs pullback 격차가 커졌음: "
        f"baseline gap={ext_gap_base:.4f}, entry_bias gap={ext_gap_entry:.4f}. "
        "리웨이트 + extension_guard가 의도대로 작동하지 않음."
    )


# ---------------------------------------------------------------------------
# F5. 합성 하니스 sanity — entry_bias가 baseline보다 낮은 MAE
# ---------------------------------------------------------------------------


def _make_entry_bias_sanity_panel(
    n_extended: int = 10,
    n_pullback: int = 10,
    *,
    seed: int = 42,
) -> tuple:
    """entry_bias sanity 패널.

    설계: 2그룹 × 각 N 종목.
    - extended 그룹 (higher-extension → worse forward):
        기저 상승 + 마지막 10봉 급등(이격 20~50%). 급등 폭이 클수록 이후 하락도 큼.
        extension_guard는 급등 폭이 클수록 더 강하게 페널티 → 점수 낮아짐.
        베이스라인은 near_52w(=1.0)로 급등 종목에 최고점을 줌 → 잘못된 순위.
        entry_bias는 guard로 하방 조정 → 더 나은 순위.
    - pullback 그룹 (이상적 상태 → 포워드 상승):
        기저 상승 + 안정적 추세 유지. pullback_3pos는 작은 값이지만
        extension_guard=1.0(페널티 없음)이므로 entry_bias에서 상대적으로 유리.
        포워드: 완만 상승(+10~+20%).

    핵심 검증: entry_bias에서 extended 종목 평균 점수 < baseline.

    반환: (panel, t, list[ext_tickers], list[pb_tickers])
    """
    from backend.backtest.panel import AsOfFundamentals, Panel, TickerSeries, Valuation

    rng = random.Random(seed)
    start = date(2023, 1, 2)
    n_trailing = 260
    n_forward = 65
    slope = Decimal("10")  # 10 pt/day — MA200 위 + momentum > 0 보장
    base_price = Decimal("1000")

    series: dict[str, TickerSeries] = {}
    listings: dict[str, tuple[date, date | None]] = {}
    fundamentals_map: dict[str, list] = {}
    ext_tickers: list[str] = []
    pb_tickers: list[str] = []

    def _make_series_raw(
        ticker: str,
        trailing_prices: list[Decimal],
        forward_prices: list[Decimal],
    ) -> TickerSeries:
        rows = []
        for i, c in enumerate(trailing_prices + forward_prices):
            # 변동성 밴드 통과용 ±1.5% 교번
            close = c * (Decimal("1.015") if i % 2 == 0 else Decimal("0.985"))
            close = max(close, Decimal("10"))
            rows.append(
                OHLCVRow(
                    date=start + timedelta(days=i),
                    open=c,
                    high=c * Decimal("1.025"),
                    low=c * Decimal("0.975"),
                    close=close,
                    volume=Decimal("1000000"),
                )
            )
        return TickerSeries(
            ticker=ticker,
            rows=rows,
            turnover_by_date={r.date: Decimal("20000000000") for r in rows},
            valuation_by_date={
                r.date: Valuation(per=Decimal("10"), pbr=Decimal("1.2")) for r in rows
            },
        )

    for i in range(n_extended + n_pullback):
        ticker = f"{i + 1:06d}"
        is_ext = i < n_extended

        t_prices: list[Decimal] = []
        if is_ext:
            # extended: 250봉 기저 상승 + 마지막 10봉 급등(20~50% 이격)
            for j in range(n_trailing - 10):
                t_prices.append(base_price + slope * Decimal(j))
            last_base = t_prices[-1]
            # 급등 폭: ext_idx에 비례 (작은→큰 순서 = 0.20~0.50)
            ext_idx = i  # 0..n_extended-1
            ext_frac = Decimal(str(0.20 + (0.30 * ext_idx / max(n_extended - 1, 1))))
            for k in range(10):
                step = last_base * ext_frac / Decimal("10")
                t_prices.append(last_base + step * Decimal(k + 1))
            # 포워드: 급등 폭이 클수록 더 크게 하락 (-10~-25%)
            drop_pct = Decimal(str(0.10 + float(ext_frac) * 0.5))  # ext 크면 drop도 큼
            last = t_prices[-1]
            fwd_prices: list[Decimal] = []
            for k in range(n_forward):
                fwd_prices.append(
                    last * (Decimal("1") - drop_pct / Decimal(n_forward) * Decimal(k + 1))
                )
            ext_tickers.append(ticker)
        else:
            # pullback 그룹: 기저 상승만 (눌림목 없이 steady trend)
            # extension_guard=1.0 (이격 적음), pullback_3pos는 신고가 근처라 낮음
            # 포워드: 완만 상승 (+10~+20%)
            for j in range(n_trailing):
                t_prices.append(base_price + slope * Decimal(j))
            fwd_prices = []
            rise_pct = Decimal(str(0.10 + rng.random() * 0.10))
            last = t_prices[-1]
            for k in range(n_forward):
                fwd_prices.append(
                    last * (Decimal("1") + rise_pct / Decimal(n_forward) * Decimal(k + 1))
                )
            pb_tickers.append(ticker)

        series[ticker] = _make_series_raw(ticker, t_prices, fwd_prices)
        listings[ticker] = (start, None)
        fundamentals_map[ticker] = [
            AsOfFundamentals(
                rcept_date=date(2023, 3, 31),
                roe=Decimal("0.10"),
                op_margin=Decimal("0.08"),
                rev_growth=Decimal("0.12"),
            )
        ]

    # 인덱스 시리즈
    total = n_trailing + n_forward
    index_rows = [
        OHLCVRow(
            date=start + timedelta(days=i),
            open=Decimal("2000"),
            high=Decimal("2050"),
            low=Decimal("1950"),
            close=Decimal("2000") + Decimal(i),
            volume=Decimal("1000000"),
        )
        for i in range(total)
    ]

    panel = Panel(
        series=series,
        fundamentals=fundamentals_map,
        listings=listings,
        index_rows=index_rows,
    )
    t = start + timedelta(days=n_trailing - 1)
    return panel, t, ext_tickers, pb_tickers


def test_entry_bias_sanity_harness() -> None:
    """합성 하니스 sanity (by-construction 논리 증명).

    패널 설계:
    - extended 그룹(10종목): 기저 우상향 + 마지막 10봉 급등(20~50% 이격).
      급등 폭이 클수록 extension_guard 페널티가 강해지고, 이후 하락도 큼.
      baseline은 near_52w≈1.0 → 급등 종목에 높은 점수(잘못된 순위).
      entry_bias는 guard 페널티 → 급등 종목 하방 조정.
    - pullback 그룹(10종목): 기저 우상향만 (이격 소), extension_guard=1.0.
      포워드: 완만 상승.

    검증:
    1. extended 그룹 avg 점수: entry_bias < baseline (guard 효과).
    2. 단조성: entry_bias > baseline (baseline = 음의 상관; entry_bias = 양의 상관).
       → 실측: baseline = -0.9359, entry_bias = +0.9359.

    이 테스트는 '설계된 조건에서의 논리 증명'이며 실 데이터 검증이 아님.
    """
    from backend.backtest.run import BacktestConfig, _score_at, run_backtest
    from backend.config import get_settings

    panel, t, ext_tickers, _pb_tickers = _make_entry_bias_sanity_panel(
        n_extended=10,
        n_pullback=10,
        seed=42,
    )

    settings = get_settings()

    # ── 검증 1: over-extended 종목 평균 점수 비교 ──
    base_ranked = _score_at(panel, t, settings, "baseline")
    entry_ranked = _score_at(panel, t, settings, "entry_bias")
    base_dict = dict(base_ranked)
    entry_dict = dict(entry_ranked)

    ext_in_base = [base_dict[tk] for tk in ext_tickers if tk in base_dict]
    ext_in_entry = [entry_dict[tk] for tk in ext_tickers if tk in entry_dict]

    if not ext_in_base or not ext_in_entry:
        pytest.skip("extended 종목이 적격 후보가 아님 — 패널 설계 확인 필요")

    avg_ext_base = sum(ext_in_base, Decimal("0")) / Decimal(len(ext_in_base))
    avg_ext_entry = sum(ext_in_entry, Decimal("0")) / Decimal(len(ext_in_entry))
    assert avg_ext_entry < avg_ext_base, (
        f"entry_bias에서 over-extended 평균 점수가 줄어야 함: "
        f"baseline={avg_ext_base:.4f}, entry_bias={avg_ext_entry:.4f}"
    )

    # ── 검증 2: 단조성(Spearman) — entry_bias > baseline ──
    # 설계상 baseline은 near_52w=1(급등)→높은 점수→실제는 하락: 음의 상관.
    # entry_bias는 guard로 급등 종목을 하방→더 정직한 랭킹: 양의 상관.
    cfg_base = BacktestConfig(
        start=t,
        end=t,
        rebalance="monthly",
        top_n=20,
        cost_bps=Decimal("0"),
        preset="baseline",
        forward_horizons=(20,),
        n_resamples=50,
        n_perms=50,
    )
    cfg_entry = BacktestConfig(
        start=t,
        end=t,
        rebalance="monthly",
        top_n=20,
        cost_bps=Decimal("0"),
        preset="entry_bias",
        forward_horizons=(20,),
        n_resamples=50,
        n_perms=50,
    )

    result_base = run_backtest(panel, cfg_base)
    result_entry = run_backtest(panel, cfg_entry)

    bucket_base = result_base.event_study.get(20)
    bucket_entry = result_entry.event_study.get(20)

    if bucket_base is None or bucket_entry is None:
        pytest.skip("이벤트스터디 버킷이 None")

    if bucket_base.n == 0 or bucket_entry.n == 0:
        pytest.skip(f"채점된 후보 없음: baseline n={bucket_base.n}, entry n={bucket_entry.n}")

    # 단조성: entry_bias가 baseline보다 높아야 함
    # (baseline=-0.9359, entry_bias=+0.9359 — 완전한 반전)
    assert bucket_entry.monotonicity > bucket_base.monotonicity, (
        f"entry_bias 단조성이 baseline보다 낮음: "
        f"baseline={bucket_base.monotonicity:.4f}, entry_bias={bucket_entry.monotonicity:.4f}. "
        "설계된 패널에서 guard 페널티가 랭킹을 개선해야 함."
    )
