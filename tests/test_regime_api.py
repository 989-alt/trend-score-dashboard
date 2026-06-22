"""``GET /api/regime`` 테스트 — sample provider 로 lifespan 구동, 네트워크 0."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from backend.app import create_app
from backend.config import Settings
from fastapi.testclient import TestClient

_REGIMES = {"UP_TREND", "CHOP_VOL", "DOWN", "UNKNOWN"}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        data_mode="sample",
        db_path=tmp_path / "dash.db",
        news_db_path=tmp_path / "news.db",
        trader_db_path=tmp_path / "trading.db",
    )
    app = create_app(settings)
    c = TestClient(app)
    c.__enter__()
    app.state.initial_thread.join(timeout=30)
    try:
        yield c
    finally:
        c.__exit__(None, None, None)


def test_regime_returns_both_markets(client: TestClient) -> None:
    """KR·US 두 시장 레짐 + 면책. 각 레짐은 허용 라벨."""
    r = client.get("/api/regime")
    assert r.status_code == 200
    body = r.json()
    assert body["disclaimer"]
    markets = {m["market"]: m for m in body["markets"]}
    assert set(markets) == {"KR", "US"}
    for m in markets.values():
        assert m["regime"] in _REGIMES
