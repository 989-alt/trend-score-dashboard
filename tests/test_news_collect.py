"""``backend.news.collect`` — RSS 파싱(모킹) · 텔레그램 자격증명 없으면 skip · 소스 격리."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import backend.news.collect as collect_mod
import pytest
from backend.config import Settings
from backend.news.collect import collect_all, collect_rss
from backend.news.sources import NewsSource

_NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)
_PARSED = time.struct_time((2026, 6, 18, 3, 0, 0, 0, 0, 0))  # UTC


class _Resp:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _Entry:
    def __init__(self, title: str, link: str, id_: str) -> None:
        self.title = title
        self.link = link
        self.id = id_
        self.published_parsed = _PARSED


class _Feed:
    def __init__(self, entries: list[_Entry]) -> None:
        self.entries = entries


def _patch_feed(monkeypatch: pytest.MonkeyPatch, entries: list[_Entry]) -> None:
    monkeypatch.setattr(collect_mod.httpx, "get", lambda *a, **k: _Resp(b"x"))
    monkeypatch.setattr(collect_mod.feedparser, "parse", lambda c: _Feed(entries))


def test_collect_rss_parses_and_skips_empty_title(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_feed(
        monkeypatch,
        [_Entry("삼성전자 신고가", "https://n/1", "g1"), _Entry("", "https://n/2", "g2")],
    )
    out = collect_rss(NewsSource("MK", "rss", "https://x"), Settings(data_mode="sample"), _NOW)
    assert len(out) == 1
    assert out[0].title == "삼성전자 신고가"
    assert out[0].external_id == "g1"
    assert out[0].source_kind == "rss"


def test_collect_all_skips_telegram_without_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_feed(monkeypatch, [])
    settings = Settings(data_mode="sample", telegram_api_id=0, telegram_api_hash="")
    sources = [NewsSource("MK", "rss", "u"), NewsSource("ch", "telegram", "ch1")]
    items, ok, failed = collect_all(sources, settings, _NOW)
    assert items == []
    assert ok == 1  # RSS 만 시도(텔레그램은 자격증명 없어 skip — 실패 아님)
    assert failed == 0


def test_collect_all_isolates_rss_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> _Resp:
        raise collect_mod.httpx.ConnectError("down")

    monkeypatch.setattr(collect_mod.httpx, "get", boom)
    settings = Settings(data_mode="sample", telegram_api_id=0, telegram_api_hash="")
    items, ok, failed = collect_all([NewsSource("MK", "rss", "u")], settings, _NOW)
    assert items == []
    assert ok == 0
    assert failed == 1
