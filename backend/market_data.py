"""데이터 소스 추상화 — 시세·펀더멘털·OHLCV·투자자별 매매의 단일 진입점.

원칙(schemas.py 계약 계승):
- 금액·가격·수량은 ``Decimal`` (float 금지).
- datetime 은 timezone-aware.
- 모든 소스는 ``MarketDataProvider`` Protocol 로 분리 → 테스트는 mock 으로 외부 API 없이 통과.

구현 모드(``Settings.data_mode``):
- ``sample``: ``SampleProvider`` — 결정론 합성데이터(키 불필요, 개발·검증용).
- ``live``: ``LiveProvider`` — KIS 국내(httpx) + yfinance 미국.
"""

from __future__ import annotations

import contextlib
import hashlib
import html
import json
import logging
import os
import re
import threading
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

import httpx
import yaml
from pydantic import BaseModel, ConfigDict
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.config import Settings
from backend.schemas import InvestorFlow, Market, OHLCVRow
from backend.store import DailyCache

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

_CFG = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------------------
# 소스 반환 모델
# ---------------------------------------------------------------------------


class Quote(BaseModel):
    """실시간(30분 갱신) 시세 스냅샷.

    ``open``/``prev_close`` 는 장 시작·전일 대비 변화율 계산용. 소스가 제공하지
    않으면 ``None``. 거래대금(``turnover``)은 유동성 하드필터의 입력.
    """

    model_config = _CFG

    price: Decimal
    open: Decimal | None = None
    prev_close: Decimal | None = None
    volume: Decimal | None = None
    turnover: Decimal | None = None
    asof: datetime


class Fundamentals(BaseModel):
    """펀더멘털·통계(일 1회 갱신). 소스가 제공하지 않는 필드는 ``None``.

    US(yfinance)와 KR(KIS)이 키 셋이 달라 모두 ``Optional`` 로 둔다.
    """

    model_config = _CFG

    market_cap: Decimal | None = None
    per: Decimal | None = None
    pbr: Decimal | None = None
    eps: Decimal | None = None
    w52_high: Decimal | None = None
    w52_low: Decimal | None = None
    return_1y_pct: Decimal | None = None
    sector: str | None = None
    industry: str | None = None
    name: str | None = None


# ---------------------------------------------------------------------------
# Provider Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class MarketDataProvider(Protocol):
    """시세·펀더멘털·OHLCV·투자자별 매매 조회 인터페이스.

    엔진·스케줄러는 이 Protocol 에만 의존한다 (의존성 주입). 시장별 분기는
    구현체 내부에서 처리하고, 호출 측은 ``market`` 인자만 넘긴다.
    """

    def list_universe(self, market: Market) -> list[str]:
        """``market`` 의 스캔 대상 ticker 전체 (KR=6자리 코드, US=심볼)."""
        ...

    def get_name(self, ticker: str, market: Market) -> str:
        """``ticker`` 의 종목명(표시용)."""
        ...

    def get_daily_ohlcv(self, ticker: str, market: Market, days: int) -> list[OHLCVRow]:
        """``ticker`` 의 최근 ``days`` 일 일봉 OHLCV (날짜 오름차순)."""
        ...

    def get_index_ohlcv(self, market: Market, days: int) -> list[OHLCVRow]:
        """``market`` 지수(KR=KOSPI, US=S&P500)의 최근 ``days`` 일 일봉. RS 분모용."""
        ...

    def get_quote(self, ticker: str, market: Market) -> Quote:
        """``ticker`` 의 현재 시세 스냅샷."""
        ...

    def get_fundamentals(self, ticker: str, market: Market) -> Fundamentals:
        """``ticker`` 의 펀더멘털·통계."""
        ...

    def get_investor_flow(self, ticker: str) -> InvestorFlow | None:
        """``ticker`` 의 투자자별 매매(외국인/기관/개인). **KR 전용** — US 는 ``None``."""
        ...

    def prepare_daily(self, tickers: list[str], market: Market) -> None:
        """일봉·펀더멘털 캐시 워밍(일1회 prep). 라이브는 배치로 Yahoo 부하 최소화.

        sample 등 캐시가 불필요한 구현은 no-op. intraday 는 캐시된 일봉을 재사용한다.
        """
        ...


# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------

_DEC0 = Decimal("0")

#: 일봉 캐시 asof_date 계산용 시장 TZ (일1회 캐시가 시장 달력일에 맞춰 롤오버되도록).
_MARKET_TZ: dict[Market, ZoneInfo] = {
    "KR": ZoneInfo("Asia/Seoul"),
    "US": ZoneInfo("America/New_York"),
}


def _market_today(market: Market) -> date:
    """``market`` 로컬 달력일(오늘) — 일봉 캐시 asof_date 키."""
    return datetime.now(tz=_MARKET_TZ[market]).date()


def _d(value: object) -> Decimal:
    """임의 수치를 ``Decimal`` 로 (float 은 문자열 경유로 정밀도 보존)."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _q2(value: Decimal) -> Decimal:
    """소수 둘째자리 반올림(가격/금액 표시 일관성)."""
    return value.quantize(Decimal("0.01"))


def _dedup(items: Iterable[str]) -> list[str]:
    """입력 순서 유지하며 중복 제거."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _amount(value: object) -> Decimal | None:
    """KIS 거래대금 문자열(KRW) → ``Decimal``. 빈문자("")·부재(None)면 ``None``.

    KIS 응답의 빈값은 ``""`` 라 ``Decimal("")`` 이 예외를 던진다 → 반드시 가드.
    """
    if value is None or value == "":
        return None
    return _d(value)


class _OHLCVList(BaseModel):
    """OHLCVRow 리스트 캐시 직렬화 래퍼 (pydantic JSON 왕복 — Decimal/date 무손실)."""

    model_config = ConfigDict(extra="forbid")

    rows: list[OHLCVRow]


def _extract_ticker_frame(data: Any, ticker: str) -> Any:
    """``yf.download(group_by='ticker')`` 결과에서 ``ticker`` 단일 종목 프레임 추출.

    멀티종목이면 컬럼 최상위 레벨(=ticker)로 슬라이스, 단일종목이면 그대로. 부재·실패는
    ``None`` (호출 측이 빈 결과로 처리).
    """
    if data is None or getattr(data, "empty", True):
        return None
    columns = getattr(data, "columns", None)
    # MultiIndex 컬럼(멀티종목) → 최상위 레벨에서 ticker 슬라이스.
    if columns is not None and getattr(columns, "nlevels", 1) > 1:
        if ticker not in columns.get_level_values(0):
            return None
        return data[ticker]
    # 단일종목(평면 컬럼) — 그대로 사용.
    return data


def _rows_from_yf_frame(hist: Any) -> list[OHLCVRow]:
    """yfinance OHLCV DataFrame(단일 종목) → ``OHLCVRow`` 리스트(날짜 오름차순).

    ``.history()`` / ``yf.download(group_by='ticker')`` 의 단일 종목 프레임을 공통 파싱한다.
    NaN 행(휴장·결측)은 건너뛴다. 빈 프레임이면 빈 리스트.
    """
    rows: list[OHLCVRow] = []
    if hist is None or getattr(hist, "empty", True):
        return rows
    for idx, rec in hist.iterrows():
        close = rec.get("Close")
        if close is None or (isinstance(close, float) and close != close):  # NaN 가드
            continue
        rows.append(
            OHLCVRow(
                date=idx.date(),
                open=_d(rec["Open"]),
                high=_d(rec["High"]),
                low=_d(rec["Low"]),
                close=_d(rec["Close"]),
                volume=_d(rec["Volume"]),
            )
        )
    return rows


#: KIS 투자자 매매 거래대금(``*_tr_pbmn``)은 **백만원** 단위 → 원 환산 배수.
#: (매수금/매수량 ≈ 종가/1e6 으로 실증 확인: 005930·000660 모두 close/ratio ≈ 1,000,000.)
_INVESTOR_TR_PBMN_UNIT = Decimal("1000000")


def _first_settled_flow_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """투자자별 매매 ``output`` 리스트에서 **첫 정산 행** 반환.

    최신일(``output[0]``)은 ``frgn_ntby_tr_pbmn`` 이 ""/"0" 으로 미정산이므로,
    외국인 순매수 거래대금이 0 이 아닌(=정산된) 첫 행을 고른다. 없으면 ``None``.
    """
    for rec in rows:
        net = _amount(rec.get("frgn_ntby_tr_pbmn"))
        if net is not None and net != _DEC0:
            return rec
    return None


def _universe_from_themes(themes_path: Path, market: Market) -> list[str]:
    """themes.yml 의 ``market`` 티커 전체 ∪ 시장별 대형주 (중복 제거, 순서 유지).

    ``themes.load_themes`` 에 의존하지 않고 직접 파싱 → 본 모듈을 독립 검증 가능하게
    한다. (themes.yml 스키마: ``themes: [{name, kr:[...], us:[...]}, ...]``.)
    """
    raw = yaml.safe_load(themes_path.read_text(encoding="utf-8")) or {}
    defs = raw.get("themes", []) if isinstance(raw, dict) else []
    key = "kr" if market == "KR" else "us"
    extra = _KR_LARGE_CAPS if market == "KR" else _US_LARGE_CAPS
    theme_tickers = (str(t) for d in defs if isinstance(d, dict) for t in (d.get(key) or []))
    return _dedup((*theme_tickers, *extra))


# 시장별 추가 대형주(themes.yml 과 합집합) — 유니버스 폭을 넓힌다.
_KR_LARGE_CAPS: tuple[str, ...] = (
    "005490",  # POSCO홀딩스
    "105560",  # KB금융
    "055550",  # 신한지주
    "015760",  # 한국전력
    "032830",  # 삼성생명
    "017670",  # SK텔레콤
)
_US_LARGE_CAPS: tuple[str, ...] = (
    "AAPL",
    "AMZN",
    "BRK-B",
    "JPM",
    "V",
    "XOM",
)

# 합성 종목명 매핑(themes.yml 코드 일부). 미등록은 ``get_name`` 이 기본 라벨로 처리.
_KR_NAMES: dict[str, str] = {
    # 반도체
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "042700": "한미반도체",
    "058470": "리노공업",
    "240810": "원익IPS",
    "357780": "솔브레인",
    "403870": "HPSP",
    "000990": "DB하이텍",
    # 2차전지·전기차
    "373220": "LG에너지솔루션",
    "006400": "삼성SDI",
    "247540": "에코프로비엠",
    "086520": "에코프로",
    "003670": "포스코퓨처엠",
    "066970": "엘앤에프",
    # AI
    "035420": "NAVER",
    "035720": "카카오",
    "030520": "한글과컴퓨터",
    "053800": "안랩",
    # 자율주행
    "005380": "현대차",
    "000270": "기아",
    "161390": "한국타이어앤테크놀로지",
    # 양자컴퓨터
    "036930": "주성엔지니어링",
    # 로봇
    "454910": "두산로보틱스",
    "277810": "레인보우로보틱스",
    "108490": "로보티즈",
    "056080": "유진로봇",
    # 방산
    "012450": "한화에어로스페이스",
    "047810": "한국항공우주",
    "064350": "현대로템",
    "042660": "한화오션",
    # 바이오
    "207940": "삼성바이오로직스",
    "068270": "셀트리온",
    "196170": "알테오젠",
    "091990": "셀트리온헬스케어",
    # 원자력
    "034020": "두산에너빌리티",
    "051600": "한전KPS",
    "052690": "한전기술",
    # 대형주
    "005490": "POSCO홀딩스",
    "105560": "KB금융",
    "055550": "신한지주",
    "015760": "한국전력",
    "032830": "삼성생명",
    "017670": "SK텔레콤",
}


# ---------------------------------------------------------------------------
# 구현 — 샘플(합성) / 라이브
# ---------------------------------------------------------------------------


class SampleProvider:
    """결정론 합성데이터 Provider — 키 불필요, 개발·검증용.

    동일 ticker → 동일 OHLCV·시세를 반환(시드 기반). 외부 호출 없음.

    추세 다양화(``ticker`` 해시로 결정):
    - 대부분(분류 0,1) → 200일선 위 상승추세(eligible 고득점).
    - 일부(분류 2) → 하락추세(ineligible).
    - 일부(분류 3) → 상승 후 최근 고점 대비 8%+ 하락(가상진입 트레일링 이탈 → 매도요구).
    """

    #: 합성 일봉 길이(약 1년치 거래일).
    _SERIES_LEN = 260

    def list_universe(self, market: Market) -> list[str]:
        """``market`` 의 합성 유니버스 (themes.yml 종목 ∪ 시장별 대형주, 중복 제거)."""
        return _universe_from_themes(Settings().themes_path, market)

    def get_name(self, ticker: str, market: Market) -> str:
        """합성 종목명 (KR 은 매핑, 미등록·US 는 기본 라벨)."""
        if market == "KR" and ticker in _KR_NAMES:
            return _KR_NAMES[ticker]
        if market == "US":
            return ticker
        return f"종목{ticker}"

    def get_daily_ohlcv(self, ticker: str, market: Market, days: int) -> list[OHLCVRow]:
        """ticker 시드로 결정론 생성한 일봉 (날짜 오름차순, 최근 ``days`` 봉)."""
        closes, volumes = self._synth_series(ticker)
        rows = self._build_rows(closes, volumes)
        if days < len(rows):
            return rows[-days:]
        return rows

    def get_index_ohlcv(self, market: Market, days: int) -> list[OHLCVRow]:
        """합성 지수 일봉 — 시장별 고정 시드로 결정론 생성(RS 분모)."""
        closes, volumes = self._synth_series(f"INDEX-{market}")
        rows = self._build_rows(closes, volumes)
        if days < len(rows):
            return rows[-days:]
        return rows

    def get_quote(self, ticker: str, market: Market) -> Quote:
        """합성 시세 — 마지막 일봉 기준 현재가·시가·거래대금.

        FIX-E: ``open`` 은 마지막 일봉의 **실제 시가**(``_build_rows`` 와 동일 규칙)로
        둔다. 전일 종가(``prev_close``)와 달라야 '장시작 대비'(price/open)와 '전일
        대비'(price/prev_close)가 구분된다 (이전엔 둘 다 직전 종가라 동일했음).
        """
        closes, volumes = self._synth_series(ticker)
        last_close = closes[-1]
        prev_close = closes[-2] if len(closes) >= 2 else last_close
        last_open = self._bar_open(last_close, prev_close if len(closes) >= 2 else None)
        volume = volumes[-1]
        return Quote(
            price=_q2(last_close),
            open=last_open,
            prev_close=_q2(prev_close),
            volume=volume,
            turnover=_q2(last_close * volume),
            asof=datetime.now(tz=UTC),
        )

    def get_fundamentals(self, ticker: str, market: Market) -> Fundamentals:
        """합성 펀더멘털 — 시총·PER·52주고저·1년수익률."""
        closes, _ = self._synth_series(ticker)
        last_close = closes[-1]
        first_close = closes[0]
        seed = self._seed(ticker)
        # 결정론 보조값(시드 바이트로 분산).
        shares = Decimal(50_000_000 + (seed % 950_000_000))
        per = _q2(Decimal("5") + Decimal((seed >> 8) % 4500) / Decimal("100"))
        w52_high = max(closes)
        w52_low = min(closes)
        return_1y = (
            (last_close - first_close) / first_close * Decimal("100")
            if first_close > _DEC0
            else _DEC0
        )
        return Fundamentals(
            market_cap=_q2(last_close * shares),
            per=per,
            pbr=_q2(Decimal("0.5") + Decimal((seed >> 16) % 600) / Decimal("100")),
            eps=_q2(last_close / per) if per > _DEC0 else None,
            w52_high=_q2(w52_high),
            w52_low=_q2(w52_low),
            return_1y_pct=_q2(return_1y),
            sector=None,
            industry=None,
            name=self.get_name(ticker, market),
        )

    def get_investor_flow(self, ticker: str) -> InvestorFlow | None:
        """KR 합성 투자자별 매매 — 매수·매도 **거래대금(KRW)** 과 순매수.

        사용자 요구(FIX-A): ``*_net`` 은 '수량'이 아니라 '매수금−매도금'(거래대금, KRW).
        외국인/기관/개인 각각 매수액·매도액을 결정론으로 수십~수천억 규모에서 생성하고,
        ``*_net = buy − sell`` 로 산출한다. 세 net 의 합은 시장 항등식(외국인+기관+개인
        순매수 = 0)을 정확히 유지하도록 개인 매도액을 보정한다.

        US 심볼은 엔진이 ``get_investor_flow`` 를 호출하지 않으나, 6자리 숫자 코드가
        아니면(US 심볼 형태) ``None`` 을 돌려 계약을 지킨다.
        """
        if not (len(ticker) == 6 and ticker.isdigit()):
            return None
        seed = self._seed(ticker)

        def amount(salt: int) -> Decimal:
            # 결정론 KRW 금액 — 50억~3,050억 규모(수십~수천억).
            h = hashlib.sha256(f"{seed}:flow:{salt}".encode()).digest()
            raw = int.from_bytes(h[:6], "big") % 3_000_000_000_000
            return Decimal(5_000_000_000 + raw)

        foreign_buy = amount(0)
        foreign_sell = amount(1)
        institution_buy = amount(2)
        institution_sell = amount(3)
        individual_buy = amount(4)
        foreign_net = foreign_buy - foreign_sell
        institution_net = institution_buy - institution_sell
        # 시장 항등식: 세 net 합 = 0 → 개인 net 은 나머지의 음수. 개인 매도액으로 흡수.
        individual_net = -(foreign_net + institution_net)
        individual_sell = individual_buy - individual_net
        return InvestorFlow(
            date=self._last_date().date(),
            foreign_net=foreign_net,
            institution_net=institution_net,
            individual_net=individual_net,
            foreign_buy=foreign_buy,
            foreign_sell=foreign_sell,
            institution_buy=institution_buy,
            institution_sell=institution_sell,
            individual_buy=individual_buy,
            individual_sell=individual_sell,
        )

    def prepare_daily(self, tickers: list[str], market: Market) -> None:
        """no-op — 합성 데이터는 결정론·무비용이라 캐시 워밍이 필요 없다."""

    # ── 내부 합성 로직 ─────────────────────────────────────────────────

    @staticmethod
    def _seed(ticker: str) -> int:
        """ticker → 결정론 정수 시드 (SHA-256 앞 8바이트)."""
        digest = hashlib.sha256(ticker.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big")

    def _classify(self, ticker: str) -> int:
        """추세 분류 0~3. 매 1/12 확률대로 매도요구 케이스(3)를 보장 배치."""
        bucket = self._seed(ticker) % 12
        if bucket == 0:
            return 3  # 상승 후 급락(매도요구)
        if bucket in (1, 2):
            return 2  # 하락추세(ineligible)
        return bucket % 2  # 0/1 — 상승추세(eligible)

    def _last_date(self) -> datetime:
        """합성 시계열의 마지막 봉 날짜(고정 기준일 — 결정론)."""
        base = datetime(2025, 1, 1, tzinfo=UTC)
        return base + timedelta(days=self._SERIES_LEN - 1)

    def _synth_series(self, ticker: str) -> tuple[list[Decimal], list[Decimal]]:
        """결정론 종가·거래량 시계열 생성.

        변동성을 ``[0.20, 0.60]`` 연환산 밴드 안으로 들어오게 작은 일별 노이즈를
        주고, 분류에 따라 추세를 다양화한다. 모든 값은 ``Decimal``.
        """
        seed = self._seed(ticker)
        cls = self._classify(ticker)
        n = self._SERIES_LEN

        # 시작가 5,000~95,000 사이 결정론.
        base = Decimal(5_000 + (seed % 90_000))
        # 일별 추세 드리프트(분류별). cls 2 는 하락, 나머지는 완만 상승.
        # cls 3 은 '상승 후 급락' 케이스 — 충분한 상승 후 200일선 위를 확실히
        # 유지하도록 상승 드리프트를 약간 키운다(꼬리 급락 후에도 above_ma200).
        if cls == 2:
            drift = Decimal("-0.0015")
        elif cls == 3:
            drift = Decimal("0.0020")
        else:
            drift = Decimal("0.0010")

        # 일별 노이즈 진폭 → 연환산 변동성 ≈ amp*0.577*√252 ≈ amp*9.15
        # (균등 [-1,1] 의 std≈0.577). 밴드 [0.20,0.60] 중심 0.40 목표 → amp≈0.044.
        # 시드로 0.034~0.054 사이 분산 → 연환산 ≈ 0.31~0.49 (밴드 안).
        amp = Decimal("0.034") + Decimal(seed % 20) / Decimal("1000")

        closes: list[Decimal] = []
        volumes: list[Decimal] = []
        price = base
        for i in range(n):
            # 결정론 의사난수 [-1,1] (인덱스+시드 해시).
            wiggle = self._wiggle(seed, i)
            ret = drift + amp * wiggle
            price = price * (Decimal("1") + ret)
            if price < Decimal("1"):
                price = Decimal("1")
            closes.append(_q2(price))
            # 거래량 — 유동성 하드필터(거래대금 ≥ 100억) 통과하도록 충분히 크게.
            vol = Decimal(800_000 + ((seed >> (i % 32)) % 700_000))
            volumes.append(vol)

        if cls == 3:
            closes = self._apply_recent_drop(closes)

        return closes, volumes

    @staticmethod
    def _wiggle(seed: int, i: int) -> Decimal:
        """결정론 [-1, 1] 의사난수."""
        h = hashlib.sha256(f"{seed}:{i}".encode()).digest()
        # 첫 2바이트 → 0~65535 → [-1,1].
        raw = int.from_bytes(h[:2], "big")
        return (Decimal(raw) / Decimal("32767.5")) - Decimal("1")

    def _apply_recent_drop(self, closes: list[Decimal]) -> list[Decimal]:
        """마지막 봉을 직전 고점 대비 **8% 초과** 하락시킨다(트레일링 이탈).

        가상진입(상승 중 BUY) 후 트레일링(고점 대비 8% 이탈)으로 매도요구가
        발동되는 케이스. 꼬리(최근 5봉) 직전 구간의 최고 종가를 기준 고점으로 잡아,
        잡음과 무관하게 마지막 종가가 그 고점 대비 8.5% 아래가 되도록 보장한다.
        200일선은 200봉 평균이라 5봉 급락에 거의 영향받지 않아 그대로 위를 유지한다.
        """
        tail_len = 5
        out = list(closes)
        # 꼬리 직전 구간의 최고가를 '진입 후 고점(peak)'으로 사용.
        peak = max(out[:-tail_len])
        # peak 대비 단계적으로 -2%,-4%,-6%,-8%,-8.5% (점점 더 하락 → 마지막이 최저).
        factors = [
            Decimal("0.98"),
            Decimal("0.96"),
            Decimal("0.94"),
            Decimal("0.92"),
            Decimal("0.915"),  # > 8% 하락 보장
        ]
        for k, factor in enumerate(factors):
            out[-tail_len + k] = _q2(peak * factor)
        return out

    @staticmethod
    def _bar_open(close: Decimal, prev_close: Decimal | None) -> Decimal:
        """일봉 시가 결정론 규칙 (``_build_rows`` 와 ``get_quote`` 의 단일 출처).

        직전 종가 대비 상승봉이면 종가의 99%, 하락봉이면 101% 를 시가로 둔다
        (전일 첫 봉은 상승봉으로 간주).
        """
        if prev_close is None or close >= prev_close:
            return _q2(close * Decimal("0.99"))
        return _q2(close * Decimal("1.01"))

    def _build_rows(self, closes: list[Decimal], volumes: list[Decimal]) -> list[OHLCVRow]:
        """종가·거래량 → OHLCVRow (open/high/low 결정론 채움, 날짜 오름차순)."""
        start = datetime(2025, 1, 1, tzinfo=UTC).date()
        rows: list[OHLCVRow] = []
        prev: Decimal | None = None
        for i, close in enumerate(closes):
            open_price = self._bar_open(close, prev)
            high = _q2(max(open_price, close) * Decimal("1.005"))
            low = _q2(min(open_price, close) * Decimal("0.995"))
            rows.append(
                OHLCVRow(
                    date=start + timedelta(days=i),
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                    volume=volumes[i],
                )
            )
            prev = close
        return rows


# ---------------------------------------------------------------------------
# 라이브
# ---------------------------------------------------------------------------

#: KIS 도메인(모의/실).
_KIS_DOMAINS = {
    "mock": "https://openapivts.koreainvestment.com:29443",
    "real": "https://openapi.koreainvestment.com:9443",
}
#: 시세 글리치 가드 — 직전 기준가 대비 이 배수 밖이면 재조회.
_GLITCH_LOW = Decimal("0.5")
_GLITCH_HIGH = Decimal("1.5")
#: KIS inquire-price 단기 TTL(초) — quote·fundamentals 가 같은 호출을 공유해 중복 제거.
_PRICE_TTL = timedelta(seconds=30)

#: 일봉 캐시 종류 키(DailyCache.kind).
_CACHE_OHLCV = "ohlcv"
_CACHE_FUND = "fundamentals"
_CACHE_INDEX = "index"
_CACHE_UNIVERSE = "universe"
#: 유니버스 캐시의 sentinel ticker — DailyCache 키를 재사용하되 종목이 아니라 시장 1행으로 둔다.
_UNIVERSE_SENTINEL = "_UNIVERSE_"
#: 네이버 금융 시가총액 순위(KRX MDC 비의존 enumeration). sosok: KOSPI=0/KOSDAQ=1, 페이지당 50종목.
_NAVER_MARKET_SUM = "https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
_NAVER_PAGE_SIZE = 50
_NAVER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
#: 시총순위 행의 종목명 앵커(class="tltle") → (종목코드, 종목명). 종목명을 KRX 비의존으로 확보.
_NAVER_ROW_RE = re.compile(
    r'<a href="/item/main\.naver\?code=(\d{6})"[^>]*class="tltle"[^>]*>([^<]+)</a>'
)

#: RS(지수대비 상대강도) 분모 지수 심볼(yfinance). KR=KOSPI, US=S&P500.
#: pykrx 지수는 데이터센터 IP 에서 KRX 403 → 양 시장 모두 yfinance 로 일원화.
_INDEX_SYMBOL: dict[Market, str] = {"KR": "^KS11", "US": "^GSPC"}

#: yfinance(Yahoo) 429 백오프 — 1·2·4·8·16초, 최대 5회. (FIX-C)
_yf_retry = retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    reraise=True,
)

#: US '거래대금 상위' 근사 — 유동성 큰 정적 화이트리스트(S&P 500 + 주요 대형주).
#: 대략 대형주·고유동성 순으로 정렬해 ``head(N)`` 이 가장 유동성 높은 N 을 주도록 한다.
#: (US 거래대금 순위 무료 단일소스 부재 → 대형주 화이트리스트가 안전한 근사.)
_US_LIQUID: tuple[str, ...] = (
    # 메가캡·초고유동성
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "GOOG",
    "META",
    "TSLA",
    "AVGO",
    "BRK-B",
    "LLY",
    "JPM",
    "V",
    "XOM",
    "UNH",
    "MA",
    "JNJ",
    "PG",
    "HD",
    "COST",
    "ORCL",
    "MRK",
    "ABBV",
    "CVX",
    "AMD",
    "NFLX",
    "KO",
    "ADBE",
    "PEP",
    "BAC",
    "CRM",
    "TMO",
    "WMT",
    "ACN",
    "MCD",
    "LIN",
    "ABT",
    "CSCO",
    "DHR",
    "WFC",
    "INTC",
    "DIS",
    "QCOM",
    "TXN",
    "VZ",
    "INTU",
    "AMGN",
    "CAT",
    "IBM",
    "NOW",
    "PM",
    "GE",
    "SPGI",
    "UNP",
    "AMAT",
    "ISRG",
    "HON",
    "RTX",
    "NEE",
    "PFE",
    "GS",
    "LOW",
    "BKNG",
    "UBER",
    "T",
    "ELV",
    "PLD",
    "SYK",
    "BLK",
    "AXP",
    "TJX",
    "MDT",
    "C",
    "VRTX",
    "PGR",
    "LRCX",
    "SCHW",
    "BSX",
    "MS",
    "REGN",
    "CB",
    "ADP",
    "MU",
    "ETN",
    "CI",
    "MMC",
    "ZTS",
    "DE",
    "BMY",
    "FI",
    "SO",
    "BX",
    "MO",
    "CMG",
    "ADI",
    "KLAC",
    "DUK",
    "ANET",
    "SHW",
    "ICE",
    "WM",
    "SNPS",
    "GD",
    "CDNS",
    "TT",
    "CL",
    "PYPL",
    "EQIX",
    "APH",
    "PH",
    "AON",
    "MCK",
    "ITW",
    "CME",
    "MSI",
    "PNC",
    "USB",
    "NOC",
    "FDX",
    "CSX",
    "EOG",
    "MPC",
    "ORLY",
    "MAR",
    "CARR",
    "ECL",
    "EMR",
    "ROP",
    "AJG",
    "PSX",
    "WELL",
    "NXPI",
    "SLB",
    "HCA",
    "TGT",
    "MMM",
    "AFL",
    "TFC",
    "FCX",
    "TDG",
    "DXCM",
    "CPRT",
    "AIG",
    "GM",
    "MET",
    "PCAR",
    "OKE",
    "SPG",
    "NSC",
    "ABNB",
    "AZO",
    "GEV",
    "AMP",
    "TRV",
    "DHI",
    "KMB",
    "URI",
    "O",
    "BK",
    "VLO",
    "PSA",
    "F",
    "PWR",
    "D",
    "GWW",
    "CMI",
    "COF",
    "HLT",
    "AEP",
    "JCI",
    "ROST",
    "FIS",
    "MSCI",
    "SRE",
    "ALL",
    "FTNT",
    "LHX",
    "OXY",
    "FAST",
    "PRU",
    "PAYX",
    "KMI",
    "IDXX",
    "CTAS",
    "DOW",
    "KVUE",
    "VRSK",
    "A",
    "EW",
    "CCI",
    "GIS",
    "ODFL",
    "KR",
    "EXC",
    "HUM",
    "KHC",
    "DD",
    "YUM",
    "GEHC",
    "BKR",
    "ACGL",
    "MCHP",
    "NUE",
    "MNST",
    "EA",
    "CTSH",
    "HES",
    "IR",
    "LEN",
    "PEG",
    "OTIS",
    "RCL",
    "XEL",
    "CSGP",
    "STZ",
    "DAL",
    "FANG",
    "VICI",
    "MLM",
    "WAB",
    "CNC",
    "ON",
    "VMC",
    "DG",
    "EFX",
    "ROK",
    "TSCO",
    "WEC",
    "HPQ",
    "GLW",
    "AVB",
    "AME",
    "WTW",
    "KDP",
    "ED",
    "MPWR",
    "TTWO",
    "CAH",
    "DLR",
    "FICO",
    "CBRE",
    "DFS",
    "HSY",
    "EL",
    "BRO",
    "MTD",
    "ANSS",
    "WMB",
    "EBAY",
    "RMD",
    "ZBH",
    "CHTR",
    "PPG",
    "TROW",
    "DELL",
    "EXR",
    "AWK",
    "STT",
    "GPN",
    "NVR",
    "VLTO",
    "IQV",
    "K",
    "WST",
    "HIG",
    "FITB",
    "ULTA",
    "KEYS",
    "TDY",
    "GRMN",
    "DOV",
    "MOH",
    "BR",
    "STE",
    "PHM",
    "VTR",
    "WDC",
    "RJF",
    "CDW",
    "HWM",
    "EQR",
    "ADM",
    "WRB",
    "DTE",
    "TYL",
    "HPE",
    "NTAP",
    "PPL",
    "SBAC",
    "CTRA",
    "TER",
    "FE",
    "ES",
    "BIIB",
    "GPC",
    "HBAN",
    "WAT",
    "INVH",
    "LYB",
    "MKC",
    "STLD",
    "BLDR",
    "VRSN",
    "ETR",
    "IFF",
    "RF",
    "CMS",
    "DRI",
    "WBD",
    "STX",
    "PFG",
    "MTB",
    "BAX",
    "LDOS",
    "DGX",
    "FSLR",
    "EXPE",
    "NDAQ",
    "COO",
    "CFG",
    "AEE",
    "TSN",
    "LH",
    "ZBRA",
    "PKG",
    "ATO",
    "JBHT",
    "CINF",
    "WY",
    "MAA",
    "ON",
    "TXT",
    "CNP",
    "FDS",
    "VTRS",
    "SWKS",
    "EXPD",
    "MAS",
    "CLX",
    "L",
    "BBY",
    "CE",
    "DPZ",
    "OMC",
    "AVY",
    "AKAM",
    "NTRS",
    "ALGN",
    "POOL",
    "ESS",
    "EG",
    "WBA",
    "ROL",
    "SNA",
    "JBL",
    "HOLX",
    "BG",
    "SWK",
    "IP",
    "UAL",
    "DVN",
    "TRGP",
    "GEN",
    "MRNA",
    "LNT",
    "KEY",
    "RVTY",
    "EVRG",
    "NRG",
    "AMCR",
    "TPR",
    "PNR",
    "JKHY",
    "INCY",
    "KIM",
    "CAG",
    "UDR",
    "BALL",
    "DOC",
    "APTV",
    "REG",
    "CPT",
    "NI",
    "MGM",
    "EMN",
    "JNPR",
    "TFX",
    "CHRW",
    "ARE",
    "FFIV",
    "ALLE",
    "HST",
    "PODD",
    "SOLV",
    "AES",
    "BXP",
    "HRL",
    "WRK",
    "LKQ",
    "DAY",
)


class LiveProviderError(RuntimeError):
    """라이브 조회 실패(키 없음·HTTP 오류·파싱 불가). 엔진이 per-ticker 로 흡수."""


class LiveProvider:
    """라이브 Provider — KIS 국내(httpx) + yfinance 미국.

    KIS 키는 ``Settings`` 에서만 읽고 하드코딩하지 않는다. 레이트리밋·재시도는
    ``tenacity`` 백오프로 처리한다. 키 없거나 호출 실패 시 ``LiveProviderError``.
    """

    def __init__(self, settings: Settings, daily_cache: DailyCache | None = None) -> None:
        self._settings = settings
        self._base = _KIS_DOMAINS[settings.kis_mode]
        self._client = httpx.Client(base_url=self._base, timeout=10.0)
        self._token: str | None = None
        self._token_exp: datetime | None = None
        #: 토큰 발급 경쟁 차단(동시성에서 중복 발급 방지). httpx.Client 자체는 스레드세이프.
        self._token_lock = threading.Lock()
        #: 유니버스 인스턴스 1회 캐시(전 종목 조회는 무거우므로 — market → tickers).
        self._universe_cache: dict[Market, list[str]] = {}
        #: inquire-price 단기 TTL 캐시 — ticker → (조회시각, output dict). quote·fundamentals 공유.
        self._price_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        #: 일봉·펀더멘털 일1회 캐시(FIX-C). 미주입 시 db_path 로 생성(싱글턴이라 1회).
        self._daily = daily_cache if daily_cache is not None else DailyCache(settings.db_path)
        #: yfinance 공유 requests 세션(연결 재사용 → Yahoo 부하·핸드셰이크 절감). lazy.
        self._yf_session: Any | None = None
        #: KR 한글 종목명 캐시(ticker→name). KIS inquire-price 가 hts_kor_isnm 을 주지
        #: 않아 pykrx 로 해석 → 인스턴스 1회 캐시(매 스캔 재조회 방지).
        self._kr_names: dict[str, str] = {}

    # ── 유니버스/이름 ─────────────────────────────────────────────────

    def list_universe(self, market: Market) -> list[str]:
        """'거래대금 상위 N 종목'(시장별) — 유동성 상위만 스캔해 속도 확보.

        - KR: 네이버 금융 시가총액 순위(KRX 비의존)로 KOSPI∪KOSDAQ 상위 N 티커(일1회 캐시).
        - US: 유동성 큰 정적 화이트리스트(S&P 500 + 대형주)에서 상위 N(거래대금 순위
          무료 단일소스 부재 → 대형주 근사).
        - 둘 다 비면 ``_universe_from_themes`` 로 graceful fallback(운영자 큐레이션).

        전 종목 조회는 무거우므로 **성공 결과만** 인스턴스 캐시한다. 폴백(빈 결과)은
        캐시하지 않아, FDR 일시 throttle 회복 시 다음 스캔이 자동으로 재시도·정상화한다.
        """
        if market in self._universe_cache:
            return self._universe_cache[market]
        fetched = self._fetch_universe_kr() if market == "KR" else self._fetch_universe_us()
        if fetched:
            self._universe_cache[market] = fetched
            return fetched
        # 폴백은 캐시 금지(고착 방지) — 다음 호출에 _fetch 재시도(디스크 캐시·소스 회복 활용).
        return _universe_from_themes(self._settings.themes_path, market)

    def _fetch_universe_kr(self) -> list[str]:
        """네이버 시총 상위 N 티커 (일1회 디스크 캐시). 실패 시 빈 리스트(→ themes fallback).

        pykrx·FDR 의 KRX(``data.krx.co.kr``) 소스가 throttle/차단으로 불안정 → **KRX 비의존**
        네이버 금융 시가총액 순위로 enumeration. **종목명도 같은 페이지에서 얻어** ``_kr_names``
        에 채운다(KRX/pykrx 비의존 표시명). 캐시는 ``{코드: 종목명}`` 매핑으로 저장해 캐시 적중
        (재시작 등)에도 표시명을 복원한다. 실패는 빈 리스트로 흡수 → themes.yml graceful degrade.
        """
        today = _market_today("KR")
        cached = self._daily.get("KR", _UNIVERSE_SENTINEL, today, _CACHE_UNIVERSE)
        if cached:
            loaded = json.loads(cached)
            if isinstance(loaded, dict) and loaded:  # {코드: 종목명}
                self._absorb_names(loaded.items())
                return [str(code) for code in loaded]
            if isinstance(loaded, list) and loaded:  # 구형식(코드만) 하위호환
                return [str(t) for t in loaded]
        pairs = self._naver_universe_kr()
        if pairs:
            self._absorb_names(pairs)
            self._daily.put(
                "KR",
                _UNIVERSE_SENTINEL,
                today,
                _CACHE_UNIVERSE,
                json.dumps(dict(pairs), ensure_ascii=False),
            )
            return [code for code, _ in pairs]
        return []

    def _absorb_names(self, pairs: Iterable[tuple[str, str]]) -> None:
        """(코드, 종목명) 쌍을 ``_kr_names`` 에 채운다(빈 이름은 건너뜀, 기존값 보존)."""
        for code, name in pairs:
            if name:
                self._kr_names.setdefault(str(code), str(name))

    def _naver_universe_kr(self) -> list[tuple[str, str]]:
        """네이버 금융 시가총액 순위 → KOSPI∪KOSDAQ (종목코드, 종목명) 쌍. 실패는 빈 리스트.

        ``live_universe_top_n`` 을 KOSPI:KOSDAQ ≈ 2:1 로 배분(대형주 KOSPI 편중). 각 시장
        시총 내림차순 페이지(50종목)에서 코드+종목명을 추출·중복 제거(코드 기준)한다.
        유동성은 점수 단계의 거래대금 하드필터가 거른다.
        """
        top_n = self._settings.live_universe_top_n
        kospi_quota = top_n * 2 // 3
        try:
            rows = self._naver_market_rows(0, kospi_quota)
            rows += self._naver_market_rows(1, top_n - kospi_quota)
        except (httpx.HTTPError, OSError):
            logger.warning("네이버 유니버스 조회 실패 — themes.yml fallback", exc_info=True)
            return []
        seen: set[str] = set()
        out: list[tuple[str, str]] = []
        for code, name in rows:
            if code not in seen:
                seen.add(code)
                out.append((code, name))
        return out

    def _naver_market_rows(self, sosok: int, quota: int) -> list[tuple[str, str]]:
        """네이버 시총순위(``sosok``: KOSPI=0/KOSDAQ=1) 상위 ``quota`` (종목코드, 종목명)."""
        pages = -(-quota // _NAVER_PAGE_SIZE)  # ceil
        out: list[tuple[str, str]] = []
        for page in range(1, pages + 1):
            text = self._naver_fetch(_NAVER_MARKET_SUM.format(sosok=sosok, page=page))
            for code, name in _NAVER_ROW_RE.findall(text):
                out.append((code, html.unescape(name).strip()))
        return out[:quota]

    def _naver_fetch(self, url: str) -> str:
        """네이버 페이지 HTML(httpx). 실패는 예외 전파(상위가 흡수). 테스트 seam."""
        resp = httpx.get(url, headers={"User-Agent": _NAVER_UA}, timeout=15.0)
        resp.raise_for_status()
        return resp.text

    def _fetch_universe_us(self) -> list[str]:
        """유동성 큰 정적 화이트리스트에서 상위 N(거래대금 상위 근사).

        US 거래대금 순위 무료 단일소스가 없어 S&P 500 + 주요 대형주 화이트리스트
        (``_US_LIQUID``)를 '거래대금 상위'의 안전한 근사로 쓴다. 정적이라 실패 없음;
        그래도 빈 경우(상수 변형) themes fallback 이 받친다.

        상한은 ``live_universe_top_n_us``(기본 30) — yfinance(Yahoo)가 다종목 조회 시
        429 로 막으므로 소수 대형주만 스캔한다.
        """
        return list(_US_LIQUID[: self._settings.live_universe_top_n_us])

    def get_name(self, ticker: str, market: Market) -> str:
        """종목명. US=yfinance shortName, KR=pykrx(인스턴스 캐시).

        KIS inquire-price 는 ``hts_kor_isnm``(한글 종목명)을 주지 않아(업종 bstp_kor_isnm
        만 제공) KR 은 이미 의존성인 pykrx 로 해석한다.
        """
        if market == "US":
            info = self._yf_info(ticker)
            name = info.get("shortName") or info.get("longName")
            return str(name) if name else ticker
        return self._kr_name(ticker) or ticker

    def _kr_name(self, ticker: str) -> str | None:
        """pykrx 한글 종목명(인스턴스 캐시). 미설치/조회 실패는 흡수 → None(호출부 ticker 폴백)."""
        if ticker in self._kr_names:
            return self._kr_names[ticker] or None
        name = ""
        try:
            from pykrx import stock

            name = (stock.get_market_ticker_name(ticker) or "").strip()
        except Exception:  # pykrx 미설치/ARM·네트워크 실패는 비핵심 → 흡수
            logger.debug("pykrx 종목명 조회 실패(흡수): %s", ticker)
        self._kr_names[ticker] = name
        return name or None

    # ── OHLCV ─────────────────────────────────────────────────────────

    def get_daily_ohlcv(self, ticker: str, market: Market, days: int) -> list[OHLCVRow]:
        """KIS 국내 일봉 / yfinance 일봉 (날짜 오름차순). 일1회 캐시(FIX-C).

        같은 날 같은 종목의 일봉은 ``DailyCache``(market+ticker+오늘) 에서 읽어 네트워크를
        타지 않는다. 캐시에는 충분한 길이(엔진 최대 요청분)를 보관하고, ``days`` 만큼 슬라이스.
        """
        cached = self._cached_ohlcv(ticker, market)
        rows = cached if cached is not None else self._fetch_and_cache_ohlcv(ticker, market, days)
        return rows[-days:] if days < len(rows) else rows

    def get_index_ohlcv(self, market: Market, days: int) -> list[OHLCVRow]:
        """시장 지수(KR=KOSPI ^KS11, US=S&P500 ^GSPC) 일봉 — RS 분모. yfinance + 일1회 캐시.

        KIS 일봉 API 는 종목용이라 지수는 yfinance 로 받는다(curl_cffi 임퍼소네이션이
        Yahoo 429 회피). 종목 일봉과 동일하게 ``DailyCache`` 에 일1회 캐시(30분 스캔마다
        재조회 방지). 실패는 호출 측(엔진 ``_index_momentum``)이 흡수 → RS 중립.
        """
        symbol = _INDEX_SYMBOL[market]
        cached = self._daily.get(market, symbol, _market_today(market), _CACHE_INDEX)
        if cached is not None:
            rows = _OHLCVList.model_validate_json(cached).rows
        else:
            rows = self._yf_ohlcv(symbol, days)
            self._daily.put(
                market,
                symbol,
                _market_today(market),
                _CACHE_INDEX,
                _OHLCVList(rows=rows).model_dump_json(),
            )
        return rows[-days:] if days < len(rows) else rows

    def _cached_ohlcv(self, ticker: str, market: Market) -> list[OHLCVRow] | None:
        """오늘자 일봉 캐시 적중 시 전체 길이 리스트, 미스면 None."""
        raw = self._daily.get(market, ticker, _market_today(market), _CACHE_OHLCV)
        if raw is None:
            return None
        return _OHLCVList.model_validate_json(raw).rows

    def _store_ohlcv(self, ticker: str, market: Market, rows: list[OHLCVRow]) -> None:
        """일봉 리스트를 오늘자 캐시에 저장."""
        payload = _OHLCVList(rows=rows).model_dump_json()
        self._daily.put(market, ticker, _market_today(market), _CACHE_OHLCV, payload)

    def _fetch_and_cache_ohlcv(self, ticker: str, market: Market, days: int) -> list[OHLCVRow]:
        """네트워크에서 일봉을 받아 캐시에 저장하고 반환(캐시 미스 경로)."""
        rows = self._yf_ohlcv(ticker, days) if market == "US" else self._kis_ohlcv(ticker, days)
        self._store_ohlcv(ticker, market, rows)
        return rows

    def _yf_ohlcv(self, ticker: str, days: int) -> list[OHLCVRow]:
        # 거래일 < 달력일 → 여유 있게 1.6배 + 5 의 기간을 요청.
        period_days = int(days * 1.6) + 5
        try:
            hist = self._yf_history(ticker, period_days)
        except Exception as exc:  # 네트워크/라이브러리 오류를 계약 예외로 변환
            raise LiveProviderError(f"yfinance history 실패: {ticker}") from exc
        rows = _rows_from_yf_frame(hist)
        if not rows:
            raise LiveProviderError(f"yfinance 빈 결과: {ticker}")
        return rows

    @_yf_retry
    def _yf_history(self, ticker: str, period_days: int) -> Any:
        """yfinance 단일 종목 history (429 백오프). 공유 세션 사용."""
        import yfinance as yf

        return yf.Ticker(ticker, session=self._session()).history(
            period=f"{period_days}d", auto_adjust=False
        )

    def _kis_ohlcv(self, ticker: str, days: int) -> list[OHLCVRow]:
        # KIS inquire-daily-itemchartprice(FHKST03010100) 는 날짜 범위를 줘도 호출당
        # 최대 ~100봉만 반환한다. days(MA200=200·1년수익률=252 충족 위해 보통 280)를
        # 채우려면 윈도우를 과거로 옮기며 페이지네이션해 누적한다. (일1회 prep 경로라
        # 종목당 ~3회 호출.) 단일 호출만 하면 ~100봉만 들어와 MA200/1년수익률이 None.
        window_cal_days = 150  # 한 윈도우 ≈ 100 거래일 (KIS 100봉 cap 이하)
        max_pages = 8  # 안전 상한(≈800 거래일) — 무한루프 방지
        end_dt = datetime.now(tz=UTC).date()
        seen: dict[date, OHLCVRow] = {}
        for _ in range(max_pages):
            start_dt = end_dt - timedelta(days=window_cal_days)
            data = self._kis_get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                tr_id="FHKST03010100",
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": ticker,
                    "FID_INPUT_DATE_1": start_dt.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": end_dt.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
                },
            )
            page: list[OHLCVRow] = []
            for rec in data.get("output2") or []:
                if not rec.get("stck_bsop_date"):
                    continue
                page.append(
                    OHLCVRow(
                        date=datetime.strptime(rec["stck_bsop_date"], "%Y%m%d")
                        .replace(tzinfo=UTC)
                        .date(),
                        open=_d(rec["stck_oprc"]),
                        high=_d(rec["stck_hgpr"]),
                        low=_d(rec["stck_lwpr"]),
                        close=_d(rec["stck_clpr"]),
                        volume=_d(rec["acml_vol"]),
                    )
                )
            if not page:
                break
            before = len(seen)
            for row in page:
                seen[row.date] = row  # 날짜 dedup
            # 충분히 모았거나, 새 봉이 더 없으면(더 과거 데이터 없음/중복 페이지) 종료.
            if len(seen) >= days or len(seen) == before:
                break
            end_dt = min(r.date for r in page) - timedelta(days=1)  # 윈도우 과거로
        if not seen:
            raise LiveProviderError(f"KIS 일봉 빈 결과: {ticker}")
        rows = sorted(seen.values(), key=lambda r: r.date)  # 오름차순
        return rows[-days:] if days < len(rows) else rows

    # ── 시세 ──────────────────────────────────────────────────────────

    def get_quote(self, ticker: str, market: Market) -> Quote:
        """KIS 국내 현재가 / yfinance 시세. 시세 글리치 가드 포함."""
        if market == "US":
            return self._yf_quote(ticker)
        return self._kis_quote(ticker)

    def _yf_quote(self, ticker: str) -> Quote:
        info = self._yf_fast_info(ticker)
        try:
            price = _d(info["last_price"])
            prev = _d(info.get("previous_close")) if info.get("previous_close") else None
            opn = _d(info.get("open")) if info.get("open") else None
            vol = _d(info.get("last_volume")) if info.get("last_volume") else None
        except (KeyError, TypeError) as exc:
            raise LiveProviderError(f"yfinance fast_info 파싱 실패: {ticker}") from exc
        turnover = price * vol if vol is not None else None
        return Quote(
            price=price,
            open=opn,
            prev_close=prev,
            volume=vol,
            turnover=turnover,
            asof=datetime.now(tz=UTC),
        )

    def _kis_quote(self, ticker: str) -> Quote:
        quote = self._kis_quote_once(ticker)
        # 시세 글리치 가드 — 전일 종가 대비 ±50% 밖이면 1회 재조회(캐시 우회로 강제).
        if quote.prev_close and quote.prev_close > _DEC0:
            ratio = quote.price / quote.prev_close
            if ratio < _GLITCH_LOW or ratio > _GLITCH_HIGH:
                quote = self._kis_quote_once(ticker, force=True)
                # FIX-D: 재조회 후에도 ±50% 밖이면 글리치로 보고 스킵(무조건 채택 금지).
                if quote.prev_close and quote.prev_close > _DEC0:
                    ratio2 = quote.price / quote.prev_close
                    if ratio2 < _GLITCH_LOW or ratio2 > _GLITCH_HIGH:
                        raise LiveProviderError(
                            f"KIS 시세 글리치 지속(전일 대비 {ratio2:.2f}x): {ticker}"
                        )
        return quote

    def _kis_inquire_price(self, ticker: str, *, force: bool = False) -> dict[str, Any]:
        """KIS inquire-price(FHKST01010100) 단일 진입점 — 단기 TTL 캐시로 중복 제거.

        quote·fundamentals 가 같은 호출을 공유한다(KR 종목당 2콜→1콜). ``force`` 면 캐시를
        무시하고 재조회한다(글리치 재조회 경로). 스레드 경쟁은 무해(마지막 쓰기 승) — Lock 불필요.
        """
        now = datetime.now(tz=UTC)
        if not force:
            cached = self._price_cache.get(ticker)
            if cached is not None and now - cached[0] < _PRICE_TTL:
                return cached[1]
        data = self._kis_get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
        )
        out: dict[str, Any] = data.get("output") or {}
        self._price_cache[ticker] = (now, out)
        return out

    def _kis_quote_once(self, ticker: str, *, force: bool = False) -> Quote:
        out = self._kis_inquire_price(ticker, force=force)
        try:
            price = _d(out["stck_prpr"])
            opn = _d(out["stck_oprc"]) if out.get("stck_oprc") else None
            prev = _d(out["stck_sdpr"]) if out.get("stck_sdpr") else None
            vol = _d(out["acml_vol"]) if out.get("acml_vol") else None
            turnover = _d(out["acml_tr_pbmn"]) if out.get("acml_tr_pbmn") else None
        except (KeyError, TypeError) as exc:
            raise LiveProviderError(f"KIS 현재가 파싱 실패: {ticker}") from exc
        return Quote(
            price=price,
            open=opn,
            prev_close=prev,
            volume=vol,
            turnover=turnover,
            asof=datetime.now(tz=UTC),
        )

    # ── 펀더멘털 ──────────────────────────────────────────────────────

    def get_fundamentals(self, ticker: str, market: Market) -> Fundamentals:
        """KIS 밸류에이션 / yfinance info·fast_info.

        US 펀더멘털(``.info`` 무거움 → Yahoo 429 주범)은 일1회 캐시(FIX-C). KR 은 현재가
        응답(inquire-price)과 호출을 공유하므로 별도 일캐시 없이 그 단기 TTL 캐시를 쓴다.
        """
        if market == "US":
            cached = self._daily.get(market, ticker, _market_today(market), _CACHE_FUND)
            if cached is not None:
                return Fundamentals.model_validate_json(cached)
            fund = self._yf_fundamentals(ticker)
            self._daily.put(
                market, ticker, _market_today(market), _CACHE_FUND, fund.model_dump_json()
            )
            return fund
        return self._kis_fundamentals(ticker)

    def _yf_fundamentals(self, ticker: str) -> Fundamentals:
        info = self._yf_info(ticker)
        fast = self._yf_fast_info(ticker)

        def opt(key: str, src: dict[str, Any] = info) -> Decimal | None:
            val = src.get(key)
            return _d(val) if val is not None else None

        # 1년수익률 — yfinance 키가 버전에 따라 '52WeekChange' 또는 'fiftyTwoWeekChange'.
        # 둘 다 시도, 결측이면 None(엔진이 OHLCV 로 폴백). (FIX-C)
        chg = info.get("52WeekChange")
        if chg is None:
            chg = info.get("fiftyTwoWeekChange")
        return_1y = _d(chg) * Decimal("100") if chg is not None else None
        return Fundamentals(
            market_cap=opt("market_cap", fast) or opt("marketCap"),
            per=opt("trailingPE"),
            pbr=opt("priceToBook"),
            eps=opt("trailingEps"),
            w52_high=opt("year_high", fast) or opt("fiftyTwoWeekHigh"),
            w52_low=opt("year_low", fast) or opt("fiftyTwoWeekLow"),
            return_1y_pct=return_1y,
            sector=info.get("sector"),
            industry=info.get("industry"),
            name=info.get("shortName") or info.get("longName"),
        )

    def _kis_fundamentals(self, ticker: str) -> Fundamentals:
        # 현재가 응답(output)에 시총·PER·52주고저가 함께 온다 — quote 와 같은 호출 공유(중복 제거).
        out = self._kis_inquire_price(ticker)

        def opt(key: str) -> Decimal | None:
            val = out.get(key)
            if val in (None, ""):
                return None
            return _d(val)

        def opt_any(*keys: str) -> Decimal | None:
            """후보 키들을 순서대로 시도(KIS 응답 키 표기 차이 보강)."""
            for key in keys:
                val = opt(key)
                if val is not None:
                    return val
            return None

        # 시총 단위: KIS hts_avls 는 '억원' → 원 환산.
        # TODO(KIS Developers 확정 필요): hts_avls 단위(억원 가정)를 응답 문서로 검증할 것.
        #   계정/엔드포인트에 따라 '원' 또는 '백만원'일 가능성 — 단위 오인 시 시총 100배 오차.
        cap_eok = opt("hts_avls")
        market_cap = cap_eok * Decimal("100000000") if cap_eok is not None else None
        return Fundamentals(
            market_cap=market_cap,
            per=opt("per"),
            pbr=opt("pbr"),
            eps=opt("eps"),
            # FIX-F: 52주 고/저가 키 보강 — w52_hgpr 우선, 표기 차이(stck_dryy_hgpr) 폴백.
            w52_high=opt_any("w52_hgpr", "stck_dryy_hgpr"),
            w52_low=opt_any("w52_lwpr", "stck_dryy_lwpr"),
            # TODO(KIS): 1년수익률 전용 필드 부재 → 엔진이 OHLCV 로 계산(여기선 None).
            return_1y_pct=None,
            sector=None,
            industry=out.get("bstp_kor_isnm") or None,
            name=out.get("hts_kor_isnm") or None,
        )

    # ── 투자자별 매매 ─────────────────────────────────────────────────

    def get_investor_flow(self, ticker: str) -> InvestorFlow | None:
        """KIS 투자자별 매매동향 — 매수·매도·순매수 **거래대금(KRW)** (KR 전용).

        FIX-A(실증): ``inquire-investor``(FHKST01010900) 응답 ``output`` 은 최근 ~30
        거래일 리스트다. **최신일(``output[0]``)은 값이 빈 문자열("")로 미정산** 이므로
        ``frgn_ntby_tr_pbmn`` 이 ""/"0" 이 아닌 **첫 정산 행**을 골라 사용한다.

        필드(전부 원/KRW, 빈값은 ""):
        - ``{frgn|orgn|prsn}_shnu_tr_pbmn`` = 매수 거래대금
        - ``{frgn|orgn|prsn}_seln_tr_pbmn`` = 매도 거래대금
        - ``{frgn|orgn|prsn}_ntby_tr_pbmn`` = 순매수 거래대금

        외국인=frgn, 기관=orgn, 개인=prsn 동일 매핑. ``Decimal("")`` 금지 → ``_amount``
        헬퍼로 빈값/부재는 ``None``. 정산 행이 하나도 없으면 ``None``. ``flow_date`` 는
        고른 행의 ``stck_bsop_date``. US 심볼 형태면 KIS 호출 전 ``None``.
        """
        if not (len(ticker) == 6 and ticker.isdigit()):
            return None
        data = self._kis_get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            tr_id="FHKST01010900",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
        )
        rows = data.get("output") or []
        rec = _first_settled_flow_row(rows)
        if rec is None:
            return None

        def amt(key: str) -> Decimal | None:
            """매매금 필드 → 원. KIS ``tr_pbmn`` 은 **백만원** 단위라 1e6 을 곱한다.

            빈문자/부재면 None (``Decimal("")`` 금지).
            """
            raw = _amount(rec.get(key))
            return raw * _INVESTOR_TR_PBMN_UNIT if raw is not None else None

        bsop = rec.get("stck_bsop_date")
        flow_date = (
            datetime.strptime(bsop, "%Y%m%d").replace(tzinfo=UTC).date()
            if bsop
            else datetime.now(tz=UTC).date()
        )
        return InvestorFlow(
            date=flow_date,
            foreign_net=amt("frgn_ntby_tr_pbmn") or _DEC0,
            institution_net=amt("orgn_ntby_tr_pbmn") or _DEC0,
            individual_net=amt("prsn_ntby_tr_pbmn") or _DEC0,
            foreign_buy=amt("frgn_shnu_tr_pbmn"),
            foreign_sell=amt("frgn_seln_tr_pbmn"),
            institution_buy=amt("orgn_shnu_tr_pbmn"),
            institution_sell=amt("orgn_seln_tr_pbmn"),
            individual_buy=amt("prsn_shnu_tr_pbmn"),
            individual_sell=amt("prsn_seln_tr_pbmn"),
        )

    # ── 일봉 캐시 워밍(prep) ───────────────────────────────────────────

    def prepare_daily(self, tickers: list[str], market: Market) -> None:
        """일봉 캐시 워밍(일1회 prep) — US 는 ``yf.download`` **배치**로 일괄 수집(FIX-C).

        US ~300종목을 종목당 ``.history`` 로 받으면 Yahoo 429. 대신 ``yf.download`` 한 번으로
        전 종목 일봉을 받아 ``DailyCache`` 에 채운다. 이후 intraday 의 ``get_daily_ohlcv`` 는
        캐시 적중으로 네트워크를 타지 않는다. 429 등 부분 실패는 흡수(빈 종목은 캐시 미적재 →
        intraday 가 per-ticker 폴백). KR 은 KIS(429 무관)라 별도 배치 없이 캐시만 채운다.
        """
        if market == "US":
            self._warm_us_daily(tickers)
            return
        # KR: KIS 는 Yahoo 429 무관 + 벌크 일봉 API 부재 → 종목별로 캐시만 워밍(실패는 흡수).
        for ticker in tickers:
            if self._cached_ohlcv(ticker, market) is not None:
                continue
            try:
                self._fetch_and_cache_ohlcv(ticker, market, self._prep_days())
            except (LiveProviderError, ValueError, KeyError):
                logger.warning("KR 일봉 prep 실패(흡수): %s", ticker)

    @staticmethod
    def _prep_days() -> int:
        """prep 시 캐시에 보관할 일봉 길이 — 1년치+여유(엔진 최대 요청분 충족)."""
        return 280

    def _warm_us_daily(self, tickers: list[str]) -> None:
        """US 일봉 배치 수집 → 종목별 캐시 적재 (``yf.download`` 1콜)."""
        if not tickers:
            return
        # 캐시에 이미 오늘자가 있는 종목은 제외(중복 배치 방지).
        pending = [t for t in tickers if self._cached_ohlcv(t, "US") is None]
        if not pending:
            return
        period_days = int(self._prep_days() * 1.6) + 5
        try:
            data = self._yf_download(pending, period_days)
        except Exception:  # 배치 실패는 흡수 — intraday 가 per-ticker 로 폴백
            logger.warning("US 일봉 배치(yf.download) 실패 — per-ticker 폴백", exc_info=True)
            return
        for ticker in pending:
            frame = _extract_ticker_frame(data, ticker)
            rows = _rows_from_yf_frame(frame)
            if rows:
                self._store_ohlcv(ticker, "US", rows)

    @_yf_retry
    def _yf_download(self, tickers: list[str], period_days: int) -> Any:
        """yfinance 배치 다운로드(429 백오프). 종목당 ``.history`` 금지 — 1콜로 일괄."""
        import yfinance as yf

        return yf.download(
            tickers,
            period=f"{period_days}d",
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            progress=False,
            session=self._session(),
        )

    # ── KIS 저수준 ────────────────────────────────────────────────────

    def _ensure_token(self) -> str:
        """OAuth 토큰 확보 — 메모리 캐시 → 디스크 → 신규 발급 (FIX-B).

        KIS 는 토큰 발급을 약 1분당 1회로 제한(초과 시 403). 재시작·다중 프로세스가
        매번 새 토큰을 받지 않도록 우선순위를 둔다:
        1. 메모리 캐시(이 인스턴스가 이미 발급/적재) — 만료 60초 전이면 유효.
        2. 디스크 캐시(``kis_token_path``) — 만료 60초 전이면 유효, 메모리에 적재 후 재사용.
        3. 신규 발급 → 메모리+디스크에 기록.

        동시성: ``_token_lock`` 으로 직렬화 + 락 획득 후 재확인(double-checked).
        """
        now = datetime.now(tz=UTC)
        if self._token and self._token_exp and now < self._token_exp:
            return self._token
        if not (self._settings.kis_app_key and self._settings.kis_app_secret):
            raise LiveProviderError("KIS 키 미설정 (KIS_APP_KEY/KIS_APP_SECRET)")
        with self._token_lock:
            now = datetime.now(tz=UTC)
            if self._token and self._token_exp and now < self._token_exp:
                return self._token
            # 디스크 캐시 우선 — 다른 프로세스/이전 실행이 받아둔 유효 토큰을 재사용(403 회피).
            disk = self._load_token_from_disk(now)
            if disk is not None:
                self._token, self._token_exp = disk
                return self._token
            try:
                payload = self._request_token()
            except httpx.HTTPError as exc:
                raise LiveProviderError("KIS 토큰 발급 실패") from exc
            token = payload.get("access_token")
            if not token:
                raise LiveProviderError("KIS 토큰 응답에 access_token 없음")
            ttl = int(payload.get("expires_in", 86400))
            self._token = str(token)
            self._token_exp = now + timedelta(seconds=max(ttl - 60, 60))
            self._save_token_to_disk(self._token, self._token_exp)
            return self._token

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def _request_token(self) -> dict[str, Any]:
        """KIS OAuth 토큰 POST — 403/HTTP 오류는 tenacity 백오프 후 ``LiveProviderError``.

        재시도는 일시적 403/네트워크 흔들림 흡수용(최대 3회). 토큰값은 로깅하지 않는다.
        """
        try:
            resp = self._client.post(
                "/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._settings.kis_app_key,
                    "appsecret": self._settings.kis_app_secret,
                },
            )
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise LiveProviderError("KIS 토큰 응답 JSON 파싱 실패") from exc
        return payload

    def _load_token_from_disk(self, now: datetime) -> tuple[str, datetime] | None:
        """디스크 토큰 캐시 적재 — 만료 60초 전이면 ``(token, expires_at)``, 아니면 ``None``.

        파일 부재·파싱 실패·만료 임박은 모두 ``None`` 으로 흡수(신규 발급 경로로 폴백).
        토큰 값은 절대 로깅하지 않는다(시크릿).
        """
        path = self._settings.kis_token_path
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw)
            token = data["access_token"]
            expires_at = datetime.fromisoformat(data["expires_at"])
        except (ValueError, KeyError, TypeError):
            return None
        if not token or expires_at.tzinfo is None:
            return None
        # 만료 60초 전이면 유효(시간 여유 — 사용 중 만료 방지).
        if now >= expires_at - timedelta(seconds=60):
            return None
        return str(token), expires_at

    def _save_token_to_disk(self, token: str, expires_at: datetime) -> None:
        """토큰을 디스크에 기록 — ``{access_token, expires_at(ISO)}`` (시크릿, 0600 권한).

        쓰기 실패(권한·디스크)는 무음 흡수(메모리 캐시로 동작 — 영속만 실패).
        토큰 값은 로깅하지 않는다.
        """
        path = self._settings.kis_token_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"access_token": token, "expires_at": expires_at.isoformat()})
            path.write_text(payload, encoding="utf-8")
            # 시크릿 — 소유자만 읽기/쓰기(POSIX). Windows 는 chmod 무시(무해).
            with contextlib.suppress(OSError):
                os.chmod(path, 0o600)
        except OSError:
            logger.warning("KIS 토큰 디스크 저장 실패 — 메모리 캐시로 계속", exc_info=True)

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def _kis_get(self, path: str, *, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        """KIS GET — 토큰·해시 헤더 부착 + 레이트리밋 백오프."""
        token = self._ensure_token()
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": self._settings.kis_app_key,
            "appsecret": self._settings.kis_app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        try:
            resp = self._client.get(path, headers=headers, params=params)
            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise LiveProviderError(f"KIS 응답 JSON 파싱 실패: {path}") from exc
        if str(body.get("rt_cd", "0")) not in ("0", ""):
            raise LiveProviderError(f"KIS 오류({body.get('msg_cd')}): {body.get('msg1')}")
        return body

    # ── yfinance 저수준 ───────────────────────────────────────────────

    def _session(self) -> Any:
        """yfinance 공유 세션(lazy) — curl_cffi 브라우저 임퍼소네이션으로 Yahoo 429 회피.

        Yahoo 는 데이터센터 IP 의 파이썬 ``requests`` UA 를 공격적으로 429 한다(OCI 등).
        curl_cffi 의 Chrome TLS 핑거프린트 임퍼소네이션은 실제 브라우저처럼 보여 429 를
        크게 줄인다(yfinance 공식 권장). 미설치 시 표준 ``requests`` 로 폴백.
        """
        if self._yf_session is None:
            try:
                from curl_cffi import requests as cffi_requests

                self._yf_session = cffi_requests.Session(impersonate="chrome")
            except Exception:  # curl_cffi 미설치/문제 → 표준 requests 폴백
                import requests

                self._yf_session = requests.Session()
        return self._yf_session

    def _yf_info(self, ticker: str) -> dict[str, Any]:
        """yfinance ``.info`` (429 백오프 후 실패는 ``LiveProviderError``). 공유 세션 사용."""
        try:
            return self._yf_info_raw(ticker)
        except Exception as exc:
            raise LiveProviderError(f"yfinance info 실패: {ticker}") from exc

    @_yf_retry
    def _yf_info_raw(self, ticker: str) -> dict[str, Any]:
        """yfinance ``.info`` 원천 호출(429 백오프). 예외는 그대로 전파(재시도 트리거)."""
        import yfinance as yf

        return dict(yf.Ticker(ticker, session=self._session()).info)

    def _yf_fast_info(self, ticker: str) -> dict[str, Any]:
        """yfinance ``.fast_info`` (429 백오프, 시세용). 공유 세션 사용.

        fast_info 는 속성 접근(snake_case)으로 읽는다 — ``_yf_quote``/``_yf_fundamentals``
        의 snake_case 키(last_price/market_cap/year_high/year_low)와 일치시킨다.
        """
        try:
            return self._yf_fast_info_raw(ticker)
        except Exception as exc:
            raise LiveProviderError(f"yfinance fast_info 실패: {ticker}") from exc

    @_yf_retry
    def _yf_fast_info_raw(self, ticker: str) -> dict[str, Any]:
        """yfinance ``.fast_info`` 원천 호출(429 백오프). 예외는 그대로 전파."""
        import yfinance as yf

        keys = (
            "last_price",
            "previous_close",
            "open",
            "day_high",
            "day_low",
            "last_volume",
            "market_cap",
            "year_high",
            "year_low",
            "shares",
        )
        fast = yf.Ticker(ticker, session=self._session()).fast_info
        return {k: getattr(fast, k, None) for k in keys}


#: LiveProvider 싱글턴 캐시 — (settings, provider). 동일 settings 면 한 인스턴스·한 토큰 재사용.
_live_provider_cache: tuple[Settings, LiveProvider] | None = None
_provider_lock = threading.Lock()


def get_provider(settings: Settings) -> MarketDataProvider:
    """``data_mode`` 에 따라 Provider 선택.

    - ``sample`` → ``SampleProvider`` (키 불필요, 매번 새 인스턴스 — 무상태·무비용).
    - 그 외(``live``) → ``LiveProvider`` **싱글턴**(FIX-B). 동일 ``settings`` 면 캐시된
      인스턴스를 재사용해 토큰·일봉캐시를 공유한다. 매 스캔/재시작마다 새 인스턴스를
      만들면 KIS 토큰을 매번 새로 발급(1분당 1회 제한 → 403)하므로 반드시 캐시한다.
    """
    if settings.data_mode == "sample":
        return SampleProvider()
    global _live_provider_cache
    with _provider_lock:
        cached = _live_provider_cache
        if cached is not None and cached[0] == settings:
            return cached[1]
        provider = LiveProvider(settings)
        _live_provider_cache = (settings, provider)
        return provider


__all__ = [
    "Fundamentals",
    "LiveProvider",
    "LiveProviderError",
    "MarketDataProvider",
    "Quote",
    "SampleProvider",
    "get_provider",
]
