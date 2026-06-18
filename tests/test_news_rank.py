"""``backend.news.rank.rank_issues`` — 급등 산정 · min_mentions · 정렬 · 상세 연결."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backend.config import Settings
from backend.news.collect import NewsItem
from backend.news.extract import Lexicon, build_lexicon
from backend.news.rank import rank_issues
from backend.schemas import Grade, Market, ScoreEntry
from backend.themes import ThemeDef

_NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


def _entry(ticker: str, name: str, score: str = "80", market: Market = "KR") -> ScoreEntry:
    return ScoreEntry(
        ticker=ticker,
        name=name,
        market=market,
        price=Decimal("1"),
        score=Decimal(score),
        grade=Grade.STRONG_BUY,
        eligible=True,
    )


def _lexicon() -> Lexicon:
    entries: dict[Market, list[ScoreEntry]] = {
        "KR": [_entry("005930", "삼성전자"), _entry("000660", "SK하이닉스")],
        "US": [],
    }
    return build_lexicon(entries, [ThemeDef(name="반도체", kr=(), us=())])


def _item(ext: str, title: str, hours_ago: int, source: str = "MK") -> NewsItem:
    return NewsItem(
        source_name=source,
        source_kind="rss",
        external_id=ext,
        title=title,
        url="https://x",
        published_at=_NOW - timedelta(hours=hours_ago),
        collected_at=_NOW,
    )


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "data_mode": "sample",
        "news_recent_hours": 24,
        "news_baseline_days": 7,
        "news_min_mentions": 2,
        "news_max_issues": 30,
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def test_min_mentions_filters_noise() -> None:
    issues, _ = rank_issues([_item("1", "삼성전자 신고가", 1)], _lexicon(), {}, _settings(), _NOW)
    assert issues == []  # 1회 언급 < min_mentions(2)


def test_spike_ranking_and_linkage() -> None:
    items = [
        _item("a", "삼성전자 어닝", 1),
        _item("b", "삼성전자 신고가", 2),
        _item("c", "삼성전자 외인매수", 3),
        _item("d", "반도체 업황", 1),
        _item("e", "반도체 회복", 2),
        _item("f", "삼성전자 과거기사", 30),  # 베이스라인(>24h)
    ]
    by_ticker = {"005930": _entry("005930", "삼성전자", "82")}
    issues, items_recent = rank_issues(items, _lexicon(), by_ticker, _settings(), _NOW)

    assert items_recent == 5  # 최근 윈도(<24h) 항목수
    keys = [e.key for e in issues]
    assert keys[0] == "005930"  # 삼성 spike(베이스라인 있어도 3건) > 반도체(2건)
    assert set(keys) == {"005930", "반도체"}

    samsung = next(e for e in issues if e.key == "005930")
    assert samsung.kind == "ticker"
    assert samsung.mention_count == 3
    assert samsung.score == Decimal("82")  # 스냅샷에서 연결
    assert samsung.grade == "strong_buy"
    assert samsung.market == "KR"
    assert len(samsung.headlines) == 3
    assert "MK" in samsung.sources
    assert samsung.spike > Decimal("0")


def test_recent_count_excludes_baseline() -> None:
    items = [_item("a", "삼성전자 오늘", 1), _item("b", "삼성전자 과거", 40)]
    issues, items_recent = rank_issues(items, _lexicon(), {}, _settings(news_min_mentions=1), _NOW)
    samsung = next(e for e in issues if e.key == "005930")
    assert samsung.mention_count == 1  # 최근 1건만(베이스라인 제외)
    assert items_recent == 1
