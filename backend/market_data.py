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

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import httpx
import yaml
from pydantic import BaseModel, ConfigDict
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.config import Settings
from backend.schemas import InvestorFlow, Market, OHLCVRow

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

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

    def get_quote(self, ticker: str, market: Market) -> Quote:
        """``ticker`` 의 현재 시세 스냅샷."""
        ...

    def get_fundamentals(self, ticker: str, market: Market) -> Fundamentals:
        """``ticker`` 의 펀더멘털·통계."""
        ...

    def get_investor_flow(self, ticker: str) -> InvestorFlow | None:
        """``ticker`` 의 투자자별 매매(외국인/기관/개인). **KR 전용** — US 는 ``None``."""
        ...


# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------

_DEC0 = Decimal("0")


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

#: NASDAQ Trader 심볼덤프 — (URL, 심볼 컬럼명). 보통주 필터는 ``_parse_nasdaq_dump`` 가 수행.
#: nasdaqlisted=NASDAQ 상장, otherlisted=NYSE/AMEX 등(NASDAQ 외).
_NASDAQ_DUMPS: tuple[tuple[str, str], ...] = (
    ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "Symbol"),
    ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "ACT Symbol"),
)


def _parse_nasdaq_dump(text: str, symbol_col: str) -> list[str]:
    """NASDAQ Trader 심볼덤프(파이프 구분) → 보통주 심볼 리스트.

    - 헤더 1행으로 컬럼 인덱스를 잡고, 마지막 ``File Creation Time`` 푸터는 건너뛴다.
    - ``Test Issue == 'Y'``(테스트이슈) 제외, ``ETF == 'Y'``(ETF) 제외 → 보통주만.
    - 심볼이 비었거나 ``$``/``.`` 등 비보통주 표기를 포함하면 제외.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    header = lines[0].split("|")
    try:
        sym_idx = header.index(symbol_col)
    except ValueError:
        return []
    test_idx = header.index("Test Issue") if "Test Issue" in header else None
    etf_idx = header.index("ETF") if "ETF" in header else None
    out: list[str] = []
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue
        cols = line.split("|")
        if len(cols) <= sym_idx:
            continue
        symbol = cols[sym_idx].strip()
        if not symbol or "$" in symbol or "." in symbol:
            continue
        if test_idx is not None and len(cols) > test_idx and cols[test_idx].strip() == "Y":
            continue
        if etf_idx is not None and len(cols) > etf_idx and cols[etf_idx].strip() == "Y":
            continue
        out.append(symbol)
    return out


class LiveProviderError(RuntimeError):
    """라이브 조회 실패(키 없음·HTTP 오류·파싱 불가). 엔진이 per-ticker 로 흡수."""


class LiveProvider:
    """라이브 Provider — KIS 국내(httpx) + yfinance 미국.

    KIS 키는 ``Settings`` 에서만 읽고 하드코딩하지 않는다. 레이트리밋·재시도는
    ``tenacity`` 백오프로 처리한다. 키 없거나 호출 실패 시 ``LiveProviderError``.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base = _KIS_DOMAINS[settings.kis_mode]
        self._client = httpx.Client(base_url=self._base, timeout=10.0)
        self._token: str | None = None
        self._token_exp: datetime | None = None
        #: 유니버스 인스턴스 1회 캐시(전 종목 조회는 무거우므로 — market → tickers).
        self._universe_cache: dict[Market, list[str]] = {}

    # ── 유니버스/이름 ─────────────────────────────────────────────────

    def list_universe(self, market: Market) -> list[str]:
        """'조회 가능한 종목 전체'(FIX-B) — KR=KOSPI+KOSDAQ, US=NASDAQ/NYSE 보통주.

        - KR: ``pykrx`` 로 KOSPI∪KOSDAQ 상장 티커 전체.
        - US: NASDAQ Trader 심볼덤프(nasdaqlisted/otherlisted)에서 보통주만 파싱.
        - 둘 다 실패하면 ``_universe_from_themes`` 로 graceful fallback(운영자 큐레이션).

        전 종목 조회는 무거우므로 인스턴스 1회만 수행하고 캐시한다.

        성능 메모: 전 종목 라이브 스캔(수천 종목 × KIS/yfinance 호출)은 30분 주기에서
        무거우므로 일봉 캐시가 후속 과제다(README 참조). 본 메서드는 유니버스만 넓힌다.
        """
        if market in self._universe_cache:
            return self._universe_cache[market]
        fetched = self._fetch_universe_kr() if market == "KR" else self._fetch_universe_us()
        result = fetched if fetched else _universe_from_themes(self._settings.themes_path, market)
        self._universe_cache[market] = result
        return result

    def _fetch_universe_kr(self) -> list[str]:
        """pykrx 로 KOSPI∪KOSDAQ 상장 티커 전체. 실패 시 빈 리스트(→ themes fallback)."""
        try:
            from pykrx import stock  # lazy import — 미설치/ARM 미지원 환경 보호

            kospi = stock.get_market_ticker_list(market="KOSPI")
            kosdaq = stock.get_market_ticker_list(market="KOSDAQ")
        except Exception:  # pykrx 임의 예외는 fallback 으로 흡수(graceful)
            logger.warning("pykrx 유니버스 조회 실패 — themes.yml fallback", exc_info=True)
            return []
        return _dedup(str(t) for t in (*kospi, *kosdaq) if str(t).strip())

    def _fetch_universe_us(self) -> list[str]:
        """NASDAQ Trader 심볼덤프에서 US 보통주 전체. 실패 시 빈 리스트(→ themes fallback).

        - ``nasdaqlisted.txt``: NASDAQ 상장. ``otherlisted.txt``: NYSE/AMEX 등.
        - 테스트이슈('Y') 제외, ETF('Y') 제외 → 보통주만. (보장: 파일 헤더 기준 컬럼.)
        """
        symbols: list[str] = []
        for url, sym_col in _NASDAQ_DUMPS:
            try:
                resp = self._client.get(url, timeout=20.0)
                resp.raise_for_status()
                text = resp.text
            except httpx.HTTPError:
                logger.warning("NASDAQ 심볼덤프 조회 실패: %s — fallback", url, exc_info=True)
                return []
            symbols.extend(_parse_nasdaq_dump(text, sym_col))
        return _dedup(symbols)

    def get_name(self, ticker: str, market: Market) -> str:
        """KIS/yfinance 종목명."""
        if market == "US":
            info = self._yf_info(ticker)
            name = info.get("shortName") or info.get("longName")
            return str(name) if name else ticker
        fund = self.get_fundamentals(ticker, market)
        return fund.name or ticker

    # ── OHLCV ─────────────────────────────────────────────────────────

    def get_daily_ohlcv(self, ticker: str, market: Market, days: int) -> list[OHLCVRow]:
        """KIS 국내 일봉 / yfinance 일봉 (날짜 오름차순)."""
        if market == "US":
            return self._yf_ohlcv(ticker, days)
        return self._kis_ohlcv(ticker, days)

    def _yf_ohlcv(self, ticker: str, days: int) -> list[OHLCVRow]:
        import yfinance as yf

        # 거래일 < 달력일 → 여유 있게 1.6배 + 5 의 기간을 요청.
        period_days = int(days * 1.6) + 5
        try:
            hist = yf.Ticker(ticker).history(period=f"{period_days}d", auto_adjust=False)
        except Exception as exc:  # 네트워크/라이브러리 오류를 계약 예외로 변환
            raise LiveProviderError(f"yfinance history 실패: {ticker}") from exc
        rows: list[OHLCVRow] = []
        for idx, rec in hist.iterrows():
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
        if not rows:
            raise LiveProviderError(f"yfinance 빈 결과: {ticker}")
        return rows[-days:] if days < len(rows) else rows

    def _kis_ohlcv(self, ticker: str, days: int) -> list[OHLCVRow]:
        end = datetime.now(tz=UTC).strftime("%Y%m%d")
        start = (datetime.now(tz=UTC) - timedelta(days=int(days * 1.6) + 10)).strftime("%Y%m%d")
        data = self._kis_get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            tr_id="FHKST03010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_DATE_1": start,
                "FID_INPUT_DATE_2": end,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )
        chart = data.get("output2") or []
        rows: list[OHLCVRow] = []
        for rec in chart:
            if not rec.get("stck_bsop_date"):
                continue
            d = rec["stck_bsop_date"]
            rows.append(
                OHLCVRow(
                    date=datetime.strptime(d, "%Y%m%d").replace(tzinfo=UTC).date(),
                    open=_d(rec["stck_oprc"]),
                    high=_d(rec["stck_hgpr"]),
                    low=_d(rec["stck_lwpr"]),
                    close=_d(rec["stck_clpr"]),
                    volume=_d(rec["acml_vol"]),
                )
            )
        if not rows:
            raise LiveProviderError(f"KIS 일봉 빈 결과: {ticker}")
        rows.sort(key=lambda r: r.date)  # KIS 는 최신순 → 오름차순 정렬
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
        # 시세 글리치 가드 — 전일 종가 대비 ±50% 밖이면 1회 재조회.
        if quote.prev_close and quote.prev_close > _DEC0:
            ratio = quote.price / quote.prev_close
            if ratio < _GLITCH_LOW or ratio > _GLITCH_HIGH:
                quote = self._kis_quote_once(ticker)
                # FIX-D: 재조회 후에도 ±50% 밖이면 글리치로 보고 스킵(무조건 채택 금지).
                if quote.prev_close and quote.prev_close > _DEC0:
                    ratio2 = quote.price / quote.prev_close
                    if ratio2 < _GLITCH_LOW or ratio2 > _GLITCH_HIGH:
                        raise LiveProviderError(
                            f"KIS 시세 글리치 지속(전일 대비 {ratio2:.2f}x): {ticker}"
                        )
        return quote

    def _kis_quote_once(self, ticker: str) -> Quote:
        data = self._kis_get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
        )
        out = data.get("output") or {}
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
        """KIS 밸류에이션 / yfinance info·fast_info."""
        if market == "US":
            return self._yf_fundamentals(ticker)
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
        # 현재가 응답(output)에 시총·PER·52주고저가 함께 온다.
        data = self._kis_get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
        )
        out = data.get("output") or {}

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
        """KIS 투자자별 매매동향 — 매수·매도 **거래대금(KRW)** + 순매수 (KR 전용).

        FIX-A: 사용자 요구는 '외국인/기관/개인별 매수금+매도금(거래대금) 합산'이다.
        따라서 **수량(``*_ntby_qty``) 필드 사용을 중단**하고 거래대금(``*_tr_pbmn``)을
        읽어 ``*_buy``/``*_sell`` 을 채우고 ``*_net = 매수금 − 매도금`` 으로 산출한다.

        TODO(KIS Developers 확정 필요): 아래 TR_ID·엔드포인트·필드명은 추정치다.
        '종목별 외국인·기관 매매동향'(매수/매도 거래대금 포함) API 를 KIS Developers
        문서로 확정해 ``_INVESTOR_*`` 매핑을 교정할 것. 필드 부재/파싱 실패 시
        ``LiveProviderError`` 를 던져 엔진이 per-ticker 로 흡수하게 한다(무음 0 금지).

        US 심볼 형태면 ``None``.
        """
        if not (len(ticker) == 6 and ticker.isdigit()):
            return None
        # TODO(KIS): 일자별 종목별 투자자 매매동향 엔드포인트·TR_ID 확정 필요(표준 경로 추정).
        data = self._kis_get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            tr_id="FHKST01010900",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
        )
        rows = data.get("output") or []
        if not rows:
            return None
        rec = rows[0]  # 최신 일자

        def amount(key: str) -> Decimal:
            """거래대금 필드(KRW). 키 부재/빈값이면 LiveProviderError(무음 0 금지)."""
            val = rec.get(key)
            if val in (None, ""):
                # TODO(KIS): 실제 매수/매도 거래대금 필드명 확정 시 이 가드도 갱신.
                raise LiveProviderError(f"KIS 투자자 거래대금 필드 부재: {key} ({ticker})")
            return _d(val)

        bsop = rec.get("stck_bsop_date")
        flow_date = (
            datetime.strptime(bsop, "%Y%m%d").replace(tzinfo=UTC).date()
            if bsop
            else datetime.now(tz=UTC).date()
        )
        # 매수/매도 거래대금(추정 필드명) — *_shnu_tr_pbmn(매수대금)/ *_seln_tr_pbmn(매도대금).
        foreign_buy = amount("frgn_shnu_tr_pbmn")
        foreign_sell = amount("frgn_seln_tr_pbmn")
        institution_buy = amount("orgn_shnu_tr_pbmn")
        institution_sell = amount("orgn_seln_tr_pbmn")
        individual_buy = amount("prsn_shnu_tr_pbmn")
        individual_sell = amount("prsn_seln_tr_pbmn")
        return InvestorFlow(
            date=flow_date,
            foreign_net=foreign_buy - foreign_sell,
            institution_net=institution_buy - institution_sell,
            individual_net=individual_buy - individual_sell,
            foreign_buy=foreign_buy,
            foreign_sell=foreign_sell,
            institution_buy=institution_buy,
            institution_sell=institution_sell,
            individual_buy=individual_buy,
            individual_sell=individual_sell,
        )

    # ── KIS 저수준 ────────────────────────────────────────────────────

    def _ensure_token(self) -> str:
        """OAuth 토큰 발급/캐시 (만료 60초 전 갱신)."""
        now = datetime.now(tz=UTC)
        if self._token and self._token_exp and now < self._token_exp:
            return self._token
        if not (self._settings.kis_app_key and self._settings.kis_app_secret):
            raise LiveProviderError("KIS 키 미설정 (KIS_APP_KEY/KIS_APP_SECRET)")
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
            payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LiveProviderError("KIS 토큰 발급 실패") from exc
        token = payload.get("access_token")
        if not token:
            raise LiveProviderError("KIS 토큰 응답에 access_token 없음")
        ttl = int(payload.get("expires_in", 86400))
        self._token = str(token)
        self._token_exp = now + timedelta(seconds=max(ttl - 60, 60))
        return self._token

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

    def _yf_info(self, ticker: str) -> dict[str, Any]:
        import yfinance as yf

        try:
            info: dict[str, Any] = dict(yf.Ticker(ticker).info)
        except Exception as exc:
            raise LiveProviderError(f"yfinance info 실패: {ticker}") from exc
        return info

    def _yf_fast_info(self, ticker: str) -> dict[str, Any]:
        import yfinance as yf

        try:
            fast = yf.Ticker(ticker).fast_info
            return {k: fast[k] for k in fast.keys()}  # noqa: SIM118 — mapping proxy
        except Exception as exc:
            raise LiveProviderError(f"yfinance fast_info 실패: {ticker}") from exc


def get_provider(settings: Settings) -> MarketDataProvider:
    """``data_mode`` 에 따라 Provider 선택.

    - ``sample`` → ``SampleProvider`` (키 불필요).
    - 그 외(``live``) → ``LiveProvider`` (KIS/yfinance).
    """
    if settings.data_mode == "sample":
        return SampleProvider()
    return LiveProvider(settings)


__all__ = [
    "Fundamentals",
    "LiveProvider",
    "LiveProviderError",
    "MarketDataProvider",
    "Quote",
    "SampleProvider",
    "get_provider",
]
