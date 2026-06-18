from __future__ import annotations

import random as _rnd
from decimal import Decimal
from typing import Any

from backend.backtest.metrics import (
    block_bootstrap_ci,
    cagr,
    max_adverse_excursion,
    max_drawdown,
    paired_diff_ci,
    percentile,
    permutation_pvalue,
    spearman_monotonicity,
    win_rate,
)

# ---------------------------------------------------------------------------
# 기존 테스트
# ---------------------------------------------------------------------------


def test_spearman_perfect_monotonic() -> None:
    scores = [Decimal(x) for x in (1, 2, 3, 4, 5)]
    fwd = [Decimal(x) for x in (-2, -1, 0, 1, 2)]
    assert spearman_monotonicity(scores, fwd) == Decimal("1")


def test_spearman_inverted() -> None:
    scores = [Decimal(x) for x in (1, 2, 3, 4, 5)]
    fwd = [Decimal(x) for x in (5, 4, 3, 2, 1)]
    assert spearman_monotonicity(scores, fwd) == Decimal("-1")


def test_mae_is_worst_drop_within_horizon() -> None:
    path = [Decimal(x) for x in (102, 95, 110, 90, 105)]
    assert max_adverse_excursion(Decimal("100"), path) == Decimal("-0.10")


def test_max_drawdown() -> None:
    nav = [Decimal(x) for x in (100, 120, 80, 130)]
    assert max_drawdown(nav).quantize(Decimal("0.0001")) == Decimal("-0.3333")


def test_win_rate() -> None:
    fwd = [Decimal("0.1"), Decimal("-0.2"), Decimal("0.0"), Decimal("0.3")]
    assert win_rate(fwd) == Decimal("0.5")


def test_cagr_two_years_doubling() -> None:
    assert cagr(Decimal("100"), Decimal("200"), years=Decimal("2")).quantize(
        Decimal("0.0001")
    ) == Decimal("0.4142")


# ---------------------------------------------------------------------------
# percentile
# ---------------------------------------------------------------------------


def test_percentile_empty_returns_zero() -> None:
    assert percentile([], Decimal("0.5")) == Decimal("0")


def test_percentile_single_element() -> None:
    assert percentile([Decimal("7")], Decimal("0.5")) == Decimal("7")
    assert percentile([Decimal("7")], Decimal("0")) == Decimal("7")
    assert percentile([Decimal("7")], Decimal("1")) == Decimal("7")


def test_percentile_q0_q1() -> None:
    vals = [Decimal(x) for x in (3, 1, 4, 1, 5, 9, 2, 6)]
    assert percentile(vals, Decimal("0")) == Decimal("1")
    assert percentile(vals, Decimal("1")) == Decimal("9")


def test_percentile_median_odd() -> None:
    # sorted: [1,2,3,4,5] — q=0.5 → pos=2.0 → x[2]=3
    vals = [Decimal(x) for x in (3, 1, 5, 2, 4)]
    result = percentile(vals, Decimal("0.5"))
    assert result == Decimal("3")


def test_percentile_median_even_interpolated() -> None:
    # sorted: [1,2,3,4] — q=0.5 → pos=1.5 → 0.5*2 + 0.5*3 = 2.5
    vals = [Decimal(x) for x in (1, 2, 3, 4)]
    result = percentile(vals, Decimal("0.5"))
    assert result == Decimal("2.5")


def test_percentile_q25_q75() -> None:
    vals = [Decimal(x) for x in range(1, 9)]  # sorted: [1..8]
    lo = percentile(vals, Decimal("0.25"))
    hi = percentile(vals, Decimal("0.75"))
    assert lo < hi
    assert Decimal("1") < lo < Decimal("4")
    assert Decimal("5") < hi < Decimal("9")


# ---------------------------------------------------------------------------
# 헬퍼 — stat_fn: (score, fwd) 튜플 리스트 → pooled Spearman
# ---------------------------------------------------------------------------


def _spearman_stat(records: list[Any]) -> Decimal:
    scores = [r[0] for r in records]
    fwds = [r[1] for r in records]
    return spearman_monotonicity(scores, fwds)


def _make_signal_groups(
    n_dates: int = 20, n_per_date: int = 8
) -> list[list[tuple[Decimal, Decimal]]]:
    """강한 양의 단조성: 각 날짜에서 score 오름 → fwd 오름 (완벽한 상관)."""
    groups: list[list[tuple[Decimal, Decimal]]] = []
    for _ in range(n_dates):
        records = [(Decimal(k + 1), Decimal(k + 1) / Decimal("10")) for k in range(n_per_date)]
        groups.append(records)
    return groups


def _make_noise_groups(
    n_dates: int = 20, n_per_date: int = 8, seed: int = 99
) -> list[list[tuple[Decimal, Decimal]]]:
    """노이즈: score 와 fwd 가 독립 (날짜마다 fwd 순서를 섞음)."""
    rng = _rnd.Random(seed)
    groups: list[list[tuple[Decimal, Decimal]]] = []
    for _ in range(n_dates):
        scores = [Decimal(k + 1) for k in range(n_per_date)]
        fwds = [Decimal(k + 1) / Decimal("10") for k in range(n_per_date)]
        rng.shuffle(fwds)
        groups.append(list(zip(scores, fwds, strict=True)))
    return groups


# ---------------------------------------------------------------------------
# block_bootstrap_ci
# ---------------------------------------------------------------------------


def test_block_bootstrap_ci_strong_signal_lo_above_zero() -> None:
    """강한 양의 신호 → CI 하한 > 0.
    완벽한 단조성이면 모든 부트스트랩 표본도 1.0 → lo == hi == 1.0 이 정상."""
    groups = _make_signal_groups(n_dates=20, n_per_date=8)
    lo, hi = block_bootstrap_ci(
        groups,
        _spearman_stat,
        n_resamples=200,
        seed=42,
    )
    assert lo > Decimal("0"), f"Signal CI lo should be > 0, got lo={lo}, hi={hi}"
    assert hi >= lo


def test_block_bootstrap_ci_noise_straddles_zero() -> None:
    """노이즈 → CI 하한 < 0 (0 을 포함)."""
    groups = _make_noise_groups(n_dates=20, n_per_date=8, seed=77)
    lo, hi = block_bootstrap_ci(
        groups,
        _spearman_stat,
        n_resamples=200,
        seed=42,
    )
    assert lo < Decimal("0"), f"Noise CI lo should be < 0, got lo={lo}, hi={hi}"
    assert hi > lo


def _make_partial_signal_groups(
    n_dates: int = 30, n_per_date: int = 10, seed: int = 123
) -> list[list[tuple[Decimal, Decimal]]]:
    """중간 강도 양의 단조성: score 와 양의 상관이나 정수 노이즈로 완벽하지 않음.

    완벽 신호(= lo==hi==1.0 의 퇴화 점-CI)와 달리 부트스트랩 CI 가 '실구간'이 되어,
    퍼센타일 방향(lo↔hi) 뒤바뀜·off-by-one 버그까지 잡을 수 있는 테스트.
    """
    rng = _rnd.Random(seed)
    groups: list[list[tuple[Decimal, Decimal]]] = []
    for _ in range(n_dates):
        records = [
            (Decimal(k + 1), (Decimal(k + 1) + Decimal(rng.randint(-3, 3))) / Decimal("10"))
            for k in range(n_per_date)
        ]
        groups.append(records)
    return groups


def test_block_bootstrap_ci_partial_signal_is_bounded_interval() -> None:
    """중간 신호 → CI 가 0 < lo < hi < 1 인 '실구간'(퇴화 점-CI 아님). 퍼센타일 방향 가드."""
    groups = _make_partial_signal_groups(n_dates=30, n_per_date=10, seed=123)
    lo, hi = block_bootstrap_ci(groups, _spearman_stat, n_resamples=300, seed=42)
    assert lo < hi, f"CI 는 실구간이어야 함(퇴화 점-CI 아님): lo={lo}, hi={hi}"
    assert lo > Decimal("0"), f"중간 양의 신호이므로 lo>0 이어야: lo={lo}, hi={hi}"
    assert hi < Decimal("1"), f"완벽상관 아니므로 hi<1 이어야: lo={lo}, hi={hi}"


def test_block_bootstrap_ci_determinism() -> None:
    """동일 seed → 동일 결과."""
    groups = _make_signal_groups()
    r1 = block_bootstrap_ci(groups, _spearman_stat, n_resamples=100, seed=7)
    r2 = block_bootstrap_ci(groups, _spearman_stat, n_resamples=100, seed=7)
    assert r1 == r2


def test_block_bootstrap_ci_different_seeds_differ() -> None:
    """다른 seed → 다른 결과 (노이즈 데이터에서 확인)."""
    groups = _make_noise_groups(seed=55)
    r1 = block_bootstrap_ci(groups, _spearman_stat, n_resamples=200, seed=1)
    r2 = block_bootstrap_ci(groups, _spearman_stat, n_resamples=200, seed=2)
    assert r1 != r2


def test_block_bootstrap_ci_empty_groups() -> None:
    lo, hi = block_bootstrap_ci([], _spearman_stat, n_resamples=100, seed=1)
    assert lo == Decimal("0") and hi == Decimal("0")


# ---------------------------------------------------------------------------
# permutation_pvalue
# ---------------------------------------------------------------------------


def _make_signal_groups_perm(
    n_dates: int = 20, n_per_date: int = 10
) -> list[tuple[list[Decimal], list[Decimal]]]:
    """강한 양의 단조성: score 오름 → fwd 오름."""
    groups = []
    for _ in range(n_dates):
        scores = [Decimal(k + 1) for k in range(n_per_date)]
        fwds = [Decimal(k + 1) / Decimal("10") for k in range(n_per_date)]
        groups.append((scores, fwds))
    return groups


def _make_noise_groups_perm(
    n_dates: int = 20, n_per_date: int = 10, seed: int = 55
) -> list[tuple[list[Decimal], list[Decimal]]]:
    """노이즈: score 와 fwd 독립."""
    rng = _rnd.Random(seed)
    groups = []
    for _ in range(n_dates):
        scores = [Decimal(k + 1) for k in range(n_per_date)]
        fwds = [Decimal(k + 1) / Decimal("10") for k in range(n_per_date)]
        rng.shuffle(fwds)
        groups.append((scores, fwds))
    return groups


def test_permutation_pvalue_strong_signal_small_p() -> None:
    """강한 신호 → p < 0.05."""
    groups = _make_signal_groups_perm(n_dates=20, n_per_date=10)
    pool_scores = [s for g, _ in groups for s in g]
    pool_fwds = [f for _, fs in groups for f in fs]
    obs = spearman_monotonicity(pool_scores, pool_fwds)
    p = permutation_pvalue(groups, observed=obs, n_perms=200, seed=42)
    assert p < Decimal("0.05"), f"Strong signal p-value should be < 0.05, got {p}"


def test_permutation_pvalue_noise_large_p() -> None:
    """노이즈 → p > 0.2."""
    groups = _make_noise_groups_perm(n_dates=20, n_per_date=10, seed=88)
    pool_scores = [s for g, _ in groups for s in g]
    pool_fwds = [f for _, fs in groups for f in fs]
    obs = spearman_monotonicity(pool_scores, pool_fwds)
    p = permutation_pvalue(groups, observed=obs, n_perms=200, seed=42)
    assert p > Decimal("0.2"), f"Noise p-value should be > 0.2, got {p}"


def test_permutation_pvalue_in_range() -> None:
    """p 는 항상 (0, 1] 범위."""
    groups = _make_signal_groups_perm()
    pool_scores = [s for g, _ in groups for s in g]
    pool_fwds = [f for _, fs in groups for f in fs]
    obs = spearman_monotonicity(pool_scores, pool_fwds)
    p = permutation_pvalue(groups, observed=obs, n_perms=50, seed=1)
    assert Decimal("0") < p <= Decimal("1")


def test_permutation_pvalue_determinism() -> None:
    """동일 seed → 동일 p."""
    groups = _make_signal_groups_perm()
    pool_scores = [s for g, _ in groups for s in g]
    pool_fwds = [f for _, fs in groups for f in fs]
    obs = spearman_monotonicity(pool_scores, pool_fwds)
    p1 = permutation_pvalue(groups, observed=obs, n_perms=100, seed=7)
    p2 = permutation_pvalue(groups, observed=obs, n_perms=100, seed=7)
    assert p1 == p2


# ---------------------------------------------------------------------------
# paired_diff_ci
# ---------------------------------------------------------------------------


def _make_better_groups(
    n_dates: int = 20, n_per_date: int = 8
) -> list[list[tuple[Decimal, Decimal]]]:
    """A: 완벽한 양의 단조성."""
    groups: list[list[tuple[Decimal, Decimal]]] = []
    for _ in range(n_dates):
        records = [(Decimal(k + 1), Decimal(k + 1) / Decimal("10")) for k in range(n_per_date)]
        groups.append(records)
    return groups


def _make_worse_groups(
    n_dates: int = 20, n_per_date: int = 8
) -> list[list[tuple[Decimal, Decimal]]]:
    """B: 완벽한 음의 단조성."""
    groups: list[list[tuple[Decimal, Decimal]]] = []
    for _ in range(n_dates):
        records = [
            (Decimal(k + 1), Decimal(n_per_date - k) / Decimal("10")) for k in range(n_per_date)
        ]
        groups.append(records)
    return groups


def test_paired_diff_ci_a_better_than_b_lo_positive() -> None:
    """A(완벽 양의 단조성) − B(완벽 음의 단조성) → CI 하한 > 0.
    완벽한 경우 모든 리샘플 diff = 2.0 → lo == hi == 2.0 이 정상."""
    groups_a = _make_better_groups()
    groups_b = _make_worse_groups()
    lo, hi = paired_diff_ci(
        groups_a,
        groups_b,
        _spearman_stat,
        n_resamples=200,
        seed=42,
        lo=Decimal("0.025"),
        hi=Decimal("0.975"),
    )
    assert lo > Decimal("0"), f"A better than B: CI lo should be > 0, got lo={lo}, hi={hi}"
    assert hi >= lo


def test_paired_diff_ci_identical_straddles_zero() -> None:
    """A == B → 차이 CI 가 0 포함 (lo <= 0 <= hi)."""
    groups_a = _make_better_groups()
    groups_b = _make_better_groups()  # 동일
    lo, hi = paired_diff_ci(
        groups_a,
        groups_b,
        _spearman_stat,
        n_resamples=200,
        seed=42,
        lo=Decimal("0.025"),
        hi=Decimal("0.975"),
    )
    assert lo <= Decimal("0") <= hi, f"Identical groups: CI should contain 0, got lo={lo}, hi={hi}"


def test_paired_diff_ci_determinism() -> None:
    """동일 seed → 동일 결과."""
    groups_a = _make_better_groups()
    groups_b = _make_worse_groups()
    r1 = paired_diff_ci(
        groups_a,
        groups_b,
        _spearman_stat,
        n_resamples=100,
        seed=5,
        lo=Decimal("0.025"),
        hi=Decimal("0.975"),
    )
    r2 = paired_diff_ci(
        groups_a,
        groups_b,
        _spearman_stat,
        n_resamples=100,
        seed=5,
        lo=Decimal("0.025"),
        hi=Decimal("0.975"),
    )
    assert r1 == r2


def test_paired_diff_ci_mismatched_lengths() -> None:
    """길이 불일치 → (0, 0) 반환."""
    lo, hi = paired_diff_ci(
        _make_better_groups(n_dates=5),
        _make_better_groups(n_dates=3),
        _spearman_stat,
        n_resamples=50,
        seed=1,
        lo=Decimal("0.025"),
        hi=Decimal("0.975"),
    )
    assert lo == Decimal("0") and hi == Decimal("0")


# ---------------------------------------------------------------------------
# bh_fdr_reject
# ---------------------------------------------------------------------------


def test_bh_fdr_rejects_only_small_pvalues() -> None:
    from backend.backtest.metrics import bh_fdr_reject

    ps = [Decimal("0.001"), Decimal("0.2"), Decimal("0.04"), Decimal("0.8")]
    rej = bh_fdr_reject(ps, q=Decimal("0.10"))
    assert rej == [True, False, True, False]


def test_bh_fdr_empty_input() -> None:
    from backend.backtest.metrics import bh_fdr_reject

    assert bh_fdr_reject([], q=Decimal("0.10")) == []


def test_bh_fdr_step_up_all_reject() -> None:
    """BH step-up: p_(k) <= q*k/m 가 모든 k 에서 성립하면 전부 기각.

    ps = [0.01, 0.02, 0.03, 0.04, 0.05], m=5, q=0.05.
    정렬 순서 = 동일(이미 오름차순).
    k=1: 0.01 <= 0.05*1/5 = 0.01  ✓
    k=2: 0.02 <= 0.05*2/5 = 0.02  ✓
    k=3: 0.03 <= 0.05*3/5 = 0.03  ✓
    k=4: 0.04 <= 0.05*4/5 = 0.04  ✓
    k=5: 0.05 <= 0.05*5/5 = 0.05  ✓
    → thresh_rank=5 → 모두 기각.
    """
    from backend.backtest.metrics import bh_fdr_reject

    ps = [Decimal("0.01"), Decimal("0.02"), Decimal("0.03"), Decimal("0.04"), Decimal("0.05")]
    rej = bh_fdr_reject(ps, q=Decimal("0.05"))
    assert rej == [True, True, True, True, True]
