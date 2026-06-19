"""trend-trader 영속 — 주문·NAV·포지션 스냅샷 (SQLite, WAL).

봇이 쓰고 대시보드 API 가 읽는다(WAL 로 동시 읽기/쓰기). 금액은 **TEXT(Decimal 문자열)** 로
보관해 정밀도를 지킨다(float 금지). 읽기 시 Decimal 로 복원.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.trader.models import OrderResult

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS orders (
    ts TEXT, order_no TEXT, org_no TEXT, ticker TEXT, side TEXT,
    qty INTEGER, reason TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS nav (ts TEXT PRIMARY KEY, total_eval TEXT, cash TEXT);
CREATE TABLE IF NOT EXISTS position_snap (
    ts TEXT, ticker TEXT, name TEXT, qty INTEGER, avg_price TEXT,
    cur_price TEXT, eval_amount TEXT, pnl_amount TEXT, pnl_pct TEXT
);
CREATE INDEX IF NOT EXISTS ix_orders_ts ON orders(ts);
CREATE INDEX IF NOT EXISTS ix_possnap_ts ON position_snap(ts);
"""


def _s(value: Decimal | None) -> str | None:
    """Decimal → TEXT(정밀도 보존). None 은 그대로."""
    return str(value) if value is not None else None


def _dec(value: Any) -> Decimal | None:
    """TEXT → Decimal. None/빈값은 None."""
    if value in (None, ""):
        return None
    return Decimal(str(value))


class TradeStore:
    """매매 기록 영속. 스레드 안전(``check_same_thread=False`` + Lock)."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── 쓰기 ───────────────────────────────────────────────────────────
    def record_order(self, order: OrderResult, *, reason: str = "") -> None:
        """주문 접수 1건 기록(append). ``reason`` = 진입/청산 사유."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    order.submitted_at.isoformat(),
                    order.order_no,
                    order.org_no,
                    order.ticker,
                    order.side,
                    order.qty,
                    reason,
                    order.message,
                ),
            )
            self._conn.commit()

    def record_snapshot(
        self,
        at: datetime,
        *,
        total_eval: Decimal,
        cash: Decimal,
        positions: list[dict[str, Any]],
    ) -> None:
        """NAV 1점 + 포지션 스냅샷 기록. ``positions`` = HoldingPosition.model_dump() 리스트."""
        ts = at.isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO nav VALUES (?, ?, ?)", (ts, _s(total_eval), _s(cash))
            )
            for p in positions:
                self._conn.execute(
                    "INSERT INTO position_snap VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ts,
                        str(p.get("ticker", "")),
                        str(p.get("name", "")),
                        int(p.get("qty", 0)),
                        _s(p.get("avg_price")),
                        _s(p.get("cur_price")),
                        _s(p.get("eval_amount")),
                        _s(p.get("pnl_amount")),
                        _s(p.get("pnl_pct")),
                    ),
                )
            self._conn.commit()

    # ── 읽기 (대시보드 API 용) ─────────────────────────────────────────
    def recent_orders(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, order_no, ticker, side, qty, reason, message "
                "FROM orders ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        keys = ("ts", "order_no", "ticker", "side", "qty", "reason", "message")
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def latest_positions(self) -> list[dict[str, Any]]:
        """가장 최근 스냅샷의 보유 종목."""
        with self._lock:
            row = self._conn.execute("SELECT MAX(ts) FROM position_snap").fetchone()
            if not row or not row[0]:
                return []
            rows = self._conn.execute(
                "SELECT ticker, name, qty, avg_price, cur_price, eval_amount, pnl_amount, pnl_pct "
                "FROM position_snap WHERE ts = ?",
                (row[0],),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "ticker": r[0],
                    "name": r[1],
                    "qty": r[2],
                    "avg_price": _dec(r[3]),
                    "cur_price": _dec(r[4]),
                    "eval_amount": _dec(r[5]),
                    "pnl_amount": _dec(r[6]),
                    "pnl_pct": _dec(r[7]),
                }
            )
        return out

    def nav_series(self, limit: int = 5000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, total_eval, cash FROM nav ORDER BY ts LIMIT ?", (limit,)
            ).fetchall()
        return [{"ts": r[0], "total_eval": _dec(r[1]), "cash": _dec(r[2])} for r in rows]


__all__ = ["TradeStore"]
