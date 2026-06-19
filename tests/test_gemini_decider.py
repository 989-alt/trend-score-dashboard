"""GeminiDecider 단위테스트 — 가짜 gemini 콜백(네트워크 0, 실 KIS 0).

검증: 정상 JSON→Decisions, 잡음/오류→None(페일세이프), 반-환각 드롭, 입력해시 캐시(히트는
재호출 안 함·포지션 변하면 재호출).
"""

from __future__ import annotations

from decimal import Decimal

from backend.config import Settings
from backend.schemas import Grade, ScoreEntry
from backend.trader.gemini_decider import GeminiDecider
from backend.trader.models import HoldingPosition


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


def _entry(ticker: str, score: str, *, grade: Grade = Grade.BUY) -> ScoreEntry:
    return ScoreEntry(
        ticker=ticker,
        name=ticker,
        market="KR",
        price=Decimal("10000"),
        score=Decimal(score),
        grade=grade,
        eligible=True,
    )


def _pos(ticker: str, qty: int = 1) -> HoldingPosition:
    return HoldingPosition(ticker=ticker, qty=qty, avg_price=Decimal("10000"))


class _FakeGemini:
    """system,prompt→고정 응답을 주는 콜백. 호출 횟수를 센다(캐시 검증용)."""

    def __init__(self, response: str | Exception) -> None:
        self._response = response
        self.calls = 0

    def __call__(self, system: str, prompt: str) -> str:
        self.calls += 1
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_valid_json_to_decisions() -> None:
    """정상 JSON → 적격 매수/보유 매도가 Decisions 로 변환."""
    fake = _FakeGemini(
        '{"buys":[{"ticker":"A","weight":0.6}],"sells":[{"ticker":"H"}],"reason":"x"}'
    )
    dec = GeminiDecider(_settings(), gemini=fake)
    out = dec.decide(
        "KR", [_entry("A", "90"), _entry("B", "80")], [_pos("H")], Decimal("1000000"), 5
    )
    assert out is not None
    assert out.buys == ["A"]
    assert out.sells == [("H", "청산:LLM판단")]


def test_garbage_json_returns_none() -> None:
    """JSON 아님(잡텍스트) → None(페일세이프)."""
    fake = _FakeGemini("죄송합니다. 매수 추천을 드릴 수 없습니다.")
    dec = GeminiDecider(_settings(), gemini=fake)
    out = dec.decide("KR", [_entry("A", "90")], [], Decimal("1000000"), 5)
    assert out is None


def test_empty_response_returns_none() -> None:
    """빈 응답 → None."""
    fake = _FakeGemini("")
    dec = GeminiDecider(_settings(), gemini=fake)
    assert dec.decide("KR", [_entry("A", "90")], [], Decimal("1000000"), 5) is None


def test_api_exception_returns_none_no_raise() -> None:
    """API 예외는 루프로 전파되지 않고 None 으로 흡수."""
    fake = _FakeGemini(RuntimeError("api down"))
    dec = GeminiDecider(_settings(), gemini=fake)
    out = dec.decide("KR", [_entry("A", "90")], [], Decimal("1000000"), 5)
    assert out is None


def test_hallucinated_buy_ticker_dropped() -> None:
    """적격 후보에 없는 매수 티커(환각)는 드롭. 남은 게 없으면 None."""
    fake = _FakeGemini('{"buys":[{"ticker":"ZZZ","weight":0.5}],"sells":[],"reason":"x"}')
    dec = GeminiDecider(_settings(), gemini=fake)
    out = dec.decide("KR", [_entry("A", "90")], [], Decimal("1000000"), 5)
    assert out is None  # ZZZ 드롭 → 매수/매도 모두 비어 None


def test_hallucinated_sell_ticker_dropped_keeps_valid_buy() -> None:
    """보유에 없는 매도 티커는 드롭하되 적격 매수는 유지."""
    fake = _FakeGemini(
        '{"buys":[{"ticker":"A","weight":0.5}],"sells":[{"ticker":"NOPE"}],"reason":"x"}'
    )
    dec = GeminiDecider(_settings(), gemini=fake)
    out = dec.decide("KR", [_entry("A", "90")], [_pos("H")], Decimal("1000000"), 5)
    assert out is not None
    assert out.buys == ["A"]
    assert out.sells == []  # NOPE 은 보유 아님 → 드롭


def test_json_in_code_fence_parsed() -> None:
    """코드펜스/설명이 섞여도 첫 JSON 오브젝트를 추출해 파싱."""
    fake = _FakeGemini('```json\n{"buys":[{"ticker":"A","weight":0.3}],"sells":[]}\n```')
    dec = GeminiDecider(_settings(), gemini=fake)
    out = dec.decide("KR", [_entry("A", "90")], [], Decimal("1000000"), 5)
    assert out is not None and out.buys == ["A"]


def test_cache_hit_skips_call() -> None:
    """동일 입력 2회차는 Gemini 재호출 없이 캐시 반환(호출 1회)."""
    fake = _FakeGemini('{"buys":[{"ticker":"A","weight":0.5}],"sells":[]}')
    dec = GeminiDecider(_settings(trader_llm_cache=True), gemini=fake)
    cands = [_entry("A", "90")]
    first = dec.decide("KR", cands, [], Decimal("1000000"), 5)
    second = dec.decide("KR", cands, [], Decimal("1000000"), 5)
    assert fake.calls == 1
    assert first == second


def test_cache_miss_after_positions_change_recalls() -> None:
    """포지션이 바뀌면 입력해시가 달라져 재호출(캐시 무효)."""
    fake = _FakeGemini('{"buys":[{"ticker":"A","weight":0.5}],"sells":[]}')
    dec = GeminiDecider(_settings(trader_llm_cache=True), gemini=fake)
    cands = [_entry("A", "90")]
    dec.decide("KR", cands, [], Decimal("1000000"), 5)
    dec.decide("KR", cands, [_pos("A")], Decimal("1000000"), 5)  # 보유 변경 → 키 변경
    assert fake.calls == 2


def test_cache_disabled_always_calls() -> None:
    """캐시 OFF 면 동일 입력도 매번 호출."""
    fake = _FakeGemini('{"buys":[{"ticker":"A","weight":0.5}],"sells":[]}')
    dec = GeminiDecider(_settings(trader_llm_cache=False), gemini=fake)
    cands = [_entry("A", "90")]
    dec.decide("KR", cands, [], Decimal("1000000"), 5)
    dec.decide("KR", cands, [], Decimal("1000000"), 5)
    assert fake.calls == 2


def test_cache_separate_per_market() -> None:
    """한 인스턴스를 KR·US 가 공유해도 시장별 캐시라 서로 thrash 하지 않는다."""
    fake = _FakeGemini('{"buys":[{"ticker":"A","weight":0.5}],"sells":[]}')
    dec = GeminiDecider(_settings(trader_llm_cache=True), gemini=fake)
    cands = [_entry("A", "90")]
    dec.decide("KR", cands, [], Decimal("1000000"), 5)  # KR 미스 → 호출1
    dec.decide("US", cands, [], Decimal("1000000"), 5)  # US 미스 → 호출2
    dec.decide("KR", cands, [], Decimal("1000000"), 5)  # KR 히트 → 호출 안 함
    dec.decide("US", cands, [], Decimal("1000000"), 5)  # US 히트 → 호출 안 함
    assert fake.calls == 2
