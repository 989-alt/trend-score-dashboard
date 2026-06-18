"""``backend.news.sources`` — YAML 소스 로딩(유효/무효/부재)."""

from __future__ import annotations

from pathlib import Path

from backend.news.sources import NewsSource, load_sources


def test_load_sources_parses_valid(tmp_path: Path) -> None:
    p = tmp_path / "s.yml"
    p.write_text(
        "sources:\n"
        '  - {name: "MK", kind: rss, url: "https://x/rss"}\n'
        '  - {name: "ch", kind: telegram, url: "ch1"}\n',
        encoding="utf-8",
    )
    assert load_sources(p) == [
        NewsSource("MK", "rss", "https://x/rss"),
        NewsSource("ch", "telegram", "ch1"),
    ]


def test_load_sources_skips_invalid(tmp_path: Path) -> None:
    p = tmp_path / "s.yml"
    p.write_text(
        "sources:\n"
        '  - {name: "bad", kind: bogus, url: "u"}\n'  # 미지원 kind
        '  - {name: "", kind: rss, url: "u"}\n'  # 빈 name
        '  - {kind: rss, url: "u"}\n'  # name 없음
        '  - {name: "ok", kind: rss, url: "u"}\n',
        encoding="utf-8",
    )
    assert load_sources(p) == [NewsSource("ok", "rss", "u")]


def test_load_sources_missing_file(tmp_path: Path) -> None:
    assert load_sources(tmp_path / "nope.yml") == []
