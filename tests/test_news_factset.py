from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backend.config import Settings
from backend.news.factset import collect_factset, parse_feed
from backend.news.store import NewsStore

_RSS = (Path(__file__).parent / "fixtures" / "factset_rss.xml").read_text(encoding="utf-8")


def test_parse_feed_extracts_items() -> None:
    arts = parse_feed(_RSS)
    assert len(arts) == 2
    first = arts[0]
    assert first.title == "S&P 500 Earnings Insight"
    assert first.url == "https://insight.factset.com/sp-500-earnings-insight-q2"
    assert first.published_at == datetime(2026, 6, 16, 21, 0, tzinfo=UTC)
    assert first.excerpt == "Earnings growth estimates for Q2."  # HTML 태그 제거


def test_parse_feed_bad_xml_returns_empty() -> None:
    assert parse_feed("<rss><broken") == []


def test_collect_factset_stores(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert collect_factset(store, s, http_get=lambda _u: _RSS) == 2
    assert collect_factset(store, s, http_get=lambda _u: _RSS) == 0  # upsert 중복
    assert len(store.recent_factset(datetime(2026, 6, 1, tzinfo=UTC))) == 2


def test_collect_factset_fail_open(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None)  # type: ignore[call-arg]

    def boom(_u: str) -> str:
        raise RuntimeError("network down")

    assert collect_factset(store, s, http_get=boom) == 0
