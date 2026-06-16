from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
from backend.backtest.loader import PanelLoader


def test_build_panel_from_mocked_sources(monkeypatch, tmp_path) -> None:
    loader = PanelLoader(dart=None, cache_dir=tmp_path)

    idx = pd.to_datetime(["2023-01-02", "2023-01-03"])
    ohlcv = pd.DataFrame(
        {
            "시가": [100, 101], "고가": [102, 103], "저가": [99, 100],
            "종가": [101, 102], "거래량": [1000, 1100], "거래대금": [2e10, 2.1e10],
        },
        index=idx,
    )
    monkeypatch.setattr(loader, "_ohlcv", lambda ticker, s, e: ohlcv)
    monkeypatch.setattr(loader, "_index_ohlcv", lambda s, e: ohlcv)
    monkeypatch.setattr(loader, "_fundamentals", lambda ticker: [])

    panel = loader.build(["000001"], date(2023, 1, 2), date(2023, 1, 3))
    rows = panel.rows_asof("000001", date(2023, 1, 3))
    assert len(rows) == 2
    assert rows[-1].close == Decimal("102")
    assert panel.turnover_asof("000001", date(2023, 1, 3)) == Decimal("21000000000")
    assert "000001" in panel.universe_asof(date(2023, 1, 2))
