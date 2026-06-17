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
    refresh_interval_min: int = 30

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

    # 점수 가중치 (합 1.0). 기존 momentum 0.25 를 momentum 0.13 + rs 0.12 로 분할 —
    # RS(지수대비 상대수익률)는 절대 momentum 과 collinear 이므로 합쳐서 0.25 를 유지해
    # 이중계상을 방지한다(나머지 4개 가중치는 불변).
    weight_52w: Decimal = Decimal("0.30")
    weight_momentum: Decimal = Decimal("0.13")
    weight_rs: Decimal = Decimal("0.12")
    weight_pocket_pivot: Decimal = Decimal("0.20")
    weight_turnover: Decimal = Decimal("0.15")
    weight_vol_fit: Decimal = Decimal("0.10")

    # ── entry_bias 프리셋 파라미터 (백테스트 전용, 라이브 스코어러 미사용) ──────
    # NOTE: 아래 값은 사전(prior)이며, 실제 백테스트 튜닝 후 조정 예정.
    #       라이브 score_candidates() 는 이 필드를 참조하지 않는다.

    # extension_guard: MA 이격도 페널티
    ext_guard_ma_window: int = 20  # 이격도 기준 MA 일수
    ext_guard_lo: Decimal = Decimal("0.05")  # 이격도 하한(이하 → 페널티 없음)
    ext_guard_hi: Decimal = Decimal("0.30")  # 이격도 상한(이상 → 최대 페널티)
    ext_guard_floor: Decimal = Decimal("0.5")  # 최대 페널티 시 승수(0 < floor ≤ 1)

    # pullback_3pos: MA 지지 위 눌림목 보너스
    pullback_ma_window: int = 20  # 지지선 MA 일수
    pullback_high_window: int = 60  # 최근 고점 탐색 윈도(봉)
    pullback_ideal: Decimal = Decimal("0.08")  # 이상적 눌림목 깊이(8%)
    pullback_max: Decimal = Decimal("0.20")  # 허용 최대 눌림목 깊이(20%)

    # entry_bias 리웨이트 — near_52w 를 0.30→0.18 로 축소, 남은 0.12 를 pullback 배분
    # (기존 weight_52w 는 라이브 스코어러가 사용; entry_bias 는 이 별도 가중치를 사용)
    weight_52w_entry: Decimal = Decimal("0.18")  # entry_bias: near_52w 가중치
    weight_pullback: Decimal = Decimal("0.12")  # entry_bias: pullback_3pos 가중치

    # ── 손절(트레일링) ────────────────────────────────────────────────
    trailing_stop_pct: Decimal = Decimal("8")
    #: 트레일링 peak 추적 윈도(일). 무상태 손절 — 최근 N봉 종가 ∪ 현재가의 최고가.
    trail_window_days: int = 60

    # ── 등급 임계 (점수 0~100) ────────────────────────────────────────
    grade_strong_buy: Decimal = Decimal("75")
    grade_buy: Decimal = Decimal("60")
    grade_hold: Decimal = Decimal("45")

    # ── 테마 ───────────────────────────────────────────────────────────
    top_n_per_theme: int = Field(default=8, ge=1)
    themes_path: Path = DATA_DIR / "themes.yml"

    # ── 영속 ───────────────────────────────────────────────────────────
    db_path: Path = DATA_DIR / "dashboard.db"
    #: KIS OAuth 토큰 디스크 캐시(시크릿 — .gitignore). 재시작·다중 프로세스 간 토큰 재사용.
    kis_token_path: Path = DATA_DIR / ".kis_token.json"

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS 오리진 문자열 → 리스트."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def get_settings() -> Settings:
    """설정 싱글턴 진입점 (테스트에서 monkeypatch 용이)."""
    return Settings()
