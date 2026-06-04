"""SQLite 영속 — 스냅샷 저장/조회.

원칙:
- 단일 ``Store`` 인스턴스가 한 DB 파일을 소유. ``__init__`` 에서 테이블 생성(idempotent).
- 직렬화는 pydantic v2 (``model_dump_json`` / ``model_validate_json``) 로 계약 일관성 유지.
  Decimal/datetime 은 pydantic JSON 직렬화(문자열)로 손실 없이 왕복한다.
- 스냅샷은 시장당 최신 1개. 손절은 무상태(가격이력에서 매 사이클 재계산)라 영속하지 않는다.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from backend.schemas import Market, Snapshot

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    market       TEXT PRIMARY KEY,
    json         TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
"""


class Store:
    """대시보드 영속 계층 (SQLite). 스냅샷(시장당 최신 1개)을 보관한다."""

    def __init__(self, db_path: Path) -> None:
        """``db_path`` 에 연결하고 필요한 테이블을 생성한다(없으면).

        부모 디렉토리가 없으면 자동 생성한다. ``check_same_thread=False`` + ``_lock`` 으로
        스레드 안전을 보장한다 — 초기 스캔(백그라운드 스레드)·스케줄러 잡·요청 핸들러가
        동일 연결을 공유하기 때문이다(sqlite3 연결은 기본적으로 단일 스레드 전용).
        """
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── 스냅샷 ─────────────────────────────────────────────────────────
    def save_snapshot(self, snap: Snapshot) -> None:
        """``snap`` 을 해당 시장의 최신 스냅샷으로 저장(upsert)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO snapshots (market, json, generated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(market) DO UPDATE SET "
                "json = excluded.json, generated_at = excluded.generated_at",
                (snap.market, snap.model_dump_json(), snap.generated_at.isoformat()),
            )
            self._conn.commit()

    def load_snapshot(self, market: Market) -> Snapshot | None:
        """``market`` 의 최신 스냅샷. 없으면 ``None``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT json FROM snapshots WHERE market = ?", (market,)
            ).fetchone()
        if row is None:
            return None
        return Snapshot.model_validate_json(row[0])


__all__ = ["Store"]
