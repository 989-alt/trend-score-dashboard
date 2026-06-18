"""뉴스 영속 — 원시 아카이브(``news_items``) + 이슈 스냅샷(``issues_snapshot``).

**별도 DB 파일**(``data/news.db``)에 둔다 — ``dashboard.db`` 캐시 초기화
(``rm data/dashboard.db*``)에도 아카이브가 살아남아, 향후 뉴스 전향(forward) 검증의
원시 데이터로 재사용된다(설계 §8-2). 시각은 저장 시 **UTC ISO**(마이크로초 제거)로
정규화해 문자열 사전순 = 시간순이 되게 한다(범위 질의 단순화).

스레드 안전: ``check_same_thread=False`` + ``Lock`` (스케줄러 잡·요청 핸들러 공유).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from backend.news.collect import NewsItem
from backend.schemas import IssuesResponse

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_items (
    source_name  TEXT NOT NULL,
    source_kind  TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    title        TEXT NOT NULL,
    url          TEXT,
    published_at TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    PRIMARY KEY (source_name, external_id)
);
CREATE INDEX IF NOT EXISTS idx_news_published ON news_items (published_at);
CREATE TABLE IF NOT EXISTS issues_snapshot (
    id   INTEGER PRIMARY KEY CHECK (id = 1),
    json TEXT NOT NULL
);
"""


def _utc_iso(dt: datetime) -> str:
    """tz-aware datetime → UTC ISO(마이크로초 제거). 사전순 = 시간순."""
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


class NewsStore:
    """뉴스 원시 아카이브 + 이슈 스냅샷(시장 무관 최신 1개) 영속."""

    def __init__(self, db_path: Path) -> None:
        """``db_path`` 에 연결하고 테이블을 생성한다(없으면). 부모 dir 자동 생성."""
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add_items(self, items: list[NewsItem]) -> int:
        """``items`` 를 적재(중복은 무시)하고 **새로 적재된 건수**를 반환.

        중복제거 키는 ``(source_name, external_id)``. ``INSERT OR IGNORE`` 로 이미
        있는 항목은 건너뛰고, ``total_changes`` 차이로 신규 적재 수를 센다.
        """
        if not items:
            return 0
        with self._lock:
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT OR IGNORE INTO news_items "
                "(source_name, source_kind, external_id, title, url, published_at, collected_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        it.source_name,
                        it.source_kind,
                        it.external_id,
                        it.title,
                        it.url,
                        _utc_iso(it.published_at),
                        _utc_iso(it.collected_at),
                    )
                    for it in items
                ],
            )
            self._conn.commit()
            return self._conn.total_changes - before

    def recent(self, since: datetime) -> list[NewsItem]:
        """``since`` 이후(발행시각 기준) 항목을 최신순으로 반환."""
        cutoff = _utc_iso(since)
        with self._lock:
            rows = self._conn.execute(
                "SELECT source_name, source_kind, external_id, title, url, "
                "published_at, collected_at FROM news_items "
                "WHERE published_at >= ? ORDER BY published_at DESC",
                (cutoff,),
            ).fetchall()
        return [
            NewsItem(
                source_name=r[0],
                source_kind=r[1],
                external_id=r[2],
                title=r[3],
                url=r[4],
                published_at=datetime.fromisoformat(r[5]),
                collected_at=datetime.fromisoformat(r[6]),
            )
            for r in rows
        ]

    def save_issues(self, resp: IssuesResponse) -> None:
        """이슈 랭킹 스냅샷(최신 1개)을 저장(upsert)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO issues_snapshot (id, json) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET json = excluded.json",
                (resp.model_dump_json(),),
            )
            self._conn.commit()

    def load_issues(self) -> IssuesResponse | None:
        """최신 이슈 랭킹 스냅샷. 없으면 ``None``."""
        with self._lock:
            row = self._conn.execute("SELECT json FROM issues_snapshot WHERE id = 1").fetchone()
        return IssuesResponse.model_validate_json(row[0]) if row is not None else None


__all__ = ["NewsStore"]
