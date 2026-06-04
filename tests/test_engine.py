"""engine 모듈 테스트 — score_market(KR/US) · build_themes_response · ticker_detail.

검증은 외부 API 없이 ``SampleProvider`` (결정론 합성데이터) + 임시 SQLite ``Store`` 로만
수행한다. 핵심 불변식:
- 엔트리가 1개 이상 생기고 점수 내림차순으로 정렬된다.
- ``counts`` 가 정합(scanned=유니버스 수, scored=엔트리 수, eligible≤scored, failed≥0).
- 매도요구(무상태 트레일링 이탈) 종목이 ``Grade.SELL`` 로 오버라이드된다.
- KR 엔트리는 ``investor_flow`` 가 채워지고 US 엔트리는 ``None``.
- ``build_themes_response`` 가 테마 그룹을 만든다.

원칙: 점수·가격은 ``Decimal``, ``now`` 는 timezone-aware (KR 영업일 장중 시각 고정).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from backend import scoring as sc
from backend.config import Settings
from backend.engine import build_themes_response, score_market, ticker_detail
from backend.market_data import SampleProvider
from backend.schemas import Grade, Market, Snapshot
from backend.store import Store
from backend.themes import ThemeDef, load_themes

# KR 영업일(목) 장중 10:00 KST — market_open=True, 결정론.
_NOW = datetime(2026, 6, 4, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
# SampleProvider 의 class-3(상승 후 급락) 종목 — 트레일링 이탈 매도요구 케이스.
_SELL_TICKER_KR = "000660"


@pytest.fixture
def settings() -> Settings:
    """sample 모드 설정. ``.env`` 의 live 모드가 새지 않도록 명시 고정."""
    return Settings(data_mode="sample")


@pytest.fixture
def provider() -> SampleProvider:
    """결정론 합성 Provider."""
    return SampleProvider()


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """임시 SQLite Store (테스트마다 격리)."""
    return Store(tmp_path / "engine_test.db")


@pytest.fixture
def themes(settings: Settings) -> list[ThemeDef]:
    """프로젝트 themes.yml 로드."""
    return load_themes(settings.themes_path)


# ── score_market: 기본 산출 ────────────────────────────────────────────────


def _run(
    market: Market,
    provider: SampleProvider,
    store: Store,
    settings: Settings,
    themes: list[ThemeDef],
) -> Snapshot:
    return score_market(market, provider, store, settings, _NOW, themes=themes)


def test_score_market_kr_produces_sorted_entries(
    provider: SampleProvider, store: Store, settings: Settings, themes: list[ThemeDef]
) -> None:
    """KR 스냅샷 — 엔트리>0, 점수 내림차순 정렬, counts 정합."""
    snap = _run("KR", provider, store, settings, themes)

    assert snap.market == "KR"
    assert snap.market_open is True  # 영업일 장중
    assert snap.generated_at == _NOW
    assert snap.next_refresh_at is not None
    assert snap.entries  # 1개 이상

    scores = [e.score for e in snap.entries]
    assert scores == sorted(scores, reverse=True)  # 내림차순
    assert all(isinstance(e.score, Decimal) for e in snap.entries)
    assert all(Decimal("0") <= e.score <= Decimal("100") for e in snap.entries)


def test_score_market_counts_consistent(
    provider: SampleProvider, store: Store, settings: Settings, themes: list[ThemeDef]
) -> None:
    """counts — scanned=유니버스, scored=엔트리, eligible≤scored, 합산 정합."""
    snap = _run("KR", provider, store, settings, themes)
    c = snap.counts

    assert c.scanned == len(provider.list_universe("KR"))
    assert c.scored == len(snap.entries)
    assert c.eligible <= c.scored
    assert c.failed >= 0
    # 샘플 데이터는 전부 조회 성공 → scored + failed == scanned.
    assert c.scored + c.failed == c.scanned
    # eligible 종목은 실제로 eligible 플래그가 True.
    assert c.eligible == sum(1 for e in snap.entries if e.eligible)
    assert c.eligible > 0  # 합성 유니버스에 통과 종목이 존재


def test_score_market_persists_snapshot(
    provider: SampleProvider, store: Store, settings: Settings, themes: list[ThemeDef]
) -> None:
    """스냅샷이 store 에 저장되어 재조회된다."""
    snap = _run("KR", provider, store, settings, themes)
    loaded = store.load_snapshot("KR")

    assert loaded is not None
    assert loaded.market == "KR"
    assert [e.ticker for e in loaded.entries] == [e.ticker for e in snap.entries]


def test_score_market_entry_fields_populated(
    provider: SampleProvider, store: Store, settings: Settings, themes: list[ThemeDef]
) -> None:
    """엔트리 표시 필드 — 장시작 대비 상승율·거래대금·근거가 채워진다."""
    snap = _run("KR", provider, store, settings, themes)
    entry = snap.entries[0]

    assert entry.price > 0
    assert entry.open_price is not None
    # 장시작 대비 = (price-open)/open*100 가 open 기반으로 산출됨.
    expected = (entry.price - entry.open_price) / entry.open_price * Decimal("100")
    assert entry.change_from_open_pct == expected
    assert entry.turnover is not None and entry.turnover > 0
    assert entry.factors is not None
    assert entry.rationale  # 비어있지 않은 한국어 한 줄


# ── score_market: 부적격 정규화 모집단 제외(FIX-1) / above_ma200 실제값(FIX-2) ──


def test_score_market_ineligible_excluded_from_normalization(
    provider: SampleProvider, store: Store, settings: Settings, themes: list[ThemeDef]
) -> None:
    """부적격 종목은 점수 0·정규화 모집단에서 제외되고, 적격 정렬은 보존된다.

    부적격이 cross-sectional min/max 를 오염시키지 않음을 간접 검증: 적격 종목만
    따로 ``score_candidates`` 로 매긴 점수와, 엔진이 산출한 적격 점수가 일치한다.
    """
    snap = _run("KR", provider, store, settings, themes)

    # 부적격은 모두 점수 0.
    for entry in snap.entries:
        if not entry.eligible:
            assert entry.score == Decimal("0")

    eligible_entries = [e for e in snap.entries if e.eligible]
    assert eligible_entries  # 적격 종목이 존재

    # 적격 종목만으로 직접 정규화·점수화한 결과(×100)와 엔진 점수가 동일.
    cands = [
        sc.Candidate(
            ticker=e.ticker,
            turnover=e.turnover if e.turnover is not None else Decimal("0"),
            momentum=e.factors.momentum if e.factors is not None else Decimal("0"),
            volatility=e.factors.volatility if e.factors is not None else Decimal("0"),
            near_52w=e.factors.near_52w if e.factors is not None else Decimal("0"),
            has_pocket_pivot=(e.factors.pocket_pivot > 0 if e.factors is not None else False),
            above_ma200=True,
            eligible=True,
        )
        for e in eligible_entries
    ]
    direct = sc.score_candidates(cands, settings)
    for e in eligible_entries:
        assert e.score == direct[e.ticker][0] * Decimal("100")


def test_score_market_above_ma200_reflects_actual_trend(
    provider: SampleProvider, store: Store, settings: Settings, themes: list[ThemeDef]
) -> None:
    """FactorBreakdown.above_ma200 은 실제 200일선 위 여부(eligible 와 별개).

    부적격(점수 0)이면서도 200일선 위인 종목이 가능하고, 그 경우 above_ma200=True.
    적격 종목은 항상 above_ma200=True(하드필터 조건).
    """
    snap = _run("KR", provider, store, settings, themes)
    for entry in snap.entries:
        assert entry.factors is not None
        if entry.eligible:
            assert entry.factors.above_ma200 is True
    # 적어도 한 종목은 above_ma200 가 True(상승추세 합성 종목 존재).
    assert any(e.factors is not None and e.factors.above_ma200 for e in snap.entries)


def test_score_market_return_1y_fallback_filled(
    provider: SampleProvider, store: Store, settings: Settings, themes: list[ThemeDef]
) -> None:
    """1년 수익률이 채워진다(소스 제공 또는 OHLCV 폴백).

    SampleProvider 는 return_1y 를 제공하지만, 폴백 경로가 None 으로 덮어쓰지 않음을
    함께 보장한다(엔트리에 값 존재).
    """
    snap = _run("KR", provider, store, settings, themes)
    assert snap.entries
    assert all(e.return_1y_pct is not None for e in snap.entries)


# ── score_market: KR 투자자별 매매 / US None ───────────────────────────────


def test_score_market_kr_fills_investor_flow(
    provider: SampleProvider, store: Store, settings: Settings, themes: list[ThemeDef]
) -> None:
    """KR 엔트리는 investor_flow 가 채워진다(외/기/개 순매수 합 0)."""
    snap = _run("KR", provider, store, settings, themes)

    flows = [e.investor_flow for e in snap.entries]
    assert all(f is not None for f in flows)
    sample = next(e.investor_flow for e in snap.entries)
    assert sample is not None
    total = sample.foreign_net + sample.institution_net + sample.individual_net
    assert total == Decimal("0")  # 시장 항등식(순매수 합 0)


def test_score_market_us_investor_flow_none(
    provider: SampleProvider, store: Store, settings: Settings, themes: list[ThemeDef]
) -> None:
    """US 엔트리는 investor_flow 가 항상 None."""
    snap = _run("US", provider, store, settings, themes)

    assert snap.market == "US"
    assert snap.entries
    assert all(e.investor_flow is None for e in snap.entries)


# ── score_market: 매도요구(트레일링 이탈) 오버라이드 ──────────────────────


def test_score_market_sell_alert_overrides_grade(
    provider: SampleProvider, store: Store, settings: Settings, themes: list[ThemeDef]
) -> None:
    """무상태 손절 — 급락 종목(class-3)이 가격이력만으로 SELL 로 오버라이드된다.

    선등록 없이 매 사이클 ``compute_trailing_stop`` 이 최근 ``trail_window_days`` 봉
    종가의 최고가를 peak 로 잡아, 현재가가 그 -8% 아래면 트레일링 매도요구가 난다.
    (SampleProvider 의 급락 기준 고점은 ~70봉 전이라, 그 고점을 포함하는 윈도로 평가.)
    """
    wide = settings.model_copy(update={"trail_window_days": 120})
    snap = _run("KR", provider, store, wide, themes)
    entry = next(e for e in snap.entries if e.ticker == _SELL_TICKER_KR)

    assert entry.sell_alert is True
    assert entry.grade is Grade.SELL  # 점수와 무관하게 오버라이드
    assert entry.sell_reason == "trailing_stop"
    assert entry.rationale is not None and "트레일링" in entry.rationale
    # 손절 산정 결과(peak/stop)가 채워진다(200일선 위 종목).
    assert entry.trailing_peak is not None
    assert entry.stop_price is not None
    # 스냅샷 전체에 매도요구 종목이 적어도 하나 존재.
    assert any(e.sell_alert for e in snap.entries)


def test_score_market_no_false_sell_for_uptrend(
    provider: SampleProvider, store: Store, settings: Settings, themes: list[ThemeDef]
) -> None:
    """무상태 손절이 거짓 매도요구를 만들지 않는다.

    매도요구가 있다면 반드시 SELL 등급·사유를 동반하고, 신고가 부근(현재가가 최근
    고점인) 종목은 트레일링이 발동하지 않는다.
    """
    snap = _run("US", provider, store, settings, themes)
    for entry in snap.entries:
        if entry.sell_alert:
            assert entry.grade is Grade.SELL
            assert entry.sell_reason is not None
        # 손절가가 산정된 종목(200일선 위)은 stop 이 현재가보다 낮아야 정상(보유 유지).
        if entry.sell_reason is None and entry.stop_price is not None:
            assert entry.price > entry.stop_price


# ── build_themes_response ──────────────────────────────────────────────────


def test_build_themes_response_groups(
    provider: SampleProvider, store: Store, settings: Settings, themes: list[ThemeDef]
) -> None:
    """KR/US 스냅샷 → 테마 그룹 생성, market_open 맵·면책 포함."""
    kr = _run("KR", provider, store, settings, themes)
    us = _run("US", provider, store, settings, themes)
    snapshots: dict[Market, Snapshot] = {"KR": kr, "US": us}

    resp = build_themes_response(snapshots, themes, settings, _NOW)

    assert resp.groups  # 테마 그룹이 생성됨
    assert resp.generated_at == _NOW
    assert set(resp.market_open) == {"KR", "US"}
    assert resp.market_open["KR"] is True
    assert resp.disclaimer
    # 각 그룹의 주도주는 점수 내림차순 top_n 이내.
    for group in resp.groups:
        assert 1 <= len(group.leaders) <= settings.top_n_per_theme
        leader_scores = [e.score for e in group.leaders]
        assert leader_scores == sorted(leader_scores, reverse=True)
    # 알려진 테마(반도체)가 그룹에 포함됨.
    assert any(g.theme == "반도체" for g in resp.groups)


# ── ticker_detail ──────────────────────────────────────────────────────────


def test_ticker_detail_found_and_missing(
    provider: SampleProvider, store: Store, settings: Settings, themes: list[ThemeDef]
) -> None:
    """저장된 스냅샷에서 단일 종목을 조회, 없는 종목은 None."""
    snap = _run("KR", provider, store, settings, themes)
    code = snap.entries[0].ticker

    found = ticker_detail("KR", code, store)
    assert found is not None
    assert found.ticker == code

    assert ticker_detail("KR", "999999", store) is None
    # 스냅샷이 없는 시장은 None.
    assert ticker_detail("US", code, store) is None
