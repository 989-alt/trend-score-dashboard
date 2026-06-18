"""수집 — RSS(feedparser) + 텔레그램(Telethon MTProto). 소스별 fail-open.

원칙:
- 수집 시 **LLM 미사용**(₩0·결정론). 원문은 그대로 ``NewsItem`` 으로 적재(B 전향검증 겸용).
- RSS 는 키 불필요·즉시 동작. 텔레그램은 ``TELEGRAM_API_ID/HASH`` + 세션이 있을 때만
  동작하고, 없거나 미인증이면 **조용히 건너뛴다**(RSS 만, fail-open).
- 시각은 전부 tz-aware. RSS/텔레그램 모두 UTC 로 받아 둔다(저장 측이 UTC 로 정규화).
- 소스 1개의 실패가 전체 수집을 막지 않도록 호출 측에서 격리(``collect_all``).
"""

from __future__ import annotations

import asyncio
import calendar
from dataclasses import dataclass
from datetime import UTC, datetime

import feedparser
import httpx

from backend.config import Settings
from backend.news.sources import NewsSource

#: RSS 요청 타임아웃(초).
_RSS_TIMEOUT = 10.0
#: 평범한 브라우저 UA — 일부 RSS 서버의 봇 차단 회피.
_UA = "Mozilla/5.0 (compatible; trend-score-dashboard/0.1; +https://board.s-edu.ai.kr)"
#: 소스 1개 수집 실패로 흡수할 예외(광범위 except 회피).
_RSS_ERRORS: tuple[type[Exception], ...] = (httpx.HTTPError, ValueError, OSError, AttributeError)


@dataclass(frozen=True)
class NewsItem:
    """수집된 기사/메시지 1건 — 원시 아카이브의 단위.

    ``external_id`` 는 같은 소스 내 중복제거 키(RSS guid/link, 텔레그램 메시지 id).
    ``title`` 은 헤드라인(텔레그램은 메시지 첫 줄). 시각은 tz-aware.
    """

    source_name: str
    source_kind: str  # "rss" | "telegram"
    external_id: str
    title: str
    url: str | None
    published_at: datetime
    collected_at: datetime


def _entry_datetime(entry: object, now: datetime) -> datetime:
    """RSS 엔트리의 발행시각(UTC tz-aware). 없으면 ``now`` 로 폴백.

    feedparser 의 ``*_parsed`` 는 UTC ``time.struct_time`` 이므로 ``calendar.timegm``
    으로 epoch 변환 후 UTC datetime 으로 만든다.
    """
    st = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if st is None:
        return now
    return datetime.fromtimestamp(calendar.timegm(st), tz=UTC)


def collect_rss(source: NewsSource, settings: Settings, now: datetime) -> list[NewsItem]:
    """``source`` (RSS) 의 최신 항목을 ``per_source_limit`` 까지 수집.

    httpx 로 타임아웃을 걸어 받은 본문을 feedparser 로 파싱한다(feedparser 자체의
    무타임아웃 urllib 호출 회피). 제목이 빈 항목은 건너뛴다.
    """
    resp = httpx.get(
        source.url, timeout=_RSS_TIMEOUT, follow_redirects=True, headers={"User-Agent": _UA}
    )
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    items: list[NewsItem] = []
    for entry in parsed.entries[: settings.news_per_source_limit]:
        title = str(getattr(entry, "title", "")).strip()
        if not title:
            continue
        link = str(getattr(entry, "link", "")).strip() or None
        external_id = str(getattr(entry, "id", "") or getattr(entry, "guid", "") or link or title)
        items.append(
            NewsItem(
                source_name=source.name,
                source_kind="rss",
                external_id=external_id,
                title=title,
                url=link,
                published_at=_entry_datetime(entry, now),
                collected_at=now,
            )
        )
    return items


async def _collect_telegram(
    channels: list[NewsSource], settings: Settings, now: datetime
) -> tuple[list[NewsItem], int, int]:
    """텔레그램 채널들에서 최근 메시지를 수집(MTProto).

    세션이 미인증이면 ``([], 0, 0)`` 으로 **건너뛴다**(최초 1회 대화형 로그인 필요 —
    실패가 아니라 미설정). 인증돼 있으면 채널별로 격리 수집하고 (성공수, 실패수)를 함께
    반환한다. 메시지 본문 첫 줄을 헤드라인으로 쓴다.
    """
    from telethon import TelegramClient
    from telethon.errors import RPCError

    items: list[NewsItem] = []
    ok = 0
    failed = 0
    client = TelegramClient(
        str(settings.telegram_session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return [], 0, 0
        for ch in channels:
            try:
                async for msg in client.iter_messages(ch.url, limit=settings.news_per_source_limit):
                    text = str(msg.message or "").strip()
                    if not text:
                        continue
                    title = text.splitlines()[0][:300]
                    published: datetime = msg.date if msg.date is not None else now
                    items.append(
                        NewsItem(
                            source_name=ch.name,
                            source_kind="telegram",
                            external_id=str(msg.id),
                            title=title,
                            url=f"https://t.me/{ch.url}/{msg.id}",
                            published_at=published,
                            collected_at=now,
                        )
                    )
                ok += 1
            except (RPCError, ValueError, OSError):
                failed += 1
    finally:
        await client.disconnect()
    return items, ok, failed


def collect_all(
    sources: list[NewsSource], settings: Settings, now: datetime
) -> tuple[list[NewsItem], int, int]:
    """모든 소스를 수집해 (항목들, 성공소스수, 실패소스수)를 반환.

    RSS 는 동기로 소스별 격리 수집. 텔레그램은 자격증명이 있을 때만 새 이벤트루프
    (``asyncio.run``, 워커 스레드)에서 일괄 수집한다 — 없으면 통째로 건너뛴다(fail-open).
    """
    rss = [s for s in sources if s.kind == "rss"]
    telegram = [s for s in sources if s.kind == "telegram"]

    items: list[NewsItem] = []
    ok = 0
    failed = 0

    for source in rss:
        try:
            items.extend(collect_rss(source, settings, now))
            ok += 1
        except _RSS_ERRORS:
            failed += 1

    if telegram and settings.telegram_api_id and settings.telegram_api_hash:
        tg_items, tg_ok, tg_failed = asyncio.run(_collect_telegram(telegram, settings, now))
        items.extend(tg_items)
        ok += tg_ok
        failed += tg_failed

    return items, ok, failed


__all__ = ["NewsItem", "collect_all", "collect_rss"]
