"""``backend.news.service`` — 수집(모킹)→적재→렉시콘→랭킹→스냅샷 저장 일관 흐름."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import backend.news.service as service_mod
import pytest
from backend.config import Settings
from backend.news.collect import NewsItem
from backend.news.service import empty_issues, refresh_issues
from backend.news.store import NewsStore
from backend.schemas import Grade, ScoreEntry, Snapshot, SnapshotCounts
from backend.store import Store

_NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


def _snapshot() -> Snapshot:
    entry = ScoreEntry(
        ticker="005930",
        name="삼성전자",
        market="KR",
        price=Decimal("1"),
        score=Decimal("77"),
        grade=Grade.BUY,
        eligible=True,
    )
    return Snapshot(
        market="KR",
        generated_at=_NOW,
        market_open=True,
        counts=SnapshotCounts(),
        entries=[entry],
    )


def _item(ext: str, title: str, hours_ago: int) -> NewsItem:
    return NewsItem(
        source_name="MK",
        source_kind="rss",
        external_id=ext,
        title=title,
        url="https://x",
        published_at=_NOW - timedelta(hours=hours_ago),
        collected_at=_NOW,
    )


def test_refresh_issues_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = Store(tmp_path / "d.db")
    store.save_snapshot(_snapshot())
    news_store = NewsStore(tmp_path / "n.db")

    fake = [_item("a", "삼성전자 신고가", 1), _item("b", "삼성전자 어닝 서프라이즈", 2)]
    monkeypatch.setattr(service_mod, "collect_all", lambda *a, **k: (fake, 1, 0))

    settings = Settings(
        data_mode="sample", news_min_mentions=2, news_recent_hours=24, news_baseline_days=7
    )
    resp = refresh_issues(store, news_store, settings, _NOW)

    assert resp.counts.collected == 2
    assert resp.counts.sources_ok == 1
    samsung = next(e for e in resp.issues if e.key == "005930")
    assert samsung.mention_count == 2
    assert samsung.score == Decimal("77")
    assert samsung.grade == "buy"
    # 영속 확인 — 저장본 == 반환본.
    assert news_store.load_issues() == resp


def test_empty_issues() -> None:
    resp = empty_issues(Settings(data_mode="sample", news_recent_hours=12), _NOW)
    assert resp.issues == []
    assert resp.window_hours == 12
