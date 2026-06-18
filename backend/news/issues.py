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
class StockMeta:
    """종목명 → 라이브 스냅샷 메타(이슈 점수연결용)."""

    ticker: str
    score: Decimal
    grade: str
    market: str


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
    # 통합(언급 급등 + 점수연결) — 표시용 추가 신호. urgency 공식·정렬은 불변.
    spike: Decimal = Decimal("0")  # 최근/(베이스라인+1) 언급 급등 배수(baseline 줄 때만)
    ticker: str | None = None  # 종목 이슈면 스냅샷 종목코드
    score: Decimal | None = None  # 라이브 추세점수(있으면)
    grade: str | None = None  # 등급(있으면)
    market: str | None = None  # "KR" | "US"
    headline: str = ""  # 정리된 대표 한 줄(가독성)


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


def _entity_key(clean: str, stock_names: set[str], severity: dict[str, Decimal]) -> str | None:
    """클러스터 키(종목명 우선, 없으면 심각도어). build_issues 본 로직과 동일 규칙."""
    stock = _match_stock(clean, stock_names)
    if stock is not None:
        return stock
    sev_term, _ = _match_severity(clean, severity)
    return sev_term or None


def _baseline_counts(
    items: list[RawNewsItem], stock_names: set[str], severity: dict[str, Decimal]
) -> dict[str, int]:
    """베이스라인 구간의 엔티티별 언급수(spike 분모)."""
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        key = _entity_key(clean_text(item.text), stock_names, severity)
        if key is not None:
            counts[key] += 1
    return counts


def _representative(items: tuple[RawNewsItem, ...]) -> str:
    """이슈 대표 한 줄 — 구성 메시지를 정리(clean_text)해 가장 정보량 큰(긴) 1줄. 140자 컷."""
    best = ""
    for it in items:
        line = clean_text(it.text)
        if len(line) > len(best):
            best = line
    return best[:140]


def build_issues(
    items: list[RawNewsItem],
    stock_names: set[str],
    severity: dict[str, Decimal],
    *,
    now: datetime,
    top_n: int = 10,
    baseline_items: list[RawNewsItem] | None = None,
    stock_meta: dict[str, StockMeta] | None = None,
) -> list[Issue]:
    """엔티티(종목명 우선, 없으면 심각도어)로 클러스터해 긴급도순 Top N 이슈를 만든다.

    ``baseline_items``(있으면): 동일 길이 과거 구간 언급수로 spike(언급 급등 = 최근/(과거+1))를
    매긴다 — **urgency 공식·정렬은 불변**, spike 는 표시 신호일 뿐. ``stock_meta``(있으면):
    종목 키 이슈에 라이브 스냅샷 점수/등급/코드를 부착(클릭→상세 연결용).
    """
    baseline = (
        _baseline_counts(baseline_items, stock_names, severity)
        if baseline_items is not None
        else {}
    )
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
        if baseline_items is not None:
            spike = (Decimal(count) / (Decimal(baseline.get(key, 0)) + Decimal(1))).quantize(
                Decimal("0.01")
            )
        else:
            spike = Decimal("0")
        meta = stock_meta.get(key) if stock_meta else None
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
                spike=spike,
                ticker=meta.ticker if meta else None,
                score=meta.score if meta else None,
                grade=meta.grade if meta else None,
                market=meta.market if meta else None,
                headline=_representative(member_items),
            )
        )

    issues.sort(key=lambda x: (x.urgency, x.last_ts, x.key), reverse=True)
    return issues[:top_n]


def group_by_layer(issues: list[Issue], top_n: int) -> dict[str, list[Issue]]:
    """이슈를 market 기준 3 레이어로 그룹핑(각 urgency 순 Top N). 입력은 이미 정렬됨.

    국내(KR) / 미국(US) / 종합(거시·심각도 키 = market None). 사용자 확정 구조.
    """
    layers: dict[str, list[Issue]] = {"domestic": [], "us": [], "macro": []}
    for issue in issues:
        if issue.market == "KR":
            layers["domestic"].append(issue)
        elif issue.market == "US":
            layers["us"].append(issue)
        else:
            layers["macro"].append(issue)
    return {name: bucket[:top_n] for name, bucket in layers.items()}


__all__ = [
    "Issue",
    "StockMeta",
    "build_issues",
    "clean_text",
    "group_by_layer",
    "load_severity",
]
