"""themes 모듈 테스트 — themes.yml 로드 + 멤버십 역매핑 + 주도주 선정.

원칙: 점수는 ``Decimal``. 주도주는 (테마, 시장)별 점수 내림차순 ``top_n``.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from backend.config import DATA_DIR
from backend.schemas import Grade, Market, ScoreEntry
from backend.themes import (
    ThemeDef,
    build_theme_groups,
    load_themes,
    themes_for_ticker,
)


def _entry(ticker: str, market: Market, score: object) -> ScoreEntry:
    """최소 필드만 채운 ScoreEntry (점수 비교용)."""
    return ScoreEntry(
        ticker=ticker,
        name=ticker,
        market=market,
        price=Decimal("100"),
        score=Decimal(str(score)),
        grade=Grade.HOLD,
        eligible=True,
    )


# ── load_themes ───────────────────────────────────────────────────────────


def test_load_themes_from_yml() -> None:
    """data/themes.yml 로드 — 알려진 테마/구성이 그대로 들어온다."""
    themes = load_themes(DATA_DIR / "themes.yml")
    assert themes  # 비어있지 않음
    assert all(isinstance(t, ThemeDef) for t in themes)
    by_name = {t.name: t for t in themes}
    assert "반도체" in by_name
    semi = by_name["반도체"]
    assert "005930" in semi.kr  # 삼성전자
    assert "NVDA" in semi.us
    # kr/us 는 불변 튜플
    assert isinstance(semi.kr, tuple)
    assert isinstance(semi.us, tuple)


def test_load_themes_handles_missing_market_key(tmp_path: Path) -> None:
    """한쪽 시장만 가진 테마도 빈 튜플로 정상 로드된다."""
    yml = tmp_path / "themes.yml"
    yml.write_text(
        'themes:\n  - name: "원자력"\n    us: ["CEG", "VST"]\n',
        encoding="utf-8",
    )
    themes = load_themes(yml)
    assert len(themes) == 1
    assert themes[0].name == "원자력"
    assert themes[0].kr == ()
    assert themes[0].us == ("CEG", "VST")


# ── themes_for_ticker (멤버십 역인덱스) ────────────────────────────────────

_THEMES = [
    ThemeDef(name="반도체", kr=("005930", "000660"), us=("NVDA", "AMD")),
    ThemeDef(name="AI", kr=("035420",), us=("NVDA", "MSFT")),
    ThemeDef(name="원자력", kr=("034020",), us=("CEG",)),
]


def test_themes_for_ticker_kr_membership() -> None:
    """KR 종목이 속한 테마만(시장 일치) 돌려준다."""
    assert themes_for_ticker("005930", "KR", _THEMES) == ["반도체"]


def test_themes_for_ticker_multi_theme() -> None:
    """여러 테마에 속한 종목은 모두 반환(정의 순서 유지)."""
    assert themes_for_ticker("NVDA", "US", _THEMES) == ["반도체", "AI"]


def test_themes_for_ticker_market_isolation() -> None:
    """같은 문자열이라도 시장이 다르면 매칭되지 않는다."""
    # NVDA 는 US 구성에만 있으므로 KR 조회 시 빈 목록.
    assert themes_for_ticker("NVDA", "KR", _THEMES) == []


def test_themes_for_ticker_unknown() -> None:
    """어느 테마에도 없는 종목은 빈 목록."""
    assert themes_for_ticker("999999", "KR", _THEMES) == []


# ── build_theme_groups (주도주 선정) ───────────────────────────────────────


def test_build_theme_groups_per_market_sorted() -> None:
    """테마 내 시장별로 점수 내림차순 정렬 → 반도체는 KR 그룹 + US 그룹 둘 다 생성."""
    entries_by_market: dict[Market, list[ScoreEntry]] = {
        "KR": [_entry("000660", "KR", 40), _entry("005930", "KR", 90)],
        "US": [_entry("AMD", "US", 70), _entry("NVDA", "US", 80)],
    }
    groups = build_theme_groups(entries_by_market, _THEMES, top_n=3)
    semi = {g.market: g for g in groups if g.theme == "반도체"}
    assert set(semi) == {"KR", "US"}
    assert [e.ticker for e in semi["KR"].leaders] == ["005930", "000660"]
    assert [e.ticker for e in semi["US"].leaders] == ["NVDA", "AMD"]
    # 각 그룹의 주도주는 모두 그 시장 소속 (시장이 서로를 밀어내지 않는다)
    assert all(e.market == "KR" for e in semi["KR"].leaders)
    assert all(e.market == "US" for e in semi["US"].leaders)


def test_build_theme_groups_skips_empty_theme() -> None:
    """소속 엔트리가 하나도 없는 테마는 그룹에서 생략."""
    entries_by_market: dict[Market, list[ScoreEntry]] = {
        "KR": [_entry("034020", "KR", 50)],
        "US": [_entry("CEG", "US", 60)],
    }
    groups = build_theme_groups(entries_by_market, _THEMES, top_n=5)
    names = {g.theme for g in groups}
    assert names == {"원자력"}  # 반도체·AI 는 매칭 엔트리 없음 → 생략


def test_build_theme_groups_top_n_cut_per_market() -> None:
    """top_n 컷은 시장별로 독립 적용된다 (한 시장이 다른 시장의 자리를 뺏지 않음)."""
    entries_by_market: dict[Market, list[ScoreEntry]] = {
        "KR": [_entry("005930", "KR", 90), _entry("000660", "KR", 85)],
        "US": [_entry("NVDA", "US", 80), _entry("AMD", "US", 70)],
    }
    groups = build_theme_groups(entries_by_market, _THEMES, top_n=1)
    semi = {g.market: g for g in groups if g.theme == "반도체"}
    assert [e.ticker for e in semi["KR"].leaders] == ["005930"]  # KR top1
    assert [e.ticker for e in semi["US"].leaders] == ["NVDA"]  # US top1 (KR 90 에 밀리지 않음)
