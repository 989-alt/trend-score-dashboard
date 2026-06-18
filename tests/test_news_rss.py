"""``backend.news.rss`` — 피드 로딩 · 파싱(모킹) · 수집 dedup · 피드별 fail-open."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import backend.news.rss as rss_mod
import pytest
from backend.config import Settings
from backend.news.rss import collect_rss_once, fetch_feed, load_feeds
from backend.news.store import NewsStore

_NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
_PARSED = time.struct_time((2026, 6, 18, 3, 0, 0, 0, 0, 0))  # UTC


class _Resp:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _Entry:
    def __init__(self, title: str, link: str) -> None:
        self.title = title
        self.link = link
        self.published_parsed = _PARSED


class _Feed:
    def __init__(self, entries: list[_Entry]) -> None:
        self.entries = entries


def test_load_feeds_parses_valid_and_skips_blank(tmp_path: Path) -> None:
    p = tmp_path / "f.yml"
    p.write_text(
        'feeds:\n  - {name: "MK", url: "https://x/rss"}\n  - {name: "", url: "u"}\n',
        encoding="utf-8",
    )
    assert load_feeds(p) == [("MK", "https://x/rss")]


def test_load_feeds_missing(tmp_path: Path) -> None:
    assert load_feeds(tmp_path / "none.yml") == []


def test_fetch_feed_parses_and_skips_empty_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rss_mod.httpx, "get", lambda *a, **k: _Resp(b"x"))
    monkeypatch.setattr(
        rss_mod.feedparser,
        "parse",
        lambda c: _Feed([_Entry("삼성전자 신고가", "https://n/1"), _Entry("", "https://n/2")]),
    )
    items = fetch_feed("MK", "https://x", _NOW)
    assert len(items) == 1  # 빈 제목 skip
    it = items[0]
    assert it.source == "rss"
    assert it.channel == "MK"
    assert it.text == "삼성전자 신고가"
    assert it.urls == ("https://n/1",)


def test_collect_rss_once_dedup_and_fail_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = NewsStore(tmp_path / "n.db")
    feeds = tmp_path / "f.yml"
    feeds.write_text(
        'feeds:\n  - {name: "MK", url: "https://x/rss"}\n  - {name: "DOWN", url: "https://y/rss"}\n',
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        data_mode="sample",
        news_db_path=tmp_path / "n.db",
        news_sources_path=feeds,
    )

    def fake_get(url: str, **k: object) -> _Resp:
        if "y/rss" in url:
            raise rss_mod.httpx.ConnectError("down")
        return _Resp(b"x")

    monkeypatch.setattr(rss_mod.httpx, "get", fake_get)
    monkeypatch.setattr(
        rss_mod.feedparser, "parse", lambda c: _Feed([_Entry("삼성전자 신고가", "https://n/1")])
    )

    first = collect_rss_once(store, settings, now=_NOW)
    assert first == {"MK": 1, "DOWN": 0}  # MK 신규 1, DOWN 은 fail-open(0)
    second = collect_rss_once(store, settings, now=_NOW)
    assert second["MK"] == 0  # 같은 URL → dedup
