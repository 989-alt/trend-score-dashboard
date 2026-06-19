"""trend-trader 영속 — 주문·NAV·포지션 스냅샷 (SQLite, WAL).

봇이 쓰고 대시보드 API 가 읽는다(WAL 로 동시 읽기/쓰기). 금액은 **TEXT(Decimal 문자열)** 로
보관해 정밀도를 지킨다(float 금지). 읽기 시 Decimal 로 복원.

**시장 인식(P10)**: ``nav``·``position_snap`` 은 ``market`` 컬럼을 갖는다(국장 KRW·미장 USD 가
같은 ts 로 섞이는 것을 방지). NAV 헤드라인은 자금이 든 KR 계좌만 본다(미장은 현재 $0).
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.schemas import Market
from backend.trader.models import OrderResult

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS orders (
    ts TEXT, order_no TEXT, org_no TEXT, ticker TEXT, side TEXT,
    qty INTEGER, reason TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS nav (
    ts TEXT, market TEXT, total_eval TEXT, cash TEXT, PRIMARY KEY (ts, market)
);
CREATE TABLE IF NOT EXISTS position_snap (
    ts TEXT, market TEXT, ticker TEXT, name TEXT, qty INTEGER, avg_price TEXT,
    cur_price TEXT, eval_amount TEXT, pnl_amount TEXT, pnl_pct TEXT
);
CREATE INDEX IF NOT EXISTS ix_orders_ts ON orders(ts);
CREATE INDEX IF NOT EXISTS ix_possnap_ts ON position_snap(ts);
"""

#: 시장 미지정 기록의 기본값 — 자금이 든 KR 계좌(헤드라인 NAV 기준).
_DEFAULT_MARKET: Market = "KR"


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
        self._migrate_if_legacy()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _migrate_if_legacy(self) -> None:
        """구 스키마(market 컬럼 없는 nav/position_snap) 감지 시 표시용 NAV 테이블만 재생성.

        NAV/이력은 표시 전용이고 실거래가 없으므로 단순 DROP→재생성으로 충분(복잡한 ALTER 불요).
        orders 테이블은 스키마 동일이라 보존한다. 구 DB 가 없으면(신규) no-op.
        """
        for table in ("nav", "position_snap"):
            row = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if row is None:
                continue
            cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "market" not in cols:
                self._conn.execute(f"DROP TABLE {table}")
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
        market: Market = _DEFAULT_MARKET,
        total_eval: Decimal,
        cash: Decimal,
        positions: list[dict[str, Any]],
    ) -> None:
        """NAV 1점 + 포지션 스냅샷 기록. ``positions`` = HoldingPosition.model_dump() 리스트.

        ``market`` 별로 분리 저장(국장·미장 통화가 섞이지 않게). 같은 (ts, market)은 덮어쓴다.
        """
        ts = at.isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO nav VALUES (?, ?, ?, ?)",
                (ts, market, _s(total_eval), _s(cash)),
            )
            for p in positions:
                self._conn.execute(
                    "INSERT INTO position_snap VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ts,
                        market,
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
        """가장 최근 스냅샷의 보유 종목 — **시장별 최신 ts 의 합집합**(국장+미장 병합).

        KR·US 는 서로 다른 사이클(ts)로 기록하므로 단일 MAX(ts)면 한쪽이 누락된다. 시장별로 각자의
        최신 스냅샷을 골라 합친다(현재 미장은 보유 0).
        """
        with self._lock:
            markets = [
                r[0] for r in self._conn.execute("SELECT DISTINCT market FROM position_snap")
            ]
            out: list[dict[str, Any]] = []
            for mkt in markets:
                row = self._conn.execute(
                    "SELECT MAX(ts) FROM position_snap WHERE market = ?", (mkt,)
                ).fetchone()
                if not row or not row[0]:
                    continue
                rows = self._conn.execute(
                    "SELECT ticker, name, qty, avg_price, cur_price, eval_amount, "
                    "pnl_amount, pnl_pct FROM position_snap WHERE ts = ? AND market = ?",
                    (row[0], mkt),
                ).fetchall()
                out.extend(
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
                    for r in rows
                )
        return out

    def nav_series(
        self, limit: int = 5000, *, market: Market | None = None
    ) -> list[dict[str, Any]]:
        """NAV 시계열(오래된→최신). ``market`` 지정 시 그 시장만, None 이면 전체.

        헤드라인/대시보드는 자금이 든 KR(``market="KR"``)만 본다(미장은 현재 $0 → 0 으로 보임).
        전진검증 리포트는 None(전체)로 호출하나 실거래는 KR 뿐이라 동일하다.
        """
        with self._lock:
            if market is None:
                rows = self._conn.execute(
                    "SELECT ts, total_eval, cash FROM nav ORDER BY ts LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT ts, total_eval, cash FROM nav WHERE market = ? ORDER BY ts LIMIT ?",
                    (market, limit),
                ).fetchall()
        return [{"ts": r[0], "total_eval": _dec(r[1]), "cash": _dec(r[2])} for r in rows]


__all__ = ["TradeStore"]
