"""뉴스 수집 데이터 모델 (순수·불변). 저장/표시 계약의 단일 출처."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class RawNewsItem:
    """수집된 원문 1건(텔레그램 메시지 등). 가공 전 원시 데이터."""

    source: str
    channel: str
    msg_id: int
    ts_utc: datetime
    text: str
    urls: tuple[str, ...]

    @property
    def ts_kst(self) -> datetime:
        """KST 표시용 시각."""
        return self.ts_utc.astimezone(_KST)

    @property
    def dedup_hash(self) -> str:
        """교차채널 동일뉴스 탐지용 해시(채널·id·본문). SHA-256 hex."""
        raw = f"{self.channel}|{self.msg_id}|{self.text}".encode()
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class FactsetArticle:
    """FactSet Insight 글 1건."""

    url: str
    title: str
    published_at: datetime
    excerpt: str


@dataclass(frozen=True)
class WeeklySummary:
    """Gemini 주간 한국어 요약 1건."""

    week_start: date
    kr_markdown: str
    model: str
    generated_at: datetime
