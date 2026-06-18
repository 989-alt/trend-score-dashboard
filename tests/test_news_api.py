from __future__ import annotations

import ast
import inspect
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from backend.app import create_app
from backend.config import Settings
from backend.news.models import RawNewsItem, WeeklySummary
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        data_mode="sample",
        db_path=tmp_path / "dash.db",
        news_db_path=tmp_path / "news.db",
    )
    app = create_app(settings)
    with TestClient(app) as c:
        app.state.initial_thread.join(timeout=30)
        yield c


def test_news_issues_route(client: TestClient) -> None:
    ns = client.app.state.news_store  # type: ignore[attr-defined]
    now = datetime.now(tz=UTC)
    ns.insert_raw(RawNewsItem("telegram", "jusikbiso", 1, now, "코스피 급락 서킷브레이커 발동", ()))
    ns.insert_raw(RawNewsItem("telegram", "getfeed", 2, now, "코스피 급락 지속 우려", ()))
    resp = client.get("/api/news/issues")
    assert resp.status_code == 200
    body = resp.json()
    assert body["disclaimer"]
    assert len(body["issues"]) >= 1
    top = body["issues"][0]
    assert top["messages"]  # 구성 원문 포함
    assert "channel" in top["messages"][0]


def test_news_weekly_route(client: TestClient) -> None:
    resp0 = client.get("/api/news/weekly")
    assert resp0.status_code == 200
    assert resp0.json()["kr_markdown"] is None  # 아직 없음
    ns = client.app.state.news_store  # type: ignore[attr-defined]
    ns.save_weekly(
        WeeklySummary(
            week_start=date(2026, 6, 15),
            kr_markdown="## 이번 주 시황\n- 요약",
            model="m",
            generated_at=datetime.now(tz=UTC),
        )
    )
    resp1 = client.get("/api/news/weekly")
    body = resp1.json()
    assert body["kr_markdown"].startswith("## 이번 주 시황")
    assert body["disclaimer"]


def test_scoring_module_has_no_news_dependency() -> None:
    """점수 무영향 회귀 — scoring/engine 이 news 모듈을 import 하지 않음(정적)."""
    import backend.engine
    import backend.scoring

    for mod in (backend.scoring, backend.engine):
        tree = ast.parse(inspect.getsource(mod))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        assert not any("news" in m for m in imported), f"{mod.__name__} imports news"
