from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from backend.config import Settings
from backend.news.models import FactsetArticle, RawNewsItem
from backend.news.store import NewsStore
from backend.news.summary import build_prompt, summarize_week


def _factset() -> FactsetArticle:
    return FactsetArticle(
        url="https://insight.factset.com/a",
        title="CPI Note",
        published_at=datetime(2026, 6, 16, tzinfo=UTC),
        excerpt="inflation",
    )


def test_build_prompt_includes_sources() -> None:
    tg = [
        RawNewsItem(
            source="telegram",
            channel="getfeed",
            msg_id=1,
            ts_utc=datetime(2026, 6, 16, tzinfo=UTC),
            text="삼성전자 급락",
            urls=(),
        )
    ]
    prompt = build_prompt([_factset()], tg, date(2026, 6, 15))
    assert "CPI Note" in prompt
    assert "삼성전자 급락" in prompt
    assert "한국어" in prompt


def test_summarize_week_saves(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    store.upsert_factset(_factset())
    s = Settings(_env_file=None, gemini_api_key="x", gemini_model="gemini-test")  # type: ignore[call-arg]
    out = summarize_week(
        store, s, now=datetime(2026, 6, 18, tzinfo=UTC), gemini=lambda _p: "## 이번 주 시황\n- 요약"
    )
    assert out is not None and out.kr_markdown.startswith("## 이번 주 시황")
    latest = store.latest_weekly()
    assert latest is not None and latest.model == "gemini-test"


def test_summarize_week_no_key_fail_open(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert summarize_week(store, s, now=datetime(2026, 6, 18, tzinfo=UTC)) is None


def test_summarize_week_error_fail_open(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.db")
    s = Settings(_env_file=None, gemini_api_key="x")  # type: ignore[call-arg]

    def boom(_p: str) -> str:
        raise RuntimeError("api down")

    assert summarize_week(store, s, now=datetime(2026, 6, 18, tzinfo=UTC), gemini=boom) is None
