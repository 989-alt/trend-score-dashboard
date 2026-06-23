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
from backend.trader.models import OrderResult, OrderStatus

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS orders (
    ts TEXT, order_no TEXT, org_no TEXT, ticker TEXT, side TEXT,
    qty INTEGER, reason TEXT, message TEXT,
    filled_qty INTEGER DEFAULT 0, status TEXT DEFAULT '접수',
    filled_price TEXT, realized TEXT, name TEXT DEFAULT ''
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
        self._ensure_order_columns()
        self._conn.commit()

    def _ensure_order_columns(self) -> None:
        """구 orders 테이블(체결 컬럼 없는)에 체결·실현 컬럼을 보강(데이터 보존 ALTER).

        ``CREATE TABLE IF NOT EXISTS`` 는 기존 테이블에 컬럼을 추가하지 않으므로, 이미 운영 중인
        DB(접수만 기록하던)는 여기서 누락 컬럼만 채운다. 신규 DB 는 _SCHEMA 로 이미 갖춰져 no-op.
        """
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(orders)").fetchall()}
        adds = {
            "filled_qty": "INTEGER DEFAULT 0",
            "status": "TEXT DEFAULT '접수'",
            "filled_price": "TEXT",
            "realized": "TEXT",
            "name": "TEXT DEFAULT ''",
        }
        for name, decl in adds.items():
            if name not in cols:
                self._conn.execute(f"ALTER TABLE orders ADD COLUMN {name} {decl}")

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
    def record_order(self, order: OrderResult, *, reason: str = "", name: str = "") -> None:
        """주문 **접수** 1건 기록(append). ``reason`` = 진입/청산 사유, ``name`` = 종목명(표시용).

        접수 시점이라 ``filled_qty=0 · status='접수'``. 실제 체결은 이후 :meth:`reconcile_fills`
        가 KIS 일별체결 조회로 채운다(접수≠체결 — KIS 모의는 접수를 '완료'로 응답).
        ``name`` 은 KIS 주문 응답엔 없어 호출부(스냅샷 종목명)가 넘겨준다 — 없으면 코드로 표시.
        """
        with self._lock:
            self._conn.execute(
                "INSERT INTO orders "
                "(ts, order_no, org_no, ticker, side, qty, reason, message, "
                " filled_qty, status, filled_price, realized, name) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, '접수', NULL, NULL, ?)",
                (
                    order.submitted_at.isoformat(),
                    order.order_no,
                    order.org_no,
                    order.ticker,
                    order.side,
                    order.qty,
                    reason,
                    order.message,
                    name,
                ),
            )
            self._conn.commit()

    def reconcile_fills(self, statuses: list[OrderStatus]) -> None:
        """KIS 일별체결 조회 결과로 접수 기록의 **실제 체결 수량·체결가·상태**를 갱신(멱등).

        ``order_no`` 로 매칭한다. 매도가 처음으로 체결(>0)되면 직전 스냅샷 평단 대비 **실현손익**을
        1회 산정해 저장한다(이미 산정된 행은 건너뜀 — 1분 루프 재호출에도 중복 집계 안 됨).
        조회 실패/미매칭은 무시(fail-open) — 표시 보강일 뿐 매매에 영향 없음.
        """
        with self._lock:
            for s in statuses:
                if not s.order_no:
                    continue
                row = self._conn.execute(
                    "SELECT ts, side, realized FROM orders WHERE order_no = ?", (s.order_no,)
                ).fetchone()
                if row is None:
                    continue
                order_ts, side, realized = row
                realized_text: str | None = None
                if side == "sell" and realized is None and s.filled_qty > 0 and s.filled_price:
                    basis = self._cost_basis(s.ticker, order_ts)
                    if basis is not None:
                        realized_text = _s((s.filled_price - basis) * Decimal(s.filled_qty))
                self._conn.execute(
                    "UPDATE orders SET filled_qty = ?, status = ?, filled_price = ?, "
                    "realized = COALESCE(realized, ?) WHERE order_no = ?",
                    (
                        s.filled_qty,
                        s.status or ("체결" if s.filled_qty >= s.order_qty else "미체결"),
                        _s(s.filled_price),
                        realized_text,
                        s.order_no,
                    ),
                )
            self._conn.commit()

    def _cost_basis(self, ticker: str, before_ts: str) -> Decimal | None:
        """``before_ts`` 직전(이하)에 그 종목을 보유했던 가장 최근 스냅샷의 평단. 없으면 None."""
        row = self._conn.execute(
            "SELECT avg_price FROM position_snap WHERE ticker = ? AND ts <= ? "
            "ORDER BY ts DESC LIMIT 1",
            (ticker, before_ts),
        ).fetchone()
        return _dec(row[0]) if row and row[0] is not None else None

    def realized_pnl_total(self) -> Decimal | None:
        """체결된 매도들의 누적 실현손익 합. 산정된 행이 하나도 없으면 None."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT realized FROM orders WHERE realized IS NOT NULL"
            ).fetchall()
        if not rows:
            return None
        return sum((_dec(r[0]) or Decimal("0") for r in rows), Decimal("0"))

    def tickers_bought_since(self, cutoff_iso: str) -> set[str]:
        """``cutoff_iso``(ISO8601) 이후 **매수 접수**한 종목 집합 — 당일 재매수(과매매) 억제용.

        저장 ts 와 동일 오프셋(앱 전역 KST)이라 ISO 문자열 사전식 비교가 시간순과 일치한다.
        체결 여부와 무관하게 '접수했으면' 차단한다(미체결 종목의 1분 무한 재주문을 끊는 게 목적).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT ticker FROM orders WHERE side = 'buy' AND ts >= ?",
                (cutoff_iso,),
            ).fetchall()
        return {r[0] for r in rows if r[0]}

    def first_nav_today(self, market: Market, day: str) -> Decimal | None:
        """``day``(YYYY-MM-DD) 그 시장의 **첫 NAV(total_eval)**. 없으면 None — 일손실 킬스위치 기준.

        익일이 되면 그날 첫 NAV 가 새 기준이 되어 킬스위치가 자동 해제된다(상태 불요).
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT total_eval FROM nav WHERE market = ? AND ts LIKE ? ORDER BY ts LIMIT 1",
                (market, f"{day}%"),
            ).fetchone()
        return _dec(row[0]) if row and row[0] is not None else None

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
                "SELECT ts, order_no, ticker, name, side, qty, reason, message, "
                "filled_qty, status FROM orders ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        keys = (
            "ts",
            "order_no",
            "ticker",
            "name",
            "side",
            "qty",
            "reason",
            "message",
            "filled_qty",
            "status",
        )
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def latest_positions(self) -> list[dict[str, Any]]:
        """**가장 최근 사이클**의 보유 종목 — 시장별 최신 NAV ts 기준(국장+미장 병합).

        기준 ts 는 ``nav`` 의 MAX(ts)다(**position_snap 이 아님**). 매 사이클 ``nav`` 행은 항상
        쓰지만 보유 0 이면 ``position_snap`` 행은 안 쓴다. position_snap 의 MAX(ts)를 쓰면 매도로
        계좌가 비어도 **마지막 보유 스냅샷이 영구 고정**되어 유령 포지션·고정 현재가가 보였다(라이브
        버그). NAV ts 기준이면 최신 사이클이 flat 일 때 빈 리스트를 정확히 반환한다.
        KR·US 는 다른 사이클(ts)이라 시장별로 각자의 최신 NAV ts 를 본다.
        """
        with self._lock:
            markets = [r[0] for r in self._conn.execute("SELECT DISTINCT market FROM nav")]
            out: list[dict[str, Any]] = []
            for mkt in markets:
                row = self._conn.execute(
                    "SELECT MAX(ts) FROM nav WHERE market = ?", (mkt,)
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
