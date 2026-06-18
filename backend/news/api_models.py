"""시황 탭 API 응답 모델 (pydantic). 라이브 스코어러 schemas.py 와 분리(무수정).

Decimal 은 pydantic JSON 직렬화에서 문자열로 나가며, 프런트 파서가 number|string 을 처리한다.
모든 응답에 면책(disclaimer)을 포함한다 — 뉴스는 검증되지 않았고 점수에 반영되지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class NewsMessage(BaseModel):
    """이슈 상세용 원문 메시지 1건."""

    channel: str
    ts_kst: datetime
    text: str
    urls: list[str]


class NewsIssue(BaseModel):
    """긴급도순 이슈 1건(엔티티 클러스터) + 구성 원문."""

    key: str
    title: str
    urgency: Decimal
    channels: list[str]
    severity: Decimal
    count: int
    last_ts: datetime
    messages: list[NewsMessage]
    # 통합(언급 급등 + 점수연결) — 표시용. 기본값으로 기존 계약 호환.
    spike: Decimal = Decimal("0")
    ticker: str | None = None
    score: Decimal | None = None
    grade: str | None = None
    market: str | None = None


class NewsIssuesResponse(BaseModel):
    """``GET /api/news/issues`` 응답 — Top N 이슈 + 면책."""

    generated_at: datetime
    disclaimer: str
    issues: list[NewsIssue]


class WeeklyResponse(BaseModel):
    """``GET /api/news/weekly`` 응답 — 최신 주간요약(없으면 None) + 면책."""

    week_start: date | None
    kr_markdown: str | None
    generated_at: datetime | None
    disclaimer: str


__all__ = ["NewsIssue", "NewsIssuesResponse", "NewsMessage", "WeeklyResponse"]
