from __future__ import annotations

from backend.config import Settings


def test_news_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.gemini_model == "gemini-2.5-flash"
    assert s.news_channel_list == ["FastStockNews", "goodnews_honey", "getfeed", "jusikbiso"]
    assert s.news_db_path.name == "news.db"
    assert s.app_api_id == ""  # .env 미로드 → 기본 빈값


def test_news_channel_list_strips_and_filters() -> None:
    s = Settings(_env_file=None, news_channels=" a , b ,, c ")  # type: ignore[call-arg]
    assert s.news_channel_list == ["a", "b", "c"]
