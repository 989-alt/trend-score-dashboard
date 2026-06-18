"""넓은 KR 유니버스 — 후보 코드 → 최근 거래대금 상위 N. 결정론(주입형)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def top_by_turnover(
    codes: list[str], turnover_of: Callable[[str], Decimal | None], *, top_n: int
) -> list[str]:
    """turnover_of(code)>0 인 코드를 거래대금 내림차순 정렬해 상위 top_n. 동률은 코드 오름차순."""
    scored: list[tuple[Decimal, str]] = []
    for c in codes:
        t = turnover_of(c)
        if t is not None and t > 0:
            scored.append((t, c))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [c for _, c in scored[:top_n]]


def build_kr_universe(top_n: int, cache_dir: Path) -> list[str]:
    """KOSPI∪KOSDAQ 거래대금 상위 top_n 코드 리스트. 실패 시 빈 리스트(fail-open).

    1. cache_dir/universe_{top_n}.json 이 있으면 즉시 반환.
    2. 없으면 pykrx 벌크 조회 → 정렬 → 캐시 저장 후 반환.
    """
    cache_path = cache_dir / f"universe_{top_n}.json"
    if cache_path.exists():
        try:
            return list(json.loads(cache_path.read_text(encoding="utf-8")))
        except Exception:
            logger.warning("유니버스 캐시 읽기 실패, 재조회", exc_info=True)

    try:
        import pandas as pd  # lazy import
        from pykrx import stock  # 임포트 시 KRX_ID/KRX_PW 자동 로그인(커스텀 래퍼)

        bday = stock.get_nearest_business_day_in_a_week()
        kospi = stock.get_market_ohlcv_by_ticker(bday, market="KOSPI")
        kosdaq = stock.get_market_ohlcv_by_ticker(bday, market="KOSDAQ")
        frames: list[Any] = [
            f for f in (kospi, kosdaq) if f is not None and not f.empty and "거래대금" in f
        ]
        if not frames:
            logger.warning("pykrx 거래대금 프레임 없음 — 빈 유니버스")
            return []

        combined = pd.concat(frames)
        turn_map: dict[str, Decimal] = {
            str(idx).zfill(6): Decimal(str(row["거래대금"])) for idx, row in combined.iterrows()
        }
        codes = list(turn_map.keys())
        result = top_by_turnover(codes, lambda c: turn_map.get(c), top_n=top_n)

        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return result

    except Exception:
        logger.warning("pykrx 유니버스 조회 실패 — 빈 유니버스 반환", exc_info=True)
        return []


# fmt: off
# Static large-cap US universe (~150 tickers, S&P 500 market-cap order, yfinance-compatible).
# SURVIVORSHIP BIAS WARNING: current survivors only — companies delisted since 2016 are absent.
# Some recent listings (post-2016 IPOs) may lack full history and will be dropped fail-open.
US_UNIVERSE: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "BRK-B", "LLY", "AVGO",
    "TSLA", "JPM", "WMT", "V", "UNH", "XOM", "MA", "PG", "JNJ", "HD",
    "COST", "ORCL", "ABBV", "BAC", "KO", "MRK", "CVX", "NFLX", "AMD", "PEP",
    "CRM", "TMO", "ADBE", "LIN", "MCD", "CSCO", "ACN", "WFC", "ABT", "DHR",
    "GE", "TXN", "QCOM", "AMGN", "PM", "DIS", "INTU", "CAT", "IBM", "VZ",
    "CMCSA", "NOW", "UNP", "GS", "SPGI", "MS", "RTX", "PFE", "NEE", "HON",
    "LOW", "AXP", "ISRG", "BKNG", "T", "ELV", "PGR", "TJX", "SYK", "VRTX",
    "LMT", "REGN", "BSX", "MDT", "C", "BLK", "CB", "ADP", "MMC", "AMAT",
    "PLD", "ETN", "CI", "DE", "ADI", "GILD", "BMY", "MU", "SBUX", "LRCX",
    "KLAC", "SCHW", "BX", "MO", "SO", "DUK", "ZTS", "ICE", "TGT", "BDX",
    "EOG", "SLB", "AON", "APH", "CME", "WM", "CL", "ITW", "MCK", "PH",
    "NKE", "GD", "USB", "MMM", "EMR", "CSX", "FCX", "NOC", "MAR", "PNC",
    "ORLY", "CDNS", "MSI", "COF", "SNPS", "ECL", "APD", "EW", "ROP", "AJG",
    "MCO", "DXCM", "HCA", "KMB", "F", "GM", "TT", "PSA", "TFC", "NSC",
    "AEP", "CARR", "ROST", "OXY", "D", "WELL", "SRE", "PCAR", "KMI", "STZ",
]
# fmt: on

__all__ = ["US_UNIVERSE", "build_kr_universe", "top_by_turnover"]
