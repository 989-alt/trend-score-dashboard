"""Task 6 — 팩터 호스레이스 엔진 합성 그라운드트루스 테스트 (오탐 제어 증명).

목표(팩터 수준의 Layer-A 자가검증):
  - 단 하나의 *진짜* 신호 팩터가 forward-return 을 예측 → OOS 단조성 양수·CI_lo>0·낮은 p
    → BH-FDR 기각 → 홀드아웃 재확인 → winner=True.
  - 여러 노이즈 팩터는 drift 와 무관 → 단조성≈0 → FDR 미기각 → winner=False.
  → 엔진이 진짜 신호를 *찾고* 동시에 오탐(false discovery)을 *제어*함을 증명.

설계 원칙(test_backtest_walkforward._wf_panel 모델 계승):
  - 워밍업 210일(≥ma200_window=200) + 윈도우 140일 → 주간 리밸런스 ≥12회 → 폴드+홀드아웃 비공백.
  - 심은 신호: 티커 인덱스 k 가 높을수록 drift(=일일 추세) 강함 → forward-return 큼.
  - "signal" FactorFn = ≤t rows 의 트레일링 추세(rows_asof) → drift 와 단조 → 예측력 보유
    (가격 추세에서 *계산* — 타우톨로지 아님: ≤t 데이터로 >t 수익을 예측).
  - "noise_k" FactorFn = (ticker,t) 결정론 해시 → drift 와 무관 → 단조성≈0.
  - 작은 n_resamples/n_perms(시드 고정) → 속도.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import Decimal

from backend.backtest.horserace import (
    FactorResult,
    Leaderboard,
    run_horserace,
)
from backend.backtest.panel import Panel, TickerSeries, Valuation
from backend.backtest.run import BacktestConfig, WalkForwardConfig
from backend.schemas import OHLCVRow

# 작은 시드 반복수 — 테스트 속도(결정론 유지)
_FAST = {"n_resamples": 120, "n_perms": 120}

_WARMUP_DAYS = 210
_WINDOW_DAYS = 140
_START = date(2023, 1, 2)
_WINDOW_START = _START + timedelta(days=_WARMUP_DAYS)
_HORIZON = 20


def _signal_panel(n_tickers: int = 30) -> Panel:
    """워밍업 후 윈도우 전체에서 적격이고, 티커 인덱스로 forward-return 이 정렬된 패널.

    drift(k) = 0.4 + k*0.25 → 높은 k = 가파른 상승 → 큰 forward-return(횡단면 순서 알려짐).
    변동성은 [0.20,0.60] 밴드(±1.5% 교번) 안에 유지해 하드필터 통과(여기선 호스레이스가
    universe_asof 전체를 보지만, fwd_return·factor 가용성을 위해 시리즈가 건전해야 함).
    """
    total = _WARMUP_DAYS + _WINDOW_DAYS
    series: dict[str, TickerSeries] = {}
    listings: dict[str, tuple[date, date | None]] = {}
    for k in range(n_tickers):
        ticker = f"{k + 1:06d}"
        drift = Decimal("0.4") + Decimal(k) * Decimal("0.25")
        base = Decimal("100") + Decimal(k) * Decimal("3")
        rows: list[OHLCVRow] = []
        for i in range(total):
            level = base + drift * Decimal(i)
            close = level * (Decimal("1.015") if i % 2 == 0 else Decimal("0.985"))
            rows.append(
                OHLCVRow(
                    date=_START + timedelta(days=i),
                    open=level,
                    high=level * Decimal("1.025"),
                    low=level * Decimal("0.975"),
                    close=close,
                    volume=Decimal("1000000"),
                )
            )
        series[ticker] = TickerSeries(
            ticker=ticker,
            rows=rows,
            turnover_by_date={r.date: Decimal("20000000000") for r in rows},
            valuation_by_date={
                r.date: Valuation(per=Decimal("10"), pbr=Decimal("1.2")) for r in rows
            },
        )
        listings[ticker] = (_START, None)
    index_rows = [
        OHLCVRow(
            date=_START + timedelta(days=i),
            open=Decimal(2000 + i),
            high=Decimal(2000 + i) * Decimal("1.01"),
            low=Decimal(2000 + i) * Decimal("0.99"),
            close=Decimal(2000 + i),
            volume=Decimal("1000000"),
        )
        for i in range(total)
    ]
    return Panel(series=series, fundamentals={}, listings=listings, index_rows=index_rows)


def _cfg(panel: Panel) -> BacktestConfig:
    last = max(r.date for s in panel.series.values() for r in s.rows)
    return BacktestConfig(
        start=_WINDOW_START,
        end=last,
        rebalance="weekly",
        top_n=5,
        forward_horizons=(_HORIZON,),
        **_FAST,
    )


# --- FactorFn 정의 -----------------------------------------------------------


def _signal_factor(panel: Panel, tk: str, t: date) -> Decimal | None:
    """≤t rows 의 트레일링 추세(최근 40봉 종가 회귀 대용 = (마지막−처음)/처음).

    drift 가 클수록 이 비율이 큼 → forward-return 과 단조. 룩어헤드 0(rows_asof ≤t).
    """
    rows = panel.rows_asof(tk, t)
    if len(rows) < 40:
        return None
    window = rows[-40:]
    first = window[0].close
    last = window[-1].close
    if first <= 0:
        return None
    return (last - first) / first


def _make_noise_factor(salt: str):
    """(ticker,t) 결정론 해시 → [0,1) — drift 와 무관(단조성≈0)."""

    def _noise(panel: Panel, tk: str, t: date) -> Decimal | None:
        h = hashlib.sha256(f"{salt}|{tk}|{t.isoformat()}".encode()).hexdigest()
        # 상위 8 hex → 정수 → [0,1) 결정론 값
        return Decimal(int(h[:8], 16)) / Decimal(0x100000000)

    return _noise


# ---------------------------------------------------------------------------
# Test 1: 합성 그라운드트루스 — signal 이 이기고 noise 가 기각됨
# ---------------------------------------------------------------------------


def test_horserace_selects_signal_rejects_noise() -> None:
    """그라운드트루스: signal=winner(FDR 기각·CI_lo>0·홀드아웃 양수), 모든 noise_*=비승자.

    이 단일 테스트가 (a) 엔진이 진짜 신호를 찾고 (b) 오탐을 제어함을 동시에 증명.
    실패 시 assertion 메시지로 관측 mono/CI/p 를 보고(임계 약화 금지).
    """
    panel = _signal_panel(n_tickers=30)
    cfg = _cfg(panel)
    wf = WalkForwardConfig(n_folds=4, holdout_frac=Decimal("0.2"))

    factors = {
        "signal": _signal_factor,
        "noise_1": _make_noise_factor("a"),
        "noise_2": _make_noise_factor("b"),
        "noise_3": _make_noise_factor("c"),
        "noise_4": _make_noise_factor("d"),
    }

    lb = run_horserace(panel, cfg, wf, factors, horizon=_HORIZON, q=Decimal("0.10"))

    by_name = {r.name: r for r in lb.results}
    assert set(by_name) == set(factors), "리더보드는 모든 팩터 결과를 포함해야 함"

    sig = by_name["signal"]
    # signal: 진짜 신호 → OOS 단조성 양수·CI_lo>0·FDR 기각·홀드아웃 양수 → winner.
    assert sig.n > 0, f"signal 에 채점된 관측 없음: n={sig.n}"
    assert sig.mono > Decimal("0"), f"signal OOS 단조성 양수여야 함: {sig.mono}"
    assert sig.ci_lo > Decimal("0"), f"signal CI_lo>0 여야 함: ci=[{sig.ci_lo},{sig.ci_hi}]"
    assert sig.holdout_mono > Decimal("0"), f"signal 홀드아웃 단조성 양수: {sig.holdout_mono}"
    assert sig.fdr_reject is True, f"signal 은 FDR 기각되어야 함: p={sig.pvalue}"
    assert sig.winner is True, (
        f"signal 은 winner 여야 함: mono={sig.mono}, ci=[{sig.ci_lo},{sig.ci_hi}], "
        f"p={sig.pvalue}, fdr={sig.fdr_reject}, holdout={sig.holdout_mono}"
    )

    # 모든 noise: winner 아님(오탐 제어).
    for nm, r in by_name.items():
        if nm.startswith("noise_"):
            assert r.winner is False, (
                f"{nm} 은 winner 가 아니어야 함(오탐): mono={r.mono}, ci=[{r.ci_lo},{r.ci_hi}], "
                f"p={r.pvalue}, fdr={r.fdr_reject}, holdout={r.holdout_mono}"
            )

    # 정렬: winner 가 맨 앞.
    assert lb.results[0].name == "signal", (
        f"winner(signal)가 리더보드 최상단이어야 함; got {[r.name for r in lb.results]}"
    )
    assert lb.horizon == _HORIZON
    assert lb.q == Decimal("0.10")


def test_horserace_determinism() -> None:
    """동일 입력 → 동일 리더보드(시드 결정론)."""
    panel = _signal_panel(n_tickers=20)
    cfg = _cfg(panel)
    wf = WalkForwardConfig(n_folds=4, holdout_frac=Decimal("0.2"))
    factors = {"signal": _signal_factor, "noise_1": _make_noise_factor("a")}
    lb1 = run_horserace(panel, cfg, wf, factors, horizon=_HORIZON)
    lb2 = run_horserace(panel, cfg, wf, factors, horizon=_HORIZON)
    assert lb1.results == lb2.results


# ---------------------------------------------------------------------------
# Test 2: 리포트 렌더링(빠른 단위 — Leaderboard 직접 구성)
# ---------------------------------------------------------------------------


def test_horserace_report_renders() -> None:
    from backend.backtest.report import (
        render_horserace_json,
        render_horserace_markdown,
    )

    lb = Leaderboard(
        results=[
            FactorResult(
                name="alpha_signal",
                mono=Decimal("0.6123"),
                ci_lo=Decimal("0.2100"),
                ci_hi=Decimal("0.8800"),
                pvalue=Decimal("0.0099"),
                fdr_reject=True,
                holdout_mono=Decimal("0.5500"),
                winner=True,
                n=240,
            ),
            FactorResult(
                name="dud_factor",
                mono=Decimal("0.0210"),
                ci_lo=Decimal("-0.1500"),
                ci_hi=Decimal("0.1900"),
                pvalue=Decimal("0.7400"),
                fdr_reject=False,
                holdout_mono=Decimal("-0.0300"),
                winner=False,
                n=240,
            ),
        ],
        horizon=20,
        q=Decimal("0.10"),
    )

    md = render_horserace_markdown(lb)
    assert "alpha_signal" in md
    assert "dud_factor" in md
    assert "winner" in md.lower()
    # 헤더/컬럼 존재
    assert "factor" in md.lower() or "팩터" in md
    assert "holdout" in md.lower() or "홀드아웃" in md
    assert str(lb.horizon) in md

    js = render_horserace_json(lb)
    assert js["horizon"] == 20
    assert js["q"] == "0.10"
    assert isinstance(js["results"], list)
    assert len(js["results"]) == 2
    first = js["results"][0]
    for key in (
        "name",
        "mono",
        "ci_lo",
        "ci_hi",
        "pvalue",
        "fdr_reject",
        "holdout_mono",
        "winner",
        "n",
    ):
        assert key in first, f"JSON 결과에 {key} 키가 있어야 함"
    # Decimal 은 문자열로 직렬화
    assert first["mono"] == "0.6123"
    assert first["winner"] is True
    assert first["n"] == 240
