"""리밸런스 루프 + 포트폴리오 시뮬 + 이벤트스터디. 결정론.

각 리밸런스일 T: universe_asof → build_candidate(rows≤T) → scoring(무수정) → 상위 N 등가중.
forward-return 은 T 이후 가격으로만 평가(룩어헤드 0). 비용은 회전분에 bps 차감.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from backend import scoring as sc
from backend.backtest import metrics
from backend.backtest.metrics import _rank, _spearman_stat
from backend.backtest.panel import Panel
from backend.config import Settings, get_settings
from backend.factors import build_candidate
from backend.schemas import OHLCVRow

if TYPE_CHECKING:
    from backend.backtest.horserace import FactorFn

_REBAL_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30}


@dataclass(frozen=True)
class BacktestConfig:
    start: date
    end: date
    rebalance: str = "weekly"
    top_n: int = 20
    cost_bps: Decimal = Decimal("41")
    preset: str = "baseline"
    forward_horizons: tuple[int, ...] = (5, 20, 60)
    # 유의성 모듈 (Layer B) 결정론 시드 및 반복 횟수
    bootstrap_seed: int = 12345
    n_resamples: int = 1000
    n_perms: int = 1000


@dataclass(frozen=True)
class EventStudyBucket:
    monotonicity: Decimal
    mae: Decimal
    win_rate: Decimal
    n: int
    # Layer B: 유의성 필드 — 기본값 제공하므로 factor_study 구축 코드 변경 불필요
    mono_ci_lo: Decimal = Decimal("0")
    mono_ci_hi: Decimal = Decimal("0")
    mono_pvalue: Decimal = Decimal("1")
    mae_ci_lo: Decimal = Decimal("0")
    mae_ci_hi: Decimal = Decimal("0")


@dataclass(frozen=True)
class BacktestResult:
    portfolio_nav: list[Decimal]
    benchmark_nav: list[Decimal]
    rebalance_dates: list[date]
    event_study: dict[int, EventStudyBucket]
    factor_study: dict[str, dict[int, EventStudyBucket]]
    turnover_count: int


@dataclass(frozen=True)
class WalkForwardConfig:
    """앵커드(확장형) 워크포워드 설정 (Layer C).

    n_folds       = 비홀드아웃 구간을 나눌 테스트 폴드 수.
    holdout_frac  = 마지막에 떼어 둘 최종 홀드아웃 비율(∈[0,1)).
    scheme        = "anchored"(=expanding) 만 지원(v1). 향후 rolling 훅.
    """

    n_folds: int = 4
    holdout_frac: Decimal = Decimal("0.2")
    scheme: str = "anchored"

    def __post_init__(self) -> None:
        if self.n_folds < 1:
            raise ValueError(f"n_folds must be ≥ 1, got {self.n_folds}")
        if not (Decimal("0") <= self.holdout_frac < Decimal("1")):
            raise ValueError(f"holdout_frac must be in [0, 1), got {self.holdout_frac}")
        if self.scheme != "anchored":
            raise NotImplementedError(
                f"scheme={self.scheme!r} is not implemented; only 'anchored' is supported"
            )


@dataclass(frozen=True)
class WalkForwardResult:
    """워크포워드 결과 — in-sample(전체) vs OOS(테스트 폴드 합집합) vs 홀드아웃.

    per_fold      = 폴드별 테스트구간 이벤트스터디(폴드 i 의 TEST dates).
    oos           = 모든 테스트 폴드 dates 합집합의 이벤트스터디(가장 중요한 지표).
    holdout       = 최종 홀드아웃 dates 의 이벤트스터디(단 1회 소진).
    in_sample     = 전체 dates 의 이벤트스터디(= run_backtest.event_study 와 동일).
    fold_ranges   = 폴드별 (train_dates, test_dates). train 은 anchored prefix(v1 미피팅).
    oos_dates / holdout_dates / in_sample_dates = 각 구간의 리밸런스 날짜들.
    """

    per_fold: list[dict[int, EventStudyBucket]]
    oos: dict[int, EventStudyBucket]
    holdout: dict[int, EventStudyBucket]
    in_sample: dict[int, EventStudyBucket]
    fold_ranges: list[tuple[list[date], list[date]]]
    oos_dates: list[date]
    holdout_dates: list[date]
    in_sample_dates: list[date]


def _rebalance_dates(panel: Panel, cfg: BacktestConfig) -> list[date]:
    trading = sorted(
        {r.date for s in panel.series.values() for r in s.rows if cfg.start <= r.date <= cfg.end}
    )
    if not trading:
        return []
    step = _REBAL_DAYS[cfg.rebalance]
    out: list[date] = []
    last: date | None = None
    for d in trading:
        if last is None or (d - last) >= timedelta(days=step):
            out.append(d)
            last = d
    return out


def _index_momentum(panel: Panel, t: date, settings: Settings) -> Decimal:
    idx = panel.index_rows_asof(t)[-settings.lookback_days :]
    return sc.compute_momentum(idx) if len(idx) >= 2 else Decimal("0")


_QUALITY_FACTORS = ("roe", "op_margin", "rev_growth")
_VALUE_FACTORS = ("per", "pbr")
_STUDY_FACTORS = _QUALITY_FACTORS + _VALUE_FACTORS


def _quality_norm(panel: Panel, tickers: list[str], t: date) -> dict[str, Decimal]:
    """후보군 횡단면 퀄리티 합성 순위점수(0~1). as-of 퀄리티 팩터 평균의 min-max."""
    raw: dict[str, Decimal] = {}
    for tk in tickers:
        f = panel.fundamentals_asof(tk, t)
        if f is None:
            continue
        vals = [v for v in (f.roe, f.op_margin, f.rev_growth) if v is not None]
        if vals:
            raw[tk] = sum(vals, Decimal("0")) / Decimal(len(vals))
    if not raw:
        return {}
    lo, hi = min(raw.values()), max(raw.values())
    return {tk: sc.min_max_norm(v, lo, hi) for tk, v in raw.items()}


def _score_at(
    panel: Panel,
    t: date,
    settings: Settings,
    preset: str = "baseline",
    *,
    alpha_factors: Mapping[str, FactorFn] | None = None,
) -> list[tuple[str, Decimal]]:
    idx_mom = _index_momentum(panel, t, settings)
    cands: list[sc.Candidate] = []
    # entry_bias 프리셋 전용: ticker 별 rows 를 재사용 위해 stash
    rows_by_ticker: dict[str, list[OHLCVRow]] = {}
    for ticker in panel.universe_asof(t):
        rows = panel.rows_asof(ticker, t)
        if len(rows) < settings.ma200_window:
            continue
        # I1: 라이브와 동일하게 '트레일링 52주(≈252거래일) 고가'를 분모로 사용
        # (None → 전체이력 최고가 fallback 은 라이브-백테스트 score drift 유발).
        trailing = rows[-252:]
        w52_high = max((r.high for r in trailing), default=None)
        cands.append(
            build_candidate(
                ticker=ticker,
                rows=rows,
                w52_high=w52_high,
                index_momentum=idx_mom,
                turnover=panel.turnover_asof(ticker, t),
                min_turnover=settings.min_turnover_krw,
                settings=settings,
            )
        )
        if preset == "entry_bias":
            rows_by_ticker[ticker] = rows
    eligible = [c for c in cands if c.eligible]
    scored = sc.score_candidates(eligible, settings)
    base = {tk: sv for tk, (sv, _) in scored.items()}
    if preset == "quality_tilt":
        qnorm = _quality_norm(panel, list(base.keys()), t)
        w = Decimal("0.08")  # 리웨이트 스펙 prior — 퀄리티 틸트 가중
        base = {
            tk: sv * (Decimal("1") - w) + qnorm.get(tk, Decimal("0.5")) * w
            for tk, sv in base.items()
        }
    elif preset == "entry_bias":
        # entry_bias: near_52w 0.30→0.18, pullback_3pos 0.12 추가,
        # 나머지 가중치(pocket_pivot·momentum·rs·turnover·vol_fit) 불변.
        # extension_guard 승수를 곱해 과도 이격 종목을 하방 조정.
        # Σ weights = 0.18+0.12+0.20+0.13+0.12+0.15+0.10 = 1.00
        new_scores: dict[str, Decimal] = {}
        for tk, (_sv, bd) in scored.items():
            rows_tk = rows_by_ticker.get(tk, [])
            pullback = sc.compute_pullback_3pos(rows_tk, settings) if rows_tk else Decimal("0")
            guard = sc.compute_extension_guard(rows_tk, settings) if rows_tk else Decimal("1")
            entry = (
                bd.near_52w * settings.weight_52w_entry  # 0.18 (was 0.30)
                + pullback * settings.weight_pullback  # 0.12 (freed from 52w)
                + bd.pocket_pivot * settings.weight_pocket_pivot  # 0.20
                + bd.momentum_norm * settings.weight_momentum  # 0.13
                + bd.rs_norm * settings.weight_rs  # 0.12
                + bd.turnover_norm * settings.weight_turnover  # 0.15
                + bd.vol_fit * settings.weight_vol_fit  # 0.10
            )  # Σ = 1.00
            entry_score = max(Decimal("0"), min(Decimal("1"), entry)) * guard
            new_scores[tk] = entry_score
        base = new_scores
    elif preset == "alpha_composite":
        if not alpha_factors:
            raise ValueError(
                "alpha_composite preset requires alpha_factors (winner 팩터 주입 필요)"
            )
        tickers = list(base.keys())  # eligible set (baseline 과 동일 유니버스 → 공정 비교)
        # 팩터별 횡단면 rank 의 z-score → 팩터 전반 등가중 평균.
        z_by_ticker: dict[str, list[Decimal]] = {tk: [] for tk in tickers}
        for _name, fn in alpha_factors.items():
            vals = {tk: fn(panel, tk, t) for tk in tickers}
            present = {tk: v for tk, v in vals.items() if v is not None}
            if len(present) < 2:
                continue  # <2 점은 rank/표준화 불가
            present_tks = list(present.keys())
            # _rank 는 오름차순(작은 값→낮은 rank) → 큰 팩터값이 큰 rank·큰 z (방향 보존).
            ranks = _rank([present[tk] for tk in present_tks])
            n = Decimal(len(ranks))
            mean = sum(ranks, Decimal("0")) / n
            var = sum(((r - mean) ** 2 for r in ranks), Decimal("0")) / n
            # std: float sqrt → 즉시 Decimal 복귀(compute_annualized_volatility 패턴 — 유일 float).
            std = Decimal(str(math.sqrt(float(var))))
            for tk, rank in zip(present_tks, ranks, strict=True):
                z = (rank - mean) / std if std != 0 else Decimal("0")
                z_by_ticker[tk].append(z)
        base = {
            tk: (sum(zs, Decimal("0")) / Decimal(len(zs)) if zs else Decimal("0"))
            for tk, zs in z_by_ticker.items()
        }
    return sorted(base.items(), key=lambda x: x[1], reverse=True)


def _index_price_on_or_after(panel: Panel, d: date) -> Decimal | None:
    for r in panel.index_rows:
        if r.date >= d:
            return r.close
    return None


def _fwd_return(panel: Panel, ticker: str, t: date, horizon: int) -> Decimal | None:
    """entry=T+1 첫 봉, horizon 봉 뒤 종가 수익률. horizon 봉 미확보 시 None(무음절단 금지)."""
    future = [r for r in panel.series[ticker].rows if r.date > t]
    if len(future) <= horizon:
        return None
    entry = future[0].close
    if entry <= 0:
        return None
    return (future[horizon].close - entry) / entry


def _mae(panel: Panel, ticker: str, t: date, horizon: int) -> Decimal | None:
    """fwd-return 과 동일 구간(entry 후 1..horizon 봉)의 저가 기준 최대역행.

    horizon 미확보 시 None."""
    future = [r for r in panel.series[ticker].rows if r.date > t]
    if len(future) <= horizon:
        return None
    entry = future[0].close
    return metrics.max_adverse_excursion(entry, [r.low for r in future[1 : horizon + 1]])


# stat_fn 헬퍼 — Decimal 리스트 → 평균 MAE
def _mean_stat(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values, Decimal("0")) / Decimal(len(values))


def build_event_study(
    panel: Panel,
    dates: list[date],
    settings: Settings,
    cfg: BacktestConfig,
    *,
    rankings: dict[date, list[tuple[str, Decimal]]] | None = None,
) -> dict[int, EventStudyBucket]:
    """순수 헬퍼 — 주어진 리밸런스 dates 에 대한 호라이즌별 이벤트스터디 버킷.

    run_backtest 의 포인트 추정치(풀링 Spearman·평균 MAE·승률·N) + Layer-B 유의성
    (날짜-블록 부트스트랩 CI·퍼뮤테이션 p)을 동일 연산으로 계산한다. dates 부분집합을
    넘기면 그 구간만의 OOS/홀드아웃/폴드 이벤트스터디가 된다(룩어헤드 0 — `_score_at`
    과 `_fwd_return` 둘 다 ≤T 슬라이스·T 이후 가격만 사용).

    포트폴리오 시뮬과 독립이므로 run_backtest 의 NAV 부분과 분리해도 수치 불변.

    rankings: 사전 계산된 날짜별 순위({t: _score_at(...)}) 를 제공하면 재채점 생략.
              None(기본)이면 날짜마다 _score_at 을 직접 호출한다.
              run_backtest 가 루프에서 수집한 rankings 를 넘겨 이중 채점을 제거한다.
    """
    # 풀링된 포인트 추정치용
    es_scores: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
    es_fwd: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
    es_mae: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
    # Layer B: 날짜-블록 그룹 수집
    # es_groups[h] = list over dates of (scores_at_date, fwds_at_date)
    es_groups: dict[int, list[tuple[list[Decimal], list[Decimal]]]] = {
        h: [] for h in cfg.forward_horizons
    }
    # mae_groups[h] = list over dates of [maes_at_date]
    mae_groups: dict[int, list[list[Decimal]]] = {h: [] for h in cfg.forward_horizons}

    for t in dates:
        ranked = (
            rankings[t]
            if (rankings is not None and t in rankings)
            else _score_at(panel, t, settings, cfg.preset)
        )
        # 날짜별 임시 버킷
        date_scores: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
        date_fwds: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
        date_maes: dict[int, list[Decimal]] = {h: [] for h in cfg.forward_horizons}
        for _tk, sv in ranked:
            for h in cfg.forward_horizons:
                fr = _fwd_return(panel, _tk, t, h)
                mae = _mae(panel, _tk, t, h)
                if fr is not None and mae is not None:
                    es_scores[h].append(sv)
                    es_fwd[h].append(fr)
                    es_mae[h].append(mae)
                    date_scores[h].append(sv)
                    date_fwds[h].append(fr)
                    date_maes[h].append(mae)
        # 날짜 그룹 추가 (비어있으면 추가 안 함)
        for h in cfg.forward_horizons:
            if date_scores[h]:
                es_groups[h].append((date_scores[h], date_fwds[h]))
                mae_groups[h].append(date_maes[h])

    event_study: dict[int, EventStudyBucket] = {}
    for h in cfg.forward_horizons:
        # 포인트 추정치 — 기존과 동일한 풀링 연산, 수치 불변
        mono = metrics.spearman_monotonicity(es_scores[h], es_fwd[h])
        mae_mean = (
            sum(es_mae[h], Decimal("0")) / Decimal(len(es_mae[h])) if es_mae[h] else Decimal("0")
        )
        wr = metrics.win_rate(es_fwd[h])
        n = len(es_fwd[h])

        # Layer B: 유의성 계산
        # bootstrap 그룹: (score, fwd) 튜플 리스트 per date
        boot_groups = [list(zip(sc_, fw_, strict=True)) for sc_, fw_ in es_groups[h]]
        if boot_groups:
            mono_ci_lo, mono_ci_hi = metrics.block_bootstrap_ci(
                boot_groups,
                _spearman_stat,
                n_resamples=cfg.n_resamples,
                seed=cfg.bootstrap_seed,
            )
            mono_pvalue = metrics.permutation_pvalue(
                es_groups[h],
                observed=mono,
                n_perms=cfg.n_perms,
                seed=cfg.bootstrap_seed,
            )
            mae_ci_lo, mae_ci_hi = metrics.block_bootstrap_ci(
                mae_groups[h],
                _mean_stat,
                n_resamples=cfg.n_resamples,
                seed=cfg.bootstrap_seed,
            )
        else:
            mono_ci_lo = mono_ci_hi = Decimal("0")
            mono_pvalue = Decimal("1")
            mae_ci_lo = mae_ci_hi = Decimal("0")

        event_study[h] = EventStudyBucket(
            monotonicity=mono,
            mae=mae_mean,
            win_rate=wr,
            n=n,
            mono_ci_lo=mono_ci_lo,
            mono_ci_hi=mono_ci_hi,
            mono_pvalue=mono_pvalue,
            mae_ci_lo=mae_ci_lo,
            mae_ci_hi=mae_ci_hi,
        )
    return event_study


def run_backtest(panel: Panel, cfg: BacktestConfig) -> BacktestResult:
    settings = get_settings()
    dates = _rebalance_dates(panel, cfg)
    nav = [Decimal("1")]
    benchmark_nav: list[Decimal] = [Decimal("1")]
    held: set[str] = set()
    turnover_count = 0

    fs_vals: dict[str, dict[int, list[Decimal]]] = {
        f: {h: [] for h in cfg.forward_horizons} for f in _STUDY_FACTORS
    }
    fs_fwd: dict[str, dict[int, list[Decimal]]] = {
        f: {h: [] for h in cfg.forward_horizons} for f in _STUDY_FACTORS
    }
    # 날짜별 사전 계산된 순위 수집 → build_event_study 에 전달(이중 채점 제거).
    precomputed_rankings: dict[date, list[tuple[str, Decimal]]] = {}

    for i, t in enumerate(dates):
        ranked = _score_at(panel, t, settings, cfg.preset)
        precomputed_rankings[t] = ranked
        picks = [tk for tk, _ in ranked[: cfg.top_n]]
        for tk, _sv in ranked:
            fund = panel.fundamentals_asof(tk, t)
            val = panel.valuation_asof(tk, t)
            factor_vals: dict[str, Decimal | None] = {
                "roe": fund.roe if fund else None,
                "op_margin": fund.op_margin if fund else None,
                "rev_growth": fund.rev_growth if fund else None,
                "per": val.per if val else None,
                "pbr": val.pbr if val else None,
            }
            for h in cfg.forward_horizons:
                fr = _fwd_return(panel, tk, t, h)
                if fr is None:
                    continue
                for fname, fval in factor_vals.items():
                    if fval is not None:
                        fs_vals[fname][h].append(fval)
                        fs_fwd[fname][h].append(fr)
        if i + 1 < len(dates) and picks:
            nxt = dates[i + 1]
            rets: list[Decimal] = []
            for tk in picks:
                a = panel.price_on_or_after(tk, t + timedelta(days=1))
                b = panel.price_on_or_after(tk, nxt + timedelta(days=1))
                if a and b and a > 0:
                    rets.append((b - a) / a)
            if rets:
                gross = sum(rets, Decimal("0")) / Decimal(len(rets))
                churn = len(set(picks) ^ held)
                cost = (cfg.cost_bps / Decimal("10000")) * (
                    Decimal(churn) / Decimal(max(len(picks), 1))
                )
                nav.append(nav[-1] * (Decimal("1") + gross - cost))
                turnover_count += churn
                held = set(picks)
                ia = _index_price_on_or_after(panel, t + timedelta(days=1))
                ib = _index_price_on_or_after(panel, nxt + timedelta(days=1))
                bench_ret = (ib / ia - Decimal("1")) if (ia and ib and ia > 0) else Decimal("0")
                benchmark_nav.append(benchmark_nav[-1] * (Decimal("1") + bench_ret))

    # 이벤트스터디 — 전체 dates 의 풀링/유의성(포트폴리오 시뮬과 독립, 수치 불변)
    # precomputed_rankings 를 재사용 → 날짜당 _score_at 1회만 호출(이중 채점 제거).
    event_study = build_event_study(panel, dates, settings, cfg, rankings=precomputed_rankings)
    factor_study = {
        fname: {
            h: EventStudyBucket(
                monotonicity=metrics.spearman_monotonicity(fs_vals[fname][h], fs_fwd[fname][h]),
                mae=Decimal("0"),
                win_rate=metrics.win_rate(fs_fwd[fname][h]),
                n=len(fs_fwd[fname][h]),
            )
            for h in cfg.forward_horizons
        }
        for fname in _STUDY_FACTORS
    }
    return BacktestResult(
        portfolio_nav=nav,
        benchmark_nav=benchmark_nav,
        rebalance_dates=dates,
        event_study=event_study,
        factor_study=factor_study,
        turnover_count=turnover_count,
    )


def _walk_forward_splits(
    dates: list[date], wf: WalkForwardConfig
) -> tuple[list[tuple[list[date], list[date]]], list[date]]:
    """앵커드 폴드 분할 + 최종 홀드아웃 분리.

    반환: (fold_ranges, holdout_dates)
      fold_ranges[i] = (train_dates, test_dates)
        - test_dates 는 비홀드아웃 구간을 n_folds 개로 나눈 i번째 연속 블록.
        - train_dates 는 그 블록 직전까지의 prefix(anchored=expanding; v1 미피팅).
        - 합집합(test) = 비홀드아웃 dates 전체(연속·비겹침·완전 커버).
      holdout_dates = 마지막 holdout_frac 비율의 dates(단 1회 소진용).
    """
    n = len(dates)
    holdout_n = int(Decimal(n) * wf.holdout_frac)  # Decimal 곱셈 → truncate (float 금지)
    pre = dates[: n - holdout_n]
    holdout = dates[n - holdout_n :]
    m = len(pre)
    # n_folds 는 __post_init__ 이 ≥1 을 보장 — clamp 불필요
    folds = min(wf.n_folds, max(1, m))  # P1 #3: m < n_folds 시 유효 폴드 수 클램프
    bounds = [m * j // folds for j in range(folds + 1)]  # 0=bounds[0] … m=bounds[-1]
    fold_ranges: list[tuple[list[date], list[date]]] = []
    for i in range(folds):
        lo, hi = bounds[i], bounds[i + 1]
        train = pre[:lo]  # anchored prefix
        test = pre[lo:hi]  # 다음 연속 블록
        fold_ranges.append((train, test))
    return fold_ranges, holdout


def run_walk_forward(panel: Panel, cfg: BacktestConfig, wf: WalkForwardConfig) -> WalkForwardResult:
    """앵커드(확장형) 워크포워드 OOS 평가 (Layer C).

    절차:
      1) 리밸런스 dates 산정(_rebalance_dates) — run_backtest 와 동일.
      2) 마지막 holdout_frac 을 최종 홀드아웃으로 예약.
      3) 나머지(pre-holdout)를 n_folds 개 앵커드 폴드로 분할:
         fold i = train dates[0:split_i] · TEST dates[split_i:split_{i+1}]
         (train 은 확장; test 는 다음 연속 블록). v1 은 피팅 없음 — "train" 은 본 prefix
         일 뿐이며, T5(파라미터 피팅)에서 이 train 슬라이스를 사용할 훅이다.
      4) 폴드별 TEST dates 로 build_event_study 호출 → per_fold.
      5) OOS = 모든 테스트 폴드 dates 합집합으로 build_event_study(가장 중요한 지표).
      6) holdout = 홀드아웃 dates 로 build_event_study(단 1회 소진).
      7) in_sample = 전체 dates 로 build_event_study(= run_backtest.event_study).

    룩어헤드 0: _score_at(≤T) + _fwd_return(T 이후 가격) 가 단일 가드 지점.

    알려진 한계: per_fold / oos / in_sample 은 각각 build_event_study 를 독립 호출하므로
    날짜가 겹치는 구간(예: oos dates ⊂ in_sample dates)에서 _score_at 이 중복 실행된다.
    run_backtest 는 precomputed_rankings 로 이중 채점을 제거하지만, 워크포워드 4-pass
    간 de-dup 은 T5(파라미터 피팅) 연계 후 리팩터 예정이다. 대규모 실전 탐색 시에는
    --n-resamples / --n-perms 를 낮춰 속도를 확보할 것.
    """
    settings = get_settings()
    dates = _rebalance_dates(panel, cfg)
    fold_ranges, holdout_dates = _walk_forward_splits(dates, wf)

    per_fold = [build_event_study(panel, test, settings, cfg) for _train, test in fold_ranges]
    oos_dates = [t for _train, test in fold_ranges for t in test]
    oos = build_event_study(panel, oos_dates, settings, cfg)
    holdout = build_event_study(panel, holdout_dates, settings, cfg)
    in_sample = build_event_study(panel, dates, settings, cfg)

    return WalkForwardResult(
        per_fold=per_fold,
        oos=oos,
        holdout=holdout,
        in_sample=in_sample,
        fold_ranges=fold_ranges,
        oos_dates=oos_dates,
        holdout_dates=holdout_dates,
        in_sample_dates=dates,
    )


__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "EventStudyBucket",
    "WalkForwardConfig",
    "WalkForwardResult",
    "_fwd_return",
    "_mae",
    "_rebalance_dates",
    "_score_at",
    "_walk_forward_splits",
    "build_event_study",
    "run_backtest",
    "run_walk_forward",
]


def main(argv: list[str] | None = None) -> int:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows cp949 콘솔 유니코드 인쇄 가드

    import argparse
    import json
    import os
    from datetime import datetime
    from pathlib import Path

    from backend.backtest.dart_client import DartClient
    from backend.backtest.loader import PanelLoader
    from backend.backtest.report import (
        render_json,
        render_markdown,
        render_walk_forward_json,
        render_walk_forward_markdown,
    )

    p = argparse.ArgumentParser(description="KR 백테스트 검증 하니스 (오프라인)")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--rebalance", default="weekly", choices=list(_REBAL_DAYS))
    p.add_argument("--top-n", type=int, default=20)
    p.add_argument("--cost-bps", default="41")
    p.add_argument(
        "--preset",
        default="baseline",
        choices=["baseline", "quality_tilt", "entry_bias", "alpha_composite"],
    )
    p.add_argument("--tickers", default="", help="콤마구분 6자리 코드. 비우면 유니버스 자동(느림)")
    p.add_argument("--out", default="data/backtest")
    # Layer C — OOS 앵커드 워크포워드
    p.add_argument(
        "--walk-forward",
        action="store_true",
        help="앵커드 워크포워드 실행(in-sample vs OOS vs 홀드아웃 리포트)",
    )
    p.add_argument("--n-folds", type=int, default=4, help="워크포워드 테스트 폴드 수")
    p.add_argument("--holdout-frac", default="0.2", help="최종 홀드아웃 비율(0~1)")
    # 부트스트랩/퍼뮤테이션 반복수 — 워크포워드 실전 실행에서 탐색 속도 조절(기본 1000).
    # 워크포워드는 현재 폴드/OOS/인샘플/홀드아웃 4pass×각 dates 를 재채점하므로
    # 대규모 실행 시 --n-resamples 200 등으로 낮추면 빠름(정밀도 감소 트레이드오프).
    p.add_argument(
        "--n-resamples",
        type=int,
        default=1000,
        help="블록 부트스트랩 반복수(기본 1000; 워크포워드 탐색 시 200 등으로 낮춤)",
    )
    p.add_argument(
        "--n-perms",
        type=int,
        default=1000,
        help="퍼뮤테이션 p-value 반복수(기본 1000)",
    )
    # T4 — 프리셋 비교 게이트 (paired_diff_ci 기반 Δ단조성·ΔMAE)
    p.add_argument(
        "--compare",
        default="",
        metavar="VARIANT",
        help=(
            "variant 프리셋 이름. 지정 시 variant vs --preset(baseline) 페어드 비교를 수행하고 "
            "report_compare_{variant}.{md,json} 을 출력(walk-forward OOS dates 기준)."
        ),
    )
    args = p.parse_args(argv)

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    dart_key = os.environ.get("DART_API_KEY")
    loader = PanelLoader(
        dart=DartClient(dart_key) if dart_key else None, cache_dir=Path(args.out) / "cache"
    )
    tickers = [t.strip().zfill(6) for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        from pykrx import stock

        tickers = [
            str(t).zfill(6)
            for t in stock.get_market_ticker_list(args.end.replace("-", ""), market="KOSPI")
        ]
    panel = loader.build(tickers, start, end)
    cfg = BacktestConfig(
        start=start,
        end=end,
        rebalance=args.rebalance,
        top_n=args.top_n,
        cost_bps=Decimal(args.cost_bps),
        preset=args.preset,
        n_resamples=args.n_resamples,
        n_perms=args.n_perms,
    )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.compare:
        from backend.backtest.compare import (
            compare_presets,
            render_comparison_json,
            render_comparison_markdown,
        )

        wf = WalkForwardConfig(n_folds=args.n_folds, holdout_frac=Decimal(args.holdout_frac))
        variant = args.compare
        cmp_result = compare_presets(panel, cfg, wf, variant_preset=variant)
        cmp_md = render_comparison_markdown(cmp_result, cfg)
        cmp_json = render_comparison_json(cmp_result, cfg)
        (out_dir / f"report_compare_{variant}.md").write_text(cmp_md, encoding="utf-8")
        (out_dir / f"report_compare_{variant}.json").write_text(
            json.dumps(cmp_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(cmp_md)
        return 0
    if args.walk_forward:
        wf = WalkForwardConfig(n_folds=args.n_folds, holdout_frac=Decimal(args.holdout_frac))
        wf_result = run_walk_forward(panel, cfg, wf)
        wf_md = render_walk_forward_markdown(wf_result, cfg, wf)
        (out_dir / f"report_walkforward_{args.preset}.md").write_text(wf_md, encoding="utf-8")
        (out_dir / f"report_walkforward_{args.preset}.json").write_text(
            json.dumps(render_walk_forward_json(wf_result, cfg, wf), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(wf_md)
        return 0
    result = run_backtest(panel, cfg)
    (out_dir / f"report_{args.preset}.md").write_text(
        render_markdown(result, cfg), encoding="utf-8"
    )
    (out_dir / f"report_{args.preset}.json").write_text(
        json.dumps(render_json(result, cfg), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(render_markdown(result, cfg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
