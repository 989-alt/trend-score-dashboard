"""앱 설정 — 환경변수(.env) + 추세추종 파라미터 단일 출처.

값은 swing-bot 의 ``default_trend_config`` / ``Settings`` 와 동일 기본을 따른다.
시크릿(KIS 키)은 ``.env`` 에서만 읽고 절대 하드코딩하지 않는다.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 프로젝트 루트 (이 파일: backend/config.py → 부모의 부모).
ROOT_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = ROOT_DIR / "data"


class Settings(BaseSettings):
    """환경변수 + 기본값. ``.env`` 자동 로드."""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── 데이터 소스 모드 ───────────────────────────────────────────────
    #: live = 실 KIS/yfinance, sample = 내장 샘플데이터(키 불필요, 개발·검증용).
    data_mode: Literal["live", "sample"] = "sample"

    # ── KIS ───────────────────────────────────────────────────────────
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_mode: Literal["mock", "real"] = "mock"

    # ── 서버 ───────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "http://localhost:5173"
    #: intraday 스냅샷 갱신 주기(분). 1=실시간(장중에만 산출·일봉캐시 재사용 → 시세만 신선).
    #: KIS 일일쿼터·yfinance throttle 부담 시 2~5 로 상향(graceful degradation 으로 안전).
    refresh_interval_min: int = 1

    # ── 라이브 스캔 성능 ───────────────────────────────────────────────
    #: 유니버스 = 거래대금 상위 N(KR). 전 종목 대신 유동성 상위만 스캔해 속도 확보.
    live_universe_top_n: int = Field(default=300, ge=1)
    #: US 유니버스 상한 — yfinance(Yahoo)는 다종목 조회 시 429 로 막히므로 소수만.
    live_universe_top_n_us: int = Field(default=30, ge=1)
    #: per-ticker 데이터 수집 동시성(ThreadPoolExecutor worker 수).
    max_workers: int = Field(default=8, ge=1)

    # ── 추세추종 하드필터 + 점수 파라미터 (swing-bot default_trend_config 동일) ──
    min_turnover_krw: Decimal = Decimal("10000000000")  # 100억 (KR 거래대금 KRW 임계)
    min_turnover_usd: Decimal = Decimal("30000000")  # 3천만 USD (US 거래대금 임계)
    vol_band_low: Decimal = Decimal("0.20")
    vol_band_high: Decimal = Decimal("0.60")
    momentum_min: Decimal = Decimal("0")
    lookback_days: int = 20
    ma200_window: int = 200
    pocket_pivot_lookback: int = 10
    breakout_52w_min: Decimal = Decimal("0.90")

    # ── 레짐(장세) 엔진 — 지수 방향(MA200)×강도(ADX) 3대 레짐. 읽기전용(1단계). ──
    regime_ma_window: int = 200  # 지수 추세 방향 기준
    regime_adx_period: int = 14  # ADX 기간(Wilder)
    regime_adx_trend: Decimal = Decimal("25")  # ADX≥ → 추세장
    regime_adx_chop: Decimal = Decimal("20")  # ADX< → 횡보장(사이는 직전 레짐 유지)

    # 점수 가중치 (합 1.0). 기존 momentum 0.25 를 momentum 0.13 + rs 0.12 로 분할 —
    # RS(지수대비 상대수익률)는 절대 momentum 과 collinear 이므로 합쳐서 0.25 를 유지해
    # 이중계상을 방지한다(나머지 4개 가중치는 불변).
    weight_52w: Decimal = Decimal("0.30")
    weight_momentum: Decimal = Decimal("0.13")
    weight_rs: Decimal = Decimal("0.12")
    weight_pocket_pivot: Decimal = Decimal("0.20")
    weight_turnover: Decimal = Decimal("0.15")
    weight_vol_fit: Decimal = Decimal("0.10")

    # ── 손절(트레일링) ────────────────────────────────────────────────
    trailing_stop_pct: Decimal = Decimal("8")
    #: 트레일링 peak 추적 윈도(일). 무상태 손절 — 최근 N봉 종가 ∪ 현재가의 최고가.
    trail_window_days: int = 60

    # ── 등급 임계 (점수 0~100) ────────────────────────────────────────
    grade_strong_buy: Decimal = Decimal("75")
    grade_buy: Decimal = Decimal("60")
    grade_hold: Decimal = Decimal("45")

    # ── 뉴스 수집 (시황 탭) ────────────────────────────────────────────
    #: my.telegram.org App api_id / api_hash (시크릿 — .env, 커밋 금지).
    app_api_id: str = ""
    app_api_hash: str = ""
    #: Gemini 주간요약(주 1회·읽기전용). 키 없으면 스킵(fail-open).
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    #: 뉴스 아카이브 DB(대시보드 DB와 분리). gitignore.
    news_db_path: Path = DATA_DIR / "news.db"
    #: Telethon 세션 베이스(.session 자동부착). 시크릿 — gitignore.
    telethon_session_path: Path = DATA_DIR / "telethon"
    #: 수집 대상 텔레그램 채널(쉼표구분, @ 없이 username).
    news_channels: str = "FastStockNews,goodnews_honey,getfeed,jusikbiso"
    #: 이슈 긴급도 심각도 사전(표시 정렬용).
    news_severity_path: Path = DATA_DIR / "news_severity_lexicon.yml"
    #: 텔레그램 폴링 주기(분). catch-up 이라 1분도 ₩0·flood 무관.
    news_poll_interval_min: int = Field(default=1, ge=1)
    #: RSS 크롤링 주기(분). 사이트 예의상 텔레그램보다 느슨하게.
    news_rss_interval_min: int = Field(default=3, ge=1)
    #: RSS 피드 목록(운영자 편집형 YAML — 국내외 증시·경제).
    news_sources_path: Path = DATA_DIR / "news_sources.yml"
    #: 3-레이어(국내/미국/종합) 각 레이어 Top N.
    news_top_n_per_layer: int = Field(default=10, ge=1)

    # ── 테마 ───────────────────────────────────────────────────────────
    top_n_per_theme: int = Field(default=8, ge=1)
    themes_path: Path = DATA_DIR / "themes.yml"

    # ── 영속 ───────────────────────────────────────────────────────────
    db_path: Path = DATA_DIR / "dashboard.db"
    #: KIS OAuth 토큰 디스크 캐시(시크릿 — .gitignore). 재시작·다중 프로세스 간 토큰 재사용.
    kis_token_path: Path = DATA_DIR / ".kis_token.json"

    # ── 모의 매매봇 (trend-trader, 전진검증) ──────────────────────────────
    #: 모의투자 전용 앱키(KIS_APPKEY/KIS_APPSECRET — 언더스코어 없음, swing-bot 과 동일). 시세용
    #: kis_app_key(실전)와 별개: KIS는 실전·모의 앱키를 따로 발급(실전키로 모의 주문 시 EGW02007).
    kis_appkey: str = ""
    kis_appsecret: str = ""
    #: 모의계좌번호(KIS_ACCOUNT) — 예 "50190719". 상품코드는 kis_account_prod(기본 01).
    kis_account: str = ""
    kis_account_prod: str = "01"
    #: 매매봇 토큰 디스크 캐시(모의 도메인 토큰 — 시세 real 토큰과 분리). 시크릿 — gitignore.
    trader_token_path: Path = DATA_DIR / ".trader_token.json"
    #: 매매봇 TradeStore 경로(대시보드가 읽기). 봇이 쓰고 API 가 읽는다(WAL).
    trader_db_path: Path = DATA_DIR / "trading.db"
    #: 보유 종목 수(점수 상위 N 진입). 목표금액 = 가용평가액 ÷ N.
    trader_top_n: int = Field(default=20, ge=1)
    #: 매매 루프 주기(초). 1분 기본, 최소 10초(과도한 폴링·중복주문 방지).
    trader_loop_sec: int = Field(default=60, ge=10)
    #: 현금버퍼 비율(평가액 중 미투자 여유). 슬리피지·체결지연 흡수.
    trader_cash_buffer: Decimal = Decimal("0.05")
    #: 킬스위치 — True 면 신규 매수 중단(매도·손절은 계속 = 리스크 축소).
    trader_kill_switch: bool = False
    #: 이 파일이 존재해도 신규 매수 중단(재배포 없이 운영 중단용 — touch/rm 로 토글).
    trader_halt_file: Path = DATA_DIR / ".trader_halt"
    #: 매수 종목 선정을 Gemini 2.5 Pro 에 위임(분석=스크립트, 결정=LLM, 실행=KIS). False 면
    #: 결정론 점수상위(현 동작). 손절 등 안전 게이트는 LLM 무관하게 항상 적용. 키 없거나 실패 시
    #: 자동 폴백(결정론) — 매매가 멈추지 않음.
    trader_use_llm: bool = True
    #: 입력 동일 시 Gemini 재호출 생략(입력해시 캐시). 무체결·시세무변동이면 같은 입력 → 캐시.
    #: 체결로 포지션/현금 변하거나 1분 스냅샷으로 점수 변하면 키가 바뀌어 재결정.
    trader_llm_cache: bool = True
    #: 매수 결정용 Gemini 모델. 결정=적격후보 중 선별이라 flash 로 충분(4주 ~$6) — pro 는 ~$200.
    #: 더 저렴=gemini-2.0-flash / 최고품질=gemini-2.5-pro (GEMINI_MODEL_DECISION 로 교체).
    gemini_model_decision: str = "gemini-2.5-flash"

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS 오리진 문자열 → 리스트."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def news_channel_list(self) -> list[str]:
        """뉴스 채널 문자열 → 공백·빈값 제거 리스트."""
        return [c.strip() for c in self.news_channels.split(",") if c.strip()]


def get_settings() -> Settings:
    """설정 싱글턴 진입점 (테스트에서 monkeypatch 용이)."""
    return Settings()
