"""통합 산출 — provider·scoring·stops·themes·store 를 묶어 스냅샷/응답을 만든다.

흐름(``score_market``):
1. ``provider.list_universe`` 로 스캔 대상 수집.
2. 종목별 OHLCV·시세·펀더멘털·투자자별 매매 조회 → 팩터 계산 → ``Candidate``.
3. ``scoring.passes_hard_filter`` → 적격(eligible) 종목만 ``score_candidates`` 로 정규화·
   점수화. 부적격 종목은 점수 0·원시 팩터로 직접 조립.
4. ``stops`` 로 200일선 위 종목의 (peak,stop)·매도요구를 무상태로 산정(매도 시
   ``Grade.SELL`` 오버라이드). 200일선 아래는 손절 미산정(회피).
5. ``ScoreEntry`` 조립 → ``Snapshot`` (counts·market_open·next_refresh 포함) → ``store`` 저장.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import httpx

from backend import market_hours
from backend import scoring as sc
from backend.config import Settings
from backend.market_data import Fundamentals, LiveProviderError, MarketDataProvider, Quote
from backend.schemas import (
    DISCLAIMER,
    FactorBreakdown,
    Grade,
    InvestorFlow,
    Market,
    OHLCVRow,
    ScoreEntry,
    SellReason,
    Snapshot,
    SnapshotCounts,
    ThemeGroup,
    ThemesResponse,
)
from backend.stops import compute_trailing_stop, evaluate_sell
from backend.store import Store
from backend.themes import ThemeDef, build_theme_groups, themes_for_ticker

#: 1년 수익률 폴백 산출용 거래일 수(약 252 거래일 ≈ 1년).
_TRADING_DAYS_1Y = 252

#: 응답 ``market_open`` 맵 구성용 시장 목록(Literal 보존).
_MARKETS: tuple[Market, ...] = ("KR", "US")

#: per-ticker 데이터 조회 실패로 흡수할 구체 예외(광범위 except 회피).
#: ``httpx.HTTPError`` 포함 — KIS 일시적 5xx/타임아웃이 한 종목 때문에 전체 스캔을
#: 무너뜨리지 않도록(라이브 300종목 스캔 회복력). 해당 종목만 failed 로 흡수.
_TICKER_ERRORS: tuple[type[Exception], ...] = (
    LiveProviderError,
    httpx.HTTPError,
    ValueError,
    KeyError,
    IndexError,
    ArithmeticError,
)


@dataclass(frozen=True)
class _Raw:
    """ticker 1개의 원천 데이터 + 산출한 raw 팩터 (점수화 전 단계)."""

    ticker: str
    name: str
    rows: list[OHLCVRow]
    quote: Quote
    fundamentals: Fundamentals
    investor_flow: InvestorFlow | None
    candidate: sc.Candidate
    ma200: Decimal | None
    above_ma200: bool


def _return_1y_pct(rows: list[OHLCVRow], current: Decimal) -> Decimal | None:
    """1년 수익률 폴백 — (현재가/약 252거래일 前 종가 - 1)×100.

    이력이 부족(``len(rows) <= _TRADING_DAYS_1Y``)하거나 기준 종가 ≤ 0 이면 ``None``.
    """
    if len(rows) <= _TRADING_DAYS_1Y:
        return None
    past_close = rows[-(_TRADING_DAYS_1Y + 1)].close
    if past_close <= 0:
        return None
    return (current / past_close - Decimal("1")) * Decimal("100")


def _index_momentum(provider: MarketDataProvider, market: Market, settings: Settings) -> Decimal:
    """시장 지수(KR=KOSPI, US=S&P500)의 lookback 모멘텀 — 스캔당 1회. RS 분모.

    지수 결측(yfinance 429·빈 결과 등)은 중립 0 으로 폴백한다 → RS 가 절대 모멘텀으로
    수렴할 뿐 전체 스캔을 막지 않는다(per-ticker 흡수 밖이라 여기서 명시적 가드).
    """
    try:
        idx_rows = provider.get_index_ohlcv(market, settings.lookback_days + 8)
    except _TICKER_ERRORS:
        return Decimal("0")
    if len(idx_rows) < 2:
        return Decimal("0")
    return sc.compute_momentum(idx_rows[-settings.lookback_days :])


def _collect_raw(
    ticker: str,
    market: Market,
    provider: MarketDataProvider,
    settings: Settings,
    index_momentum: Decimal,
) -> _Raw:
    """ticker 의 원천 데이터를 조회하고 raw 팩터를 산출해 ``_Raw`` 로 묶는다.

    ``index_momentum`` 은 스캔당 1회 산정한 시장 지수의 lookback 모멘텀으로,
    RS(지수대비 상대수익률 = 종목모멘텀 − 지수모멘텀) 계산에 쓴다.

    조회 실패는 호출 측(``score_market``)이 try/except 로 흡수하므로 여기서는
    예외를 그대로 전파한다.
    """
    # 1년 수익률 폴백 계산을 위해 OHLCV 요청일수를 약 252거래일+버퍼까지 확대.
    ohlcv_days = max(settings.ma200_window + settings.lookback_days, _TRADING_DAYS_1Y) + 8
    rows = provider.get_daily_ohlcv(ticker, market, ohlcv_days)
    if not rows:
        raise ValueError(f"no ohlcv: {ticker}")
    quote = provider.get_quote(ticker, market)
    fundamentals = provider.get_fundamentals(ticker, market)
    if fundamentals.return_1y_pct is None:
        # 소스가 1년 수익률을 주지 않으면 OHLCV 로 결정론 폴백.
        fundamentals = fundamentals.model_copy(
            update={"return_1y_pct": _return_1y_pct(rows, quote.price)}
        )
    flow = provider.get_investor_flow(ticker) if market == "KR" else None

    turnover = quote.turnover if quote.turnover is not None else Decimal("0")
    min_turnover = settings.min_turnover_krw if market == "KR" else settings.min_turnover_usd
    from backend.factors import build_candidate  # 지역 import (순환 회피)

    candidate = build_candidate(
        ticker=ticker,
        rows=rows,
        w52_high=fundamentals.w52_high,
        index_momentum=index_momentum,
        turnover=turnover,
        min_turnover=min_turnover,
        settings=settings,
        market=market,
    )
    above = candidate.above_ma200
    ma200 = sc.simple_moving_average(rows, settings.ma200_window)
    # 이미 받은 fundamentals.name(KR=hts_kor_isnm, US=shortName)을 우선 사용해
    # get_name 의 중복 inquire-price 호출을 제거한다(없을 때만 폴백).
    name = fundamentals.name or provider.get_name(ticker, market)
    return _Raw(
        ticker=ticker,
        name=name,
        rows=rows,
        quote=quote,
        fundamentals=fundamentals,
        investor_flow=flow,
        candidate=candidate,
        ma200=ma200,
        above_ma200=above,
    )


def _ineligible_breakdown(
    raw: _Raw, settings: Settings, bounds: sc.FactorBounds | None
) -> FactorBreakdown:
    """부적격(ineligible) 종목의 팩터 분해 — '어디서 미달했는지' 진단 표시.

    momentum/rs/turnover 는 점수 정규화 모집단(적격)에서 빠지지만, 진단을 위해 **통과
    종목 밴드 기준 선형 위치**(``linear_position`` — 클램프 없음: 밴드 미달이면 음수,
    초과면 1↑)로 매긴다. 통과 종목이 없으면(``bounds`` 가 None) 0. near_52w·
    pocket_pivot(0/1)·vol_fit 은 원시 기반(실제 신호 — 변동성 밴드 밖이면 vol_fit=0 등)
    이라 그대로 둔다. (부적격 점수 자체는 호출 측에서 0 — 본 분해는 표시·진단용.)
    """
    cand = raw.candidate
    if bounds is not None:
        momentum_norm = sc.linear_position(cand.momentum, bounds.momentum_lo, bounds.momentum_hi)
        rs_norm = sc.linear_position(cand.rs, bounds.rs_lo, bounds.rs_hi)
        turnover_norm = sc.linear_position(cand.turnover, bounds.turnover_lo, bounds.turnover_hi)
    else:
        momentum_norm = rs_norm = turnover_norm = Decimal("0")
    return FactorBreakdown(
        near_52w=cand.near_52w,
        pocket_pivot=Decimal("1") if cand.has_pocket_pivot else Decimal("0"),
        momentum_norm=momentum_norm,
        rs_norm=rs_norm,
        turnover_norm=turnover_norm,
        vol_fit=sc.volatility_fit(cand.volatility, settings.vol_band_low, settings.vol_band_high),
        momentum=cand.momentum,
        rs=cand.rs,
        volatility=cand.volatility,
        above_ma200=raw.above_ma200,
    )


def _change_from_open(price: Decimal, open_price: Decimal | None) -> Decimal | None:
    """장시작 대비 상승율 (price-open)/open*100. open 없거나 0 이면 ``None``."""
    if open_price is None or open_price == 0:
        return None
    return (price - open_price) / open_price * Decimal("100")


def _change_pct(price: Decimal, prev_close: Decimal | None) -> Decimal | None:
    """전일 종가 대비 등락율. prev_close 없거나 0 이면 ``None``."""
    if prev_close is None or prev_close == 0:
        return None
    return (price - prev_close) / prev_close * Decimal("100")


def _rationale(
    *,
    sell_reason: SellReason | None,
    factors: FactorBreakdown,
    near_52w_pct: Decimal | None,
    trailing_stop_pct: Decimal,
) -> str:
    """한국어 한 줄 근거. 매도요구면 그 사유, 아니면 강점 요약.

    매도요구(매도판단)는 점수(매수판단)와 독립인 축이다 — 추세 종목이 최근 고점
    대비 트레일링 이탈하면 점수와 무관히 매도가 옳다.
    """
    if sell_reason == "trailing_stop":
        return f"최근 고점 대비 -{trailing_stop_pct.normalize()}% 트레일링 이탈"
    if sell_reason == "ma200_break":
        return "200일선 이탈 — 추세 훼손"
    parts: list[str] = []
    if factors.above_ma200:
        parts.append("200일선 위")
    if near_52w_pct is not None:
        parts.append(f"52주 신고가 {near_52w_pct.quantize(Decimal('1'))}% 근접")
    if factors.pocket_pivot > 0:
        parts.append("포켓피봇 발생")
    return "·".join(parts) if parts else "추세 조건 미충족"


def _build_entry(
    raw: _Raw,
    market: Market,
    score_100: Decimal,
    factors: FactorBreakdown,
    grade: Grade,
    *,
    stop_price: Decimal | None,
    trailing_peak: Decimal | None,
    sell_reason: SellReason | None,
    themes: list[ThemeDef],
    settings: Settings,
) -> ScoreEntry:
    """``_Raw`` + 점수/등급/손절 결과 → 표시용 ``ScoreEntry`` 조립."""
    quote = raw.quote
    fund = raw.fundamentals
    near_52w_pct = (
        quote.price / fund.w52_high * Decimal("100")
        if fund.w52_high is not None and fund.w52_high > 0
        else None
    )
    sell_alert = sell_reason is not None
    final_grade = Grade.SELL if sell_alert else grade
    rationale = _rationale(
        sell_reason=sell_reason,
        factors=factors,
        near_52w_pct=near_52w_pct,
        trailing_stop_pct=settings.trailing_stop_pct,
    )
    return ScoreEntry(
        ticker=raw.ticker,
        name=raw.name,
        market=market,
        themes=themes_for_ticker(raw.ticker, market, themes),
        price=quote.price,
        open_price=quote.open,
        change_from_open_pct=_change_from_open(quote.price, quote.open),
        change_pct=_change_pct(quote.price, quote.prev_close),
        volume=quote.volume,
        turnover=quote.turnover,
        market_cap=fund.market_cap,
        w52_high=fund.w52_high,
        w52_low=fund.w52_low,
        near_52w_pct=near_52w_pct,
        return_1y_pct=fund.return_1y_pct,
        per=fund.per,
        pbr=fund.pbr,
        eps=fund.eps,
        sector=fund.sector,
        industry=fund.industry,
        score=score_100,
        grade=final_grade,
        eligible=raw.candidate.eligible,
        factors=factors,
        ma200=raw.ma200,
        stop_price=stop_price,
        trailing_peak=trailing_peak,
        sell_alert=sell_alert,
        sell_reason=sell_reason,
        rationale=rationale,
        investor_flow=raw.investor_flow,
    )


def score_market(
    market: Market,
    provider: MarketDataProvider,
    store: Store,
    settings: Settings,
    now: datetime,
    themes: list[ThemeDef] | None = None,
) -> Snapshot:
    """``market`` 전체를 스캔·점수화해 ``Snapshot`` 을 만들고 ``store`` 에 저장한다.

    점수(매수판단)는 적격(eligible) 종목만 cross-sectional 정규화하고, 부적격은
    점수 0·원시 팩터로 직접 조립한다. 매도요구(매도판단)는 200일선 위(추세 진입)
    종목의 (peak,stop)·트레일링·200일선 이탈을 매 사이클 가격이력에서 무상태로
    산정하며, 발동 시 등급을 ``Grade.SELL`` 로 오버라이드한다(점수와 독립 축).
    ``counts`` 로 스캔/통과/점수/실패 수를 보고한다.
    """
    theme_defs = themes if themes is not None else []
    universe = provider.list_universe(market)

    # I/O 병렬 — provider 호출(_collect_raw)만 ThreadPoolExecutor 로 동시 수집한다.
    # httpx.Client·yfinance 는 스레드세이프(토큰 발급만 Lock). 이후 점수화·손절·저장은
    # 순차(스레드 안전). per-ticker 예외는 _TICKER_ERRORS 로 흡수해 failed 카운트.
    raws: list[_Raw] = []
    failed = 0
    # 지수 모멘텀은 스캔당 1회만 산정(종목별 반복 조회 금지) → 모든 종목에 공유 주입.
    index_momentum = _index_momentum(provider, market, settings)
    with ThreadPoolExecutor(max_workers=settings.max_workers) as pool:
        futures = [
            pool.submit(_collect_raw, t, market, provider, settings, index_momentum)
            for t in universe
        ]
        for future in futures:
            try:
                raws.append(future.result())
            except _TICKER_ERRORS:
                failed += 1

    # 정규화 모집단은 적격 종목만 — 부적격이 cross-sectional min/max 를 오염시키지 않게
    # (원본 screener.py:805-850 과 동치: 하드필터 통과분만 _score_trend_candidates 로).
    eligible_raws = [r for r in raws if r.candidate.eligible]
    eligible_cands = [r.candidate for r in eligible_raws]
    scored = sc.score_candidates(eligible_cands, settings)
    # 부적격 진단 표시용 — 통과 종목 밴드(momentum/rs/turnover min·max).
    bounds = sc.factor_bounds(eligible_cands)

    entries: list[ScoreEntry] = []
    for raw in raws:
        if raw.candidate.eligible:
            score_norm, factors = scored[raw.ticker]
            score_100 = score_norm * Decimal("100")
        else:
            # 부적격 — 점수 0. 팩터는 통과 종목 밴드 대비 위치로 매겨 미달 항목을 드러낸다.
            score_100 = Decimal("0")
            factors = _ineligible_breakdown(raw, settings, bounds)
        grade = sc.grade_for_score(score_100, settings)

        # 무상태 트레일링 손절 — 매도요구 게이트는 200일선 위(추세 진입)이며 eligible 이
        # 아니다(매수판단·매도판단은 독립 축). 200일선 아래는 손절 미산정(회피).
        peak: Decimal | None = None
        stop_price: Decimal | None = None
        sell_reason: SellReason | None = None
        if raw.above_ma200:
            peak, stop_price = compute_trailing_stop(
                raw.rows,
                raw.quote.price,
                window=settings.trail_window_days,
                pct=settings.trailing_stop_pct,
            )
            sell_reason = evaluate_sell(
                current=raw.quote.price,
                peak=peak,
                ma200=raw.ma200,
                pct=settings.trailing_stop_pct,
            )

        entries.append(
            _build_entry(
                raw,
                market,
                score_100,
                factors,
                grade,
                stop_price=stop_price,
                trailing_peak=peak,
                sell_reason=sell_reason,
                themes=theme_defs,
                settings=settings,
            )
        )

    entries.sort(key=lambda e: e.score, reverse=True)

    counts = SnapshotCounts(
        scanned=len(universe),
        eligible=sum(1 for r in raws if r.candidate.eligible),
        scored=len(entries),
        failed=failed,
    )
    snapshot = Snapshot(
        market=market,
        generated_at=now,
        next_refresh_at=market_hours.next_refresh_at(market, now, settings.refresh_interval_min),
        market_open=market_hours.is_market_open(market, now),
        counts=counts,
        entries=entries,
    )
    store.save_snapshot(snapshot)
    return snapshot


def build_themes_response(
    snapshots: dict[Market, Snapshot],
    themes: list[ThemeDef],
    settings: Settings,
    now: datetime,
) -> ThemesResponse:
    """시장별 스냅샷 + 테마 정의 → 테마별 주도주 응답.

    각 시장 스냅샷의 엔트리를 테마별로 묶어(``settings.top_n_per_theme``) 주도주를
    추린다. ``market_open`` 맵과 ``disclaimer`` 를 포함한다.
    """
    entries_by_market: dict[Market, list[ScoreEntry]] = {
        m: snap.entries for m, snap in snapshots.items()
    }
    groups: list[ThemeGroup] = build_theme_groups(
        entries_by_market, themes, settings.top_n_per_theme
    )
    market_open: dict[str, bool] = {str(m): market_hours.is_market_open(m, now) for m in _MARKETS}
    return ThemesResponse(
        generated_at=now,
        market_open=market_open,
        disclaimer=DISCLAIMER,
        groups=groups,
    )


def ticker_detail(market: Market, code: str, store: Store) -> ScoreEntry | None:
    """저장된 스냅샷에서 단일 종목 상세를 조회. 없으면 ``None``."""
    snapshot = store.load_snapshot(market)
    if snapshot is None:
        return None
    for entry in snapshot.entries:
        if entry.ticker == code:
            return entry
    return None


__all__ = ["build_themes_response", "score_market", "ticker_detail"]
