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


__all__ = [
    "cagr",
    "max_adverse_excursion",
    "max_drawdown",
    "spearman_monotonicity",
    "win_rate",
]
