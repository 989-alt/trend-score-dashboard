from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from backend.news.models import FactsetArticle, RawNewsItem, WeeklySummary
from backend.news.store import NewsStore


def _item(channel: str, msg_id: int, *, text: str = "급락 속보", mins: int = 0) -> RawNewsItem:
    ts = datetime(2026, 6, 18, 3, 0, tzinfo=UTC) + timedelta(minutes=mins)
    return RawNewsItem(
        source="telegram", channel=channel, msg_id=msg_id, ts_utc=ts, text=text, urls=()
    )


def test_insert_raw_dedup(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    assert store.insert_raw(_item("ch", 1)) is True
    assert store.insert_raw(_item("ch", 1)) is False
    assert store.insert_raw(_item("ch", 2)) is True


def test_cursor_roundtrip(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    assert store.get_cursor("ch") == 0
    store.set_cursor("ch", 42)
    assert store.get_cursor("ch") == 42
    store.set_cursor("ch", 99)
    assert store.get_cursor("ch") == 99


def test_recent_raw_window_and_kst(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    store.insert_raw(_item("ch", 1, mins=0))
    store.insert_raw(_item("ch", 2, mins=120))
    rows = store.recent_raw(datetime(2026, 6, 18, 4, 0, tzinfo=UTC))
    assert [r.msg_id for r in rows] == [2]
    assert rows[0].ts_kst.utcoffset() == timedelta(hours=9)


def test_factset_upsert_and_recent(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    art = FactsetArticle(
        url="https://x/a",
        title="T",
        excerpt="e",
        published_at=datetime(2026, 6, 16, tzinfo=UTC),
    )
    assert store.upsert_factset(art) is True
    assert store.upsert_factset(art) is False
    got = store.recent_factset(datetime(2026, 6, 1, tzinfo=UTC))
    assert len(got) == 1 and got[0].title == "T"


def test_weekly_save_latest(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    assert store.latest_weekly() is None
    store.save_weekly(
        WeeklySummary(
            week_start=date(2026, 6, 8),
            kr_markdown="A",
            model="m",
            generated_at=datetime(2026, 6, 13, tzinfo=UTC),
        )
    )
    store.save_weekly(
        WeeklySummary(
            week_start=date(2026, 6, 15),
            kr_markdown="B",
            model="m",
            generated_at=datetime(2026, 6, 20, tzinfo=UTC),
        )
    )
    latest = store.latest_weekly()
    assert latest is not None and latest.kr_markdown == "B"
