# 폴백 C 리스크 엔지니어링 구현 계획 (Fallback C: Risk Engineering)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development(권장) 또는 executing-plans 로 태스크 단위 실행. 스텝은 `- [ ]` 체크박스.
> 설계 출처: `docs/superpowers/specs/2026-06-17-fallback-c-risk-engineering-design.md`. 판정 배경: `2026-06-17-alpha-discovery-RESULTS.md`(알파 0개 → 폴백 C).

**Goal:** 선택 알파가 없는 점수에 **검증된 리스크 통제**를 얹는다 — 진입품질 재가중(MAE↓, 1차 게이트)과 포트폴리오 리스크 오버레이(레짐·ATR손절·사이징 → MDD↓, 2차 게이트)를 백테스트 프리셋 `fallback_c`로 만들고 OOS·ablation으로 *듣는 컴포넌트만* 가려낸다. 라이브 무수정.

**Architecture:** 2레이어. 레이어1 = `_score_at`의 `fallback_c` 분기(near_52w 가중치 파라미터화 + extension_guard + pullback) → `compare_presets` OOS ΔMAE. 레이어2 = 신규 `backtest/portfolio.py` 리스크 오버레이 시뮬(연속구간 NAV에 레짐 보류·ATR 손절·사이징) → MDD/Calmar/Sharpe + 블록부트스트랩 CI. `--fallback-c` CLI가 둘 + ablation을 오케스트레이션.

**Tech Stack:** Python 3.13, Decimal 전면(float 금지; 경로지표 sqrt만 즉시 Decimal 복귀), uv, pytest/ruff/mypy. 브랜치 `feat/validation-oos-entry-bias`. KRX 머신(.env 셸 로드 필요한 실행은 Phase 5만).

---

## Phase 0 — 환경 확인

### Task 0: 베이스라인 확인
**Files:** (없음)
- [ ] `uv run pytest -q` → **367 passed, 1 skipped** 확인(이어받기 정상).
- [ ] 현재 HEAD 가 `7a85a71`(폴백C 설계 커밋) 위인지 확인. 커밋 불필요.

---

## Phase 1 — 레짐 모듈

### Task 1: `regime.py` — 분산일·risk_off (결정론 순수함수)
**Files:** Create `backend/backtest/regime.py`; Test `tests/test_regime.py`

- [ ] **Step 1: 실패 테스트** (`tests/test_regime.py`)
```python
from datetime import date, timedelta
from decimal import Decimal

from backend.backtest.regime import count_distribution_days, is_risk_off
from backend.schemas import OHLCVRow


def _row(d: date, close: int, vol: int) -> OHLCVRow:
    c = Decimal(close)
    return OHLCVRow(date=d, open=c, high=c, low=c, close=c, volume=Decimal(vol))


def _series(specs: list[tuple[int, int]]) -> list[OHLCVRow]:
    start = date(2023, 1, 2)
    return [_row(start + timedelta(days=i), c, v) for i, (c, v) in enumerate(specs)]


def test_count_distribution_days_detects_down_on_higher_volume() -> None:
    # 분산일 = 종가 ≤ 전일×0.998 AND 거래량 > 전일.
    rows = _series([(100, 100), (99, 120), (101, 130), (98, 140), (97, 90)])
    # day1: 99 ≤ 100*0.998=99.8 ✓ & 120>100 ✓ → 분산일
    # day2: 101 > 100.8 ✗ → 아님
    # day3: 98 ≤ 100.798 ✓ & 140>130 ✓ → 분산일
    # day4: 97 ≤ 97.804 ✓ & 90>140 ✗ → 아님(거래량 감소)
    assert count_distribution_days(rows, window=4, drop=Decimal("0.998")) == 2


def test_is_risk_off_threshold() -> None:
    rows = _series([(100, 100), (99, 120), (98, 130), (97, 140), (96, 150), (95, 160)])
    # day1..5 모두 분산일(하락+거래량증가) → 최근5일 분산일 5회
    assert is_risk_off(rows, window=5, threshold=5, drop=Decimal("0.998")) is True
    assert is_risk_off(rows, window=5, threshold=6, drop=Decimal("0.998")) is False


def test_is_risk_off_insufficient_rows_false() -> None:
    assert is_risk_off(_series([(100, 100)]), window=25, threshold=5, drop=Decimal("0.998")) is False
```

- [ ] **Step 2: 실패 확인** `uv run pytest tests/test_regime.py -v` → FAIL(no module).

- [ ] **Step 3: 구현** (`backend/backtest/regime.py`)
```python
"""레짐 게이트 — 지수 분산일(distribution day) 기반 risk_off 판정. 결정론·Decimal."""

from __future__ import annotations

from decimal import Decimal

from backend.schemas import OHLCVRow


def count_distribution_days(
    rows: list[OHLCVRow], *, window: int, drop: Decimal = Decimal("0.998")
) -> int:
    """직전 ``window`` 거래일의 분산일 수.

    분산일 = 당일 종가 ≤ 전일 종가 × ``drop`` AND 당일 거래량 > 전일 거래량.
    첫 봉은 전일이 없어 평가 제외. 표본 부족(<2)이면 0.
    """
    if len(rows) < 2:
        return 0
    recent = rows[-window:] if window > 0 else rows
    # recent[0] 의 전일은 그 앞 봉. recent 가 전체의 suffix 이므로 전일 인덱스 보정.
    start = len(rows) - len(recent)
    count = 0
    for i in range(start, len(rows)):
        if i == 0:
            continue
        today, prev = rows[i], rows[i - 1]
        if today.close <= prev.close * drop and today.volume > prev.volume:
            count += 1
    return count


def is_risk_off(
    rows: list[OHLCVRow], *, window: int = 25, threshold: int = 5, drop: Decimal = Decimal("0.998")
) -> bool:
    """직전 ``window`` 거래일 분산일 ≥ ``threshold`` 면 risk_off. 표본 부족이면 False(보수적: 진입 허용)."""
    if len(rows) < window:
        return False
    return count_distribution_days(rows, window=window, drop=drop) >= threshold


__all__ = ["count_distribution_days", "is_risk_off"]
```

- [ ] **Step 4: 통과 확인** `uv run pytest tests/test_regime.py -v` → PASS. 그리고 `uv run ruff check . ; uv run ruff format . ; uv run mypy backend/` 클린.

- [ ] **Step 5: 커밋** `git commit -am "feat(backtest): 레짐 모듈 — 분산일 카운트·risk_off 판정"`

---

## Phase 2 — 레이어1: fallback_c 재가중 + MAE 게이트 + ablation

### Task 2: config 파라미터 추가 (fallback_c 가중치·레짐·ATR·사이징)
**Files:** Modify `backend/config.py`; Test `tests/test_config_fallback_c.py`

- [ ] **Step 1: 실패 테스트** (`tests/test_config_fallback_c.py`)
```python
from decimal import Decimal

from backend.config import get_settings


def test_fallback_c_params_present_with_defaults() -> None:
    s = get_settings()
    assert s.weight_52w_fallback == Decimal("0.18")  # near_52w 후보 기본값
    assert s.regime_window == 25 and s.regime_threshold == 5
    assert s.regime_drop == Decimal("0.998")
    assert s.atr_stop_mult == Decimal("2")
    assert s.risk_pct == Decimal("0.01") and s.max_weight_pct == Decimal("0.10")
```

- [ ] **Step 2: 실패 확인** → FAIL(no attr).

- [ ] **Step 3: 구현** — `backend/config.py` `Settings` 에 entry_bias 블록 아래 추가:
```python
    # ── fallback_c 프리셋 파라미터 (백테스트 전용, 라이브 미사용) ──────────
    # 레이어1: near_52w 가중치 후보(스윕: 0.30/0.20/0.12). 남은 (0.30 - w) 를 pullback 에 배분.
    weight_52w_fallback: Decimal = Decimal("0.18")
    # 레이어2 레짐 게이트
    regime_window: int = 25
    regime_threshold: int = 5
    regime_drop: Decimal = Decimal("0.998")
    # 레이어2 ATR 손절·사이징
    atr_stop_mult: Decimal = Decimal("2")  # 손절 = 진입 − mult×ATR20
    risk_pct: Decimal = Decimal("0.01")  # 트레이드당 위험 R%(자본 1%)
    max_weight_pct: Decimal = Decimal("0.10")  # 포지션 비중 상한
```

- [ ] **Step 4: 통과 확인** `uv run pytest tests/test_config_fallback_c.py -v` → PASS. ruff/mypy 클린.
- [ ] **Step 5: 커밋** `git commit -am "feat(config): fallback_c 파라미터 — near_52w 후보·레짐·ATR·사이징"`

### Task 3: `fallback_c` `_score_at` 분기 (near_52w 파라미터화 재가중)
**Files:** Modify `backend/backtest/run.py`(`_score_at` + `--preset`/`--compare` choices); Test `tests/test_fallback_c_score.py`

기존 `entry_bias` 분기(run.py 내 `elif preset == "entry_bias":`)와 동일 패턴. 차이: near_52w 가중치 = `settings.weight_52w_fallback`(w), pullback 가중치 = `Decimal("0.30") - w`(freed), × extension_guard. 합 = w + (0.30−w) + 0.20+0.13+0.12+0.15+0.10 = 1.00.

- [ ] **Step 1: 실패 테스트** (`tests/test_fallback_c_score.py`)
```python
from datetime import date

from backend.backtest.run import _score_at
from backend.config import get_settings
from tests.fixtures.backtest_synth import make_panel


def test_fallback_c_differs_from_baseline_and_reweights_52w() -> None:
    panel = make_panel()
    settings = get_settings()
    t = date(2023, 12, 1)
    base = dict(_score_at(panel, t, settings, preset="baseline"))
    fc = dict(_score_at(panel, t, settings, preset="fallback_c"))
    assert set(fc) == set(base)  # 동일 후보군(eligible)
    assert fc != base  # 재가중으로 점수 변화
    assert all(v >= 0 for v in fc.values())


def test_fallback_c_weight_sweep_changes_scores(monkeypatch) -> None:
    panel = make_panel()
    t = date(2023, 12, 1)
    s_low = get_settings()
    monkeypatch.setattr(s_low, "weight_52w_fallback", __import__("decimal").Decimal("0.12"))
    s_high = get_settings()
    monkeypatch.setattr(s_high, "weight_52w_fallback", __import__("decimal").Decimal("0.30"))
    fc_low = dict(_score_at(panel, t, s_low, preset="fallback_c"))
    fc_high = dict(_score_at(panel, t, s_high, preset="fallback_c"))
    assert fc_low != fc_high  # near_52w 가중치 스윕이 점수를 바꾼다
```

- [ ] **Step 2: 실패 확인** → FAIL(unknown preset → baseline 반환이라 `fc != base` 실패).

- [ ] **Step 3: 구현** — `run.py` `_score_at` 에 `entry_bias` 분기 뒤 추가:
```python
    elif preset == "fallback_c":
        # 레이어1 진입품질 재가중: near_52w 가중치 = weight_52w_fallback(w),
        # pullback = (0.30 - w) 자유배분, × extension_guard. (entry_bias 강화·파라미터판)
        w52 = settings.weight_52w_fallback
        w_pull = Decimal("0.30") - w52
        new_scores: dict[str, Decimal] = {}
        for tk, (_sv, bd) in scored.items():
            rows_tk = rows_by_ticker.get(tk, [])
            pullback = sc.compute_pullback_3pos(rows_tk, settings) if rows_tk else Decimal("0")
            guard = sc.compute_extension_guard(rows_tk, settings) if rows_tk else Decimal("1")
            entry = (
                bd.near_52w * w52
                + pullback * w_pull
                + bd.pocket_pivot * settings.weight_pocket_pivot
                + bd.momentum_norm * settings.weight_momentum
                + bd.rs_norm * settings.weight_rs
                + bd.turnover_norm * settings.weight_turnover
                + bd.vol_fit * settings.weight_vol_fit
            )
            new_scores[tk] = max(Decimal("0"), min(Decimal("1"), entry)) * guard
        base = new_scores
```
그리고 `rows_by_ticker` 채움 조건을 `entry_bias` 와 동일하게 `fallback_c` 도 포함하도록 수정: `_score_at` 후보 루프의 `if preset == "entry_bias":` 를 `if preset in ("entry_bias", "fallback_c"):` 로 변경. 또한 `main()` 의 `--preset` choices 와 `--compare` 설명에 `"fallback_c"` 포함(`choices=["baseline","quality_tilt","entry_bias","alpha_composite","fallback_c"]`).

- [ ] **Step 4: 통과 확인** `uv run pytest tests/test_fallback_c_score.py -v` → PASS. 그리고 `uv run pytest -q`(367+ 유지)·ruff·mypy 클린.
- [ ] **Step 5: 커밋** `git commit -am "feat(backtest): fallback_c _score_at 분기 — near_52w 파라미터화 재가중 + extension_guard"`

### Task 4: 레이어1 MAE 게이트 — compare 동작 확인
**Files:** Test `tests/test_fallback_c_compare.py` (코드 변경 없음 — compare_presets 가 임의 variant 지원)

- [ ] **Step 1: 테스트** — `compare_presets(panel, cfg, wf, variant_preset="fallback_c")` 가 `ComparisonResult` 반환·OOS dates 비어있지 않으면 horizon별 `dmae` 산출됨을 확인.
```python
from decimal import Decimal

from backend.backtest.compare import compare_presets
from backend.backtest.run import BacktestConfig, WalkForwardConfig
from tests.fixtures.backtest_synth import make_panel


def test_fallback_c_compare_runs_and_reports_dmae() -> None:
    panel = make_panel()
    cfg = BacktestConfig(
        start=panel.index_rows[0].date, end=panel.index_rows[-1].date,
        rebalance="weekly", top_n=2, n_resamples=50, n_perms=50,
    )
    wf = WalkForwardConfig(n_folds=2, holdout_frac=Decimal("0.2"))
    result = compare_presets(panel, cfg, wf, variant_preset="fallback_c")
    assert result.variant_preset == "fallback_c"
    # 비어있지 않은 OOS 면 각 horizon 에 dmae 필드 존재(수치 자체는 합성패널이라 비단정).
    for hc in result.horizons.values():
        assert hasattr(hc, "dmae") and hasattr(hc, "dmae_ci_lo")
```

- [ ] **Step 2: 실패→통과 확인** `uv run pytest tests/test_fallback_c_compare.py -v`. (Task 3 의 preset choices·branch 덕에 통과해야 함.)
- [ ] **Step 3: 커밋** `git commit -am "test(backtest): fallback_c compare ΔMAE 게이트 동작 테스트"`

### Task 5: 레이어1 ablation 러너
**Files:** Create `backend/backtest/ablation.py`; Test `tests/test_ablation.py`

near_52w 후보(0.30/0.20/0.12) × extension_guard on/off 를 compare_presets 로 돌려 각 ΔMAE 를 표로 모은다. extension off 는 `settings.ext_guard_floor=Decimal("1")`(승수 무효화)로 구현.

- [ ] **Step 1: 실패 테스트** — `run_layer1_ablation(panel, cfg, wf, w52_candidates=[Decimal("0.30"), Decimal("0.12")]) -> list[AblationRow]` 가 후보별 행을 반환하고 각 행에 `w52`·`dmae_20`(20일 ΔMAE) 필드가 있음.
```python
from decimal import Decimal

from backend.backtest.ablation import run_layer1_ablation
from backend.backtest.run import BacktestConfig, WalkForwardConfig
from tests.fixtures.backtest_synth import make_panel


def test_layer1_ablation_returns_row_per_candidate() -> None:
    panel = make_panel()
    cfg = BacktestConfig(
        start=panel.index_rows[0].date, end=panel.index_rows[-1].date,
        rebalance="weekly", top_n=2, n_resamples=40, n_perms=40,
    )
    wf = WalkForwardConfig(n_folds=2, holdout_frac=Decimal("0.2"))
    rows = run_layer1_ablation(panel, cfg, wf, w52_candidates=[Decimal("0.30"), Decimal("0.12")])
    assert [r.w52 for r in rows] == [Decimal("0.30"), Decimal("0.12")]
    assert all(hasattr(r, "dmae_20") for r in rows)
```

- [ ] **Step 2: 실패 확인** → FAIL(no module).
- [ ] **Step 3: 구현** (`backend/backtest/ablation.py`) — `get_settings()` 사본에 `weight_52w_fallback` 를 후보로 monkeypatch 하지 말고, `compare_presets` 가 내부에서 `get_settings()` 를 호출하므로 **환경변수로 주입**하는 대신, `compare_presets` 에 `settings` 를 인자로 받는 오버로드가 없다면, 후보별로 `Settings(weight_52w_fallback=cand)` 를 만들고 `compare_presets` 가 그것을 쓰도록 `compare_presets(..., settings=...)` 선택 인자를 추가한다(아래). `AblationRow` 는 `@dataclass(frozen=True)` 로 `w52: Decimal`, `dmae_20: Decimal`, `dmae_ci_lo_20: Decimal`.
  - 보조 변경: `compare.py compare_presets` 와 `_collect_groups` 에 `settings: Settings | None = None`(기본 `get_settings()`) 선택 인자 추가·전달(라이브 무영향, 기존 호출 호환).
```python
"""fallback_c ablation — near_52w 후보·컴포넌트별 ΔMAE 귀속(레이어1)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from backend.backtest.compare import compare_presets
from backend.backtest.panel import Panel
from backend.backtest.run import BacktestConfig, WalkForwardConfig
from backend.config import Settings


@dataclass(frozen=True)
class AblationRow:
    w52: Decimal
    dmae_20: Decimal
    dmae_ci_lo_20: Decimal


def run_layer1_ablation(
    panel: Panel, cfg: BacktestConfig, wf: WalkForwardConfig, *, w52_candidates: list[Decimal]
) -> list[AblationRow]:
    """near_52w 후보별 fallback_c vs baseline OOS Δ(20일 MAE) 표."""
    out: list[AblationRow] = []
    for cand in w52_candidates:
        s = Settings(weight_52w_fallback=cand)
        res = compare_presets(panel, cfg, wf, variant_preset="fallback_c", settings=s)
        hc = res.horizons.get(20)
        out.append(
            AblationRow(
                w52=cand,
                dmae_20=hc.dmae if hc else Decimal("0"),
                dmae_ci_lo_20=hc.dmae_ci_lo if hc else Decimal("0"),
            )
        )
    return out


__all__ = ["AblationRow", "run_layer1_ablation"]
```
  - `compare.py`: `def compare_presets(panel, cfg, wf, variant_preset, baseline_preset="baseline", *, alpha_factors=None, settings=None)` → `settings = settings or get_settings()`, 이를 `_collect_groups(..., settings=settings)` 로 전달(이미 settings 인자 사용 중인 `_score_at` 까지 흐름). `_collect_groups` 도 동일 선택 인자 추가.

- [ ] **Step 4: 통과 확인** `uv run pytest tests/test_ablation.py -v`·`uv run pytest -q`·ruff·mypy 클린.
- [ ] **Step 5: 커밋** `git commit -am "feat(backtest): 레이어1 ablation — near_52w 후보별 ΔMAE + compare settings 주입"`

---

## Phase 3 — 레이어2: 리스크 오버레이 시뮬

### Task 6: ATR 손절가·사이징 순수 헬퍼
**Files:** Modify `backend/scoring.py`(신규 함수만); Test `tests/test_risk_helpers.py`

- [ ] **Step 1: 실패 테스트** (`tests/test_risk_helpers.py`)
```python
from decimal import Decimal

from backend.scoring import atr_stop_price, suggested_weight


def test_atr_stop_price() -> None:
    # 진입 100, ATR 4, 배수 2 → 손절 100 - 8 = 92
    assert atr_stop_price(Decimal("100"), Decimal("4"), mult=Decimal("2")) == Decimal("92")


def test_atr_stop_price_floors_at_zero() -> None:
    assert atr_stop_price(Decimal("5"), Decimal("4"), mult=Decimal("2")) == Decimal("0")


def test_suggested_weight_caps_and_scales() -> None:
    # weight = risk_pct / (mult × atr/price), 상한 cap.
    # atr/price=0.04, mult=2 → 분모 0.08; risk 0.01 → 0.125 → cap 0.10
    w = suggested_weight(Decimal("0.04"), risk_pct=Decimal("0.01"), mult=Decimal("2"),
                         cap=Decimal("0.10"))
    assert w == Decimal("0.10")


def test_suggested_weight_zero_atr_returns_zero() -> None:
    assert suggested_weight(Decimal("0"), risk_pct=Decimal("0.01"), mult=Decimal("2"),
                           cap=Decimal("0.10")) == Decimal("0")
```

- [ ] **Step 2: 실패 확인** → FAIL.
- [ ] **Step 3: 구현** (`scoring.py` 신규 함수, 라이브 무수정):
```python
def atr_stop_price(entry: Decimal, atr: Decimal, *, mult: Decimal = Decimal("2")) -> Decimal:
    """ATR 손절가 = max(0, entry − mult×atr)."""
    return max(Decimal("0"), entry - mult * atr)


def suggested_weight(
    atr_over_price: Decimal,
    *,
    risk_pct: Decimal = Decimal("0.01"),
    mult: Decimal = Decimal("2"),
    cap: Decimal = Decimal("0.10"),
) -> Decimal:
    """포지션 비중 = risk_pct / (mult × atr/price), [0, cap] 클램프. atr/price≤0 이면 0."""
    denom = mult * atr_over_price
    if denom <= 0:
        return Decimal("0")
    return max(Decimal("0"), min(cap, risk_pct / denom))
```
`__all__` 에 두 이름 추가.

- [ ] **Step 4: 통과 확인** `uv run pytest tests/test_risk_helpers.py -v`·`uv run pytest -q`·ruff·mypy.
- [ ] **Step 5: 커밋** `git commit -am "feat(scoring): ATR 손절가·포지션 사이징 순수 헬퍼(라이브 무수정)"`

### Task 7: 리스크 오버레이 시뮬 (`portfolio.py`)
**Files:** Create `backend/backtest/portfolio.py`; Test `tests/test_portfolio_risk.py`

핵심: 연속 리밸런스 dates 에 대해 NAV 경로를 만든다. 각 구간 [t, t_next]:
1. 레짐: `is_risk_off(panel.index_rows_asof(t), ...)` 면 신규 진입 없음(현금, 수익 0).
2. 아니면 preset 랭킹 상위 top_n 픽. 각 픽: 사이징 비중 = `suggested_weight(atr20_over_price(rows≤t))`(정규화), 진입가 = t+1 첫 봉 종가, 손절 = `atr_stop_price(진입가, ATR20)`.
3. 구간 내 일중: 어떤 픽의 일중 저가 ≤ 손절가면 그 픽은 손절가에 청산(수익=손절가/진입−1), 아니면 t_next 종가로 평가.
4. 구간 수익 = Σ 비중×픽수익 (현금 잔여 비중 수익 0) − 비용. NAV 누적.

- [ ] **Step 1: 실패 테스트** (`tests/test_portfolio_risk.py`)
```python
from decimal import Decimal

from backend.backtest.portfolio import simulate_risk_overlay
from backend.backtest.run import BacktestConfig
from backend.config import get_settings
from tests.fixtures.backtest_synth import make_panel


def test_simulate_risk_overlay_returns_nav_and_period_returns() -> None:
    panel = make_panel()
    cfg = BacktestConfig(
        start=panel.index_rows[0].date, end=panel.index_rows[-1].date,
        rebalance="weekly", top_n=2, preset="fallback_c",
    )
    res = simulate_risk_overlay(panel, cfg, get_settings(),
                                dates=[r.date for r in panel.index_rows][::5])
    assert res.nav[0] == Decimal("1")
    assert len(res.nav) == len(res.period_returns) + 1  # NAV = 시작 1 + 각 구간
    assert all(isinstance(r, Decimal) for r in res.period_returns)


def test_risk_off_period_holds_cash_zero_return() -> None:
    # 레짐 강제 risk_off → 해당 구간 수익 0 (현금).
    # (구현 후: 분산일 패턴 심은 index 로 첫 구간 risk_off 만들고 period_returns[0]==0 단정)
    ...
```
(두 번째 테스트는 구현 시 분산일 패턴을 심은 패널로 채운다 — 빈 `...` 금지: Step 3 에서 실제 패널 구성 후 `assert res.period_returns[0] == Decimal("0")`.)

- [ ] **Step 2: 실패 확인** → FAIL(no module).
- [ ] **Step 3: 구현** (`backend/backtest/portfolio.py`) — `@dataclass(frozen=True) class OverlayResult: nav: list[Decimal]; period_returns: list[Decimal]; regime_off_dates: list[date]`. `simulate_risk_overlay(panel, cfg, settings, dates) -> OverlayResult`:
```python
"""리스크 오버레이 포트폴리오 시뮬 — 레짐 보류·ATR 손절·사이징. 연속 dates 의 NAV 경로."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import backend.scoring as sc
from backend.backtest.panel import Panel
from backend.backtest.regime import is_risk_off
from backend.backtest.run import BacktestConfig, _score_at
from backend.config import Settings


@dataclass(frozen=True)
class OverlayResult:
    nav: list[Decimal]
    period_returns: list[Decimal]
    regime_off_dates: list[date]


def _atr20(rows: list[sc.OHLCVRow]) -> Decimal:
    if not rows:
        return Decimal("0")
    return sc.atr20_over_price(rows) * rows[-1].close


def simulate_risk_overlay(
    panel: Panel, cfg: BacktestConfig, settings: Settings, dates: list[date]
) -> OverlayResult:
    nav: list[Decimal] = [Decimal("1")]
    period_returns: list[Decimal] = []
    regime_off: list[date] = []
    cost = cfg.cost_bps / Decimal("10000")
    for i in range(len(dates) - 1):
        t, t_next = dates[i], dates[i + 1]
        if is_risk_off(
            panel.index_rows_asof(t),
            window=settings.regime_window,
            threshold=settings.regime_threshold,
            drop=settings.regime_drop,
        ):
            regime_off.append(t)
            period_returns.append(Decimal("0"))  # 현금
            nav.append(nav[-1])
            continue
        ranked = _score_at(panel, t, settings, cfg.preset)
        picks = [tk for tk, _ in ranked[: cfg.top_n]]
        weighted: list[tuple[Decimal, Decimal]] = []  # (비중, 픽수익)
        for tk in picks:
            rows = panel.rows_asof(tk, t)
            entry = panel.price_on_or_after(tk, t + timedelta(days=1))
            if entry is None or entry <= 0 or not rows:
                continue
            atr = _atr20(rows)
            wt = sc.suggested_weight(
                sc.atr20_over_price(rows), risk_pct=settings.risk_pct,
                mult=settings.atr_stop_mult, cap=settings.max_weight_pct,
            )
            stop = sc.atr_stop_price(entry, atr, mult=settings.atr_stop_mult)
            # 구간 내 일중 저가가 손절 터치 → 손절가 청산, 아니면 t_next 종가.
            fut = [r for r in panel.series[tk].rows if t < r.date <= t_next]
            exit_px = None
            for bar in fut:
                if bar.low <= stop:
                    exit_px = stop
                    break
            if exit_px is None:
                exit_px = fut[-1].close if fut else entry
            weighted.append((wt, exit_px / entry - Decimal("1")))
        total_w = sum((w for w, _ in weighted), Decimal("0"))
        if total_w > 0:
            gross = sum((w * r for w, r in weighted), Decimal("0")) / total_w  # 정규화
            churn = cost  # 단순 회전비용(보수적 1회전)
            period_returns.append(gross - churn)
        else:
            period_returns.append(Decimal("0"))
        nav.append(nav[-1] * (Decimal("1") + period_returns[-1]))
    return OverlayResult(nav=nav, period_returns=period_returns, regime_off_dates=regime_off)


__all__ = ["OverlayResult", "simulate_risk_overlay"]
```

- [ ] **Step 4: 통과 확인** (두 테스트 모두 — 두 번째는 분산일 심은 패널로 risk_off 구간 0수익 단정) → PASS. `uv run pytest -q`·ruff·mypy 클린.
- [ ] **Step 5: 커밋** `git commit -am "feat(backtest): 리스크 오버레이 시뮬 — 레짐 보류·ATR 손절·사이징 NAV 경로"`

### Task 8: 포트폴리오 위험조정 지표 + 블록부트스트랩 CI
**Files:** Modify `backend/backtest/metrics.py`(신규 함수); Test `tests/test_portfolio_metrics.py`

- [ ] **Step 1: 실패 테스트**
```python
from decimal import Decimal

from backend.backtest.metrics import portfolio_metrics


def test_portfolio_metrics_basic() -> None:
    nav = [Decimal("1"), Decimal("1.1"), Decimal("0.99"), Decimal("1.05")]
    rets = [Decimal("0.1"), Decimal("-0.1"), Decimal("0.0606")]
    m = portfolio_metrics(nav, rets, periods_per_year=12)
    assert m["mdd"] <= 0  # 최대낙폭 ≤ 0
    assert "sharpe" in m and "calmar" in m and "cagr" in m
    # 모든 값 Decimal
    assert all(isinstance(v, Decimal) for v in m.values())
```

- [ ] **Step 2: 실패 확인** → FAIL.
- [ ] **Step 3: 구현** (`metrics.py` 신규; 기존 `max_drawdown`·`cagr`·`annualized_volatility` 재사용; Sharpe 의 mean/vol 만 신규):
```python
def portfolio_metrics(
    nav: list[Decimal], period_returns: list[Decimal], *, periods_per_year: int
) -> dict[str, Decimal]:
    """NAV·구간수익 → {mdd, cagr, sharpe, calmar}. 모두 Decimal. 표본 부족시 0."""
    mdd = max_drawdown(nav)
    n = len(period_returns)
    if n < 2 or not nav:
        return {"mdd": mdd, "cagr": Decimal("0"), "sharpe": Decimal("0"), "calmar": Decimal("0")}
    years = Decimal(n) / Decimal(periods_per_year)
    cg = cagr(nav[0], nav[-1], years=years)
    mean = sum(period_returns, Decimal("0")) / Decimal(n)
    vol = annualized_volatility(period_returns, periods_per_year)
    ann_mean = mean * Decimal(periods_per_year)
    sharpe = ann_mean / vol if vol > 0 else Decimal("0")
    calmar = cg / abs(mdd) if mdd < 0 else Decimal("0")
    return {"mdd": mdd, "cagr": cg, "sharpe": sharpe, "calmar": calmar}
```
`__all__` 에 추가. (MDD/Sharpe 블록부트스트랩 CI 는 Task 9 에서 `block_bootstrap_ci` 로 구간수익 시퀀스를 리샘플해 사용 — 별도 함수 불필요: `block_bootstrap_ci([[r] for r in period_returns], stat_fn=lambda g: portfolio_metrics(_nav_from(g), [x for [x] in g], ...)["sharpe"], ...)` 형태. 복잡하면 Task 9 에서 간이 구현.)

- [ ] **Step 4: 통과 확인**·ruff·mypy.
- [ ] **Step 5: 커밋** `git commit -am "feat(backtest): 포트폴리오 위험조정 지표 portfolio_metrics(MDD·CAGR·Sharpe·Calmar)"`

---

## Phase 4 — CLI 오케스트레이션 + 레이어2 ablation + 합성 그라운드트루스

### Task 9: `--fallback-c` CLI + OOS 연속구간 평가 + 레이어2 ablation
**Files:** Modify `backend/backtest/run.py`(`main()`); Modify `backend/backtest/report.py`(리포트); Test `tests/test_fallback_c_cli.py`

- [ ] **Step 1: 실패 테스트** — `main(["--fallback-c","--start",...,"--end",...,"--rebalance","weekly","--tickers", "<synth>", "--out", str(tmp)])` 가 0 반환하고 `report_fallback_c.md` 생성. (합성: `--tickers` 로 make_panel 종목 주입 불가하므로, 테스트는 `_fallback_c_report(...)` 순수 헬퍼를 직접 호출해 md 문자열에 "레이어1"·"레이어2"·"MDD" 포함 확인.)
```python
from decimal import Decimal

from backend.backtest.report import render_fallback_c_markdown


def test_render_fallback_c_markdown_has_sections() -> None:
    md = render_fallback_c_markdown(
        layer1_rows=[("0.30", "0.001", "0.0003"), ("0.12", "0.002", "0.001")],
        layer2={"baseline": {"mdd": "-0.20", "sharpe": "0.5", "calmar": "0.4"},
                "fallback_c": {"mdd": "-0.12", "sharpe": "0.6", "calmar": "0.7"}},
    )
    assert "레이어1" in md and "레이어2" in md and "MDD" in md and "fallback_c" in md
```

- [ ] **Step 2: 실패 확인** → FAIL(no func).
- [ ] **Step 3: 구현**
  - `report.py`: `render_fallback_c_markdown(layer1_rows, layer2) -> str` — 레이어1 ablation 표(w52·ΔMAE·CI) + 레이어2 baseline vs fallback_c 위험조정 표(MDD·Sharpe·Calmar). Decimal 은 문자열로 받음.
  - `run.py main()`: `--fallback-c`(store_true) + 핸들러(`--compare`/`--horserace` 와 동형, out_dir 생성 후):
    1. `wf = WalkForwardConfig(...)`; `fold_ranges, holdout = _walk_forward_splits(_rebalance_dates(panel,cfg), wf)`.
    2. 레이어1: `run_layer1_ablation(panel, cfg, wf, w52_candidates=[Decimal("0.30"),Decimal("0.20"),Decimal("0.12")])`.
    3. 레이어2: 각 **연속구간**(fold별 test + holdout)에서 `simulate_risk_overlay(panel, cfg(preset=baseline), ...)` vs `(preset=fallback_c)` → `portfolio_metrics`; 구간 병합(구간수익 이어붙여 한 NAV) 또는 holdout 단일 구간 사용. baseline/fallback_c MDD·Sharpe·Calmar 비교.
    4. `render_fallback_c_markdown(...)` → `out_dir/report_fallback_c.md`(+ json). `print`. `return 0`.
  - `--preset` choices 에 `fallback_c` 는 Task 3 에서 이미 추가.
- [ ] **Step 4: 통과 확인** `uv run pytest tests/test_fallback_c_cli.py -v`·`uv run pytest -q`·ruff·mypy.
- [ ] **Step 5: 커밋** `git commit -am "feat(backtest): --fallback-c CLI — 레이어1 ablation + 레이어2 위험조정 평가 + 리포트"`

### Task 10: 합성 그라운드트루스 테스트 (엔진 정확성 증명)
**Files:** Test `tests/test_fallback_c_groundtruth.py`

- [ ] **Step 1: 테스트(레짐이 MDD 줄임)** — 지수에 *초반 risk_off(분산일 다발) 구간 + 그 구간 종목 급락*을 심은 패널 구성. `simulate_risk_overlay(preset=fallback_c)`(레짐 on)의 MDD 가 레짐 무시 baseline 시뮬보다 **덜 깊음**(`mdd_fallback >= mdd_baseline`, 둘 다 ≤0)을 단정.
```python
# 의사: 지수 첫 6봉 분산일 패턴 → 첫 구간 risk_off → fallback_c 진입 보류 → 그 구간 급락 회피.
# baseline(레짐 무시) 은 급락 종목 진입 → 큰 낙폭. assert mdd_fallback >= mdd_baseline.
```
- [ ] **Step 2: 테스트(재가중이 MAE 줄임)** — 과열(near_52w 높지만 직후 급락) 종목 + 눌림목(near_52w 낮고 직후 상승) 종목을 심은 패널. `compare_presets(variant="fallback_c", w52=0.12)` 의 20일 `dmae > 0`(MAE 개선)을 단정. (못 맞추면 BLOCKED 보고 — 단정 약화 금지.)
- [ ] **Step 3: 통과 확인**·`uv run pytest -q`·ruff·mypy.
- [ ] **Step 4: 커밋** `git commit -am "test(backtest): fallback_c 합성 그라운드트루스 — 레짐 MDD↓·재가중 MAE↓ 증명"`

---

## Phase 5 — 실데이터 실행·판정

### Task 11: 실데이터 fallback_c 실행 + RESULTS 추가
**Files:** (통합 실행) Modify `docs/superpowers/specs/2026-06-17-alpha-discovery-RESULTS.md` 또는 신규 `...-fallback-c-RESULTS.md`

- [ ] **Step 1:** `.env` 셸 로드 후(하니스 자동로드 안 함):
```
uv run python -m backend.backtest.run --fallback-c --start 2016-01-01 --end 2024-12-31 \
  --rebalance monthly --top-n 5 --universe-top-n 150 --n-resamples 250 --n-perms 250 \
  --out data/backtest
```
- [ ] **Step 2: 판정** — 레이어1: OOS ΔMAE 유의 양수면 채택(어느 near_52w 후보·extension 기여). 레이어2: OOS MDD 축소 & MAE 무회귀면 채택. ablation 으로 컴포넌트별 귀속.
- [ ] **Step 3: 정직 보고** — 결과를 RESULTS 문서에 기록(채택/미채택·기여 컴포넌트·정직성 한계). 둘 다 미개선이면 그대로 보고. 커밋.
- [ ] **Step 4: 라이브 승격(통과 시·별도 결정)** — 검증된 컴포넌트만 라이브 반영은 *본 계획 밖*(별도 스펙·라이브 회귀 필수).

---

## 자기검토 (작성자 체크)
- **스펙 커버리지:** 레짐(§6)=Task1 · 레이어1 재가중+MAE게이트(§4,§7)=Task2-5 · ATR/사이징(§5)=Task6 · 리스크오버레이 시뮬(§5)=Task7 · 위험조정 지표+부트스트랩(§7)=Task8 · CLI+레이어2 ablation(§7)=Task9 · 합성 그라운드트루스(§9)=Task10 · 실데이터 판정(§8)=Task11. ✓
- **플레이스홀더:** Task7/10 의 두 번째 테스트는 "구현 시 패널 구성"으로 명시(빈 `...` 는 Step에서 실제 단정으로 채움) — 의도된 TDD 패턴. near_52w 후보값은 *측정 대상*(§3.5)이라 고정 아님.
- **타입 일관성:** `_score_at(preset="fallback_c")` · `compare_presets(..., settings=)` · `simulate_risk_overlay(panel,cfg,settings,dates)->OverlayResult` · `portfolio_metrics(nav,returns,*,periods_per_year)->dict[str,Decimal]` · `run_layer1_ablation(...)->list[AblationRow]`. 일관.
- **범위:** 라이브 승격은 본 계획 밖(Task11 Step4). 레이어2가 최대 작업(Task7).
