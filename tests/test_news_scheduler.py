from __future__ import annotations

from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from backend.config import Settings
from backend.news.store import NewsStore
from backend.scheduler import build_scheduler
from backend.store import Store


def _ids(scheduler: AsyncIOScheduler) -> set[str]:
    return {j.id for j in scheduler.get_jobs()}


def test_news_jobs_registered_with_store_and_keys(tmp_path: Path) -> None:
    store = Store(tmp_path / "dash.db")
    ns = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None, app_api_id="123", app_api_hash="h")  # type: ignore[call-arg]
    ids = _ids(build_scheduler(store, s, ns))
    assert "news-poll" in ids
    assert "news-weekly" in ids


def test_news_jobs_absent_without_store(tmp_path: Path) -> None:
    store = Store(tmp_path / "dash.db")
    s = Settings(_env_file=None, app_api_id="123", app_api_hash="h")  # type: ignore[call-arg]
    assert "news-poll" not in _ids(build_scheduler(store, s))  # news_store 미주입


def test_news_jobs_absent_without_keys(tmp_path: Path) -> None:
    store = Store(tmp_path / "dash.db")
    ns = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert "news-poll" not in _ids(build_scheduler(store, s, ns))
