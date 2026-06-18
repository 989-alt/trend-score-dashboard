"""언급 추출 — 스냅샷 + 테마로 렉시콘 구성 후 헤드라인에서 종목/테마를 찾는다.

원칙(LLM 미사용·₩0·결정론):
- 종목 렉시콘은 **이미 산출된 스냅샷 entries(종목명→코드)** 에서 공짜로 얻는다 →
  이슈가 대시보드 유니버스·점수와 곧바로 연결된다.
- 테마 키워드는 ``themes.yml`` 의 테마명(및 ``·/,`` 분절 토큰)에서.
- 매칭: 한국어 용어는 부분일치, **ASCII 용어(예: "AI")는 단어경계**로 오탐 차단
  (영문/숫자 인접 시 미매칭 — "AI" 가 "rain" 에 걸리지 않게).
- 종목 코드 자체(6자리 숫자 등) 매칭은 오탐이 커 v1 에서 제외(종목명만).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.schemas import IssueKind, Market, ScoreEntry
from backend.themes import ThemeDef

#: 매칭 최소 용어 길이(1글자 종목명/키워드의 오탐 방지).
_MIN_TERM = 2
#: 순수 ASCII 판별(단어경계 매칭 적용 대상).
_ASCII_RE = re.compile(r"^[\x00-\x7f]+$")
#: 테마명 분절 구분자.
_THEME_SPLIT = re.compile(r"[·/,]")


@dataclass(frozen=True)
class Mention:
    """헤드라인에서 검출된 1개 언급 — 종목 또는 테마."""

    kind: IssueKind
    key: str  # 종목코드 또는 테마명
    name: str
    market: Market | None


@dataclass(frozen=True)
class Lexicon:
    """컴파일된 (정규식, Mention) 목록. 헤드라인마다 각 패턴을 ``search`` 한다."""

    patterns: tuple[tuple[re.Pattern[str], Mention], ...]


def _compile(term: str) -> re.Pattern[str]:
    """용어 매칭 정규식. ASCII 는 단어경계, 그 외(한국어)는 부분일치."""
    if _ASCII_RE.match(term):
        return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(re.escape(term))


def build_lexicon(
    entries_by_market: dict[Market, list[ScoreEntry]], themes: list[ThemeDef]
) -> Lexicon:
    """스냅샷 entries + 테마 정의 → 매칭 렉시콘.

    종목: ``name`` (길이 ≥ 2) → ``Mention("ticker", code, name, market)``.
    테마: 테마명 + 분절 토큰 → ``Mention("theme", theme, theme, None)`` (한 테마의 여러
    토큰은 동일 Mention 으로 수렴 → 항목당 1회만 계수).
    """
    patterns: list[tuple[re.Pattern[str], Mention]] = []
    seen: set[tuple[str, str]] = set()  # (term_lower, key) 중복 패턴 방지

    for market, entries in entries_by_market.items():
        for entry in entries:
            name = entry.name.strip()
            if len(name) < _MIN_TERM:
                continue
            key = (name.lower(), entry.ticker)
            if key in seen:
                continue
            seen.add(key)
            patterns.append((_compile(name), Mention("ticker", entry.ticker, entry.name, market)))

    theme_seen: set[str] = set()
    for theme in themes:
        base = theme.name.strip()
        if not base or base in theme_seen:
            continue
        theme_seen.add(base)
        terms = [base, *(_THEME_SPLIT.split(base))]
        for term in dict.fromkeys(t.strip() for t in terms):
            if len(term) < _MIN_TERM:
                continue
            patterns.append((_compile(term), Mention("theme", theme.name, theme.name, None)))

    return Lexicon(patterns=tuple(patterns))


def extract_mentions(text: str, lexicon: Lexicon) -> set[Mention]:
    """``text`` (헤드라인)에서 검출된 모든 언급의 집합(항목당 엔티티 1회)."""
    found: set[Mention] = set()
    for pattern, mention in lexicon.patterns:
        if pattern.search(text):
            found.add(mention)
    return found


__all__ = ["Lexicon", "Mention", "build_lexicon", "extract_mentions"]
