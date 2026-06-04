"""테마 집계 — themes.yml 로드 + 종목→테마 역매핑 + 테마별 주도주 그룹.

원칙:
- 테마 정의는 운영자 편집형 YAML(``data/themes.yml``). 주도주는 "테마 내 점수 상위"로
  자동 산출(여기서는 후보 구성만 큐레이션).
- 종목 1개가 여러 테마에 속할 수 있다.
- 주도주(``leaders``)는 KR/US 를 한데 모아 점수 내림차순 ``top_n`` 으로 뽑는다(시장 혼합 허용).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from backend.schemas import Market, ScoreEntry, ThemeGroup


@dataclass(frozen=True)
class ThemeDef:
    """테마 1개 정의 — 이름 + 시장별 구성 종목.

    ``kr`` 은 6자리 종목코드, ``us`` 는 심볼. 불변(frozen)이라 ``tuple`` 로 보관한다.
    """

    name: str
    kr: tuple[str, ...]
    us: tuple[str, ...]


def load_themes(path: Path) -> list[ThemeDef]:
    """``path`` (themes.yml) 를 파싱해 ``ThemeDef`` 목록으로 반환.

    최상위 키 ``themes`` 아래 ``{name, kr, us}`` 매핑 목록을 기대한다. ``kr``/``us`` 가
    없으면 빈 튜플로 본다(한쪽 시장에만 종목이 있는 테마 허용).
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    themes_raw = raw.get("themes", []) if isinstance(raw, dict) else []
    result: list[ThemeDef] = []
    for item in themes_raw:
        result.append(
            ThemeDef(
                name=item["name"],
                kr=tuple(item.get("kr") or ()),
                us=tuple(item.get("us") or ()),
            )
        )
    return result


def themes_for_ticker(ticker: str, market: Market, themes: list[ThemeDef]) -> list[str]:
    """``ticker`` 가 속한 테마 이름 목록 (``market`` 의 구성만 조회)."""
    return [t.name for t in themes if ticker in (t.kr if market == "KR" else t.us)]


def build_theme_groups(
    entries_by_market: dict[Market, list[ScoreEntry]],
    themes: list[ThemeDef],
    top_n: int,
) -> list[ThemeGroup]:
    """테마마다 **시장별로** 소속 엔트리를 모아 점수 상위 ``top_n`` 을 주도주로 묶는다.

    한 테마가 KR·US 양쪽 구성을 가지면 각 시장의 주도주가 한쪽에 밀려 사라지지 않도록
    ``(테마, 시장)`` 단위로 그룹을 만든다(예: '반도체'→KR 그룹 + US 그룹). 소속 엔트리가
    없는 (테마, 시장) 조합은 생략한다. 프론트는 테마 이름으로 두 그룹을 묶어 보여준다.
    """
    by_ticker: dict[Market, dict[str, ScoreEntry]] = {
        market: {e.ticker: e for e in entries} for market, entries in entries_by_market.items()
    }

    groups: list[ThemeGroup] = []
    for theme in themes:
        market_codes: tuple[tuple[Market, tuple[str, ...]], ...] = (
            ("KR", theme.kr),
            ("US", theme.us),
        )
        for market, codes in market_codes:
            members = [e for c in codes if (e := by_ticker.get(market, {}).get(c)) is not None]
            if not members:
                continue
            leaders = sorted(members, key=lambda e: e.score, reverse=True)[:top_n]
            groups.append(ThemeGroup(theme=theme.name, market=market, leaders=leaders))
    return groups


__all__ = ["ThemeDef", "build_theme_groups", "load_themes", "themes_for_ticker"]
