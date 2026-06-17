"""팩터 호스레이스 엔진 (Task 6) — 알파 발굴의 엄밀성 코어.

후보 팩터 dict 에 대해 각 팩터의 **OOS 단조성**(forward-return 대비)을 측정하고,
날짜-블록 부트스트랩 CI · 퍼뮤테이션 p 를 계산한 뒤, 전 팩터에 걸쳐 **BH-FDR**
다중검정 보정을 적용하고, 생존 팩터를 **홀드아웃**에서 재확인하여, 승자를 표시한
리더보드를 반환한다.

룩어헤드 0: 팩터 fn 은 ≤t 데이터만 사용(호출자 보장 — `rows_asof` 등), `_fwd_return`
은 >t 가격만 사용. 이 단일 가드를 깨지 않는다.

엔진은 방향(orientation)-불가지론적이다: 팩터는 주어진 방향 그대로 평가되며(실전 팩터
풀은 후속 태스크에서 "높을수록 좋음"으로 정렬: 예 per/pbr 역수), 엔진 자체는 유의한
**양의** 단조성 승자만 추대한다.

`run.py`/`metrics.py` 의 검증된 헬퍼를 재사용(재구현 금지). 날짜별 그룹 수집은
`build_event_study` 와 정확히 동일하게 미러링한다(블록 부트스트랩 정합).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from backend.backtest.metrics import (
    bh_fdr_reject,
    block_bootstrap_ci,
    permutation_pvalue,
    spearman_monotonicity,
)
from backend.backtest.panel import Panel
from backend.backtest.run import (
    BacktestConfig,
    WalkForwardConfig,
    _fwd_return,
    _rebalance_dates,
    _spearman_stat,
    _walk_forward_splits,
)

#: 팩터 값 함수 — ticker 의 t 시점 팩터값(≤t 데이터만). None = 가용 불가.
FactorFn = Callable[[Panel, str, date], Decimal | None]


@dataclass(frozen=True)
class FactorResult:
    name: str
    mono: Decimal  # OOS 풀링 Spearman 단조성(점추정)
    ci_lo: Decimal  # OOS 날짜-블록 부트스트랩 CI
    ci_hi: Decimal
    pvalue: Decimal  # OOS 퍼뮤테이션 p
    fdr_reject: bool  # 전 팩터에 걸친 BH-FDR(q)
    holdout_mono: Decimal
    winner: bool
    n: int  # OOS 풀링 관측 수


@dataclass(frozen=True)
class Leaderboard:
    results: list[FactorResult]  # 정렬: 승자 우선, 그다음 mono 내림차순
    horizon: int
    q: Decimal


def run_horserace(
    panel: Panel,
    cfg: BacktestConfig,
    wf: WalkForwardConfig,
    factors: dict[str, FactorFn],
    *,
    horizon: int = 20,
    q: Decimal = Decimal("0.10"),
) -> Leaderboard:
    """후보 팩터 horse-race — OOS 단조성 + 부트스트랩 CI + 퍼뮤테이션 p + BH-FDR + 홀드아웃.

    절차(설계 확정):
      1) 리밸런스 dates → 워크포워드 분할 → OOS dates(테스트 폴드 합집합) · 홀드아웃 dates.
      2) collect(): 날짜별로 universe 를 돌며 fwd_return 가용 종목의 각 팩터값을 그 날짜
         그룹에 모은다(블록 부트스트랩용 per-date 그룹 — build_event_study 와 동일).
      3) 팩터별(삽입 순서): 풀링 OOS 단조성·CI·p, 홀드아웃 단조성 계산.
      4) BH-FDR: 동일 순서의 p 리스트로 기각 마스크.
      5) winner = fdr_reject AND ci_lo>0 AND holdout_mono>0 (유의한 *양의* OOS 단조성,
         홀드아웃 재확인).
      6) 정렬: 승자 우선, 그다음 mono 내림차순.
    """
    dates = _rebalance_dates(panel, cfg)
    fold_ranges, holdout_dates = _walk_forward_splits(dates, wf)
    oos_dates = [t for _tr, test in fold_ranges for t in test]

    def collect(
        date_subset: list[date],
    ) -> dict[str, list[tuple[list[Decimal], list[Decimal]]]]:
        """팩터별 날짜-블록 그룹 수집.

        반환[nm] = list over dates of (factor_values_at_date, fwds_at_date).
        각 날짜 t: universe_asof(t) 종목 중 fwd_return 가용 종목만, 각 팩터값(None 아님)을
        그 날짜 임시 버킷에 모은 뒤, 비어있지 않은 팩터에 한해 날짜 그룹을 추가.
        (build_event_study 의 per-date 그룹핑을 정확히 미러링 — 룩어헤드 0.)
        """
        groups: dict[str, list[tuple[list[Decimal], list[Decimal]]]] = {nm: [] for nm in factors}
        for t in date_subset:
            date_values: dict[str, list[Decimal]] = {nm: [] for nm in factors}
            date_fwds: dict[str, list[Decimal]] = {nm: [] for nm in factors}
            for tk in panel.universe_asof(t):
                fr = _fwd_return(panel, tk, t, horizon)
                if fr is None:
                    continue
                for nm in factors:
                    v = factors[nm](panel, tk, t)
                    if v is not None:
                        date_values[nm].append(v)
                        date_fwds[nm].append(fr)
            for nm in factors:
                if date_values[nm]:
                    groups[nm].append((date_values[nm], date_fwds[nm]))
        return groups

    oos_groups = collect(oos_dates)
    holdout_groups = collect(holdout_dates)

    results: list[FactorResult] = []
    pvalues: list[Decimal] = []  # BH-FDR 입력 — factors 순서와 정렬
    for nm in factors:
        # 풀링 OOS 점추정
        vals: list[Decimal] = []
        fwds: list[Decimal] = []
        for vs, fs in oos_groups[nm]:
            vals.extend(vs)
            fwds.extend(fs)
        mono = spearman_monotonicity(vals, fwds)
        n = len(fwds)

        if oos_groups[nm]:
            boot_groups = [list(zip(vs, fs, strict=True)) for vs, fs in oos_groups[nm]]
            ci_lo, ci_hi = block_bootstrap_ci(
                boot_groups,
                _spearman_stat,
                n_resamples=cfg.n_resamples,
                seed=cfg.bootstrap_seed,
            )
            pval = permutation_pvalue(
                oos_groups[nm],
                observed=mono,
                n_perms=cfg.n_perms,
                seed=cfg.bootstrap_seed,
            )
        else:
            ci_lo = ci_hi = Decimal("0")
            pval = Decimal("1")

        # 홀드아웃 단조성
        h_vals: list[Decimal] = []
        h_fwds: list[Decimal] = []
        for vs, fs in holdout_groups[nm]:
            h_vals.extend(vs)
            h_fwds.extend(fs)
        holdout_mono = spearman_monotonicity(h_vals, h_fwds) if holdout_groups[nm] else Decimal("0")

        pvalues.append(pval)
        results.append(
            FactorResult(
                name=nm,
                mono=mono,
                ci_lo=ci_lo,
                ci_hi=ci_hi,
                pvalue=pval,
                fdr_reject=False,  # 아래에서 채움
                holdout_mono=holdout_mono,
                winner=False,  # 아래에서 채움
                n=n,
            )
        )

    rejects = bh_fdr_reject(pvalues, q=q)
    finalized: list[FactorResult] = []
    for r, reject in zip(results, rejects, strict=True):
        winner = reject and r.ci_lo > Decimal("0") and r.holdout_mono > Decimal("0")
        finalized.append(
            FactorResult(
                name=r.name,
                mono=r.mono,
                ci_lo=r.ci_lo,
                ci_hi=r.ci_hi,
                pvalue=r.pvalue,
                fdr_reject=reject,
                holdout_mono=r.holdout_mono,
                winner=winner,
                n=r.n,
            )
        )

    # 정렬: 승자 우선, 그다음 mono 내림차순.
    finalized.sort(key=lambda r: (not r.winner, -r.mono))
    return Leaderboard(results=finalized, horizon=horizon, q=q)


__all__ = [
    "FactorFn",
    "FactorResult",
    "Leaderboard",
    "run_horserace",
]
