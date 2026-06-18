"""app 모듈 테스트 — FastAPI 라우트 (sample 모드, 네트워크 0).

검증:
- ``GET /healthz`` 200 + 데이터 모드 노출.
- ``GET /api/snapshot?market=kr`` 200 · entries>0 · disclaimer 포함.
- ``GET /api/themes`` 200 · groups>0 · disclaimer 포함.
- ``GET /api/ticker/{market}/{code}`` 200(존재) / 404(부재).
- 비차단 startup — 초기 스캔 완료 전 ``/api/snapshot`` 은 '준비 중' 빈 스냅샷을 반환.

``TestClient`` 를 컨텍스트 매니저로 써서 lifespan(startup/shutdown) 을 구동한다.
초기 스캔은 백그라운드 daemon 스레드에서 돈다(서버 즉시 서빙) → ``client`` fixture 는
스캔 완료를 기다린 뒤 검증하고, ``client_no_wait`` fixture 는 기다리지 않아 '준비 중'
상태를 검증한다. sample provider 라 외부 호출은 없다.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from backend.app import create_app
from backend.config import Settings
from backend.schemas import DISCLAIMER
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """격리 DB(sample 모드) 로 구성한 앱의 ``TestClient`` — 초기 스캔 완료까지 대기."""
    settings = Settings(
        data_mode="sample",
        db_path=tmp_path / "test.db",
        news_db_path=tmp_path / "news.db",
        news_enabled=False,  # 이슈 수집(네트워크) 비활성 — app 테스트는 hermetic 유지.
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        # 비차단 startup → 초기 스캔(백그라운드 스레드) 완료를 기다린 뒤 검증한다.
        app.state.initial_thread.join(timeout=30)
        yield test_client


def test_healthz_ok(client: TestClient) -> None:
    """헬스체크 200 · data_mode=sample · KR/US 스냅샷 시각 노출."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["data_mode"] == "sample"
    # startup 에서 KR/US 를 산출하므로 두 시각이 채워져 있어야 한다.
    assert body["last_kr_snapshot"] is not None
    assert body["last_us_snapshot"] is not None


def test_snapshot_kr(client: TestClient) -> None:
    """KR 스냅샷 200 · entries>0 · disclaimer 포함."""
    resp = client.get("/api/snapshot", params={"market": "kr"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "KR"
    assert len(body["entries"]) > 0
    assert body["disclaimer"] == DISCLAIMER


def test_snapshot_market_case_insensitive(client: TestClient) -> None:
    """대문자 ``US`` 도 동일하게 처리된다."""
    resp = client.get("/api/snapshot", params={"market": "US"})
    assert resp.status_code == 200
    assert resp.json()["market"] == "US"


def test_snapshot_unsupported_market(client: TestClient) -> None:
    """미지원 시장은 400."""
    resp = client.get("/api/snapshot", params={"market": "jp"})
    assert resp.status_code == 400


def test_themes(client: TestClient) -> None:
    """테마 응답 200 · groups>0 · disclaimer 포함."""
    resp = client.get("/api/themes")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["groups"]) > 0
    assert body["disclaimer"] == DISCLAIMER


def test_ticker_found(client: TestClient) -> None:
    """스냅샷 1위 종목 코드로 조회하면 200 + 동일 ticker 반환."""
    snap = client.get("/api/snapshot", params={"market": "kr"}).json()
    code = snap["entries"][0]["ticker"]
    resp = client.get(f"/api/ticker/kr/{code}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == code
    assert body["market"] == "KR"


def test_ticker_not_found(client: TestClient) -> None:
    """유니버스에 없는 코드는 404."""
    resp = client.get("/api/ticker/kr/000000")
    assert resp.status_code == 404


def test_issues_endpoint_non_blocking(client: TestClient) -> None:
    """이슈 라우트 — 미산출(수집 비활성)이어도 200 + '준비 중' 빈 응답(비차단)."""
    resp = client.get("/api/issues")
    assert resp.status_code == 200
    body = resp.json()
    assert body["issues"] == []
    assert body["disclaimer"] == DISCLAIMER
    assert "window_hours" in body


def test_get_snapshot_non_blocking_returns_preparing(tmp_path: Path) -> None:
    """비차단: 저장된 스냅샷이 없으면 동기 refresh 없이 '준비 중' 빈 스냅샷을 즉시 반환.

    초기 스캔 전 상태를 결정론으로 검증한다(빈 Store 직접 사용 → 백그라운드 스레드 무관).
    """
    from backend.app import _get_snapshot
    from backend.store import Store

    store = Store(tmp_path / "empty.db")  # 스냅샷 미저장
    snap = _get_snapshot("KR", store)
    assert snap.market == "KR"
    assert snap.entries == []
    assert snap.counts.scanned == 0  # 프론트가 '준비 중'으로 처리
    assert snap.next_refresh_at is None
