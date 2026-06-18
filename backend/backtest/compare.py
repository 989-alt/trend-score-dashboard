"""프리셋 비교 게이트 — paired_diff_ci 기반 Δ단조성·ΔMAE 유의성 (T4).

compare_presets(panel, cfg, wf, variant_preset, baseline_preset="baseline")
  -> ComparisonResult

설계:
  - 두 프리셋을 동일 OOS dates × 동일 ticker set 에서 비교.
  - 점수는 프리셋별로 다르지만, fwd/mae 는 preset-independent(사후 가격).
  - 날짜별로 페어링하여 paired_diff_ci 로 Δmono/ΔMAE CI 를 구한다.

게이트 (엄격):
  - Δmono = mono_variant - mono_baseline > 0  AND  CI lo > 0
  - ΔMAE  = mae_variant  - mae_baseline  > 0  AND  CI lo > 0
    (MAE 는 음수; ΔMAE > 0 = 더 0에 가까운 = 개선)
  - strict_pass = 두 조건 모두 충족

결정론: bootstrap_seed, n_resamples 는 BacktestConfig 에서 상속.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from backend.backtest import metrics
from backend.backtest.panel import Panel
from backend.backtest.run import (
    BacktestConfig,
    WalkForwardConfig,
    _fwd_return,
    _mae,
    _rebalance_dates,
    _score_at,
    _walk_forward_splits,
)
from backend.config import Settings, get_settings

if TYPE_CHECKING:
    from backend.backtest.horserace import FactorFn


@dataclass(frozen=True)
class HorizonComparison:
    """단일 호라이즌 비교 결과."""

    horizon: int
    # 단조성(Spearman)
    mono_variant: Decimal
    mono_baseline: Decimal
    dmono: Decimal
    dmono_ci_lo: Decimal
    dmono_ci_hi: Decimal
    # MAE (음수; 더 0에 가까울수록 개선)
    mae_variant: Decimal
    mae_baseline: Decimal
    dmae: Decimal
    dmae_ci_lo: Decimal
    dmae_ci_hi: Decimal
    # 관찰 수
    n: int
    n_dates: int
    # 게이트 통과 여부
    mono_pass: bool
    mae_pass: bool
    strict_pass: bool


@dataclass(frozen=True)
class ComparisonResult:
    """compare_presets 반환값."""

    variant_preset: str
    baseline_preset: str
    oos_dates: list[date]
    n_rebalance_dates: int
    n_tickers: int
    horizons: dict[int, HorizonComparison]
    overall_pass: bool  # 전체 호라이즌 strict_pass 모두 True


def _collect_groups(
    panel: Panel,
    dates: list[date],
    settings: Settings,
    cfg: BacktestConfig,
    preset: str,
    horizons: tuple[int, ...],
    *,
    alpha_factors: Mapping[str, FactorFn] | None = None,
) -> tuple[
    dict[int, list[list[tuple[Decimal, Decimal]]]],
    dict[int, list[list[Decimal]]],
]:
    """날짜-블록 그룹을 수집한다 (block_bootstrap_ci 호환 형식).

    반환:
      mono_groups[h] = list over dates of [(score, fwd), ...]
                      (전체 eligible tickers — Spearman 단조성 비교용)
      mae_groups[h]  = list over dates of [mae, ...]
                      (top_n 픽 기준 — 프리셋 랭킹이 다르면 다른 종목 선택됨)

    Spearman 단조성: ALL eligible tickers 풀링 (순위 정보 전체 사용).
    MAE: top_n 픽 기준 — 두 프리셋이 다른 순위를 매기면 서로 다른 종목을 상위에
    올리게 되고, 그 종목들의 평균 MAE 가 달라진다. ALL tickers 를 쓰면
    eligible set 이 동일하므로 ΔMAE = 0 이 된다.

    alpha_factors: preset=="alpha_composite" 일 때 _score_at 로 전달(승자 팩터).
                   baseline 호출에는 넘기지 않는다(None).
    """
    mono_groups: dict[int, list[list[tuple[Decimal, Decimal]]]] = {h: [] for h in horizons}
    mae_groups: dict[int, list[list[Decimal]]] = {h: [] for h in horizons}
    top_n = cfg.top_n

    for t in dates:
        ranked = _score_at(panel, t, settings, preset, alpha_factors=alpha_factors)
        top_picks = [tk for tk, _ in ranked[:top_n]]

        date_mono: dict[int, list[tuple[Decimal, Decimal]]] = {h: [] for h in horizons}
        date_mae: dict[int, list[Decimal]] = {h: [] for h in horizons}

        for _tk, sv in ranked:
            for h in horizons:
                fr = _fwd_return(panel, _tk, t, h)
                mae_val = _mae(panel, _tk, t, h)
                if fr is not None and mae_val is not None:
                    # Spearman: 전체 eligible
                    date_mono[h].append((sv, fr))
                    # MAE: top_n 픽만
                    if _tk in top_picks:
                        date_mae[h].append(mae_val)

        for h in horizons:
            if date_mono[h]:
                mono_groups[h].append(date_mono[h])
            if date_mae[h]:
                mae_groups[h].append(date_mae[h])

    return mono_groups, mae_groups


def _spearman_from_pairs(pairs: list[tuple[Decimal, Decimal]]) -> Decimal:
    """(score, fwd) 튜플 리스트 -> pooled Spearman."""
    s = [p[0] for p in pairs]
    f = [p[1] for p in pairs]
    return metrics.spearman_monotonicity(s, f)


def _mean_mae(vals: list[Decimal]) -> Decimal:
    """MAE 리스트 -> 평균."""
    if not vals:
        return Decimal("0")
    return sum(vals, Decimal("0")) / Decimal(len(vals))


def compare_presets(
    panel: Panel,
    cfg: BacktestConfig,
    wf: WalkForwardConfig,
    variant_preset: str,
    baseline_preset: str = "baseline",
    *,
    alpha_factors: Mapping[str, FactorFn] | None = None,
    settings: Settings | None = None,
) -> ComparisonResult:
    """OOS dates 에서 variant vs baseline 을 페어드 비교한다.

    cfg.preset 값은 무시하고 variant_preset / baseline_preset 을 직접 사용.
    OOS dates 는 run_walk_forward 와 동일한 walk-forward 분할 로직으로 산정.

    게이트:
      Δmono > 0 AND dmono_ci_lo > 0  (단조성 개선 + CI 가 0 제외)
      ΔMAE  > 0 AND dmae_ci_lo  > 0  (MAE 개선   + CI 가 0 제외)

    alpha_factors: variant 가 alpha_composite 인 경우에만 variant 채점에 전달.
                   baseline 채점에는 절대 전달하지 않는다(공정 비교).
    settings: 외부에서 주입할 Settings 인스턴스. None 이면 get_settings() 로 로드.
              ablation 등에서 weight_52w_fallback 등을 오버라이드할 때 사용.
    """
    settings = settings if settings is not None else get_settings()
    horizons = cfg.forward_horizons

    dates = _rebalance_dates(panel, cfg)
    fold_ranges, _holdout = _walk_forward_splits(dates, wf)
    oos_dates: list[date] = [t for _train, test in fold_ranges for t in test]

    if not oos_dates:
        empty_h = {
            h: HorizonComparison(
                horizon=h,
                mono_variant=Decimal("0"),
                mono_baseline=Decimal("0"),
                dmono=Decimal("0"),
                dmono_ci_lo=Decimal("0"),
                dmono_ci_hi=Decimal("0"),
                mae_variant=Decimal("0"),
                mae_baseline=Decimal("0"),
                dmae=Decimal("0"),
                dmae_ci_lo=Decimal("0"),
                dmae_ci_hi=Decimal("0"),
                n=0,
                n_dates=0,
                mono_pass=False,
                mae_pass=False,
                strict_pass=False,
            )
            for h in horizons
        }
        return ComparisonResult(
            variant_preset=variant_preset,
            baseline_preset=baseline_preset,
            oos_dates=oos_dates,
            n_rebalance_dates=len(dates),
            n_tickers=len(panel.series),
            horizons=empty_h,
            overall_pass=False,
        )

    var_mono_groups, var_mae_groups = _collect_groups(
        panel, oos_dates, settings, cfg, variant_preset, horizons, alpha_factors=alpha_factors
    )
    # baseline 은 alpha_factors 미전달(고정 None) — 변형만 팩터로 채점해야 공정 비교.
    base_mono_groups, base_mae_groups = _collect_groups(
        panel, oos_dates, settings, cfg, baseline_preset, horizons
    )

    result_horizons: dict[int, HorizonComparison] = {}
    for h in horizons:
        vg = var_mono_groups[h]
        bg = base_mono_groups[h]
        vmg = var_mae_groups[h]
        bmg = base_mae_groups[h]

        n_dates_v = len(vg)
        n_dates_b = len(bg)
        n_paired = min(n_dates_v, n_dates_b)

        paired_vg = vg[:n_paired]
        paired_bg = bg[:n_paired]
        paired_vmg = vmg[:n_paired]
        paired_bmg = bmg[:n_paired]

        all_vg_flat = [pair for grp in vg for pair in grp]
        all_bg_flat = [pair for grp in bg for pair in grp]
        all_vmg_flat = [m for grp in vmg for m in grp]
        all_bmg_flat = [m for grp in bmg for m in grp]

        mono_v = _spearman_from_pairs(all_vg_flat) if all_vg_flat else Decimal("0")
        mono_b = _spearman_from_pairs(all_bg_flat) if all_bg_flat else Decimal("0")
        mae_v = _mean_mae(all_vmg_flat) if all_vmg_flat else Decimal("0")
        mae_b = _mean_mae(all_bmg_flat) if all_bmg_flat else Decimal("0")

        dmono = mono_v - mono_b
        dmae = mae_v - mae_b

        n_obs = max(len(all_vg_flat), len(all_bg_flat))

        if paired_vg and paired_bg and n_paired > 0:
            dmono_ci_lo, dmono_ci_hi = metrics.paired_diff_ci(
                paired_vg,
                paired_bg,
                _spearman_from_pairs,
                n_resamples=cfg.n_resamples,
                seed=cfg.bootstrap_seed,
            )
            dmae_ci_lo, dmae_ci_hi = metrics.paired_diff_ci(
                paired_vmg,
                paired_bmg,
                _mean_mae,
                n_resamples=cfg.n_resamples,
                seed=cfg.bootstrap_seed,
            )
        else:
            dmono_ci_lo = dmono_ci_hi = Decimal("0")
            dmae_ci_lo = dmae_ci_hi = Decimal("0")

        mono_pass = bool(dmono > Decimal("0") and dmono_ci_lo > Decimal("0"))
        mae_pass = bool(dmae > Decimal("0") and dmae_ci_lo > Decimal("0"))
        strict_pass = mono_pass and mae_pass

        result_horizons[h] = HorizonComparison(
            horizon=h,
            mono_variant=mono_v,
            mono_baseline=mono_b,
            dmono=dmono,
            dmono_ci_lo=dmono_ci_lo,
            dmono_ci_hi=dmono_ci_hi,
            mae_variant=mae_v,
            mae_baseline=mae_b,
            dmae=dmae,
            dmae_ci_lo=dmae_ci_lo,
            dmae_ci_hi=dmae_ci_hi,
            n=n_obs,
            n_dates=n_paired,
            mono_pass=mono_pass,
            mae_pass=mae_pass,
            strict_pass=strict_pass,
        )

    overall_pass = all(hc.strict_pass for hc in result_horizons.values())
    return ComparisonResult(
        variant_preset=variant_preset,
        baseline_preset=baseline_preset,
        oos_dates=oos_dates,
        n_rebalance_dates=len(dates),
        n_tickers=len(panel.series),
        horizons=result_horizons,
        overall_pass=overall_pass,
    )


def render_comparison_markdown(result: ComparisonResult, cfg: BacktestConfig) -> str:
    """ComparisonResult -> 마크다운 리포트."""
    oos_span = ""
    if result.oos_dates:
        oos_span = f" ({result.oos_dates[0].isoformat()}~{result.oos_dates[-1].isoformat()})"
    lines = [
        f"# 프리셋 비교 리포트 — {result.variant_preset} vs {result.baseline_preset}",
        "",
        "## 설계",
        f"- 기간 {cfg.start}~{cfg.end} · 리밸런스 {cfg.rebalance}",
        f"- OOS 날짜 수: {len(result.oos_dates)}{oos_span}",
        f"- 전체 리밸런스 날짜 수(in-sample 포함): {result.n_rebalance_dates}",
        f"- 유니버스 종목 수: {result.n_tickers}",
        f"- Bootstrap n_resamples={cfg.n_resamples}",
        "",
        "## 게이트 판정",
        f"- **전체(overall_pass)**: {'PASS' if result.overall_pass else 'FAIL'}",
        "  - 기준: 모든 호라이즌에서 Δmono > 0 AND CI lo > 0, ΔMAE > 0 AND CI lo > 0",
        "",
        "## 호라이즌별 상세",
        "| 호라이즌 | mono_base | mono_var | Δmono | Δmono 95%CI lo | Δmono 95%CI hi | mono_pass |"
        " mae_base | mae_var | ΔMAE | ΔMAE CI lo | ΔMAE CI hi | mae_pass | strict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for h, hc in sorted(result.horizons.items()):
        mono_pass_s = "PASS" if hc.mono_pass else "FAIL"
        mae_pass_s = "PASS" if hc.mae_pass else "FAIL"
        strict_s = "PASS" if hc.strict_pass else "FAIL"
        lines.append(
            f"| {h}d | {hc.mono_baseline} | {hc.mono_variant} | {hc.dmono}"
            f" | {hc.dmono_ci_lo:.4f} | {hc.dmono_ci_hi:.4f} | {mono_pass_s}"
            f" | {hc.mae_baseline:.4f} | {hc.mae_variant:.4f} | {hc.dmae:.4f}"
            f" | {hc.dmae_ci_lo:.4f} | {hc.dmae_ci_hi:.4f} | {mae_pass_s} | {strict_s} |"
        )
    lines += [
        "",
        "## N (관찰 수 / 날짜 수)",
        "| 호라이즌 | N(관찰) | N(날짜) |",
        "|---|---|---|",
    ]
    for h, hc in sorted(result.horizons.items()):
        lines.append(f"| {h}d | {hc.n} | {hc.n_dates} |")
    lines += [
        "",
        "> 수익 보장 없음. 유의성 게이트 통과 != 실전 수익 보장.",
        "> 게이트 기준: Δmono > 0 AND CI lo > 0; ΔMAE > 0 AND CI lo > 0 (MAE는 음수, Δ>0=개선).",
    ]
    return "\n".join(lines)


def render_comparison_json(result: ComparisonResult, cfg: BacktestConfig) -> dict[str, Any]:
    """ComparisonResult -> JSON-직렬화용 dict."""
    return {
        "config": {
            "start": cfg.start.isoformat(),
            "end": cfg.end.isoformat(),
            "rebalance": cfg.rebalance,
            "variant_preset": result.variant_preset,
            "baseline_preset": result.baseline_preset,
            "n_resamples": cfg.n_resamples,
        },
        "summary": {
            "oos_date_count": len(result.oos_dates),
            "oos_start": result.oos_dates[0].isoformat() if result.oos_dates else None,
            "oos_end": result.oos_dates[-1].isoformat() if result.oos_dates else None,
            "n_rebalance_dates": result.n_rebalance_dates,
            "n_tickers": result.n_tickers,
            "overall_pass": result.overall_pass,
        },
        "horizons": {
            str(h): {
                "mono_variant": str(hc.mono_variant),
                "mono_baseline": str(hc.mono_baseline),
                "dmono": str(hc.dmono),
                "dmono_ci_lo": str(hc.dmono_ci_lo),
                "dmono_ci_hi": str(hc.dmono_ci_hi),
                "mae_variant": str(hc.mae_variant),
                "mae_baseline": str(hc.mae_baseline),
                "dmae": str(hc.dmae),
                "dmae_ci_lo": str(hc.dmae_ci_lo),
                "dmae_ci_hi": str(hc.dmae_ci_hi),
                "n": hc.n,
                "n_dates": hc.n_dates,
                "mono_pass": hc.mono_pass,
                "mae_pass": hc.mae_pass,
                "strict_pass": hc.strict_pass,
            }
            for h, hc in result.horizons.items()
        },
    }


__all__ = [
    "ComparisonResult",
    "HorizonComparison",
    "compare_presets",
    "render_comparison_json",
    "render_comparison_markdown",
]
