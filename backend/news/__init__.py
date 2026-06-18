"""실시간 이슈 랭킹 — RSS + 텔레그램(MTProto) 수집 → 종목·테마 언급 급등 랭킹.

LLM 미사용(₩0·결정론). 원시 아카이브는 별도 ``data/news.db`` 에 적재되어 향후 뉴스
전향(forward) 검증의 데이터로 재사용된다(라이브 스코어러 무수정).
"""

from __future__ import annotations

from backend.news.collect import NewsItem, collect_all, collect_rss
from backend.news.extract import Lexicon, Mention, build_lexicon, extract_mentions
from backend.news.rank import rank_issues
from backend.news.service import empty_issues, refresh_issues
from backend.news.sources import NewsSource, load_sources
from backend.news.store import NewsStore

__all__ = [
    "Lexicon",
    "Mention",
    "NewsItem",
    "NewsSource",
    "NewsStore",
    "build_lexicon",
    "collect_all",
    "collect_rss",
    "empty_issues",
    "extract_mentions",
    "load_sources",
    "rank_issues",
    "refresh_issues",
]
