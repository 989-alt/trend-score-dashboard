"""텔레그램 수집기 — 메시지→RawNewsItem(순수) + catch-up 폴링(telethon 어댑터).

순수 함수는 네트워크 없이 테스트한다. cursor==0(첫 폴링)은 최신 SEED_LIMIT 건만 시드해
과거 전체 역주입을 피하고, 이후는 min_id 초과분만 catch-up 한다(누락 0, 텔레그램 히스토리 보존).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from backend.config import Settings
from backend.news.models import RawNewsItem
from backend.news.store import NewsStore

_log = logging.getLogger(__name__)
_URL_RE = re.compile(r"https?://[^\s]+")

#: 첫 폴링(커서 없음) 때 시드할 최신 메시지 수.
SEED_LIMIT = 30
#: catch-up 1회 최대 건수(폭주 방지; 초과분은 다음 폴링이 흡수).
BATCH_LIMIT = 200


def extract_urls(text: str) -> tuple[str, ...]:
    """본문에서 http(s) URL 을 등장순으로 추출."""
    return tuple(str(u) for u in _URL_RE.findall(text))


def message_to_item(channel: str, msg: Any) -> RawNewsItem | None:
    """telethon Message(또는 .id/.date/.message 페이크) → RawNewsItem. 본문 없으면 None."""
    text = str(msg.message or "").strip()
    if not text:
        return None
    return RawNewsItem(
        source="telegram",
        channel=channel,
        msg_id=int(msg.id),
        ts_utc=msg.date,
        text=text,
        urls=extract_urls(text),
    )


def store_items(store: NewsStore, items: list[RawNewsItem]) -> int:
    """``items`` 저장 후 신규 건수 반환(중복 제외)."""
    return sum(1 for it in items if store.insert_raw(it))


async def collect_channel(store: NewsStore, client: Any, channel: str) -> int:
    """한 채널 수집: 첫 폴링이면 최신 시드, 아니면 catch-up. 신규 건수 반환·커서 전진."""
    min_id = store.get_cursor(channel)
    if min_id == 0:
        messages: list[Any] = await client.get_messages(channel, limit=SEED_LIMIT)
    else:
        messages = await client.get_messages(
            channel, min_id=min_id, reverse=True, limit=BATCH_LIMIT
        )
    items: list[RawNewsItem] = []
    max_id = min_id
    for msg in messages:
        item = message_to_item(channel, msg)
        if item is not None:
            items.append(item)
        if int(msg.id) > max_id:
            max_id = int(msg.id)
    new = store_items(store, items)
    if max_id > min_id:
        store.set_cursor(channel, max_id)
    return new


async def collect_once(
    store: NewsStore, settings: Settings, *, client: Any | None = None
) -> dict[str, int]:
    """전체 채널 폴링. 채널→신규건수. ``client`` 미주입이면 세션·키로 telethon 접속(fail-open)."""
    if client is not None:
        return {ch: await collect_channel(store, client, ch) for ch in settings.news_channel_list}

    if not settings.app_api_id or not settings.app_api_hash:
        _log.info("news: APP_API_ID/HASH 없음 → 수집 스킵(fail-open)")
        return {}
    session_file = Path(f"{settings.telethon_session_path}.session")
    if not session_file.exists():
        _log.info("news: telethon 세션 없음(%s) → 수집 스킵", session_file)
        return {}

    from telethon import TelegramClient

    tg: Any = TelegramClient(
        str(settings.telethon_session_path), int(settings.app_api_id), settings.app_api_hash
    )
    await tg.connect()
    try:
        if not await tg.is_user_authorized():
            _log.warning("news: 세션 미인증 → scripts/telegram_login.py 재실행 필요")
            return {}
        result: dict[str, int] = {}
        for ch in settings.news_channel_list:
            try:
                result[ch] = await collect_channel(store, tg, ch)
            except Exception as exc:
                _log.warning("news: 채널 %s 수집 실패: %s", ch, exc)
                result[ch] = 0
        return result
    finally:
        await tg.disconnect()


__all__ = ["collect_channel", "collect_once", "extract_urls", "message_to_item", "store_items"]
