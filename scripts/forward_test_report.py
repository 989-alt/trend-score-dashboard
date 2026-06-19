"""전진검증(forward-test) 리포트 — 모의 매매봇 4주 실현성과 vs 벤치마크 매수후보유.

봇이 쌓은 NAV(``total_eval``) 시계열에서 포트폴리오 총수익·CAGR·MDD 를 산출하고, 같은
구간의 KOSPI(^KS11)/KOSDAQ(^KQ11)/S&P500(^GSPC) 매수후보유 CAGR 과 비교한다. 콘솔에
한글 요약표를 출력하고 동일 내용을 Markdown 으로 저장한다.

실행:
    uv run python scripts/forward_test_report.py [--db DB] [--out OUT.md]

벤치마크는 yfinance(curl_cffi 임퍼소네이션)로 조회하며, 실패 시 해당 벤치마크만 "조회실패"
로 보고하고 크래시하지 않는다. 금액 연산은 전부 Decimal(float 금지) — 벤치마크 float 는
``str()`` 경유로 Decimal 복원.
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

#: 프로젝트 루트를 import 경로에 추가(uv 외 직접 실행 대비).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.backtest import metrics  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.trader.store import TradeStore  # noqa: E402

#: 기간이 1일 미만일 때 CAGR div0 를 피하기 위한 연수 하한(≈ 하루).
_YEARS_FLOOR = Decimal("1") / Decimal("365.25")

#: 벤치마크 심볼(yfinance). 라벨 → Yahoo 심볼.
_BENCHMARKS: tuple[tuple[str, str], ...] = (
    ("KOSPI", "^KS11"),
    ("KOSDAQ", "^KQ11"),
    ("S&P500", "^GSPC"),
)

#: 면책 문구(헤더·푸터 상시 — 투자 자문 아님).
_DISCLAIMER = (
    "본 대시보드는 투자 자문에 해당하지 않으며, 투자의 판단과 결정은 철저히 개인에게 있습니다."
)


def compute_portfolio_metrics(nav: list[dict[str, Any]]) -> dict[str, Any] | None:
    """NAV 스냅샷에서 포트폴리오 메트릭 산출(네트워크 불요·순수 함수).

    ``nav`` = ``TradeStore.nav_series`` 형식(키 ``ts``=ISO str, ``total_eval``=Decimal|None).
    ``total_eval`` 가 있는 행만 사용하며, 유효 2점 미만이면 ``None``(데이터 부족 sentinel).
    반환 dict: first/last(Decimal), first_ts/last_ts(datetime), days(int), years(Decimal),
    total_return/cagr/mdd(Decimal).
    """
    points = [r for r in nav if r.get("total_eval") is not None]
    if len(points) < 2:
        return None
    first_ts = datetime.fromisoformat(points[0]["ts"])
    last_ts = datetime.fromisoformat(points[-1]["ts"])
    first: Decimal = points[0]["total_eval"]
    last: Decimal = points[-1]["total_eval"]
    days = max((last_ts - first_ts).days, 0)
    years = Decimal(days) / Decimal("365.25")
    if years < _YEARS_FLOOR:
        years = _YEARS_FLOOR  # 기간 부족 — div0 방지(하루로 바닥)
    total_return = (last / first - Decimal("1")) if first > 0 else Decimal("0")
    return {
        "first": first,
        "last": last,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "days": days,
        "years": years,
        "total_return": total_return,
        "cagr": metrics.cagr(first, last, years=years),
        "mdd": metrics.max_drawdown([r["total_eval"] for r in points]),
    }


def _yf_session() -> Any:
    """yfinance 공유 세션 — curl_cffi 브라우저 임퍼소네이션(Yahoo 429 회피). 미설치 시 None.

    market_data._session 패턴 미러. None 반환 시 yfinance 기본 세션 사용.
    """
    with contextlib.suppress(Exception):
        from curl_cffi import requests as cffi_requests

        return cffi_requests.Session(impersonate="chrome")
    return None


def fetch_benchmark_cagr(symbol: str, start: date, end: date, years: Decimal) -> Decimal | None:
    """``symbol`` 의 [start, end+1d] 종가로 매수후보유 CAGR 산출. 실패 시 None(조회실패).

    yfinance ``auto_adjust=False``. 종가 float 는 ``str()`` 경유로 Decimal 복원(정밀도).
    네트워크/파싱 오류는 호출 측이 "조회실패" 로 표기하도록 None 으로 흡수한다.
    """
    try:
        import yfinance as yf

        session = _yf_session()
        kwargs: dict[str, Any] = {
            "start": start.isoformat(),
            "end": (end + timedelta(days=1)).isoformat(),
            "auto_adjust": False,
            "progress": False,
        }
        if session is not None:
            kwargs["session"] = session
        data = yf.download(symbol, **kwargs)
        if data is None or getattr(data, "empty", True):
            return None
        closes = data["Close"]
        # 멀티종목 컬럼(단일 심볼이라도 MultiIndex 가능) → 첫 컬럼으로 평탄화.
        if getattr(closes, "ndim", 1) > 1:
            closes = closes.iloc[:, 0]
        closes = closes.dropna()
        if len(closes) < 2:
            return None
        first_close = Decimal(str(closes.iloc[0]))
        last_close = Decimal(str(closes.iloc[-1]))
        return metrics.cagr(first_close, last_close, years=years)
    except Exception:  # 네트워크/yfinance/파싱 실패 — 벤치마크 단위로 흡수
        return None


def _order_stats(orders: list[dict[str, Any]]) -> dict[str, int]:
    """주문 목록에서 매수/매도 건수 + 회전(왕복≈매도수) 집계."""
    buys = sum(1 for o in orders if o.get("side") == "buy")
    sells = sum(1 for o in orders if o.get("side") == "sell")
    return {"buys": buys, "sells": sells, "turnover": sells}


def _pct(value: Decimal) -> str:
    """Decimal 비율(0.1234) → 백분율 문자열(+12.34%)."""
    return f"{value * Decimal('100'):+.2f}%"


def _bench_pct(value: Decimal | None) -> str:
    """벤치마크 CAGR → 백분율 문자열. None 이면 '조회실패'."""
    return _pct(value) if value is not None else "조회실패"


def _build_report(
    pm: dict[str, Any],
    bench: dict[str, Decimal | None],
    stats: dict[str, int],
) -> str:
    """포트폴리오·벤치마크·주문 통계를 한글 Markdown 리포트 문자열로 조립."""
    first_ts: datetime = pm["first_ts"]
    last_ts: datetime = pm["last_ts"]
    kospi = bench.get("KOSPI")
    excess = (pm["cagr"] - kospi) if kospi is not None else None
    lines = [
        "# 전진검증 리포트 (모의 매매)",
        "",
        f"> {_DISCLAIMER}",
        "",
        "## 기간",
        "",
        f"- 시작: {first_ts.isoformat()}",
        f"- 종료: {last_ts.isoformat()}",
        f"- 일수: {pm['days']}일 (≈ {pm['years']:.3f}년)",
        "",
        "## 포트폴리오 (NAV 기준)",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| 시작 평가액 | {pm['first']:,} |",
        f"| 종료 평가액 | {pm['last']:,} |",
        f"| 총수익률 | {_pct(pm['total_return'])} |",
        f"| 연환산수익률(CAGR) | {_pct(pm['cagr'])} |",
        f"| 최대낙폭(MDD) | {_pct(pm['mdd'])} |",
        "",
        "## 벤치마크 매수후보유 (CAGR, 동일 구간)",
        "",
        "| 지수 | CAGR |",
        "| --- | --- |",
        f"| KOSPI | {_bench_pct(bench.get('KOSPI'))} |",
        f"| KOSDAQ | {_bench_pct(bench.get('KOSDAQ'))} |",
        f"| S&P500 | {_bench_pct(bench.get('S&P500'))} |",
        "",
        "## 초과수익 / 매매 통계",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| 초과수익(CAGR) vs KOSPI | {_bench_pct(excess)} |",
        f"| 매수 건수 | {stats['buys']} |",
        f"| 매도 건수 | {stats['sells']} |",
        f"| 회전(왕복≈매도) | {stats['turnover']} |",
        "",
        f"_{_DISCLAIMER}_",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    """엔트리포인트 — 인자 파싱, 메트릭 산출, 콘솔 출력 + Markdown 저장."""
    # Windows 콘솔 한글 깨짐 방지(서버는 PYTHONUTF8=1; 로컬 직접 실행 안전망).
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    settings = get_settings()
    parser = argparse.ArgumentParser(description="모의 매매봇 전진검증 리포트")
    parser.add_argument(
        "--db", type=Path, default=settings.trader_db_path, help="TradeStore DB 경로"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "forward_test_report.md",
        help="리포트 저장 경로",
    )
    args = parser.parse_args()

    store = TradeStore(args.db)
    nav = store.nav_series(limit=100000)
    pm = compute_portfolio_metrics(nav)
    if pm is None:
        print("데이터 부족(NAV 2점 미만)")
        return 0

    bench: dict[str, Decimal | None] = {
        label: fetch_benchmark_cagr(
            symbol, pm["first_ts"].date(), pm["last_ts"].date(), pm["years"]
        )
        for label, symbol in _BENCHMARKS
    }
    stats = _order_stats(store.recent_orders(100000))
    report = _build_report(pm, bench, stats)

    print(report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"\n[저장] {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
