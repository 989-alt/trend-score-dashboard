"""Gemini 2.5 Pro 매수 결정 레이어 — 분석=스크립트, 결정=LLM, 실행=KIS.

스크립트가 산정한 **적격(하드필터 통과·매수등급) 후보**만 입력으로 받아, 그 안에서 어떤 종목을
매수(+재량 매도)할지와 목표비중을 Gemini 가 고른다. 점수/팩터 계산은 일절 바꾸지 않는다 —
*누가 매수 종목을 고르는가* 만 결정론 → LLM 으로 옮긴 것이다.

안전 원칙(``StrategyEngine`` 가 최종 강제):
- **반-환각**: 입력 후보/보유 집합에 없는 티커는 전부 드롭(LLM 이 새 종목을 지어낼 수 없음).
- **페일세이프**: API 실패·JSON 파싱 실패·빈 응답 등 *어떤 오류*든 ``None`` 반환(예외 전파 금지).
  호출 측은 ``None`` 이면 결정론 점수상위로 폴백 → 매매가 멈추지 않는다.
- **입력해시 캐시**: (시장, 후보 티커+점수, 보유 티커+수량, 현금 버킷) 해시가 직전과 같으면
  Gemini 재호출 없이 캐시된 결정 반환. 30분 스냅샷+무체결이면 1분 루프여도 ₩0.

금액은 ``Decimal`` 전면(float 금지).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from backend.config import Settings
from backend.schemas import Market, ScoreEntry
from backend.trader.models import HoldingPosition
from backend.trader.strategy import Decisions

logger = logging.getLogger(__name__)

#: 현금 해시 버킷(원). 이 단위 미만 변동은 같은 키로 묶어 미세 잔돈 변화로 인한 재호출을 막는다.
_CASH_BUCKET = Decimal("1000000")

_SYSTEM = (
    "당신은 추세추종(trend-following) 포트폴리오 매니저입니다. 입력으로 주는 '적격 후보'는 이미 "
    "하드필터(거래대금·모멘텀·200일선·변동성 밴드)와 매수등급을 통과한 종목들입니다. 규칙: "
    "① 매수/매도 티커는 반드시 제공된 적격 후보 또는 현재 보유 목록 안에서만 고른다(새 티커 금지). "
    "② 가용 현금 한도를 존중하고 과도하게 분산하지 말 것(선별적으로). "
    "③ 추세가 강하고 점수가 높은 종목을 우선한다. "
    "④ 출력은 아래 JSON 스키마 그대로, 다른 텍스트 없이 JSON 만 반환한다. "
    '스키마: {"buys":[{"ticker":"...","weight":0.0~1.0}],'
    '"sells":[{"ticker":"...","reason":"..."}],"reason":"전체 근거 한 줄"}'
)


@dataclass(frozen=True)
class _CacheEntry:
    """직전 호출의 입력해시 + 결정(캐시 1칸)."""

    key: str
    decision: Decisions


def _gemini_call(settings: Settings) -> Callable[[str, str], str]:
    """google-genai 호출 클로저(system, prompt → 텍스트). news.summary 패턴 재사용."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.gemini_api_key)
    model = settings.gemini_model_decision

    def _call(system: str, prompt: str) -> str:
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
            ),
        )
        text: str = resp.text or ""
        return text

    return _call


class GeminiDecider:
    """적격 후보 안에서 매수/재량매도 종목+비중을 Gemini 2.5 Pro 로 결정.

    ``StrategyEngine`` 에 주입된다. 손절 등 안전 게이트는 엔진이 따로 강제하므로 이 클래스는
    *재량 결정*만 담당한다. 모든 실패는 ``None`` 으로 흡수(페일세이프).
    """

    def __init__(
        self, settings: Settings, *, gemini: Callable[[str, str], str] | None = None
    ) -> None:
        self._s = settings
        #: 테스트 주입용. None 이면 첫 호출 시 google-genai 클라이언트를 지연 생성.
        self._gemini = gemini
        #: 시장별 캐시 1칸(KR·US 가 한 인스턴스를 공유해도 서로 thrash 하지 않게 시장 키 분리).
        self._cache: dict[Market, _CacheEntry] = {}

    def decide(
        self,
        market: Market,
        candidates: list[ScoreEntry],
        positions: list[HoldingPosition],
        cash: Decimal,
        top_n: int,
    ) -> Decisions | None:
        """적격 후보·보유·현금 → 매수/재량매도 결정. 실패 시 ``None``(페일세이프).

        입력해시가 직전과 같고 캐시가 켜져 있으면 Gemini 재호출 없이 캐시를 반환한다.
        """
        valid_buy = {c.ticker for c in candidates}
        valid_sell = {p.ticker for p in positions}

        key = _input_key(market, candidates, positions, cash)
        cached = self._cache.get(market)
        if self._s.trader_llm_cache and cached is not None and cached.key == key:
            logger.info("gemini: 입력 동일 — 캐시 사용(재호출 생략) (market=%s)", market)
            return cached.decision

        prompt = _build_prompt(market, candidates, positions, cash, top_n)
        try:
            call = self._gemini or _gemini_call(self._s)
            raw = call(_SYSTEM, prompt)
        except Exception as exc:
            logger.warning("gemini: 결정 호출 실패 → 폴백(None) (market=%s): %s", market, exc)
            return None

        decision = _parse(raw, valid_buy=valid_buy, valid_sell=valid_sell)
        if decision is None:
            logger.warning("gemini: 응답 파싱 실패/빈 결과 → 폴백(None) (market=%s)", market)
            return None

        if self._s.trader_llm_cache:
            self._cache[market] = _CacheEntry(key=key, decision=decision)
        return decision


def _input_key(
    market: Market, candidates: list[ScoreEntry], positions: list[HoldingPosition], cash: Decimal
) -> str:
    """입력 안정 해시 — (시장, 후보 티커+점수, 보유 티커+수량, 현금 버킷). 순서 무관(정렬)."""
    cands = sorted((c.ticker, str(c.score)) for c in candidates)
    poss = sorted((p.ticker, p.qty) for p in positions)
    bucket = (cash / _CASH_BUCKET).to_integral_value() if cash > 0 else Decimal("0")
    payload = json.dumps(
        {"market": market, "cands": cands, "poss": poss, "cash": str(bucket)},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_prompt(
    market: Market,
    candidates: list[ScoreEntry],
    positions: list[HoldingPosition],
    cash: Decimal,
    top_n: int,
) -> str:
    """입력 → 간결 프롬프트(순수). 후보는 점수 내림차순으로 제시."""
    ranked = sorted(candidates, key=lambda c: c.score, reverse=True)
    lines = [
        f"[시장: {market}] 가용현금={cash} 목표보유수={top_n}",
        "",
        "## 적격 후보(이 목록 안에서만 매수 선택)",
    ]
    for c in ranked:
        near = c.near_52w_pct if c.near_52w_pct is not None else "-"
        mom = c.factors.momentum if c.factors is not None else "-"
        lines.append(
            f"- {c.ticker} ({c.name}) 점수={c.score} 등급={c.grade.value} "
            f"가격={c.price} 52주근접%={near} 모멘텀={mom}"
        )
    lines.append("")
    lines.append("## 현재 보유")
    if positions:
        for p in positions:
            pnl = p.pnl_pct if p.pnl_pct is not None else "-"
            lines.append(f"- {p.ticker} 수량={p.qty} 평단={p.avg_price} 손익%={pnl}")
    else:
        lines.append("- (없음)")
    lines.append("")
    lines.append("위 적격 후보 중 매수 종목·목표비중(0~1)과 청산할 보유 종목을 JSON 으로만 답하라.")
    return "\n".join(lines)


def _parse(raw: str, *, valid_buy: set[str], valid_sell: set[str]) -> Decisions | None:
    """Gemini 응답(JSON) → ``Decisions``. 견고 파싱 + 반-환각 검증 + 비중 클램프.

    적격(매수)·보유(매도) 집합에 없는 티커는 드롭. 매수/매도 모두 비면 ``None``.
    """
    data = _loads(raw)
    if data is None:
        return None

    buys: list[str] = []
    seen_buy: set[str] = set()
    for item in _as_list(data.get("buys")):
        ticker = _ticker(item)
        if ticker is None or ticker not in valid_buy or ticker in seen_buy:
            continue
        # 비중은 [0,1] 로 클램프해 형식 견고성만 확보(엔진은 균등 사이징이라 값은 미사용 — 미래
        # 가중 사이징 도입 시 여기서 받은 weight 를 전달). 잘못된 형식이어도 매수 선택은 유효.
        _ = _clamp_weight(item)
        seen_buy.add(ticker)
        buys.append(ticker)

    sells: list[tuple[str, str]] = []
    seen_sell: set[str] = set()
    for item in _as_list(data.get("sells")):
        ticker = _ticker(item)
        if ticker is None or ticker not in valid_sell or ticker in seen_sell:
            continue
        reason = item.get("reason") if isinstance(item, dict) else None
        label = "청산:LLM판단" if not isinstance(reason, str) or not reason.strip() else "청산:LLM"
        seen_sell.add(ticker)
        sells.append((ticker, label))

    if not buys and not sells:
        return None
    return Decisions(buys=buys, sells=sells)


def _loads(raw: str) -> dict[str, object] | None:
    """문자열 → dict. 코드펜스/잡텍스트가 섞여도 첫 JSON 오브젝트를 추출해 파싱."""
    text = raw.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        obj = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _as_list(value: object) -> list[object]:
    """리스트면 그대로, 아니면 빈 리스트(누락/형식오류 방어)."""
    return value if isinstance(value, list) else []


def _ticker(item: object) -> str | None:
    """항목에서 ticker 문자열 추출(공백 제거). dict 아니거나 비면 None."""
    if not isinstance(item, dict):
        return None
    raw = item.get("ticker")
    if not isinstance(raw, str):
        return None
    ticker = raw.strip()
    return ticker or None


def _clamp_weight(item: object) -> Decimal:
    """비중을 [0,1] 로 클램프(파싱 불가·음수=0, 1 초과=1). 부수효과 없음(값만 반환)."""
    raw = item.get("weight") if isinstance(item, dict) else None
    try:
        w = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")
    if w < 0:
        return Decimal("0")
    if w > 1:
        return Decimal("1")
    return w


__all__ = ["GeminiDecider"]
