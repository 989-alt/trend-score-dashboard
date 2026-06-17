"""순수 메트릭 — float 보조연산은 문자열 경유로 Decimal 복귀(정밀도 보존)."""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from decimal import Decimal
from typing import Any


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
    cov = sum(((a - mean_s) * (b - mean_f) for a, b in zip(rs, rf, strict=True)), Decimal("0"))
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
    """연복리수익률. start≤0 또는 years≤0 또는 end≤0 이면 0."""
    if start <= 0 or years <= 0 or end <= 0:
        return Decimal("0")
    ratio = float(end) / float(start)
    val = ratio ** (1.0 / float(years)) - 1.0
    return Decimal(str(val)).quantize(Decimal("0.000001"))


def annualized_volatility(returns: list[Decimal], periods_per_year: int) -> Decimal:
    """기간수익률 표준편차 × √(periods_per_year). 표본<2 이면 0."""
    n = len(returns)
    if n < 2:
        return Decimal("0")
    mean = sum(returns, Decimal("0")) / Decimal(n)
    var = sum(((r - mean) ** 2 for r in returns), Decimal("0")) / Decimal(n)
    std = Decimal(str(math.sqrt(float(var))))
    return (std * Decimal(str(math.sqrt(periods_per_year)))).quantize(Decimal("0.0001"))


# ---------------------------------------------------------------------------
# Part A — 유의성 모듈 (Layer B)
# ---------------------------------------------------------------------------


def percentile(values: list[Decimal], q: Decimal) -> Decimal:
    """q ∈ [0,1] 분위수 — 선형보간법(nearest-rank 대신).

    정렬된 order statistics x[0]..x[n-1] 에 대해 i = q*(n-1) 를 구한 뒤
    floor/ceil 사이를 (1-frac)*x[floor] + frac*x[ceil] 로 보간.

    Edge cases:
    - 빈 리스트 → Decimal("0")
    - 단일 원소 → 그 원소
    - q=0 → 최솟값, q=1 → 최댓값
    """
    if not values:
        return Decimal("0")
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    # 선형보간: position = q * (n-1)
    pos = q * Decimal(n - 1)
    lo_idx = int(pos)  # floor (정수 변환 — q∈[0,1]이므로 범위 안전)
    hi_idx = min(lo_idx + 1, n - 1)
    frac = pos - Decimal(lo_idx)
    return sorted_vals[lo_idx] * (Decimal("1") - frac) + sorted_vals[hi_idx] * frac


def block_bootstrap_ci(
    groups: list[list[Any]],
    stat_fn: Callable[[list[Any]], Decimal],
    *,
    n_resamples: int,
    seed: int,
    lo: Decimal = Decimal("0.025"),
    hi: Decimal = Decimal("0.975"),
) -> tuple[Decimal, Decimal]:
    """날짜-블록 부트스트랩 신뢰구간.

    groups[i]  = i번째 리밸런스 날짜의 이벤트 레코드 리스트 (임의 타입).
    stat_fn    = 풀링된 레코드 리스트 → Decimal 통계량.

    알고리즘:
      rng = random.Random(seed)
      각 리샘플: len(groups) 개 날짜-인덱스를 복원추출 → 해당 그룹 연결 → stat_fn 적용.
      n_resamples 개 통계량 수집 후 (percentile(lo), percentile(hi)) 반환.

    날짜 단위 리샘플이므로 동일 날짜 내 횡단면 구조 보존,
    인접 날짜 간 겹침(temporal overlap)도 처리됨.
    """
    if not groups:
        return Decimal("0"), Decimal("0")
    rng = random.Random(seed)
    n_groups = len(groups)
    boot_stats: list[Decimal] = []
    for _ in range(n_resamples):
        indices = [rng.randrange(n_groups) for _ in range(n_groups)]
        pooled: list[Any] = []
        for idx in indices:
            pooled.extend(groups[idx])
        if pooled:
            boot_stats.append(stat_fn(pooled))
    if not boot_stats:
        return Decimal("0"), Decimal("0")
    return percentile(boot_stats, lo), percentile(boot_stats, hi)


def permutation_pvalue(
    groups_scores_fwd: list[tuple[list[Decimal], list[Decimal]]],
    *,
    observed: Decimal,
    n_perms: int,
    seed: int,
) -> Decimal:
    """단조성 귀무분포 기반 양측 퍼뮤테이션 p-값.

    groups_scores_fwd[i] = (scores_at_date_i, forward_returns_at_date_i).

    귀무가설 = 점수와 수익률 사이에 연관 없음.
    날짜 내에서 forward_return 을 섞어(within-date shuffle) 귀무분포를 생성.
    같은 날짜 내 종목 간 구조(분포 등)는 보존됨.

    양측 p-값:
      p = (count(|null_stat| >= |observed|) + 1) / (n_perms + 1)

    +1 분자/분모 → 보수적(conservative), 절대로 0 이 되지 않음.

    ⚠ 가정: 각 날짜 그룹은 이벤트 ≥ 2 개. 1-이벤트 날짜는 셔플이 무효과라 그 (score,fwd)
    쌍이 모든 퍼뮤테이션에서 고정 → 귀무분포를 좁혀 p 를 과소추정(anti-conservative)함.
    실거래 유니버스(날짜당 수십 종목)에선 비발생하나, 얇은 유니버스/초기 구간에선 p 를
    낙관적으로 볼 것. (호출자가 그룹 크기를 보장; 매 호출 경고는 경고피로라 두지 않음.)
    """
    rng = random.Random(seed)
    abs_obs = abs(observed)
    count_extreme = 0
    for _ in range(n_perms):
        pool_scores: list[Decimal] = []
        pool_fwd: list[Decimal] = []
        for scores, fwds in groups_scores_fwd:
            shuffled = list(fwds)
            rng.shuffle(shuffled)
            pool_scores.extend(scores)
            pool_fwd.extend(shuffled)
        null_stat = spearman_monotonicity(pool_scores, pool_fwd)
        if abs(null_stat) >= abs_obs:
            count_extreme += 1
    return (Decimal(count_extreme + 1) / Decimal(n_perms + 1)).quantize(Decimal("0.0001"))


def paired_diff_ci(
    groups_a: list[list[Any]],
    groups_b: list[list[Any]],
    stat_fn: Callable[[list[Any]], Decimal],
    *,
    n_resamples: int,
    seed: int,
    lo: Decimal = Decimal("0.025"),
    hi: Decimal = Decimal("0.975"),
) -> tuple[Decimal, Decimal]:
    """페어드 차이의 부트스트랩 신뢰구간: stat_fn(A) − stat_fn(B).

    groups_a[i], groups_b[i] 는 동일 날짜 i 의 각 조건(variant/baseline) 레코드.
    두 리스트는 같은 길이(paired)이어야 함.

    각 리샘플:
      날짜-인덱스를 복원추출 → a_pooled, b_pooled 각각 연결
      → stat_fn(a_pooled) - stat_fn(b_pooled) 계산.

    T4 에서 'variant − baseline CI 가 0 을 포함하지 않으면 유의한 개선'에 사용.
    """
    if not groups_a or not groups_b or len(groups_a) != len(groups_b):
        return Decimal("0"), Decimal("0")
    rng = random.Random(seed)
    n_groups = len(groups_a)
    boot_diffs: list[Decimal] = []
    for _ in range(n_resamples):
        indices = [rng.randrange(n_groups) for _ in range(n_groups)]
        pooled_a: list[Any] = []
        pooled_b: list[Any] = []
        for idx in indices:
            pooled_a.extend(groups_a[idx])
            pooled_b.extend(groups_b[idx])
        if pooled_a and pooled_b:
            boot_diffs.append(stat_fn(pooled_a) - stat_fn(pooled_b))
    if not boot_diffs:
        return Decimal("0"), Decimal("0")
    return percentile(boot_diffs, lo), percentile(boot_diffs, hi)


def bh_fdr_reject(pvalues: list[Decimal], *, q: Decimal) -> list[bool]:
    """Benjamini-Hochberg: FDR<=q 로 기각할 가설 마스크. 원래 순서로 반환."""
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    thresh_rank = -1
    for rank, i in enumerate(order, start=1):
        if pvalues[i] <= q * Decimal(rank) / Decimal(m):
            thresh_rank = rank
    reject = [False] * m
    for rank, i in enumerate(order, start=1):
        if rank <= thresh_rank:
            reject[i] = True
    return reject


__all__ = [
    "annualized_volatility",
    "bh_fdr_reject",
    "block_bootstrap_ci",
    "cagr",
    "max_adverse_excursion",
    "max_drawdown",
    "paired_diff_ci",
    "percentile",
    "permutation_pvalue",
    "spearman_monotonicity",
    "win_rate",
]
