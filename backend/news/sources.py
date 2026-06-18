"""뉴스 수집 소스 정의 — 운영자 편집형 YAML(``data/news_sources.yml``) 로드.

``themes.py`` 와 동일한 패턴: 소스를 코드가 아니라 데이터(YAML)로 큐레이션한다.
``kind`` 는 ``rss``(feedparser) 또는 ``telegram``(Telethon MTProto).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

#: 허용 소스 종류.
VALID_KINDS = ("rss", "telegram")


@dataclass(frozen=True)
class NewsSource:
    """수집 소스 1개 — 이름 + 종류 + 주소.

    ``url`` 은 ``rss`` 면 피드 URL, ``telegram`` 이면 채널 username(``t.me/<handle>``
    의 handle). 불변(frozen)이라 set/dict 키로 쓸 수 있다.
    """

    name: str
    kind: str  # "rss" | "telegram"
    url: str


def load_sources(path: Path) -> list[NewsSource]:
    """``path`` (news_sources.yml) 를 파싱해 ``NewsSource`` 목록으로 반환.

    최상위 키 ``sources`` 아래 ``{name, kind, url}`` 매핑 목록을 기대한다. ``kind`` 가
    미지원이거나 ``name``/``url`` 이 비면 그 항목은 건너뛴다(fail-open). 파일이 없으면 빈
    목록(이슈 랭킹은 비차단).
    """
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    items = raw.get("sources", []) if isinstance(raw, dict) else []
    out: list[NewsSource] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip()
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if kind not in VALID_KINDS or not name or not url:
            continue
        out.append(NewsSource(name=name, kind=kind, url=url))
    return out


__all__ = ["VALID_KINDS", "NewsSource", "load_sources"]
