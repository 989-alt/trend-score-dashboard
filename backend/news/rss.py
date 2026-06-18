"""RSS 수집기 — 국내외 증시·경제 피드 → RawNewsItem(news_raw 합류). 피드별 fail-open·₩0.

httpx 로 받아 feedparser 로 파싱. 텔레그램과 같은 ``news_raw`` 에 적재돼 동일 이슈
클러스터링을 탄다(``source="rss"``, ``channel=피드명``, ``msg_id=crc32(기사url)``). LLM 미사용.
"""

from __future__ import annotations

import calendar
import logging
import zlib
from datetime import UTC, datetime
from pathlib import Path

import feedparser
import httpx
import yaml

from backend.config import Settings
from backend.news.models import RawNewsItem
from backend.news.store import NewsStore

_log = logging.getLogger(__name__)
_RSS_TIMEOUT = 10.0
_UA = "Mozilla/5.0 (compatible; trend-score-dashboard/0.1; +https://board.s-edu.ai.kr)"
_PER_FEED_LIMIT = 50
#: 피드 1개 수집 실패로 흡수할 예외(광범위 except 회피).
_FEED_ERRORS: tuple[type[Exception], ...] = (httpx.HTTPError, ValueError, OSError, AttributeError)


def load_feeds(path: Path) -> list[tuple[str, str]]:
    """``news_sources.yml`` → ``[(name, url)]``. 없거나 항목이 깨지면 그 항목만 건너뛴다."""
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = raw.get("feeds", []) if isinstance(raw, dict) else []
    out: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if name and url:
            out.append((name, url))
    return out


def _url_id(url: str) -> int:
    """기사 URL → 32비트 안정 정수(news_raw 중복제거 PK용)."""
    return zlib.crc32(url.encode("utf-8"))


def _entry_datetime(entry: object, now: datetime) -> datetime:
    """RSS 엔트리 발행시각(UTC tz-aware). 없으면 ``now``."""
    st = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if st is None:
        return now
    return datetime.fromtimestamp(calendar.timegm(st), tz=UTC)


def fetch_feed(
    name: str, url: str, now: datetime, *, limit: int = _PER_FEED_LIMIT
) -> list[RawNewsItem]:
    """한 RSS 피드 수집 → ``RawNewsItem`` 목록(제목 빈 항목 skip). httpx 타임아웃 + feedparser."""
    resp = httpx.get(url, timeout=_RSS_TIMEOUT, follow_redirects=True, headers={"User-Agent": _UA})
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    items: list[RawNewsItem] = []
    for entry in parsed.entries[:limit]:
        title = str(getattr(entry, "title", "")).strip()
        if not title:
            continue
        link = str(getattr(entry, "link", "")).strip()
        items.append(
            RawNewsItem(
                source="rss",
                channel=name,
                msg_id=_url_id(link or title),
                ts_utc=_entry_datetime(entry, now),
                text=title,
                urls=(link,) if link else (),
            )
        )
    return items


def collect_rss_once(
    store: NewsStore, settings: Settings, *, now: datetime | None = None
) -> dict[str, int]:
    """모든 RSS 피드 수집 → ``news_raw`` 적재. 피드→신규건수(피드별 fail-open). 키 불필요."""
    at = now or datetime.now(tz=UTC)
    result: dict[str, int] = {}
    for name, url in load_feeds(settings.news_sources_path):
        try:
            items = fetch_feed(name, url, at)
            result[name] = sum(1 for it in items if store.insert_raw(it))
        except _FEED_ERRORS as exc:
            _log.warning("rss: 피드 %s 수집 실패: %s", name, exc)
            result[name] = 0
    return result


__all__ = ["collect_rss_once", "fetch_feed", "load_feeds"]
