"""``backend.store.Store`` 라운드트립 테스트 — 임시 DB(tmp_path) 사용.

검증:
- 스냅샷 저장→로드 시 Decimal/datetime 이 손실 없이 왕복한다.
- 스냅샷은 시장당 1개(같은 market 재저장 = 덮어쓰기), 없는 시장은 ``None``.
- 부모 디렉토리가 없어도 자동 생성된다.

(손절은 무상태 — 가격이력에서 매 사이클 재계산하므로 영속 대상이 아니다.)
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from backend.schemas import (
    Grade,
    Market,
    ScoreEntry,
    Snapshot,
    SnapshotCounts,
)
from backend.store import DailyCache, Store


def _entry(ticker: str, *, score: str, price: str) -> ScoreEntry:
    return ScoreEntry(
        ticker=ticker,
        name=f"name-{ticker}",
        market="KR",
        price=Decimal(price),
        score=Decimal(score),
        grade=Grade.BUY,
        eligible=True,
    )


def _snapshot(market: Market, *, generated_at: datetime) -> Snapshot:
    return Snapshot(
        market=market,
        generated_at=generated_at,
        market_open=True,
        counts=SnapshotCounts(scanned=3, eligible=2, scored=2, failed=0),
        entries=[
            _entry("005930", score="82.5", price="71500.25"),
            _entry("000660", score="64.0", price="123400"),
        ],
    )


def test_save_and_load_snapshot_roundtrip(tmp_path: Path) -> None:
    store = Store(tmp_path / "db.sqlite")
    generated = datetime(2026, 6, 5, 6, 30, tzinfo=UTC)
    snap = _snapshot("KR", generated_at=generated)

    store.save_snapshot(snap)
    loaded = store.load_snapshot("KR")

    assert loaded is not None
    assert loaded == snap
    # Decimal/datetime 이 타입까지 보존되는지 명시 확인.
    assert loaded.generated_at == generated
    assert loaded.entries[0].price == Decimal("71500.25")
    assert isinstance(loaded.entries[0].score, Decimal)


def test_load_missing_snapshot_returns_none(tmp_path: Path) -> None:
    store = Store(tmp_path / "db.sqlite")
    assert store.load_snapshot("US") is None


def test_save_snapshot_upserts_per_market(tmp_path: Path) -> None:
    store = Store(tmp_path / "db.sqlite")
    first = _snapshot("KR", generated_at=datetime(2026, 6, 5, 6, 0, tzinfo=UTC))
    second = _snapshot("KR", generated_at=datetime(2026, 6, 5, 7, 0, tzinfo=UTC))

    store.save_snapshot(first)
    store.save_snapshot(second)

    loaded = store.load_snapshot("KR")
    assert loaded is not None
    assert loaded.generated_at == second.generated_at
    # US 는 KR 저장에 영향받지 않는다.
    assert store.load_snapshot("US") is None


def test_creates_parent_directory(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nested" / "db.sqlite"
    Store(nested)
    assert nested.parent.is_dir()


# ── DailyCache (FIX-C) ───────────────────────────────────────────────────────


def test_daily_cache_put_get_roundtrip(tmp_path: Path) -> None:
    """일봉 캐시 — (market,ticker,asof,kind) 키로 JSON 왕복."""
    cache = DailyCache(tmp_path / "cache.db")
    asof = date(2026, 6, 5)
    assert cache.get("US", "NVDA", asof, "ohlcv") is None  # 미적재
    cache.put("US", "NVDA", asof, "ohlcv", '{"rows":[]}')
    assert cache.get("US", "NVDA", asof, "ohlcv") == '{"rows":[]}'
    # 다른 날짜·종류·시장은 격리(키의 일부가 다르면 별개).
    assert cache.get("US", "NVDA", date(2026, 6, 4), "ohlcv") is None
    assert cache.get("US", "NVDA", asof, "fundamentals") is None
    assert cache.get("KR", "NVDA", asof, "ohlcv") is None


def test_daily_cache_upsert(tmp_path: Path) -> None:
    """같은 키 재저장은 덮어쓰기(upsert)."""
    cache = DailyCache(tmp_path / "cache.db")
    asof = date(2026, 6, 5)
    cache.put("US", "AAPL", asof, "ohlcv", "v1")
    cache.put("US", "AAPL", asof, "ohlcv", "v2")
    assert cache.get("US", "AAPL", asof, "ohlcv") == "v2"
