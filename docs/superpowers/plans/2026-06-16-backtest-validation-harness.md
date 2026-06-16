# 백테스트 검증 하니스 (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KR 종목의 결정론 점수를 과거 시점 T에 룩어헤드 없이 replay해 점수의 forward-return 예측력(버킷 단조성·MAE·승률)과 포트폴리오 성과(vs ^KS11)를 측정하는 오프라인 CLI 하니스를 만든다.

**Architecture:** `engine._collect_raw`의 팩터 계산을 순수 함수 `factors.build_candidate`로 추출(라이브 행동 변경 0)해 라이브·백테스트가 공유한다. 별도 오프라인 데이터층(pykrx 가격·밸류·일자별 상장종목 + DART 접수일 기준 재무)이 시점별 패널을 만들고, 백테스트 루프가 리밸런스일마다 ≤T로 슬라이스해 동일 `scoring` 함수로 채점·시뮬한다.

**Tech Stack:** Python 3.11, Decimal 전면(float 금지), pydantic v2, pykrx, yfinance, httpx, OpenDART REST, SQLite(stdlib), pytest. 설계서: `docs/superpowers/specs/2026-06-16-backtest-validation-harness-design.md`.

---

## 파일 구조 (생성/수정 + 책임)

| 파일 | 동작 | 책임 |
|---|---|---|
| `backend/factors.py` | 생성 | 공유 순수 스코어러 `build_candidate` (rows≤T + w52 + index_mom → `Candidate`) |
| `backend/engine.py` | 수정 (`_collect_raw`) | provider I/O 후 `factors.build_candidate` 위임 — **행동 변경 0** |
| `backend/backtest/__init__.py` | 생성 | 패키지 마커 |
| `backend/backtest/metrics.py` | 생성 | 순수 메트릭: 단조성(Spearman)·MAE·승률·CAGR·MDD·연환산vol |
| `backend/backtest/panel.py` | 생성 | 시점별 패널 자료구조 + as-of 슬라이싱(룩어헤드 가드 중심) |
| `backend/backtest/dart_client.py` | 생성 | OpenDART 접수일 기준 재무(corpCode·공시목록·전체재무제표) |
| `backend/backtest/loader.py` | 생성 | pykrx OHLCV·밸류·유니버스 + dart + ^KS11 → `Panel`, SQLite 캐시 |
| `backend/backtest/run.py` | 생성 | 리밸런스 루프·포트폴리오 시뮬·이벤트스터디·리포트·CLI |
| `backend/backtest/report.py` | 생성 | 결과 → 마크다운 + JSON |
| `tests/test_factors.py` | 생성 | `build_candidate` 회귀(추출 동치) |
| `tests/test_backtest_metrics.py` | 생성 | 메트릭 산술 |
| `tests/test_backtest_panel.py` | 생성 | as-of/룩어헤드/생존편향 슬라이싱 |
| `tests/test_backtest_dart.py` | 생성 | DART 접수일 as-of 선택(HTTP mock) |
| `tests/test_backtest_run.py` | 생성 | 합성 패널 end-to-end(룩어헤드·비용·상폐) |
| `tests/fixtures/backtest_synth.py` | 생성 | 결정론 합성 패널 빌더(테스트 공용) |
| `.env.example` | 수정 | `DART_API_KEY=` 템플릿 추가 |

---

## 선결 조건 (구현 시작 전)

- [ ] OpenDART 무료 API 키 발급 → 로컬 `.env`에 `DART_API_KEY=<key>` (커밋 금지; `.gitignore`에 `.env` 이미 포함 확인).
- [ ] 작업 브랜치 확인: `git -C . rev-parse --abbrev-ref HEAD` → `docs/score-reweight-rationale` (이미 spec이 올라간 브랜치에서 이어서 작업).
- [ ] 의존성 동기화: `uv sync` (신규 패키지 없음 — pykrx/yfinance/httpx/pandas 모두 기존).

---

# 마일스톤 M1 — 기반 (공유 스코어러 + 메트릭)

## Task 1: `factors.build_candidate` 추출 (라이브 행동 변경 0)

**Files:**
- Create: `backend/factors.py`
- Modify: `backend/engine.py` (`_collect_raw`, 현재 줄 106-181의 팩터 계산 블록)
- Test: `tests/test_factors.py`

- [ ] **Step 1: 실패하는 테스트 작성** — 추출 함수가 현 `_collect_raw` 계산과 동치임을 고정한다.

```python
# tests/test_factors.py
from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.config import Settings
from backend.factors import build_candidate
from backend.schemas import OHLCVRow


def _rows(closes: list[int]) -> list[OHLCVRow]:
    out: list[OHLCVRow] = []
    for i, c in enumerate(closes):
        cd = Decimal(c)
        out.append(
            OHLCVRow(
                date=date(2024, 1, 1 + i),
                open=cd,
                high=cd * Decimal("1.01"),
                low=cd * Decimal("0.99"),
                close=cd,
                volume=Decimal("1000000"),
            )
        )
    return out


def test_build_candidate_eligible_uptrend() -> None:
    settings = Settings(data_mode="sample")
    rows = _rows(list(range(100, 360)))  # 260봉 상승추세 (200일선 위)
    cand = build_candidate(
        ticker="000001",
        rows=rows,
        w52_high=None,                 # None → rows 최고가로 근접도 산정(룩어헤드 0)
        index_momentum=Decimal("0"),
        turnover=Decimal("20000000000"),   # 200억 (>100억 임계)
        min_turnover=settings.min_turnover_krw,
        settings=settings,
    )
    assert cand.ticker == "000001"
    assert cand.eligible is True
    assert cand.above_ma200 is True
    assert cand.momentum > 0
    assert Decimal("0") <= cand.near_52w <= Decimal("1")


def test_build_candidate_ineligible_low_turnover() -> None:
    settings = Settings(data_mode="sample")
    rows = _rows(list(range(100, 360)))
    cand = build_candidate(
        ticker="000002",
        rows=rows,
        w52_high=None,
        index_momentum=Decimal("0"),
        turnover=Decimal("1000000000"),    # 10억 (<100억 임계) → 부적격
        min_turnover=settings.min_turnover_krw,
        settings=settings,
    )
    assert cand.eligible is False
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_factors.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.factors`.

- [ ] **Step 3: `backend/factors.py` 작성** — `_collect_raw`의 계산 블록을 그대로 옮긴다(동일 함수 호출·동일 순서).

```python
# backend/factors.py
"""공유 순수 스코어러 — rows(≤T)+w52+지수모멘텀 → Candidate.

engine._collect_raw 와 backtest 가 동일 팩터 로직을 공유하기 위한 추출.
provider I/O 는 호출 측이 담당하고, 본 함수는 순수 계산만 한다(결정론).
"""

from __future__ import annotations

from decimal import Decimal

from backend import scoring as sc
from backend.config import Settings
from backend.schemas import Market, OHLCVRow


def build_candidate(
    *,
    ticker: str,
    rows: list[OHLCVRow],
    w52_high: Decimal | None,
    index_momentum: Decimal,
    turnover: Decimal,
    min_turnover: Decimal,
    settings: Settings,
    market: Market = "KR",
) -> sc.Candidate:
    """rows(오름차순, ≤T)로부터 팩터를 계산해 Candidate 조립.

    ``w52_high`` 가 None 이면 ``proximity_to_52w_high`` 이 rows 최고가를 분모로 쓴다
    (백테스트는 ≤T 슬라이스의 최고가 = 시점별 52주고 → 룩어헤드 0).
    """
    recent = rows[-settings.lookback_days :]
    momentum = sc.compute_momentum(recent)
    rs = momentum - index_momentum
    volatility = sc.compute_annualized_volatility(recent)
    near_52w = sc.proximity_to_52w_high(rows, high_52w=w52_high)
    has_pp = sc.pocket_pivot(rows, lookback=settings.pocket_pivot_lookback)
    above = sc.above_ma200(rows, settings.ma200_window)
    eligible = sc.passes_hard_filter(
        turnover=turnover,
        momentum=momentum,
        volatility=volatility,
        near_52w=near_52w,
        above_ma200_flag=above,
        min_turnover=min_turnover,
        settings=settings,
    )
    return sc.Candidate(
        ticker=ticker,
        turnover=turnover,
        momentum=momentum,
        rs=rs,
        volatility=volatility,
        near_52w=near_52w,
        has_pocket_pivot=has_pp,
        above_ma200=above,
        eligible=eligible,
    )


__all__ = ["build_candidate"]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_factors.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: `engine._collect_raw` 가 `build_candidate` 를 호출하도록 수정** — 기존 계산 블록을 위임으로 교체(행동 동일).

`backend/engine.py` 의 `_collect_raw` 안에서, `recent = rows[-settings.lookback_days :]` 부터 `candidate = sc.Candidate(...)` 까지의 블록을 아래로 교체:

```python
    # (provider 호출로 rows/quote/fundamentals/flow 확보 후)
    turnover = quote.turnover if quote.turnover is not None else Decimal("0")
    min_turnover = settings.min_turnover_krw if market == "KR" else settings.min_turnover_usd
    from backend.factors import build_candidate  # 지역 import (순환 회피)

    candidate = build_candidate(
        ticker=ticker,
        rows=rows,
        w52_high=fundamentals.w52_high,
        index_momentum=index_momentum,
        turnover=turnover,
        min_turnover=min_turnover,
        settings=settings,
        market=market,
    )
    above = candidate.above_ma200
    ma200 = sc.simple_moving_average(rows, settings.ma200_window)
```

> 주의: `ma200`(표시용)·`above`·`name` 등 `_Raw` 조립에 쓰던 값은 유지. `build_candidate` 가 `above_ma200` 을 Candidate 에 담으므로 `above = candidate.above_ma200` 로 재사용한다. 기존 `momentum/rs/volatility/near_52w/has_pp` 지역변수를 뒤에서 쓰지 않는지 확인하고, 쓰면 `candidate.<field>` 로 대체.

- [ ] **Step 6: 기존 엔진 테스트가 그대로 통과하는지 확인 (행동 변경 0 증명)**

Run: `uv run pytest tests/test_engine.py tests/test_scoring.py -v`
Expected: PASS (모두 통과 — 회귀 없음).

- [ ] **Step 7: 린트·타입 확인**

Run: `uv run ruff check backend/factors.py backend/engine.py && uv run mypy backend/`
Expected: 통과(0 errors).

- [ ] **Step 8: 커밋**

```bash
git add backend/factors.py backend/engine.py tests/test_factors.py
git commit -m "refactor: _collect_raw 팩터 계산을 factors.build_candidate 로 추출 (행동 변경 0)"
```

---

## Task 2: `backtest/metrics.py` 순수 메트릭

**Files:**
- Create: `backend/backtest/__init__.py`, `backend/backtest/metrics.py`
- Test: `tests/test_backtest_metrics.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/test_backtest_metrics.py
from __future__ import annotations

from decimal import Decimal

from backend.backtest.metrics import (
    cagr,
    max_adverse_excursion,
    max_drawdown,
    spearman_monotonicity,
    win_rate,
)


def test_spearman_perfect_monotonic() -> None:
    # 점수 오름차순 = 수익 오름차순 → +1.0
    scores = [Decimal(x) for x in (1, 2, 3, 4, 5)]
    fwd = [Decimal(x) for x in (-2, -1, 0, 1, 2)]
    assert spearman_monotonicity(scores, fwd) == Decimal("1")


def test_spearman_inverted() -> None:
    scores = [Decimal(x) for x in (1, 2, 3, 4, 5)]
    fwd = [Decimal(x) for x in (5, 4, 3, 2, 1)]
    assert spearman_monotonicity(scores, fwd) == Decimal("-1")


def test_mae_is_worst_drop_within_horizon() -> None:
    # 진입가 100, 이후 5봉: 102, 95(-5%), 110, 90(-10%), 105 → MAE = -10%
    path = [Decimal(x) for x in (102, 95, 110, 90, 105)]
    assert max_adverse_excursion(Decimal("100"), path) == Decimal("-0.10")


def test_max_drawdown() -> None:
    nav = [Decimal(x) for x in (100, 120, 80, 130)]  # 120→80 = -33.33%
    assert max_drawdown(nav).quantize(Decimal("0.0001")) == Decimal("-0.3333")


def test_win_rate() -> None:
    fwd = [Decimal("0.1"), Decimal("-0.2"), Decimal("0.0"), Decimal("0.3")]
    # >0 인 것: 0.1, 0.3 → 2/4 = 0.5 (0.0 은 비승)
    assert win_rate(fwd) == Decimal("0.5")


def test_cagr_two_years_doubling() -> None:
    # 2년에 2배 → CAGR ≈ 0.4142
    assert cagr(Decimal("100"), Decimal("200"), years=Decimal("2")).quantize(
        Decimal("0.0001")
    ) == Decimal("0.4142")
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_backtest_metrics.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.backtest`.

- [ ] **Step 3: 패키지 + 메트릭 구현**

```python
# backend/backtest/__init__.py
"""백테스트 검증 하니스 (오프라인 CLI). 라이브 파이프라인과 분리."""
```

```python
# backend/backtest/metrics.py
"""순수 메트릭 — float 보조연산은 문자열 경유로 Decimal 복귀(정밀도 보존)."""

from __future__ import annotations

import math
from decimal import Decimal


def _rank(values: list[Decimal]) -> list[Decimal]:
    """평균순위(동률은 평균). 1-based."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [Decimal(0)] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = Decimal(sum(range(i + 1, j + 2))) / Decimal(j - i + 1)
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman_monotonicity(scores: list[Decimal], forward_returns: list[Decimal]) -> Decimal:
    """점수 vs forward-return 의 Spearman 순위상관(−1~1). 표본<2 또는 분산0이면 0."""
    n = len(scores)
    if n < 2 or len(forward_returns) != n:
        return Decimal("0")
    rs, rf = _rank(scores), _rank(forward_returns)
    mean_s = sum(rs, Decimal("0")) / n
    mean_f = sum(rf, Decimal("0")) / n
    cov = sum(((a - mean_s) * (b - mean_f) for a, b in zip(rs, rf)), Decimal("0"))
    var_s = sum(((a - mean_s) ** 2 for a in rs), Decimal("0"))
    var_f = sum(((b - mean_f) ** 2 for b in rf), Decimal("0"))
    if var_s == 0 or var_f == 0:
        return Decimal("0")
    denom = Decimal(str(math.sqrt(float(var_s) * float(var_f))))
    return (cov / denom).quantize(Decimal("0.0001"))


def max_adverse_excursion(entry: Decimal, path: list[Decimal]) -> Decimal:
    """매수후 최대역행 = min((p−entry)/entry). path 비었거나 entry≤0 이면 0."""
    if entry <= 0 or not path:
        return Decimal("0")
    return min((p - entry) / entry for p in path)


def win_rate(forward_returns: list[Decimal]) -> Decimal:
    """fwd>0 비율. 표본 0이면 0."""
    if not forward_returns:
        return Decimal("0")
    wins = sum(1 for r in forward_returns if r > 0)
    return (Decimal(wins) / Decimal(len(forward_returns))).quantize(Decimal("0.0001"))


def max_drawdown(nav: list[Decimal]) -> Decimal:
    """NAV 시계열 최대낙폭(≤0). 표본<2 이면 0."""
    if len(nav) < 2:
        return Decimal("0")
    peak = nav[0]
    mdd = Decimal("0")
    for v in nav:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak
            if dd < mdd:
                mdd = dd
    return mdd


def cagr(start: Decimal, end: Decimal, *, years: Decimal) -> Decimal:
    """연복리수익률. start≤0 또는 years≤0 이면 0."""
    if start <= 0 or years <= 0 or end <= 0:
        return Decimal("0")
    ratio = float(end) / float(start)
    val = ratio ** (1.0 / float(years)) - 1.0
    return Decimal(str(val)).quantize(Decimal("0.000001"))


__all__ = [
    "cagr",
    "max_adverse_excursion",
    "max_drawdown",
    "spearman_monotonicity",
    "win_rate",
]
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_backtest_metrics.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: 린트·타입**

Run: `uv run ruff check backend/backtest/ && uv run mypy backend/`
Expected: 통과.

- [ ] **Step 6: 커밋**

```bash
git add backend/backtest/__init__.py backend/backtest/metrics.py tests/test_backtest_metrics.py
git commit -m "feat(backtest): 순수 메트릭(단조성·MAE·MDD·CAGR·승률) + 테스트"
```

---

# 마일스톤 M2 — 시점별 데이터층

## Task 3: `backtest/panel.py` — 패널 + as-of 슬라이싱 (룩어헤드 가드)

**Files:**
- Create: `backend/backtest/panel.py`
- Test: `tests/test_backtest_panel.py`, `tests/fixtures/backtest_synth.py`

- [ ] **Step 1: 합성 패널 픽스처 작성**

```python
# tests/fixtures/backtest_synth.py
"""결정론 합성 패널 — 외부 API 없이 가드 단위검증용."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from backend.backtest.panel import (
    AsOfFundamentals,
    Panel,
    TickerSeries,
)
from backend.schemas import OHLCVRow


def make_series(ticker: str, start: date, closes: list[int]) -> TickerSeries:
    rows = []
    for i, c in enumerate(closes):
        cd = Decimal(c)
        rows.append(
            OHLCVRow(
                date=start + timedelta(days=i),
                open=cd,
                high=cd * Decimal("1.01"),
                low=cd * Decimal("0.99"),
                close=cd,
                volume=Decimal("1000000"),
            )
        )
    return TickerSeries(ticker=ticker, rows=rows, turnover_by_date={r.date: Decimal("20000000000") for r in rows})


def make_panel() -> Panel:
    start = date(2023, 1, 2)
    a = make_series("000001", start, list(range(100, 360)))   # 전체기간 상장
    b = make_series("000002", start + timedelta(days=100), list(range(50, 210)))  # 100일 뒤 상장
    fundamentals = {
        # 접수일(rcept) 기준 as-of: 2023-03-31 접수분, 2024-03-31 접수분
        "000001": [
            AsOfFundamentals(rcept_date=date(2023, 3, 31), roe=Decimal("0.10"), op_margin=Decimal("0.08"), eps_growth=Decimal("0.15")),
            AsOfFundamentals(rcept_date=date(2024, 3, 31), roe=Decimal("0.12"), op_margin=Decimal("0.09"), eps_growth=Decimal("0.20")),
        ],
        "000002": [],
    }
    listings = {
        "000001": (start, None),                          # (상장일, 상폐일)
        "000002": (start + timedelta(days=100), None),
    }
    index_rows = make_series("KS11", start, list(range(2000, 2260))).rows
    return Panel(series={"000001": a, "000002": b}, fundamentals=fundamentals, listings=listings, index_rows=index_rows)
```

- [ ] **Step 2: 실패하는 테스트 작성** — as-of/룩어헤드/생존편향.

```python
# tests/test_backtest_panel.py
from __future__ import annotations

from datetime import date

from tests.fixtures.backtest_synth import make_panel


def test_rows_asof_never_exceeds_T() -> None:
    panel = make_panel()
    T = date(2023, 4, 1)
    rows = panel.rows_asof("000001", T)
    assert rows, "기간 내 데이터가 있어야 함"
    assert all(r.date <= T for r in rows), "룩어헤드: T 이후 봉이 새면 안 됨"


def test_universe_excludes_not_yet_listed() -> None:
    panel = make_panel()
    early = date(2023, 2, 1)   # 000002 상장 전
    uni = panel.universe_asof(early)
    assert "000001" in uni
    assert "000002" not in uni, "생존편향: 미상장 종목이 유니버스에 들면 안 됨"


def test_fundamentals_asof_picks_latest_filed_on_or_before_T() -> None:
    panel = make_panel()
    f = panel.fundamentals_asof("000001", date(2023, 6, 1))
    assert f is not None and f.rcept_date == date(2023, 3, 31)
    f2 = panel.fundamentals_asof("000001", date(2024, 6, 1))
    assert f2 is not None and f2.rcept_date == date(2024, 3, 31), "접수일 ≤ T 중 최신"
    f0 = panel.fundamentals_asof("000001", date(2023, 1, 10))
    assert f0 is None, "아직 공시 전이면 None(fail-open)"
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_backtest_panel.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.backtest.panel`.

- [ ] **Step 4: `panel.py` 구현**

```python
# backend/backtest/panel.py
"""시점별 패널 — 모든 조회가 ≤T 만 반환(룩어헤드 가드의 단일 지점)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from backend.schemas import OHLCVRow


@dataclass(frozen=True)
class AsOfFundamentals:
    """접수일(rcept_date) 시점에 공시된 재무 파생값."""

    rcept_date: date
    roe: Decimal | None = None
    op_margin: Decimal | None = None
    eps_growth: Decimal | None = None


@dataclass(frozen=True)
class TickerSeries:
    ticker: str
    rows: list[OHLCVRow]                       # 날짜 오름차순
    turnover_by_date: dict[date, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class Panel:
    series: dict[str, TickerSeries]
    fundamentals: dict[str, list[AsOfFundamentals]]   # 접수일 오름차순 가정
    listings: dict[str, tuple[date, date | None]]     # ticker -> (상장일, 상폐일|None)
    index_rows: list[OHLCVRow]

    def rows_asof(self, ticker: str, t: date) -> list[OHLCVRow]:
        s = self.series.get(ticker)
        if s is None:
            return []
        return [r for r in s.rows if r.date <= t]

    def index_rows_asof(self, t: date) -> list[OHLCVRow]:
        return [r for r in self.index_rows if r.date <= t]

    def turnover_asof(self, ticker: str, t: date) -> Decimal:
        s = self.series.get(ticker)
        if s is None:
            return Decimal("0")
        # t 당일 거래대금, 없으면 t 이하 최신 봉의 거래대금.
        if t in s.turnover_by_date:
            return s.turnover_by_date[t]
        rows = [r for r in s.rows if r.date <= t]
        return s.turnover_by_date.get(rows[-1].date, Decimal("0")) if rows else Decimal("0")

    def universe_asof(self, t: date) -> list[str]:
        """t 시점 상장 중인 종목(상장일 ≤ t < 상폐일). 생존편향 차단."""
        out: list[str] = []
        for ticker, (listed, delisted) in self.listings.items():
            if listed <= t and (delisted is None or t < delisted):
                out.append(ticker)
        return sorted(out)

    def fundamentals_asof(self, ticker: str, t: date) -> AsOfFundamentals | None:
        """접수일 ≤ t 중 최신. 없으면 None(fail-open)."""
        items = [f for f in self.fundamentals.get(ticker, []) if f.rcept_date <= t]
        return max(items, key=lambda f: f.rcept_date) if items else None

    def price_on_or_after(self, ticker: str, t: date) -> Decimal | None:
        """t 당일 또는 이후 첫 종가(진입 T+1 시가 대용 — 평가/체결용)."""
        s = self.series.get(ticker)
        if s is None:
            return None
        for r in s.rows:
            if r.date >= t:
                return r.close
        return None


__all__ = ["AsOfFundamentals", "Panel", "TickerSeries"]
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_backtest_panel.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: 커밋**

```bash
git add backend/backtest/panel.py tests/test_backtest_panel.py tests/fixtures/backtest_synth.py
git commit -m "feat(backtest): 시점별 Panel + as-of 슬라이싱(룩어헤드·생존편향 가드) + 합성 픽스처"
```

---

## Task 4: `backtest/dart_client.py` — OpenDART 접수일 기준 재무

**Files:**
- Create: `backend/backtest/dart_client.py`
- Modify: `.env.example`
- Test: `tests/test_backtest_dart.py`

> 외부 API. TDD 루프는 HTTP를 mock 하고, 실데이터 확인은 Step 7 통합 스모크로 분리한다.

- [ ] **Step 1: 실패하는 테스트 작성 (HTTP mock — 접수일 as-of 선택 + 비율 산출)**

```python
# tests/test_backtest_dart.py
from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.backtest.dart_client import DartClient, _ratios_from_accounts


def test_ratios_from_accounts() -> None:
    # 전체재무제표 account_nm → thstrm_amount(당기), frmtrm_amount(전기)
    accounts = [
        {"account_nm": "당기순이익", "thstrm_amount": "1,200", "frmtrm_amount": "1,000"},
        {"account_nm": "자본총계", "thstrm_amount": "10,000", "frmtrm_amount": "9,000"},
        {"account_nm": "영업이익", "thstrm_amount": "1,500", "frmtrm_amount": "1,300"},
        {"account_nm": "매출액", "thstrm_amount": "20,000", "frmtrm_amount": "18,000"},
    ]
    r = _ratios_from_accounts(accounts)
    assert r["roe"] == (Decimal("1200") / Decimal("10000"))           # 0.12
    assert r["op_margin"] == (Decimal("1500") / Decimal("20000"))     # 0.075
    # 매출성장 = 20000/18000 - 1
    assert r["rev_growth"].quantize(Decimal("0.0001")) == Decimal("0.1111")


def test_pick_latest_filing_on_or_before(monkeypatch) -> None:
    client = DartClient(api_key="TEST")
    filings = [
        {"rcept_dt": "20230331", "reprt_code": "11011", "bsns_year": "2022"},
        {"rcept_dt": "20240331", "reprt_code": "11011", "bsns_year": "2023"},
    ]
    monkeypatch.setattr(client, "_list_filings", lambda corp, bgn, end: filings)
    picked = client.latest_filing_on_or_before("00126380", date(2023, 6, 1))
    assert picked is not None and picked["rcept_dt"] == "20230331"
    picked2 = client.latest_filing_on_or_before("00126380", date(2024, 6, 1))
    assert picked2 is not None and picked2["rcept_dt"] == "20240331"
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_backtest_dart.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.backtest.dart_client`.

- [ ] **Step 3: `dart_client.py` 구현**

```python
# backend/backtest/dart_client.py
"""OpenDART 접수일(rcept_dt) 기준 as-of 재무.

corpCode(zip) → corp_code 매핑, list.json → 접수일 목록, fnlttSinglAcntAll.json
→ 전체재무제표 계정. ROE/영업이익률/성장을 결정론 산출. 키는 .env(DART_API_KEY).
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

_BASE = "https://opendart.fss.or.kr/api"


def _amt(value: str | None) -> Decimal | None:
    if value is None or value in ("", "-"):
        return None
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def _account(accounts: list[dict[str, Any]], name: str, field: str) -> Decimal | None:
    for a in accounts:
        if a.get("account_nm") == name:
            return _amt(a.get(field))
    return None


def _ratios_from_accounts(accounts: list[dict[str, Any]]) -> dict[str, Decimal]:
    """계정 리스트 → roe·op_margin·rev_growth(가능한 것만)."""
    out: dict[str, Decimal] = {}
    ni = _account(accounts, "당기순이익", "thstrm_amount")
    eq = _account(accounts, "자본총계", "thstrm_amount")
    op = _account(accounts, "영업이익", "thstrm_amount")
    rev = _account(accounts, "매출액", "thstrm_amount")
    rev_prev = _account(accounts, "매출액", "frmtrm_amount")
    if ni is not None and eq and eq != 0:
        out["roe"] = ni / eq
    if op is not None and rev and rev != 0:
        out["op_margin"] = op / rev
    if rev is not None and rev_prev and rev_prev != 0:
        out["rev_growth"] = rev / rev_prev - Decimal("1")
    return out


class DartClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._key = api_key
        self._http = client or httpx.Client(timeout=20.0)
        self._corp_map: dict[str, str] | None = None  # stock_code(6) -> corp_code(8)

    def corp_code(self, ticker: str) -> str | None:
        if self._corp_map is None:
            self._corp_map = self._load_corp_map()
        return self._corp_map.get(ticker)

    def _load_corp_map(self) -> dict[str, str]:
        r = self._http.get(f"{_BASE}/corpCode.xml", params={"crtfc_key": self._key})
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            xml = z.read(z.namelist()[0]).decode("utf-8")
        import xml.etree.ElementTree as ET

        mapping: dict[str, str] = {}
        for el in ET.fromstring(xml).iter("list"):
            stock = (el.findtext("stock_code") or "").strip()
            corp = (el.findtext("corp_code") or "").strip()
            if len(stock) == 6 and corp:
                mapping[stock] = corp
        return mapping

    def _list_filings(self, corp_code: str, bgn: str, end: str) -> list[dict[str, Any]]:
        """정기보고서(pblntf_ty=A) 목록 — 접수일 포함."""
        r = self._http.get(
            f"{_BASE}/list.json",
            params={
                "crtfc_key": self._key,
                "corp_code": corp_code,
                "bgn_de": bgn,
                "end_de": end,
                "pblntf_ty": "A",
                "page_count": "100",
            },
        )
        r.raise_for_status()
        data = r.json()
        return list(data.get("list", [])) if data.get("status") == "000" else []

    def latest_filing_on_or_before(self, corp_code: str, t: date) -> dict[str, Any] | None:
        end = t.strftime("%Y%m%d")
        bgn = f"{t.year - 2}0101"  # 직전 2년 창
        filings = [f for f in self._list_filings(corp_code, bgn, end) if f.get("rcept_dt", "") <= end]
        if not filings:
            return None
        return max(filings, key=lambda f: f["rcept_dt"])

    def financial_ratios(self, corp_code: str, bsns_year: str, reprt_code: str) -> dict[str, Decimal]:
        r = self._http.get(
            f"{_BASE}/fnlttSinglAcntAll.json",
            params={
                "crtfc_key": self._key,
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
                "fs_div": "CFS",  # 연결 우선 (없으면 OFS 폴백 — 구현계획 외 확장)
            },
        )
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "000":
            return {}
        return _ratios_from_accounts(list(data.get("list", [])))


__all__ = ["DartClient", "_ratios_from_accounts"]
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_backtest_dart.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: `.env.example` 에 키 템플릿 추가**

`.env.example` 끝에 추가:

```
# 백테스트 하니스(오프라인) — OpenDART 무료 키 (https://opendart.fss.or.kr)
DART_API_KEY=
```

- [ ] **Step 6: 린트·타입**

Run: `uv run ruff check backend/backtest/dart_client.py && uv run mypy backend/`
Expected: 통과.

- [ ] **Step 7: (통합 스모크 — 네트워크·키 필요) 실데이터 1종목 확인**

Run (로컬, `.env` 에 키 있을 때):
```bash
uv run python -c "from backend.backtest.dart_client import DartClient; import os; c=DartClient(os.environ['DART_API_KEY']); cc=c.corp_code('005930'); import datetime as d; f=c.latest_filing_on_or_before(cc, d.date(2024,6,1)); print(cc, f and f['rcept_dt']); print(c.financial_ratios(cc, f['bsns_year'], f['reprt_code']))"
```
Expected: 삼성전자 corp_code + 접수일 + `{'roe':..., 'op_margin':..., 'rev_growth':...}`. **계정명/필드가 다르면** `_ratios_from_accounts` 의 account_nm("당기순이익"·"자본총계"·"영업이익"·"매출액") 또는 `fs_div` 를 응답에 맞춰 조정(이 스모크가 그 확인 지점).

- [ ] **Step 8: 커밋**

```bash
git add backend/backtest/dart_client.py tests/test_backtest_dart.py .env.example
git commit -m "feat(backtest): OpenDART 접수일 as-of 재무 클라이언트(ROE·마진·성장) + 테스트"
```

---

## Task 5: `backtest/loader.py` — pykrx + DART → Panel (SQLite 캐시)

**Files:**
- Create: `backend/backtest/loader.py`
- Test: `tests/test_backtest_loader.py` (pykrx/dart mock — 조립 로직만)

> pykrx 실호출은 느리고 네트워크 의존 → 단위테스트는 로더의 **조립/캐시 로직**을 mock 으로 검증하고, 실데이터는 Task 6 의 CLI 스모크에서 확인.

- [ ] **Step 1: 실패하는 테스트 작성 (mock 한 pykrx/dart 입력으로 Panel 조립)**

```python
# tests/test_backtest_loader.py
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from backend.backtest.loader import PanelLoader


def test_build_panel_from_mocked_sources(monkeypatch, tmp_path) -> None:
    loader = PanelLoader(dart=None, cache_dir=tmp_path)

    # pykrx OHLCV mock (한 종목)
    idx = pd.to_datetime(["2023-01-02", "2023-01-03"])
    ohlcv = pd.DataFrame(
        {"시가": [100, 101], "고가": [102, 103], "저가": [99, 100], "종가": [101, 102],
         "거래량": [1000, 1100], "거래대금": [2e10, 2.1e10]}, index=idx
    )
    monkeypatch.setattr(loader, "_ohlcv", lambda ticker, s, e: ohlcv)
    monkeypatch.setattr(loader, "_universe_dates", lambda s, e: {date(2023, 1, 2): ["000001"]})
    monkeypatch.setattr(loader, "_listing_range", lambda ticker: (date(2023, 1, 2), None))
    monkeypatch.setattr(loader, "_index_ohlcv", lambda s, e: ohlcv)
    monkeypatch.setattr(loader, "_fundamentals", lambda ticker: [])

    panel = loader.build(["000001"], date(2023, 1, 2), date(2023, 1, 3))
    rows = panel.rows_asof("000001", date(2023, 1, 3))
    assert len(rows) == 2
    assert rows[-1].close == Decimal("102")
    assert panel.turnover_asof("000001", date(2023, 1, 3)) == Decimal("21000000000")
    assert "000001" in panel.universe_asof(date(2023, 1, 2))
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_backtest_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.backtest.loader`.

- [ ] **Step 3: `loader.py` 구현**

```python
# backend/backtest/loader.py
"""pykrx(가격·밸류·유니버스) + DART(재무) + ^KS11 → Panel. SQLite 캐시.

Decimal 전면. pykrx DataFrame 의 한글 컬럼(시가/고가/저가/종가/거래량/거래대금)을 OHLCVRow 로 변환.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.backtest.dart_client import DartClient
from backend.backtest.panel import AsOfFundamentals, Panel, TickerSeries
from backend.schemas import OHLCVRow


def _d(v: Any) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


class PanelLoader:
    def __init__(self, dart: DartClient | None, cache_dir: Path) -> None:
        self._dart = dart
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ── 외부 소스(실호출 — 테스트에서 monkeypatch) ──────────────────────────
    def _ohlcv(self, ticker: str, start: date, end: date) -> Any:
        from pykrx import stock

        return stock.get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker)

    def _index_ohlcv(self, start: date, end: date) -> Any:
        import yfinance as yf

        hist = yf.Ticker("^KS11").history(start=start.isoformat(), end=end.isoformat(), auto_adjust=False)
        return hist.rename(columns={"Open": "시가", "High": "고가", "Low": "저가", "Close": "종가", "Volume": "거래량"})

    def _universe_dates(self, start: date, end: date) -> dict[date, list[str]]:
        from pykrx import stock

        # 월 1회 스냅샷으로 상장 종목 집합 추적(생존편향 완화) — 일별은 과중.
        out: dict[date, list[str]] = {}
        for y in range(start.year, end.year + 1):
            for m in range(1, 13):
                d0 = date(y, m, 1)
                if d0 < start or d0 > end:
                    continue
                ds = d0.strftime("%Y%m%d")
                tickers = stock.get_market_ticker_list(ds, market="KOSPI") + stock.get_market_ticker_list(
                    ds, market="KOSDAQ"
                )
                out[d0] = [str(t).zfill(6) for t in tickers]
        return out

    def _listing_range(self, ticker: str) -> tuple[date, date | None]:
        # pykrx 는 명시 상폐일 API 가 빈약 → OHLCV 존재구간으로 근사(첫/마지막 거래일).
        raise NotImplementedError  # build() 가 _ohlcv 구간으로 채운다(아래)

    def _fundamentals(self, ticker: str) -> list[AsOfFundamentals]:
        if self._dart is None:
            return []
        corp = self._dart.corp_code(ticker)
        if corp is None:
            return []
        out: list[AsOfFundamentals] = []
        seen: set[str] = set()
        # 직전 6년 정기보고서 접수일들을 수집(연 1~4건).
        for yr in range(date.today().year - 6, date.today().year + 1):
            filing = self._dart.latest_filing_on_or_before(corp, date(yr, 12, 31))
            if not filing or filing["rcept_dt"] in seen:
                continue
            seen.add(filing["rcept_dt"])
            ratios = self._dart.financial_ratios(corp, filing["bsns_year"], filing["reprt_code"])
            out.append(
                AsOfFundamentals(
                    rcept_date=date(int(filing["rcept_dt"][:4]), int(filing["rcept_dt"][4:6]), int(filing["rcept_dt"][6:8])),
                    roe=ratios.get("roe"),
                    op_margin=ratios.get("op_margin"),
                    eps_growth=ratios.get("rev_growth"),  # v1 성장 프록시(매출성장)
                )
            )
        return sorted(out, key=lambda f: f.rcept_date)

    # ── 변환 ────────────────────────────────────────────────────────────
    @staticmethod
    def _rows(frame: Any) -> tuple[list[OHLCVRow], dict[date, Decimal]]:
        rows: list[OHLCVRow] = []
        turnover: dict[date, Decimal] = {}
        for ts, rec in frame.iterrows():
            d = ts.date()
            rows.append(
                OHLCVRow(
                    date=d,
                    open=_d(rec["시가"]),
                    high=_d(rec["고가"]),
                    low=_d(rec["저가"]),
                    close=_d(rec["종가"]),
                    volume=_d(rec["거래량"]),
                )
            )
            if "거래대금" in rec:
                turnover[d] = _d(rec["거래대금"])
        return rows, turnover

    def build(self, tickers: list[str], start: date, end: date) -> Panel:
        series: dict[str, TickerSeries] = {}
        listings: dict[str, tuple[date, date | None]] = {}
        fundamentals: dict[str, list[AsOfFundamentals]] = {}
        for t in tickers:
            frame = self._ohlcv(t, start, end)
            if frame is None or frame.empty:
                continue
            rows, turnover = self._rows(frame)
            series[t] = TickerSeries(ticker=t, rows=rows, turnover_by_date=turnover)
            listings[t] = (rows[0].date, None)  # 상장 근사 = 첫 거래일(구간 내)
            fundamentals[t] = self._fundamentals(t)
        idx_rows, _ = self._rows(self._index_ohlcv(start, end))
        return Panel(series=series, fundamentals=fundamentals, listings=listings, index_rows=idx_rows)


__all__ = ["PanelLoader"]
```

> 주의: `_listing_range` 는 build 가 OHLCV 첫 거래일로 채우므로 직접 호출하지 않는다(테스트에서 monkeypatch 한 버전은 무시됨). 상폐 종목의 정확한 일자별 유니버스는 v1 근사(존재구간) — 한계는 리포트에 표기(설계서 §5).

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_backtest_loader.py -v`
Expected: PASS.

- [ ] **Step 5: 린트·타입**

Run: `uv run ruff check backend/backtest/loader.py && uv run mypy backend/`
Expected: 통과(mypy 가 pykrx/yfinance/pandas 는 ignore_missing_imports).

- [ ] **Step 6: 커밋**

```bash
git add backend/backtest/loader.py tests/test_backtest_loader.py
git commit -m "feat(backtest): pykrx+DART+^KS11 → Panel 로더(조립·변환) + 테스트"
```

---

# 마일스톤 M3 — 하니스 (시뮬·이벤트스터디·리포트·CLI)

## Task 6: `backtest/run.py` — 리밸런스 루프 + 포트폴리오 시뮬 + 이벤트스터디

**Files:**
- Create: `backend/backtest/run.py`
- Test: `tests/test_backtest_run.py`

- [ ] **Step 1: 실패하는 테스트 작성 (합성 패널 end-to-end — 결정론)**

```python
# tests/test_backtest_run.py
from __future__ import annotations

from datetime import date
from decimal import Decimal

from backend.backtest.run import BacktestConfig, run_backtest
from tests.fixtures.backtest_synth import make_panel


def test_run_backtest_baseline_deterministic() -> None:
    panel = make_panel()
    cfg = BacktestConfig(
        start=date(2023, 1, 2), end=date(2023, 9, 1),
        rebalance="monthly", top_n=1, cost_bps=Decimal("41"), preset="baseline",
        forward_horizons=(5, 20),
    )
    result = run_backtest(panel, cfg)
    # 결정론: 동일 입력 → 동일 출력
    assert run_backtest(panel, cfg).portfolio_nav == result.portfolio_nav
    # 포트폴리오 NAV 가 양수로 시작
    assert result.portfolio_nav[0] > 0
    # 이벤트스터디에 호라이즌별 단조성 키 존재
    assert set(result.event_study.keys()) == {5, 20}
    # 룩어헤드 가드: 각 픽의 forward-return 은 리밸런스일 이후 가격만 사용(내부 보장) — 스모크로 NaN 없음
    assert all(v.monotonicity is not None for v in result.event_study.values())
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_backtest_run.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.backtest.run`.

- [ ] **Step 3: `run.py` 구현**

```python
# backend/backtest/run.py
"""리밸런스 루프 + 포트폴리오 시뮬 + 이벤트스터디. 결정론.

각 리밸런스일 T: universe_asof → build_candidate(rows≤T) → scoring(무수정) → 상위 N 등가중.
forward-return 은 T 이후 가격으로만 평가(룩어헤드 0). 비용은 회전분에 bps 차감.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from backend import scoring as sc
from backend.backtest import metrics
from backend.backtest.panel import Panel
from backend.config import Settings, get_settings
from backend.factors import build_candidate

_REBAL_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30}


@dataclass(frozen=True)
class BacktestConfig:
    start: date
    end: date
    rebalance: str = "weekly"
    top_n: int = 20
    cost_bps: Decimal = Decimal("41")
    preset: str = "baseline"          # baseline | quality_tilt
    forward_horizons: tuple[int, ...] = (5, 20, 60)


@dataclass(frozen=True)
class EventStudyBucket:
    monotonicity: Decimal
    mae: Decimal
    win_rate: Decimal
    n: int


@dataclass(frozen=True)
class BacktestResult:
    portfolio_nav: list[Decimal]
    rebalance_dates: list[date]
    event_study: dict[int, EventStudyBucket]
    turnover_count: int


def _rebalance_dates(panel: Panel, cfg: BacktestConfig) -> list[date]:
    trading = sorted({r.date for s in panel.series.values() for r in s.rows if cfg.start <= r.date <= cfg.end})
    if not trading:
        return []
    step = _REBAL_DAYS[cfg.rebalance]
    out, last = [], None
    for d in trading:
        if last is None or (d - last) >= timedelta(days=step):
            out.append(d)
            last = d
    return out


def _index_momentum(panel: Panel, t: date, settings: Settings) -> Decimal:
    idx = [r for r in panel.index_rows_asof(t)][-settings.lookback_days :]
    return sc.compute_momentum(idx) if len(idx) >= 2 else Decimal("0")


def _score_at(panel: Panel, t: date, settings: Settings) -> list[tuple[str, Decimal]]:
    idx_mom = _index_momentum(panel, t, settings)
    cands: list[sc.Candidate] = []
    for ticker in panel.universe_asof(t):
        rows = panel.rows_asof(ticker, t)
        if len(rows) < settings.ma200_window:
            continue
        cands.append(
            build_candidate(
                ticker=ticker, rows=rows, w52_high=None, index_momentum=idx_mom,
                turnover=panel.turnover_asof(ticker, t), min_turnover=settings.min_turnover_krw,
                settings=settings,
            )
        )
    eligible = [c for c in cands if c.eligible]
    scored = sc.score_candidates(eligible, settings)
    return sorted(((tk, sv) for tk, (sv, _) in scored.items()), key=lambda x: x[1], reverse=True)


def _fwd_return(panel: Panel, ticker: str, t: date, horizon: int) -> Decimal | None:
    rows = panel.series[ticker].rows
    future = [r for r in rows if r.date > t]
    if not future:
        return None
    entry = future[0].close
    target = future[min(horizon, len(future)) - 1].close
    return (target - entry) / entry if entry > 0 else None


def _mae(panel: Panel, ticker: str, t: date, horizon: int) -> Decimal | None:
    rows = panel.series[ticker].rows
    future = [r for r in rows if r.date > t][:horizon]
    if not future:
        return None
    entry = future[0].close
    return metrics.max_adverse_excursion(entry, [r.low for r in future])


def run_backtest(panel: Panel, cfg: BacktestConfig) -> BacktestResult:
    settings = get_settings()
    dates = _rebalance_dates(panel, cfg)
    nav = [Decimal("1")]
    held: set[str] = set()
    turnover_count = 0
    es_scores: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
    es_fwd: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
    es_mae: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}

    for i, t in enumerate(dates):
        ranked = _score_at(panel, t, settings)
        picks = [tk for tk, _ in ranked[: cfg.top_n]]
        # 이벤트스터디: 적격·점수>0 픽의 (점수, fwd, MAE) 누적
        for tk, sv in ranked:
            for h in cfg.forward_horizons:
                fr = _fwd_return(panel, tk, t, h)
                mae = _mae(panel, tk, t, h)
                if fr is not None and mae is not None:
                    es_scores[h].append(sv)
                    es_fwd[h].append(fr)
                    es_mae[h].append(mae)
        # 포트폴리오: 다음 리밸런스까지 등가중 보유 수익(있을 때만)
        if i + 1 < len(dates) and picks:
            nxt = dates[i + 1]
            rets = []
            for tk in picks:
                a = panel.price_on_or_after(tk, t)
                b = panel.price_on_or_after(tk, nxt)
                if a and b and a > 0:
                    rets.append((b - a) / a)
            if rets:
                gross = sum(rets, Decimal("0")) / Decimal(len(rets))
                churn = len(set(picks) ^ held)
                cost = (cfg.cost_bps / Decimal("10000")) * (Decimal(churn) / Decimal(max(len(picks), 1)))
                nav.append(nav[-1] * (Decimal("1") + gross - cost))
                turnover_count += churn
                held = set(picks)

    event_study = {
        h: EventStudyBucket(
            monotonicity=metrics.spearman_monotonicity(es_scores[h], es_fwd[h]),
            mae=(sum(es_mae[h], Decimal("0")) / Decimal(len(es_mae[h]))) if es_mae[h] else Decimal("0"),
            win_rate=metrics.win_rate(es_fwd[h]),
            n=len(es_fwd[h]),
        )
        for h in cfg.forward_horizons
    }
    return BacktestResult(
        portfolio_nav=nav, rebalance_dates=dates, event_study=event_study, turnover_count=turnover_count
    )


__all__ = ["BacktestConfig", "BacktestResult", "EventStudyBucket", "run_backtest"]
```

> 참고: v1 `preset="quality_tilt"` 는 본 Task 에서 점수 합성을 바꾸지 않는다(baseline 경로). 퀄리티 틸트 점수합성은 이벤트스터디가 ROE/마진/성장의 *예측력*을 먼저 보고 결정하므로, 틸트 적용은 Task 8 이후 별도 단계로 둔다. 본 Task 의 이벤트스터디는 **점수 vs forward-return** 의 베이스라인 단조성/MAE 를 산출(핵심 진단).

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_backtest_run.py -v`
Expected: PASS.

- [ ] **Step 5: 린트·타입**

Run: `uv run ruff check backend/backtest/run.py && uv run mypy backend/`
Expected: 통과.

- [ ] **Step 6: 커밋**

```bash
git add backend/backtest/run.py tests/test_backtest_run.py
git commit -m "feat(backtest): 리밸런스 루프·포트폴리오 시뮬·이벤트스터디(룩어헤드 0) + 테스트"
```

---

## Task 7: `backtest/report.py` — 마크다운 + JSON 리포트

**Files:**
- Create: `backend/backtest/report.py`
- Test: `tests/test_backtest_run.py` (리포트 테스트 추가)

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_backtest_run.py` 끝에 추가:

```python
def test_report_render_has_assumptions_and_metrics() -> None:
    from backend.backtest.report import render_json, render_markdown
    from backend.backtest.run import BacktestConfig

    panel = make_panel()
    cfg = BacktestConfig(start=date(2023, 1, 2), end=date(2023, 9, 1), rebalance="monthly", top_n=1)
    result = run_backtest(panel, cfg)
    md = render_markdown(result, cfg)
    assert "가정" in md and "이벤트스터디" in md and "41" in md   # 비용 가정 노출
    js = render_json(result, cfg)
    assert js["config"]["cost_bps"] == "41"
    assert "event_study" in js
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_backtest_run.py::test_report_render_has_assumptions_and_metrics -v`
Expected: FAIL — `ModuleNotFoundError: backend.backtest.report`.

- [ ] **Step 3: `report.py` 구현**

```python
# backend/backtest/report.py
"""BacktestResult → 마크다운(사람) + JSON(기계). Decimal 은 문자열 직렬화."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from backend.backtest.metrics import cagr, max_drawdown
from backend.backtest.run import BacktestConfig, BacktestResult


def _summary(result: BacktestResult, cfg: BacktestConfig) -> dict[str, Any]:
    nav = result.portfolio_nav
    years = Decimal(max(len(result.rebalance_dates), 1)) / Decimal("52" if cfg.rebalance == "weekly" else "12")
    return {
        "final_nav": nav[-1],
        "total_return": nav[-1] - Decimal("1"),
        "cagr": cagr(nav[0], nav[-1], years=years) if len(nav) > 1 else Decimal("0"),
        "mdd": max_drawdown(nav),
        "turnover_count": result.turnover_count,
    }


def render_markdown(result: BacktestResult, cfg: BacktestConfig) -> str:
    s = _summary(result, cfg)
    lines = [
        f"# 백테스트 리포트 — preset={cfg.preset}",
        "",
        "## 가정",
        f"- 기간 {cfg.start}~{cfg.end} · 리밸런스 {cfg.rebalance} · 상위 {cfg.top_n} · 등가중 · 비용 {cfg.cost_bps}bp/회전 · 무레버리지",
        f"- 벤치마크 ^KS11 · 리밸런스 {len(result.rebalance_dates)}회 · 회전 {result.turnover_count}",
        "",
        "## 포트폴리오",
        f"- 최종 NAV {s['final_nav']:.4f} · 누적 {s['total_return']:.4f} · CAGR {s['cagr']:.4f} · MDD {s['mdd']:.4f}",
        "",
        "## 이벤트스터디 (점수 vs forward-return)",
        "| 호라이즌 | 단조성(Spearman) | 평균 MAE | 승률 | N |",
        "|---|---|---|---|---|",
    ]
    for h, b in sorted(result.event_study.items()):
        lines.append(f"| {h}일 | {b.monotonicity} | {b.mae:.4f} | {b.win_rate} | {b.n} |")
    lines += ["", "> 수익 보장 없음. 룩어헤드 0(≤T 슬라이스)·생존편향 근사(상장구간) 가드 적용."]
    return "\n".join(lines)


def render_json(result: BacktestResult, cfg: BacktestConfig) -> dict[str, Any]:
    return {
        "config": {
            "start": cfg.start.isoformat(), "end": cfg.end.isoformat(), "rebalance": cfg.rebalance,
            "top_n": cfg.top_n, "cost_bps": str(cfg.cost_bps), "preset": cfg.preset,
        },
        "summary": {k: str(v) for k, v in _summary(result, cfg).items()},
        "event_study": {
            str(h): {"monotonicity": str(b.monotonicity), "mae": str(b.mae), "win_rate": str(b.win_rate), "n": b.n}
            for h, b in result.event_study.items()
        },
    }


__all__ = ["render_json", "render_markdown"]
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_backtest_run.py -v`
Expected: PASS (모두).

- [ ] **Step 5: 커밋**

```bash
git add backend/backtest/report.py tests/test_backtest_run.py
git commit -m "feat(backtest): 마크다운+JSON 리포트(가정·포트폴리오·이벤트스터디)"
```

---

## Task 8: CLI 엔트리 (`python -m backend.backtest.run`)

**Files:**
- Modify: `backend/backtest/run.py` (argparse `main` 추가)

- [ ] **Step 1: `run.py` 끝에 CLI 추가**

```python
def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import os
    from datetime import datetime
    from pathlib import Path

    from backend.backtest.dart_client import DartClient
    from backend.backtest.loader import PanelLoader
    from backend.backtest.report import render_json, render_markdown

    p = argparse.ArgumentParser(description="KR 백테스트 검증 하니스 (오프라인)")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--rebalance", default="weekly", choices=list(_REBAL_DAYS))
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--cost-bps", default="41")
    p.add_argument("--preset", default="baseline", choices=["baseline", "quality_tilt"])
    p.add_argument("--tickers", default="", help="콤마구분 6자리 코드. 비우면 유니버스 자동(느림)")
    p.add_argument("--out", default="data/backtest")
    args = p.parse_args(argv)

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    dart_key = os.environ.get("DART_API_KEY")
    loader = PanelLoader(dart=DartClient(dart_key) if dart_key else None, cache_dir=Path(args.out) / "cache")
    tickers = [t.strip().zfill(6) for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        from pykrx import stock

        tickers = [str(t).zfill(6) for t in stock.get_market_ticker_list(args.end.replace("-", ""), market="KOSPI")]
    panel = loader.build(tickers, start, end)
    cfg = BacktestConfig(
        start=start, end=end, rebalance=args.rebalance, top_n=args.top_n,
        cost_bps=Decimal(args.cost_bps), preset=args.preset,
    )
    result = run_backtest(panel, cfg)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"report_{args.preset}.md").write_text(render_markdown(result, cfg), encoding="utf-8")
    (out_dir / f"report_{args.preset}.json").write_text(
        json.dumps(render_json(result, cfg), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(render_markdown(result, cfg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 린트·타입·전체 테스트**

Run: `uv run ruff check backend/ && uv run ruff format backend/ && uv run mypy backend/ && uv run pytest -q`
Expected: 전부 통과.

- [ ] **Step 3: (통합 스모크 — 네트워크·DART 키 필요) 소수 종목 실행**

Run:
```bash
uv run python -m backend.backtest.run --start 2020-01-01 --end 2024-12-31 \
  --rebalance monthly --top-n 5 --preset baseline \
  --tickers 005930,000660,035420,051910,005380
```
Expected: `data/backtest/report_baseline.md` + `.json` 생성, 콘솔에 이벤트스터디 표(단조성/MAE/승률) 출력. **현 점수 베이스라인의 단조성이 낮거나 음수·MAE 가 큰지** 확인 = 리웨이트 스펙 §1 진단의 정량 확인.

- [ ] **Step 4: 커밋**

```bash
git add backend/backtest/run.py
git commit -m "feat(backtest): CLI 엔트리(python -m backend.backtest.run) — md+json 리포트 출력"
```

---

## 완료 정의 (DoD)

- [ ] `uv run pytest -q` 전부 통과 · `uv run ruff check` · `uv run ruff format --check` · `uv run mypy backend/` 통과.
- [ ] 기존 `tests/test_engine.py`·`test_scoring.py` 회귀 없음(Task 1 행동 변경 0).
- [ ] 통합 스모크(Task 4 Step7, Task 8 Step3)로 DART 접수일 as-of + 베이스라인 리포트 생성 확인.
- [ ] 베이스라인 이벤트스터디 수치(단조성·MAE)를 리웨이트 스펙 §1 진단과 대조.

---

## Self-Review (작성자 점검 결과)

**1. 스펙 커버리지:** 설계서 각 섹션 → Task 매핑 — 공유 스코어러(§2)=Task1 · 메트릭(§6)=Task2 · 패널/as-of·룩어헤드·생존편향(§4·§5)=Task3 · DART 접수일(§3)=Task4 · 로더(§3)=Task5 · 리밸런스·시뮬·이벤트스터디(§4·§6)=Task6 · 리포트(§6)=Task7 · CLI(§6)=Task8 · 비용/OOS/관측성(§5·§7)=run/report+카운트. **퀄리티 틸트 점수합성**은 설계서 "검증 먼저, 배선 나중"대로 이벤트스터디 검증 후 별도 단계(Task6 참고 노트에 명시) — 의도된 deferral.
**2. Placeholder 스캔:** `_listing_range` 의 `NotImplementedError` 는 의도적(주석으로 build 가 대체 설명) — 실행 경로 아님. "TBD/TODO" 없음.
**3. 타입 일관성:** `build_candidate`(Task1) 시그니처 ↔ run `_score_at` 호출 일치. `Panel` 메서드(rows_asof·universe_asof·fundamentals_asof·turnover_asof·price_on_or_after·index_rows_asof) ↔ run/test 사용처 일치. `BacktestConfig`/`EventStudyBucket`/`BacktestResult` 필드 ↔ report 사용처 일치. `_ratios_from_accounts` 키(roe/op_margin/rev_growth) ↔ loader 매핑 일치.

---

## 다음(이 플랜 밖, 본 하니스로 게이팅)

1. 이벤트스터디가 ROE/마진/성장의 예측력 확인 시 → `quality_tilt` 점수합성 구현 + 프로덕션 배선.
2. 추세 진입편향(trend_template·pullback_3pos·extension_guard) 구현 후 하니스 재실행으로 MAE·단조성 개선 검증.
3. US 시장·레짐 게이트·가치 렌즈·종가 배지.
