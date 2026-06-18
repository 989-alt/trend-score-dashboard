"""언급 급등(spike) 랭킹 — 최근 윈도 vs 베이스라인 평균(Decimal 전면).

각 엔티티(종목/테마)에 대해:
- ``recent`` = 최근 ``news_recent_hours`` 시간 내 언급수(분자).
- ``baseline_equiv`` = 베이스라인 구간(``baseline_days`` 중 최근 윈도를 뺀 과거)의
  언급수를 최근-윈도 길이로 환산한 기대 언급수(분모 기준).
- ``spike`` = recent / (baseline_equiv + 1)  (라플라스 +1 — 0분모 방지·신규 급등 우대).
정렬: spike 내림차순 → 동률이면 recent 언급수. ``min_mentions`` 미만은 노이즈로 제외.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from backend.config import Settings
from backend.news.collect import NewsItem
from backend.news.extract import Lexicon, Mention, extract_mentions
from backend.schemas import IssueEntry, IssueHeadline, IssueKind, ScoreEntry

#: 헤드라인 샘플 최대 노출 수.
_MAX_HEADLINES = 3


def rank_issues(
    window_items: list[NewsItem],
    lexicon: Lexicon,
    snapshot_by_ticker: dict[str, ScoreEntry],
    settings: Settings,
    now: datetime,
) -> tuple[list[IssueEntry], int]:
    """``window_items`` (베이스라인 구간 전체)를 랭킹해 (이슈목록, 최근항목수) 반환.

    종목 언급은 ``snapshot_by_ticker`` 에 있으면 점수·등급·시장을 채워 상세로 잇는다.
    """
    recent_cut = now - timedelta(hours=settings.news_recent_hours)
    recent_span = Decimal(settings.news_recent_hours)
    baseline_span = Decimal(settings.news_baseline_days * 24) - recent_span
    if baseline_span <= 0:  # 설정상 베이스라인이 최근 윈도보다 짧으면 동일 길이로 폴백.
        baseline_span = recent_span

    recent_items: dict[tuple[IssueKind, str], list[NewsItem]] = {}
    baseline_n: dict[tuple[IssueKind, str], int] = {}
    meta: dict[tuple[IssueKind, str], Mention] = {}
    items_recent = 0

    for item in window_items:
        is_recent = item.published_at >= recent_cut
        if is_recent:
            items_recent += 1
        for mention in extract_mentions(item.title, lexicon):
            key = (mention.kind, mention.key)
            meta.setdefault(key, mention)
            if is_recent:
                recent_items.setdefault(key, []).append(item)
            else:
                baseline_n[key] = baseline_n.get(key, 0) + 1

    entries: list[IssueEntry] = []
    for key, mention in meta.items():
        recent_list = recent_items.get(key, [])
        recent_count = len(recent_list)
        if recent_count < settings.news_min_mentions:
            continue
        base_equiv = Decimal(baseline_n.get(key, 0)) * recent_span / baseline_span
        spike = (Decimal(recent_count) / (base_equiv + Decimal(1))).quantize(Decimal("0.01"))

        recent_sorted = sorted(recent_list, key=lambda it: it.published_at, reverse=True)
        headlines = [
            IssueHeadline(
                title=it.title, url=it.url, source=it.source_name, published_at=it.published_at
            )
            for it in recent_sorted[:_MAX_HEADLINES]
        ]
        sources = list(dict.fromkeys(it.source_name for it in recent_sorted))

        score: Decimal | None = None
        grade = None
        market = mention.market
        if mention.kind == "ticker":
            entry = snapshot_by_ticker.get(mention.key)
            if entry is not None:
                score = entry.score
                grade = entry.grade
                market = entry.market

        entries.append(
            IssueEntry(
                kind=mention.kind,
                key=mention.key,
                name=mention.name,
                market=market,
                mention_count=recent_count,
                baseline_count=int(base_equiv.quantize(Decimal("1"))),
                spike=spike,
                score=score,
                grade=grade,
                headlines=headlines,
                sources=sources,
            )
        )

    entries.sort(key=lambda e: (e.spike, e.mention_count), reverse=True)
    return entries[: settings.news_max_issues], items_recent


__all__ = ["rank_issues"]
