"""SQLite 영속 — 스냅샷 저장/조회 + 일봉·펀더멘털 일1회 캐시.

원칙:
- 단일 ``Store`` 인스턴스가 한 DB 파일을 소유. ``__init__`` 에서 테이블 생성(idempotent).
- 직렬화는 pydantic v2 (``model_dump_json`` / ``model_validate_json``) 로 계약 일관성 유지.
  Decimal/datetime 은 pydantic JSON 직렬화(문자열)로 손실 없이 왕복한다.
- 스냅샷은 시장당 최신 1개. 손절은 무상태(가격이력에서 매 사이클 재계산)라 영속하지 않는다.
- ``DailyCache`` 는 일봉·펀더멘털을 (market,ticker,asof_date) 키로 캐시(FIX-C, Yahoo 429
  회피). 같은 날 재호출은 네트워크 없이 캐시 사용.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date
from pathlib import Path

from backend.schemas import Market, Snapshot

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    market       TEXT PRIMARY KEY,
    json         TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
"""

_DAILY_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_cache (
    market     TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    asof_date  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    json       TEXT NOT NULL,
    PRIMARY KEY (market, ticker, asof_date, kind)
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


class DailyCache:
    """일봉·펀더멘털 일1회 캐시 (FIX-C — Yahoo 429 회피).

    키는 ``(market, ticker, asof_date, kind)``. 같은 날 같은 종목의 일봉/펀더멘털은
    네트워크 없이 캐시에서 읽는다(``kind`` = ``"ohlcv"``/``"fundamentals"``). 값은 호출
    측이 직렬화한 JSON 문자열(pydantic ``model_dump_json``)을 그대로 보관한다.

    스레드 안전: ``check_same_thread=False`` + ``_lock`` (병렬 수집 스레드가 공유).
    """

    def __init__(self, db_path: Path) -> None:
        """``db_path`` 에 연결하고 ``daily_cache`` 테이블을 생성한다(없으면)."""
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.executescript(_DAILY_SCHEMA)
        self._conn.commit()

    def get(self, market: Market, ticker: str, asof: date, kind: str) -> str | None:
        """``(market, ticker, asof, kind)`` 의 캐시 JSON. 없으면 ``None``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT json FROM daily_cache "
                "WHERE market = ? AND ticker = ? AND asof_date = ? AND kind = ?",
                (market, ticker, asof.isoformat(), kind),
            ).fetchone()
        return row[0] if row is not None else None

    def put(self, market: Market, ticker: str, asof: date, kind: str, payload: str) -> None:
        """``(market, ticker, asof, kind)`` 캐시에 JSON 저장(upsert)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO daily_cache (market, ticker, asof_date, kind, json) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(market, ticker, asof_date, kind) DO UPDATE SET json = excluded.json",
                (market, ticker, asof.isoformat(), kind, payload),
            )
            self._conn.commit()


__all__ = ["DailyCache", "Store"]
