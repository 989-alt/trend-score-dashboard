# 선택 알파 발견 엔진 구현 계획 (Alpha Discovery via Factor Horse-Race)

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development(권장) 또는 executing-plans 로 태스크 단위 실행. 스텝은 `- [ ]` 체크박스.
> 설계 출처: `docs/superpowers/specs/2026-06-17-alpha-discovery-design.md`. 검증 인프라(Layer A/B/C·compare 게이트)는 구축 완료(`2026-06-17-validation-method.md`).

**Goal:** trend-score 점수가 OOS에서 forward-return을 **유의하게 예측(선택 알파)** 하는지 — 넓은 KR 유니버스에서 후보 팩터를 개별 검정→BH-FDR 보정→승자만 합성→OOS 검증으로 — 자기기만 없이 가려낸다. 안 되면 위험조정 폴백.

**Architecture:** 기존 백테스트 하니스(`build_event_study`·`run_walk_forward`·`metrics`·`compare`·`report`) 위에 ① 넓은 유니버스 취득 ② 팩터 라이브러리 ③ 호스레이스(FDR+홀드아웃) ④ rank-z 합성 프리셋+검증을 얹는다. **라이브 스코어러(`scoring.score_candidates`·`engine`·`schemas`) 무수정** — 알파 로직은 백테스트 프리셋으로 게이트.

**Tech Stack:** Python 3.13, Decimal 전면(float 금지), uv(`C:\Users\4F 전담실\.local\bin\uv.exe` — KRX 머신에선 PATH의 `uv`), pytest/ruff/mypy, yfinance·DART(·KRX 머신은 pykrx 가능). 브랜치 `feat/validation-oos-entry-bias`.

---

## ⚠ 크로스-머신 메모 (반드시 먼저 읽기)

이 계획은 **KRX가 차단된 4F PC**에서 작성됐다(그래서 데이터=yfinance+DART). **KRX가 되는 머신**에서 이어 작업하면 선택지가 생긴다:

| 항목 | 4F PC(KRX 차단) | KRX 머신(권장 활용) |
|---|---|---|
| 유니버스 목록 | DART `corpCode.xml`→6자리 코드→yfinance 거래대금 랭킹 | `pykrx.get_market_ticker_list`(KOSPI+KOSDAQ) 직접 — 더 단순 |
| KR OHLCV | yfinance `.KS/.KQ`(T7, 커밋 `cab6271`) | pykrx `get_market_ohlcv_by_date`(거래대금 컬럼 정확) **또는** yfinance 유지 |
| **밸류(PER/PBR)** | **불가(N=0)** | **pykrx `get_market_fundamental_by_date` 작동 → 가치 렌즈 부활!** |

→ **KRX 머신에서는 Phase 2 팩터 풀에 `per`·`pbr`(가치)와 정확한 거래대금을 포함**시킬 것. Task 0(아래)에서 데이터 경로를 선택한다. 어느 경로든 **로더 인터페이스(`PanelLoader.build`→`Panel`)는 동일**하므로 상위 파이프라인(Phase 2~4)은 데이터 소스와 무관하게 동작한다.

---

## Phase 0 — 환경·데이터 경로 확정

### Task 0: 환경 셋업 + 데이터 경로 선택
**Files:** (없음 — 셋업·결정)
- [ ] `uv sync` 후 `uv run pytest -q` → **현재 321 passed, 1 skipped** 확인(이어받기 정상).
- [ ] `.env`에 `DART_API_KEY` 확인. KRX 머신이면 `KRX_ID`/`KRX_PW`도(이미 .env에 있음).
- [ ] **데이터 경로 결정**: (A) 포터블=yfinance+DART 유지(밸류 없음), (B) KRX 머신=pykrx OHLCV+밸류 복원. 본 계획은 (A) 기준 코드를 제시하고 (B) 차이를 각 Task에 명시.
- [ ] 커밋 불필요(셋업).

---

## Phase 1 — 넓은 유니버스 + 데이터 로더

### Task 1: DART 전 상장코드 노출
**Files:** Modify `backend/backtest/dart_client.py`; Test `tests/test_backtest_dart.py`

- [ ] **Step 1: 실패 테스트**
```python
def test_all_listed_codes_returns_six_digit_codes(monkeypatch):
    from backend.backtest.dart_client import DartClient
    c = DartClient("k")
    monkeypatch.setattr(c, "_load_corp_map", lambda: {"005930": "00126380", "000660": "00164779"})
    c._corp_map = None
    codes = c.all_listed_codes()
    assert "005930" in codes and all(len(x) == 6 for x in codes)
```
- [ ] **Step 2: 실패 확인** `uv run pytest tests/test_backtest_dart.py::test_all_listed_codes_returns_six_digit_codes -v` → FAIL(no attr).
- [ ] **Step 3: 구현** (dart_client.py `corp_code` 아래 추가)
```python
def all_listed_codes(self) -> list[str]:
    """corpCode.xml 의 6자리 종목코드 전체(상장사 근사). 캐시 재사용."""
    if self._corp_map is None:
        self._corp_map = self._load_corp_map()
    return sorted(self._corp_map.keys())
```
- [ ] **Step 4: 통과 확인** 같은 명령 → PASS.
- [ ] **Step 5: 커밋** `git commit -am "feat(backtest): DART 전 상장코드 노출 all_listed_codes"`
- **(B/KRX 머신)**: 대안으로 `pykrx.stock.get_market_ticker_list(date, market=...)` 사용 가능 — 이 경우 Task 2에서 분기.

### Task 2: 유니버스 빌더 (거래대금 상위 N)
**Files:** Create `backend/backtest/universe.py`; Test `tests/test_backtest_universe.py`

- [ ] **Step 1: 실패 테스트** (결정론·네트워크 없음 — turnover 함수 주입)
```python
from datetime import date
from decimal import Decimal
from backend.backtest.universe import top_by_turnover

def test_top_by_turnover_ranks_and_caps():
    codes = ["A", "B", "C", "D"]
    turn = {"A": Decimal("10"), "B": Decimal("40"), "C": Decimal("30"), "D": Decimal("0")}
    out = top_by_turnover(codes, lambda c: turn[c], top_n=2)
    assert out == ["B", "C"]  # 거래대금 내림차순, 0/결측 제외, 상위 2
```
- [ ] **Step 2: 실패 확인** → FAIL(no module).
- [ ] **Step 3: 구현** `backend/backtest/universe.py`
```python
"""넓은 KR 유니버스 — 후보 코드 → 최근 거래대금 상위 N. 결정론(주입형)."""
from __future__ import annotations
from collections.abc import Callable
from decimal import Decimal

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
```
- [ ] **Step 4: 통과 확인** → PASS.
- [ ] **Step 5: 실데이터 어댑터(통합, 테스트 제외)** 같은 파일에 추가 — `turnover_of`를 yfinance 최근 60일 평균(종가×거래량)으로 구현하는 `build_kr_universe(dart, top_n, cache_dir)`. corpCode→`all_listed_codes`→배치 yfinance→`top_by_turnover`. (B/KRX: `turnover_of`를 pykrx 거래대금으로.) 디스크 캐시 `universe_{top_n}.json`.
- [ ] **Step 6: 커밋** `git commit -am "feat(backtest): 유니버스 빌더 top_by_turnover + 실데이터 어댑터"`

### Task 3: OHLCV 디스크 캐시 (수백 종목 1회 fetch)
**Files:** Modify `backend/backtest/loader.py`; Test `tests/test_backtest_loader.py`
- [ ] **Step 1: 실패 테스트** — 같은 (ticker,start,end) 2회 호출 시 네트워크 함수가 1회만 불리고 동일 프레임 반환(캐시 적중).
- [ ] **Step 2: 실패 확인.**
- [ ] **Step 3: 구현** `PanelLoader._ohlcv`를 디스크 캐시로 감싼다(키=ticker+start+end, parquet 없으면 json records; `cache_dir/ohlcv/`). 적중 시 재파싱, 미스 시 yfinance fetch 후 저장.
- [ ] **Step 4: 통과 확인.**
- [ ] **Step 5: 커밋** `git commit -am "feat(backtest): OHLCV 디스크 캐시 — 수백 종목 재실행 가속"`
- **주의:** Decimal 직렬화는 문자열 경유. fail-open 유지.

---

## Phase 2 — 팩터 라이브러리 (신규 순수함수, 라이브 무수정)

### Task 4: 팩터 함수 (worked example + 나머지 동일 패턴)
**Files:** Modify `backend/scoring.py`(신규 함수만); Test `tests/test_factors_alpha.py`

**워크드 예시 — `trend_template` (Minervini 0~8):**
- [ ] **Step 1: 실패 테스트**
```python
from decimal import Decimal
from backend.scoring import trend_template
from tests.fixtures.backtest_synth import make_series  # OHLCVRow 빌더

def test_trend_template_strong_uptrend_high_score():
    rows = make_series("X", __import__("datetime").date(2022,1,3), list(range(100, 460))).rows
    s = trend_template(rows)  # 강한 정배열 → 높은 점수
    assert s >= Decimal("6") / Decimal("8")
```
- [ ] **Step 2: 실패 확인.**
- [ ] **Step 3: 구현** (scoring.py에 신규 함수; 기존 `simple_moving_average` 재사용)
```python
def trend_template(rows: list[OHLCVRow]) -> Decimal:
    """Minervini 8조건 충족수/8 (0~1). 데이터 부족 조건은 미충족 처리."""
    if len(rows) < 200:
        return Decimal("0")
    p = rows[-1].close
    ma50 = simple_moving_average(rows, 50); ma150 = simple_moving_average(rows, 150)
    ma200 = simple_moving_average(rows, 200)
    ma200_prev = simple_moving_average(rows[:-21], 200) if len(rows) > 221 else None
    lo252 = min(r.low for r in rows[-252:]); hi252 = max(r.high for r in rows[-252:])
    conds = [
        ma150 is not None and ma200 is not None and p > ma150 and p > ma200,
        ma150 is not None and ma200 is not None and ma150 > ma200,
        ma200 is not None and ma200_prev is not None and ma200 > ma200_prev,
        ma50 is not None and ma150 is not None and ma200 is not None and ma50 > ma150 > ma200,
        ma50 is not None and p > ma50,
        p >= Decimal("1.30") * lo252,
        p >= Decimal("0.75") * hi252,
        True,  # RS≥70 은 횡단면 — 합성 단계에서 rs_rank 로 대체(여기선 8번째 자리표시)
    ]
    return Decimal(sum(1 for c in conds if c)) / Decimal("8")
```
- [ ] **Step 4: 통과 확인.**
- [ ] **Step 5: 나머지 팩터 — 동일 TDD로 추가**(각자 실패테스트→구현→통과→커밋). 시그니처·공식:
  - `ma_alignment(rows)->Decimal`: P>MA50>MA150>MA200 모두 참=1 else 부분점수(참 개수/4).
  - `mom_12_1(rows)->Decimal`: `(close[-21]/close[-252]-1)` (12개월 전→1개월 전), 데이터 부족 0.
  - `vcp_tightness(rows)->Decimal`: `(1-minmax(ATR20/price)) + (1-minmax(SMA(vol,5)/SMA(vol,50)))`/2 — minmax는 호출측 횡단면이므로 여기선 raw `ATR20/price`·`SMA5/SMA50` 튜플 반환 헬퍼로 두고 합성단계서 정규화. (단순화: 우선 `atr20_over_price(rows)`·`vol_dryup(rows)` 두 raw 함수로 분리.)
  - `volume_surge(rows)->Decimal`: `clamp((vol[-1]/SMA(vol,20)-1)/(3-1),0,1)`.
  - `gross_profitability` (펀더, Novy-Marx): DART 계정 `(매출액-매출원가)/자산총계` — `dart_client._ratios_from_accounts`에 `gp` 추가(매출원가=account "매출원가", 자산=「자산총계」). 가능시만.
  - 기존 재사용: `proximity_to_52w_high`(near_52w)·`pocket_pivot`·`compute_momentum`·`compute_annualized_volatility`.
- [ ] **Step 6: 커밋**(팩터별 또는 묶음) `git commit -am "feat(scoring): 알파 후보 팩터 — trend_template·ma_alignment·mom_12_1·vcp·volume_surge·gross_profitability"`
- **(B/KRX)**: 밸류 `per`·`pbr`는 `Panel.valuation_asof`로 이미 노출 — 팩터 풀에 추가(역수/순위는 합성단계). 4F PC에선 valuation N=0이라 자동 제외됨.

---

## Phase 3 — 호스레이스 (FDR + 홀드아웃) — 엄밀성 핵심

### Task 5: BH-FDR 다중검정 보정 (순수함수)
**Files:** Modify `backend/backtest/metrics.py`; Test `tests/test_backtest_metrics.py`
- [ ] **Step 1: 실패 테스트**
```python
from decimal import Decimal
from backend.backtest.metrics import bh_fdr_reject
def test_bh_fdr_rejects_only_small_pvalues():
    ps = [Decimal("0.001"), Decimal("0.2"), Decimal("0.04"), Decimal("0.8")]
    rej = bh_fdr_reject(ps, q=Decimal("0.10"))
    assert rej == [True, False, True, False]
```
- [ ] **Step 2: 실패 확인.**
- [ ] **Step 3: 구현**
```python
def bh_fdr_reject(pvalues: list[Decimal], *, q: Decimal) -> list[bool]:
    """Benjamini-Hochberg: FDR<=q 로 기각할 가설 마스크. 원래 순서로 반환."""
    m = len(pvalues)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    thresh_rank = -1
    for rank, i in enumerate(order, start=1):
        if pvalues[i] <= q * Decimal(rank) / Decimal(m):
            thresh_rank = rank
    reject = [False] * m
    for rank, i in enumerate(order, start=1):
        if rank <= thresh_rank:
            reject[i] = True
    return reject
```
- [ ] **Step 4: 통과 확인.** **Step 5: 커밋** `git commit -am "feat(backtest): BH-FDR 다중검정 보정 bh_fdr_reject"`

### Task 6: 호스레이스 엔진 + 리더보드 리포트
**Files:** Create `backend/backtest/horserace.py`; Test `tests/test_horserace.py`
- [ ] **Step 1: 실패 테스트(합성 그라운드트루스 — 발견엔진 정확성):** 패널에 *알려진 예측 팩터 1개 + 노이즈 팩터 다수*를 심고 `run_horserace` → 예측 팩터는 승자(FDR 통과·holdout 부호일치), 노이즈는 전부 기각. (Layer A의 팩터판.)
- [ ] **Step 2: 실패 확인.**
- [ ] **Step 3: 구현** `run_horserace(panel, cfg, wf, factors: dict[str, FactorFn]) -> Leaderboard`:
  - 각 팩터에 대해 OOS(test 폴드 합집합) (값, fwd) 그룹 수집 → `spearman_monotonicity`(점추정) + `block_bootstrap_ci` + `permutation_pvalue`(기존 모듈).
  - 전 팩터 p값 → `bh_fdr_reject(q=0.10)`.
  - 각 승자 후보를 홀드아웃에서 1회 평가 → 단조성 부호.
  - `winner = fdr_reject AND test_ci_lo>0 AND holdout 부호 일치`.
  - 반환: 팩터별 {mono, ci, p, fdr_reject, holdout_mono, winner}.
- [ ] **Step 4: 통과 확인.**
- [ ] **Step 5: 리포트** `report.render_horserace_markdown/json` — 리더보드 표(factor·mono·CI·p·FDR·holdout·승자). 테스트로 substring 확인.
- [ ] **Step 6: 커밋** `git commit -am "feat(backtest): 팩터 호스레이스 엔진 + FDR/홀드아웃 + 리더보드 (합성 그라운드트루스 테스트)"`

---

## Phase 4 — 합성 + 검증 + 실데이터 판정

### Task 7: `alpha_composite` 프리셋 (rank-z 등가중 승자 합성)
**Files:** Modify `backend/backtest/run.py`(`_score_at` 신규 분기 + CLI choices); Test `tests/test_alpha_composite.py`
- [ ] **Step 1: 실패 테스트** — 승자 팩터 2개를 주입했을 때 `_score_at(preset="alpha_composite")`가 각 종목의 두 팩터 횡단면 rank의 z평균으로 점수화하고 baseline과 다름.
- [ ] **Step 2~4: TDD** — `elif preset == "alpha_composite":` 분기. 승자 팩터 목록은 설정/인자로 주입(호스레이스 결과를 config에 기록). 각 팩터값을 후보군 횡단면 rank→z-score→평균. **라이브 무수정**(run.py 백테스트 레이어 한정).
- [ ] **Step 5: 커밋** `git commit -am "feat(backtest): alpha_composite 프리셋 — 승자 rank-z 등가중 합성"`

### Task 8: 검증 게이트 + 실데이터 실행
**Files:** Modify `backend/backtest/run.py`(CLI `--horserace`); 통합(테스트 제외) 실행.
- [ ] **Step 1:** CLI에 `--horserace`(유니버스 자동 top_n) 추가 — 호스레이스 실행→리더보드 저장→승자로 `alpha_composite` 구성.
- [ ] **Step 2: 실데이터 실행**(KRX 머신 권장): 상위 500·2015~2024·monthly·`--n-resamples 300`. 호스레이스 리더보드 확인.
- [ ] **Step 3: 검증** `compare_presets(variant="alpha_composite", baseline)` — OOS `paired_diff_ci`로 Δ단조성 유의?
- [ ] **Step 4: 판정** — 유의 개선이면 **알파 채택**(라이브 승격은 별도 결정). 아니면 **폴백 C**(별도 스펙: 레짐+ATR손절+사이징+extension_guard, 위험조정 평가).
- [ ] **Step 5:** 결과를 `docs/superpowers/specs/2026-06-17-...-RESULTS.md`로 기록·커밋.

---

## 자기검토 (작성자 체크)
- **스펙 커버리지:** 유니버스(§3)=Task1-3 · 팩터(§4)=Task4 · 호스레이스/FDR/홀드아웃(§5)=Task5-6 · 합성/검증(§6)=Task7-8 · 폴백(§7)=Task8 Step4(별도 스펙) · 테스트(§8)=Task6 합성 그라운드트루스. ✓
- **플레이스홀더:** 없음(각 Task에 코드/공식/명령). 단 Task4 "나머지 팩터"는 시그니처+공식 제시(엔지니어가 동일 TDD 반복) — 의도된 패턴.
- **타입 일관성:** 팩터 = `(rows|fundamentals)->Decimal`; 호스레이스 `factors: dict[str, FactorFn]`; FDR `list[Decimal]->list[bool]`; 합성은 횡단면 rank-z. 일관. ✓
- **범위:** 폴백 C는 본 계획 밖(트리거 시 별도 스펙). KRX 머신은 Phase2에 per/pbr 추가.
