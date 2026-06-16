from __future__ import annotations

from datetime import date

from tests.fixtures.backtest_synth import make_panel


def test_rows_asof_never_exceeds_T() -> None:  # noqa: N802
    panel = make_panel()
    T = date(2023, 4, 1)  # noqa: N806
    rows = panel.rows_asof("000001", T)
    assert rows, "기간 내 데이터가 있어야 함"
    assert all(r.date <= T for r in rows), "룩어헤드: T 이후 봉이 새면 안 됨"


def test_universe_excludes_not_yet_listed() -> None:
    panel = make_panel()
    early = date(2023, 2, 1)
    uni = panel.universe_asof(early)
    assert "000001" in uni
    assert "000002" not in uni, "생존편향: 미상장 종목이 유니버스에 들면 안 됨"


def test_fundamentals_asof_picks_latest_filed_on_or_before_T() -> None:  # noqa: N802
    panel = make_panel()
    f = panel.fundamentals_asof("000001", date(2023, 6, 1))
    assert f is not None and f.rcept_date == date(2023, 3, 31)
    f2 = panel.fundamentals_asof("000001", date(2024, 6, 1))
    assert f2 is not None and f2.rcept_date == date(2024, 3, 31), "접수일 ≤ T 중 최신"
    f0 = panel.fundamentals_asof("000001", date(2023, 1, 10))
    assert f0 is None, "아직 공시 전이면 None(fail-open)"
