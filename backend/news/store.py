"""뉴스 아카이브 영속 (SQLite). 기존 backend/store.py 패턴(연결+Lock, 멱등 스키마).

테이블: news_raw(원문)·news_state(폴링 커서)·news_factset·weekly_summary.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, date, datetime
from pathlib import Path

from backend.news.models import FactsetArticle, RawNewsItem, WeeklySummary

_NEWS_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_raw (
    source     TEXT NOT NULL,
    channel    TEXT NOT NULL,
    msg_id     INTEGER NOT NULL,
    ts_utc     TEXT NOT NULL,
    text       TEXT NOT NULL,
    urls       TEXT NOT NULL,
    dedup_hash TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, channel, msg_id)
);
CREATE INDEX IF NOT EXISTS idx_news_raw_ts ON news_raw (ts_utc);

CREATE TABLE IF NOT EXISTS news_state (
    channel      TEXT PRIMARY KEY,
    last_seen_id INTEGER NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS news_factset (
    url          TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    published_at TEXT NOT NULL,
    excerpt      TEXT NOT NULL,
    fetched_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weekly_summary (
    week_start   TEXT PRIMARY KEY,
    kr_markdown  TEXT NOT NULL,
    model        TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
"""


def _now_iso() -> str:
    """현재 UTC ISO 문자열(저장용)."""
    return datetime.now(tz=UTC).isoformat()


class NewsStore:
    """뉴스 아카이브 SQLite 계층. 스레드 안전(check_same_thread=False + Lock)."""

    def __init__(self, db_path: Path) -> None:
        """``db_path`` 에 연결하고 4테이블을 생성한다(없으면)."""
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.executescript(_NEWS_SCHEMA)
        self._conn.commit()

    # ── 원문(raw) ──────────────────────────────────────────────────────
    def insert_raw(self, item: RawNewsItem) -> bool:
        """원문 1건 저장. 신규면 True, (source,channel,msg_id) 중복이면 False."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO news_raw "
                "(source, channel, msg_id, ts_utc, text, urls, dedup_hash, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.source,
                    item.channel,
                    item.msg_id,
                    item.ts_utc.isoformat(),
                    item.text,
                    "\n".join(item.urls),
                    item.dedup_hash,
                    _now_iso(),
                ),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def recent_raw(self, since: datetime) -> list[RawNewsItem]:
        """``since``(포함) 이후 원문을 시각 오름차순으로."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, channel, msg_id, ts_utc, text, urls FROM news_raw "
                "WHERE ts_utc >= ? ORDER BY ts_utc ASC",
                (since.isoformat(),),
            ).fetchall()
        out: list[RawNewsItem] = []
        for source, channel, msg_id, ts_utc, text, urls in rows:
            out.append(
                RawNewsItem(
                    source=source,
                    channel=channel,
                    msg_id=int(msg_id),
                    ts_utc=datetime.fromisoformat(ts_utc),
                    text=text,
                    urls=tuple(u for u in urls.split("\n") if u),
                )
            )
        return out

    # ── 폴링 커서 ──────────────────────────────────────────────────────
    def get_cursor(self, channel: str) -> int:
        """``channel`` 의 마지막 본 msg_id. 없으면 0."""
        with self._lock:
            row = self._conn.execute(
                "SELECT last_seen_id FROM news_state WHERE channel = ?", (channel,)
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def set_cursor(self, channel: str, last_id: int) -> None:
        """``channel`` 커서를 ``last_id`` 로 갱신(upsert)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO news_state (channel, last_seen_id, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(channel) DO UPDATE SET "
                "last_seen_id = excluded.last_seen_id, updated_at = excluded.updated_at",
                (channel, last_id, _now_iso()),
            )
            self._conn.commit()

    # ── FactSet ────────────────────────────────────────────────────────
    def upsert_factset(self, art: FactsetArticle) -> bool:
        """FactSet 글 저장(url upsert). 신규면 True, 기존 갱신이면 False."""
        with self._lock:
            existed = (
                self._conn.execute(
                    "SELECT 1 FROM news_factset WHERE url = ?", (art.url,)
                ).fetchone()
                is not None
            )
            self._conn.execute(
                "INSERT INTO news_factset (url, title, published_at, excerpt, fetched_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(url) DO UPDATE SET "
                "title = excluded.title, excerpt = excluded.excerpt",
                (art.url, art.title, art.published_at.isoformat(), art.excerpt, _now_iso()),
            )
            self._conn.commit()
            return not existed

    def recent_factset(self, since: datetime) -> list[FactsetArticle]:
        """``since`` 이후 발행 FactSet 글(발행 내림차순)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT url, title, published_at, excerpt FROM news_factset "
                "WHERE published_at >= ? ORDER BY published_at DESC",
                (since.isoformat(),),
            ).fetchall()
        return [
            FactsetArticle(url=u, title=t, published_at=datetime.fromisoformat(p), excerpt=e)
            for u, t, p, e in rows
        ]

    # ── 주간 요약 ──────────────────────────────────────────────────────
    def save_weekly(self, summary: WeeklySummary) -> None:
        """주간 요약 저장(week_start upsert)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO weekly_summary (week_start, kr_markdown, model, generated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(week_start) DO UPDATE SET "
                "kr_markdown = excluded.kr_markdown, model = excluded.model, "
                "generated_at = excluded.generated_at",
                (
                    summary.week_start.isoformat(),
                    summary.kr_markdown,
                    summary.model,
                    summary.generated_at.isoformat(),
                ),
            )
            self._conn.commit()

    def latest_weekly(self) -> WeeklySummary | None:
        """가장 최근(week_start 기준) 주간 요약. 없으면 None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT week_start, kr_markdown, model, generated_at FROM weekly_summary "
                "ORDER BY week_start DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return WeeklySummary(
            week_start=date.fromisoformat(row[0]),
            kr_markdown=row[1],
            model=row[2],
            generated_at=datetime.fromisoformat(row[3]),
        )


__all__ = ["NewsStore"]
