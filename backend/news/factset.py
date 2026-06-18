"""FactSet Insight 수집 — 공식 RSS 피드(rss.xml) 파싱·저장(fail-open).

실측(2026-06): ``insight.factset.com/rss.xml`` 가 표준 RSS 2.0(title·link·pubDate·description)
을 제공한다. HTML 스크래핑보다 견고하고 1회 요청으로 끝나므로 RSS 를 1차 소스로 쓴다.
description 은 HTML 이라 태그를 제거해 짧은 발췌로 저장한다. 실패는 fail-open(0).
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC
from email.utils import parsedate_to_datetime

from backend.config import Settings
from backend.news.models import FactsetArticle
from backend.news.store import NewsStore

_log = logging.getLogger(__name__)
_FEED_URL = "https://insight.factset.com/rss.xml"
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
#: 발췌 최대 길이.
_EXCERPT_MAX = 280


def _clean_excerpt(html: str) -> str:
    """description HTML → 태그 제거·공백 정리한 짧은 발췌."""
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", html)).strip()
    return text[:_EXCERPT_MAX]


def parse_feed(xml: str) -> list[FactsetArticle]:
    """RSS XML → FactsetArticle 리스트. title·link·pubDate 없는 item 은 건너뛴다."""
    try:
        root = ET.fromstring(xml)  # 신뢰 소스(FactSet RSS); stdlib ET 는 외부엔티티 미해석
    except ET.ParseError:
        return []
    out: list[FactsetArticle] = []
    for item in root.iter("item"):
        title = item.findtext("title")
        link = item.findtext("link")
        pub = item.findtext("pubDate")
        if not title or not link or not pub:
            continue
        try:
            dt = parsedate_to_datetime(pub)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        out.append(
            FactsetArticle(
                url=link.strip(),
                title=title.strip(),
                published_at=dt.astimezone(UTC),
                excerpt=_clean_excerpt(item.findtext("description") or ""),
            )
        )
    return out


def _httpx_get(url: str) -> str:
    """기본 HTTP GET(httpx). 데스크톱 UA."""
    import httpx

    resp = httpx.get(
        url,
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (news-archive)"},
    )
    resp.raise_for_status()
    return resp.text


def collect_factset(
    store: NewsStore, settings: Settings, *, http_get: Callable[[str], str] | None = None
) -> int:
    """RSS fetch→파싱→저장. 신규 건수. 예외는 fail-open(0)."""
    get = http_get or _httpx_get
    try:
        articles = parse_feed(get(_FEED_URL))
    except Exception as exc:
        _log.warning("news: FactSet RSS 수집 실패: %s", exc)
        return 0
    return sum(1 for a in articles if store.upsert_factset(a))


__all__ = ["collect_factset", "parse_feed"]
