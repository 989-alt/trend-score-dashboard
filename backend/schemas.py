"""공유 데이터 계약(contract) — 전 모듈이 import 하는 단일 출처.

원칙(swing-bot 컨벤션 계승):
- 금액·가격·수량은 ``Decimal`` (float 금지).
- datetime 은 timezone-aware (naive 금지). 날짜는 ``date``.
- pydantic v2, ``extra="forbid"`` 로 오타·계약 위반을 즉시 차단.

이 파일은 **데이터 모델만** 담는다. 산식·판정 로직은 ``scoring.py`` / ``stops.py`` 에 둔다.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: 지원 시장. (CRYPTO 는 본 대시보드 범위 밖.)
Market = Literal["KR", "US"]

#: 청산(매도요구) 사유.
SellReason = Literal["trailing_stop", "ma200_break"]

#: 면책 — 모든 응답 meta·화면에 노출되는 필수 문구.
DISCLAIMER: str = (
    "본 대시보드는 투자 자문에 해당하지 않으며, 투자의 판단과 결정은 철저히 개인에게 있습니다."
)

_CFG = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------------------
# 시세/원천 데이터
# ---------------------------------------------------------------------------


class OHLCVRow(BaseModel):
    """일봉 한 행. 모멘텀·변동성·MA·52주·포켓피봇 산출의 입력 단위."""

    model_config = _CFG

    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class InvestorFlow(BaseModel):
    """투자자별 매매(외국인/기관/개인). **KR 전용**(KIS 투자자별 매매동향).

    US(yfinance)는 동일 데이터를 제공하지 않으므로 ``ScoreEntry.investor_flow`` 가 ``None``.
    금액 단위는 KRW. ``*_net`` 은 순매수(매수−매도). ``*_buy``/``*_sell`` 은 가용 시 합산액.
    """

    model_config = _CFG

    date: date
    foreign_net: Decimal
    institution_net: Decimal
    individual_net: Decimal
    foreign_buy: Decimal | None = None
    foreign_sell: Decimal | None = None
    institution_buy: Decimal | None = None
    institution_sell: Decimal | None = None
    individual_buy: Decimal | None = None
    individual_sell: Decimal | None = None


# ---------------------------------------------------------------------------
# 추세추종 판정 결과
# ---------------------------------------------------------------------------


class Grade(str, Enum):
    """매수 추천 등급. 점수(0~100) 매핑은 ``scoring.grade_for_score``.

    ``SELL`` 은 점수와 무관하게 손절(매도요구)이 발동하면 등급을 오버라이드한다.
    한국어 라벨은 프론트 i18n(JSON)에서 매핑한다(백엔드는 영문 enum 값만 보관).
    """

    STRONG_BUY = "strong_buy"  # 적극매수  (score >= 75)
    BUY = "buy"  # 매수      (60 <= score < 75)
    HOLD = "hold"  # 관망      (45 <= score < 60)
    AVOID = "avoid"  # 회피      (score < 45)
    SELL = "sell"  # 매도요구  (손절 발동 — 오버라이드)


class FactorBreakdown(BaseModel):
    """점수 기여 팩터 분해(전부 0~1, 투명성·상세 드로어용) + 원시값."""

    model_config = _CFG

    near_52w: Decimal  # 52주 신고가 근접도 0~1
    pocket_pivot: Decimal  # 포켓피봇 0/1
    momentum_norm: Decimal  # 모멘텀 정규화 0~1
    rs_norm: Decimal = Decimal("0")  # 지수대비 상대강도(RS) 정규화 0~1
    turnover_norm: Decimal  # 거래대금 정규화 0~1
    vol_fit: Decimal  # 변동성 밴드 적합도 0~1
    # 원시값(정규화 전) — 상세 표시·디버그
    momentum: Decimal
    rs: Decimal = Decimal("0")  # 지수대비 상대수익률 (종목모멘텀 − 지수모멘텀)
    volatility: Decimal
    above_ma200: bool


class ScoreEntry(BaseModel):
    """대시보드 한 종목의 전체 표시 데이터(시세 + 판정 + 상세).

    소스가 제공하지 않는 필드는 ``None`` (예: US 의 ``investor_flow``).
    """

    model_config = _CFG

    # 식별
    ticker: str
    name: str
    market: Market
    themes: list[str] = Field(default_factory=list)

    # 시세 (30분 갱신)
    price: Decimal
    open_price: Decimal | None = None
    change_from_open_pct: Decimal | None = None  # (price-open)/open*100 — 장시작 대비
    change_pct: Decimal | None = None  # 전일 종가 대비
    volume: Decimal | None = None
    turnover: Decimal | None = None  # 거래대금

    # 펀더멘털/통계 (일1회 갱신)
    market_cap: Decimal | None = None
    w52_high: Decimal | None = None
    w52_low: Decimal | None = None
    near_52w_pct: Decimal | None = None  # price/w52_high*100
    return_1y_pct: Decimal | None = None
    per: Decimal | None = None
    pbr: Decimal | None = None
    eps: Decimal | None = None
    sector: str | None = None
    industry: str | None = None

    # 추세추종 판정
    score: Decimal  # 0~100
    grade: Grade
    eligible: bool  # 하드필터 통과 여부 (미통과면 점수 0·회피)
    factors: FactorBreakdown | None = None
    ma200: Decimal | None = None

    # 손절/매도요구
    stop_price: Decimal | None = None
    trailing_peak: Decimal | None = None
    sell_alert: bool = False
    sell_reason: SellReason | None = None
    rationale: str | None = None

    # 투자자별 매매 (KR only)
    investor_flow: InvestorFlow | None = None


# ---------------------------------------------------------------------------
# API 응답
# ---------------------------------------------------------------------------


class SnapshotCounts(BaseModel):
    """관측성 — 무음 절단 방지(몇 개를 스캔/통과/점수화했는지)."""

    model_config = _CFG

    scanned: int = 0
    eligible: int = 0
    scored: int = 0
    failed: int = 0


class Snapshot(BaseModel):
    """단일 시장(국장/미장) 랭킹 스냅샷. ``GET /api/snapshot?market=...``."""

    model_config = _CFG

    market: Market
    generated_at: datetime
    next_refresh_at: datetime | None = None
    market_open: bool
    disclaimer: str = DISCLAIMER
    counts: SnapshotCounts = Field(default_factory=SnapshotCounts)
    entries: list[ScoreEntry]


class ThemeGroup(BaseModel):
    """테마 1개 + 그 안의 주도주(점수 상위 N)."""

    model_config = _CFG

    theme: str
    market: Market
    leaders: list[ScoreEntry]


class ThemesResponse(BaseModel):
    """테마별 주도주 응답. ``GET /api/themes``."""

    model_config = _CFG

    generated_at: datetime
    market_open: dict[str, bool] = Field(default_factory=dict)  # {"KR":bool,"US":bool}
    disclaimer: str = DISCLAIMER
    groups: list[ThemeGroup]


#: 이슈 단위 — 종목 또는 테마(언급 급등 랭킹의 행 종류).
IssueKind = Literal["ticker", "theme"]


class IssueHeadline(BaseModel):
    """이슈를 구성한 개별 기사/메시지 헤드라인(샘플 표시·근거)."""

    model_config = _CFG

    title: str
    url: str | None = None
    source: str
    published_at: datetime | None = None


class IssueEntry(BaseModel):
    """실시간 이슈 랭킹 한 항목 — 종목/테마의 최근 언급 급등.

    ``mention_count`` 는 최근 윈도(``IssuesResponse.window_hours``)의 언급수,
    ``baseline_count`` 는 동일 길이 환산 과거 평균 언급수. ``spike`` 는 분자/(분모+1)
    비율(클수록 평소보다 급증). ``score``/``grade`` 는 해당 종목이 최신 스냅샷에 있을
    때만 채운다(테마·미스캔 종목이면 ``None``) — 클릭 시 종목 상세로 잇기 위함.
    """

    model_config = _CFG

    kind: IssueKind
    key: str  # 종목코드(ticker) 또는 테마명(theme)
    name: str
    market: Market | None = None
    mention_count: int
    baseline_count: int
    spike: Decimal
    score: Decimal | None = None
    grade: Grade | None = None
    headlines: list[IssueHeadline] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class IssueCounts(BaseModel):
    """이슈 수집 관측성 — 무음 절단 방지(몇 개 적재·분석, 소스 성공/실패)."""

    model_config = _CFG

    collected: int = 0  # 이번 사이클 새로 적재된 항목수
    items_recent: int = 0  # 최근 윈도 내 분석 대상 항목수
    sources_ok: int = 0
    sources_failed: int = 0


class IssuesResponse(BaseModel):
    """실시간 이슈 랭킹 응답. ``GET /api/issues``."""

    model_config = _CFG

    generated_at: datetime
    window_hours: int
    disclaimer: str = DISCLAIMER
    counts: IssueCounts = Field(default_factory=IssueCounts)
    issues: list[IssueEntry]


class HealthResponse(BaseModel):
    """``GET /healthz``."""

    model_config = _CFG

    status: Literal["ok", "degraded"]
    data_mode: str
    last_kr_snapshot: datetime | None = None
    last_us_snapshot: datetime | None = None


__all__ = [
    "DISCLAIMER",
    "FactorBreakdown",
    "Grade",
    "HealthResponse",
    "InvestorFlow",
    "IssueCounts",
    "IssueEntry",
    "IssueHeadline",
    "IssueKind",
    "IssuesResponse",
    "Market",
    "OHLCVRow",
    "ScoreEntry",
    "SellReason",
    "Snapshot",
    "SnapshotCounts",
    "ThemeGroup",
    "ThemesResponse",
]
