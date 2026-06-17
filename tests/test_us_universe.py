"""Tests for US universe constant + US valuation skip (Change 1 & 2)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from unittest.mock import patch


def test_us_universe_is_list_of_strings() -> None:
    from backend.backtest.universe import US_UNIVERSE

    assert isinstance(US_UNIVERSE, list)
    assert len(US_UNIVERSE) > 100, "Expected ~150 tickers"


def test_us_universe_no_duplicates() -> None:
    from backend.backtest.universe import US_UNIVERSE

    assert len(US_UNIVERSE) == len(set(US_UNIVERSE)), "Duplicate tickers found"


def test_us_universe_all_uppercase_alpha_hyphen() -> None:
    from backend.backtest.universe import US_UNIVERSE

    pattern = re.compile(r"^[A-Z][A-Z0-9\-]*$")
    bad = [t for t in US_UNIVERSE if not pattern.match(t)]
    assert not bad, f"Non-uppercase/non-alpha tickers: {bad}"


def test_us_universe_no_6digit_codes() -> None:
    from backend.backtest.universe import US_UNIVERSE

    six_digit = [t for t in US_UNIVERSE if re.fullmatch(r"\d{6}", t)]
    assert not six_digit, f"KR-style 6-digit codes found: {six_digit}"


def test_us_universe_contains_known_names() -> None:
    from backend.backtest.universe import US_UNIVERSE

    for ticker in ("AAPL", "MSFT"):
        assert ticker in US_UNIVERSE, f"{ticker} not in US_UNIVERSE"


def test_us_universe_in_all() -> None:
    import backend.backtest.universe as mod

    assert "US_UNIVERSE" in mod.__all__


def test_valuation_returns_none_for_us(tmp_path: Path) -> None:
    """_valuation must return None for US market without touching pykrx."""
    from backend.backtest.loader import PanelLoader

    loader = PanelLoader(dart=None, cache_dir=tmp_path, market="US")

    with patch("pykrx.stock.get_market_fundamental_by_date") as mock_krx:
        result = loader._valuation("AAPL", date(2016, 1, 1), date(2024, 1, 1))

    assert result is None
    mock_krx.assert_not_called()


def test_valuation_calls_pykrx_for_kr(tmp_path: Path) -> None:
    """Sanity check: KR market still calls pykrx (or at least doesn't skip early)."""
    from backend.backtest.loader import PanelLoader

    loader = PanelLoader(dart=None, cache_dir=tmp_path, market="KR")

    with patch("pykrx.stock.get_market_fundamental_by_date", return_value=None) as mock_krx:
        result = loader._valuation("005930", date(2023, 1, 1), date(2023, 12, 31))

    # pykrx was called (not skipped) for KR
    mock_krx.assert_called_once()
    assert result is None  # patched to return None


def test_main_help_contains_market(capsys: object) -> None:
    """--help output must mention --market."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "backend.backtest.run", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert "--market" in result.stdout, "--market not found in --help output"
