"""폴백 C 리포트 렌더 — 레이어1 ΔMAE 표 + 레이어2 오버레이 위험조정 표."""

from __future__ import annotations

from backend.backtest.report import render_fallback_c_markdown


def test_render_fallback_c_markdown_has_sections() -> None:
    md = render_fallback_c_markdown(
        layer1_rows=[("0.30", "0.001", "0.0003"), ("0.12", "0.002", "0.001")],
        layer2={
            "no_overlay": {"mdd": "-0.20", "sharpe": "0.5", "calmar": "0.4"},
            "+regime+atr+sizing": {"mdd": "-0.12", "sharpe": "0.6", "calmar": "0.7"},
        },
    )
    assert "레이어1" in md and "레이어2" in md and "MDD" in md and "no_overlay" in md
    assert "0.30" in md and "-0.12" in md
