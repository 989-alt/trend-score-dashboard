"""``backend.news.store.NewsStore`` — 적재 중복제거 · 최근 질의 · 이슈 라운드트립."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from backend.news.collect import NewsItem
from backend.news.store import NewsStore
from backend.schemas import IssueCounts, IssueEntry, IssuesResponse

_NOW = datetime(2026, 6, 18, 12, 0, tzinfo=UTC)


def _item(ext: str, *, hours_ago: int = 0, source: str = "MK") -> NewsItem:
    return NewsItem(
        source_name=source,
        source_kind="rss",
        external_id=ext,
        title=f"title {ext}",
        url="https://x",
        published_at=_NOW - timedelta(hours=hours_ago),
        collected_at=_NOW,
    )


def test_add_items_dedup(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "n.db")
    assert store.add_items([_item("a"), _item("b")]) == 2
    # 같은 (source, external_id) 는 무시 → 신규 1건만.
    assert store.add_items([_item("a"), _item("c")]) == 1
    rows = store.recent(_NOW - timedelta(days=1))
    assert {r.external_id for r in rows} == {"a", "b", "c"}


def test_recent_window_filters_old(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "n.db")
    store.add_items([_item("old", hours_ago=48), _item("new", hours_ago=1)])
    recent = store.recent(_NOW - timedelta(hours=24))
    assert [r.external_id for r in recent] == ["new"]


def test_issues_roundtrip(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "n.db")
    assert store.load_issues() is None
    resp = IssuesResponse(
        generated_at=_NOW,
        window_hours=24,
        counts=IssueCounts(collected=1, items_recent=3, sources_ok=2, sources_failed=0),
        issues=[
            IssueEntry(
                kind="theme",
                key="AI",
                name="AI",
                mention_count=3,
                baseline_count=1,
                spike=Decimal("1.50"),
            )
        ],
    )
    store.save_issues(resp)
    loaded = store.load_issues()
    assert loaded == resp
    assert loaded is not None
    assert isinstance(loaded.issues[0].spike, Decimal)
