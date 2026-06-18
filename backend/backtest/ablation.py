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
