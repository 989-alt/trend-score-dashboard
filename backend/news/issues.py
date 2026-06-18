"""이슈 클러스터·긴급도 휴리스틱 (₩0·결정론·순수). 시황 탭 사이드바용.

매매 무영향(검증 전). 종목명(라이브 스냅샷 세트)·심각도 사전으로 메시지를 엔티티에
묶고, 교차채널·건수·최신성·심각도로 긴급도(Decimal)를 매겨 Top N 이슈를 만든다.
긴급도는 *표시 정렬용 고정 가중*이며 수익률 튜닝이 아니다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import yaml

from backend.news.models import RawNewsItem

# 긴급도 가중(표시 정렬용 고정값).
_W_CHAN = Decimal("2.0")  # 교차채널 1개당
_W_VEL = Decimal("1.0")  # 건수 1건당(상한 5)
_W_REC = Decimal("1.5")  # 최신성(48h 선형감쇠)
_W_SEV = Decimal("2.0")  # 최대 심각도 가중
_RECENCY_WINDOW_H = Decimal("48")
_VEL_CAP = 5

_URL_RE = re.compile(r"https?://\S+")
_HASHTAG_RE = re.compile(r"#\S+")
_WS_RE = re.compile(r"\s+")
#: 채널 보일러플레이트 마커(실데이터 관찰).
_MARKERS = (
    "📝 핵심적 본문 요약",
    "핵심적 본문 요약",
    "📜원문보기📜",
    "원문보기",
    "✨(In)sight",
    "[✨ 리서치]",
)
#: 단독 노이즈 이모지/표지.
_NOISE_CHARS = "✅📝📜✨📈📉🔔🚨"


@dataclass(frozen=True)
class Issue:
    """이슈 클러스터 1건(엔티티 단위)."""

    key: str
    title: str
    urgency: Decimal
    channels: tuple[str, ...]
    severity: Decimal
    count: int
    last_ts: datetime
    items: tuple[RawNewsItem, ...]


def load_severity(path: Path) -> dict[str, Decimal]:
    """심각도 사전 yml → {키워드: Decimal 가중}. 파일 없거나 비면 빈 dict."""
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {str(k): Decimal(str(v)) for k, v in data.items()}


def clean_text(text: str) -> str:
    """URL·해시태그·보일러플레이트 마커·노이즈 이모지 제거 후 공백 정리."""
    out = _URL_RE.sub(" ", text)
    out = _HASHTAG_RE.sub(" ", out)
    for marker in _MARKERS:
        out = out.replace(marker, " ")
    out = out.translate({ord(c): " " for c in _NOISE_CHARS})
    return _WS_RE.sub(" ", out).strip()


def _match_stock(text: str, names: set[str]) -> str | None:
    """본문에 등장하는 종목명 중 최장(가장 구체적)을 반환. 없으면 None."""
    best: str | None = None
    for name in names:
        if len(name) >= 2 and name in text and (best is None or len(name) > len(best)):
            best = name
    return best


def _match_severity(text: str, severity: dict[str, Decimal]) -> tuple[str, Decimal]:
    """본문에 등장하는 심각도어 중 최고가중을 (term, weight)로. 없으면 ("", 0)."""
    best_term = ""
    best_w = Decimal(0)
    for term, weight in severity.items():
        if term in text and weight > best_w:
            best_term, best_w = term, weight
    return best_term, best_w


def build_issues(
    items: list[RawNewsItem],
    stock_names: set[str],
    severity: dict[str, Decimal],
    *,
    now: datetime,
    top_n: int = 10,
) -> list[Issue]:
    """엔티티(종목명 우선, 없으면 심각도어)로 클러스터해 긴급도순 Top N 이슈를 만든다."""
    groups: dict[str, list[tuple[RawNewsItem, Decimal]]] = defaultdict(list)
    for item in items:
        clean = clean_text(item.text)
        stock = _match_stock(clean, stock_names)
        sev_term, sev_w = _match_severity(clean, severity)
        if stock is not None:
            key = stock
        elif sev_term:
            key = sev_term
        else:
            continue
        groups[key].append((item, sev_w))

    issues: list[Issue] = []
    for key, members in groups.items():
        member_items = tuple(it for it, _ in members)
        channels = tuple(sorted({it.channel for it in member_items}))
        count = len(members)
        max_sev = max((w for _, w in members), default=Decimal(0))
        last_ts = max(it.ts_utc for it in member_items)
        secs = max(int((now - last_ts).total_seconds()), 0)
        hours = Decimal(secs) / Decimal(3600)
        recency = max(Decimal(0), Decimal(1) - hours / _RECENCY_WINDOW_H)
        urgency = (
            _W_CHAN * len(channels)
            + _W_VEL * min(count, _VEL_CAP)
            + _W_REC * recency
            + _W_SEV * max_sev
        ).quantize(Decimal("0.01"))
        issues.append(
            Issue(
                key=key,
                title=key,
                urgency=urgency,
                channels=channels,
                severity=max_sev,
                count=count,
                last_ts=last_ts,
                items=member_items,
            )
        )

    issues.sort(key=lambda x: (x.urgency, x.last_ts, x.key), reverse=True)
    return issues[:top_n]


__all__ = ["Issue", "build_issues", "clean_text", "load_severity"]
