"""``backend.news.extract`` — 렉시콘 구성 + 헤드라인 언급 추출(오탐 차단)."""

from __future__ import annotations

from decimal import Decimal

from backend.news.extract import Lexicon, Mention, build_lexicon, extract_mentions
from backend.schemas import Grade, Market, ScoreEntry
from backend.themes import ThemeDef


def _entry(ticker: str, name: str, market: Market = "KR") -> ScoreEntry:
    return ScoreEntry(
        ticker=ticker,
        name=name,
        market=market,
        price=Decimal("1"),
        score=Decimal("50"),
        grade=Grade.HOLD,
        eligible=True,
    )


def _lexicon() -> Lexicon:
    entries: dict[Market, list[ScoreEntry]] = {
        "KR": [_entry("005930", "삼성전자"), _entry("000660", "SK하이닉스")],
        "US": [_entry("NVDA", "NVIDIA", "US")],
    }
    themes = [
        ThemeDef(name="반도체", kr=("005930",), us=("NVDA",)),
        ThemeDef(name="AI", kr=(), us=("NVDA",)),
        ThemeDef(name="2차전지·전기차", kr=(), us=()),
    ]
    return build_lexicon(entries, themes)


def test_extract_ticker_by_name() -> None:
    found = extract_mentions("삼성전자, 3분기 어닝 서프라이즈", _lexicon())
    assert Mention("ticker", "005930", "삼성전자", "KR") in found


def test_extract_theme_keyword() -> None:
    found = extract_mentions("반도체 업황 회복 기대", _lexicon())
    assert Mention("theme", "반도체", "반도체", None) in found


def test_theme_split_token_matches() -> None:
    # "전기차" 단독으로도 "2차전지·전기차" 테마가 잡힌다.
    found = extract_mentions("전기차 보조금 확대", _lexicon())
    assert Mention("theme", "2차전지·전기차", "2차전지·전기차", None) in found


def test_ascii_keyword_word_boundary() -> None:
    lex = _lexicon()
    assert any(m.key == "AI" for m in extract_mentions("AI 반도체 수요 급증", lex))
    # 영문 단어 내부('rain')에는 걸리지 않는다.
    assert not any(m.key == "AI" for m in extract_mentions("rain in Spain", lex))


def test_no_false_match() -> None:
    assert extract_mentions("오늘 날씨가 맑습니다", _lexicon()) == set()


def test_dedup_within_item() -> None:
    # 같은 종목을 두 번 언급해도 Mention 1개(집합).
    tickers = [
        m for m in extract_mentions("삼성전자 삼성전자 신고가", _lexicon()) if m.kind == "ticker"
    ]
    assert len(tickers) == 1
