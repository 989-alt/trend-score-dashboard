from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backend.config import Settings
from backend.news.collector import (
    collect_channel,
    collect_once,
    extract_urls,
    message_to_item,
    store_items,
)
from backend.news.store import NewsStore


@dataclass
class FakeMsg:
    id: int
    date: datetime
    message: str


class FakeClient:
    """telethon get_messages 흉내. reverse=False=최신우선(시드)·True=오래된우선(catch-up)."""

    def __init__(self, data: dict[str, list[FakeMsg]]) -> None:
        self._data = data

    async def get_messages(
        self, channel: str, limit: int | None = None, *, min_id: int = 0, reverse: bool = False
    ) -> list[FakeMsg]:
        msgs = [m for m in self._data.get(channel, []) if m.id > min_id]
        msgs.sort(key=lambda m: m.id, reverse=not reverse)
        return msgs[:limit] if limit is not None else msgs


def _msgs(n: int) -> list[FakeMsg]:
    ts = datetime(2026, 6, 18, 3, 0, tzinfo=UTC)
    return [FakeMsg(id=i, date=ts, message=f"뉴스{i}") for i in range(1, n + 1)]


def test_extract_urls() -> None:
    assert extract_urls("속보 https://a.com/x 그리고 http://b.kr 끝") == (
        "https://a.com/x",
        "http://b.kr",
    )
    assert extract_urls("링크 없음") == ()


def test_message_to_item_maps_and_skips_empty() -> None:
    ts = datetime(2026, 6, 18, 3, 0, tzinfo=UTC)
    item = message_to_item("getfeed", FakeMsg(id=7, date=ts, message="삼성전자 급락 https://x.io"))
    assert item is not None
    assert (item.channel, item.msg_id, item.source) == ("getfeed", 7, "telegram")
    assert item.urls == ("https://x.io",)
    assert message_to_item("getfeed", FakeMsg(id=8, date=ts, message="   ")) is None


def test_store_items_counts_new(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    ts = datetime(2026, 6, 18, 3, 0, tzinfo=UTC)
    items = [message_to_item("c", FakeMsg(id=i, date=ts, message=f"뉴스{i}")) for i in (1, 2)]
    items = [i for i in items if i is not None]
    assert store_items(store, items) == 2
    assert store_items(store, items) == 0


async def test_collect_channel_seed_then_catchup(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    client = FakeClient({"c": _msgs(3)})
    assert await collect_channel(store, client, "c") == 3  # 시드(최신 3)
    assert store.get_cursor("c") == 3
    assert await collect_channel(store, client, "c") == 0  # 새 메시지 없음
    client._data["c"] = _msgs(5)  # 4,5 추가
    assert await collect_channel(store, client, "c") == 2  # catch-up 4,5
    assert store.get_cursor("c") == 5


async def test_collect_once_no_keys_fail_open(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert await collect_once(store, s) == {}


async def test_collect_once_injected_client(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None, news_channels="a,b")  # type: ignore[call-arg]
    client = FakeClient({"a": _msgs(2), "b": _msgs(1)})
    assert await collect_once(store, s, client=client) == {"a": 2, "b": 1}
