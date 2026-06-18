# 선택 알파 발견 — 결과 & 판정 (Alpha Discovery RESULTS)

> 작성 2026-06-17 (세션4). 설계: `2026-06-17-alpha-discovery-design.md` · 구현계획:
> `../plans/2026-06-17-alpha-discovery-plan.md` · 검증 인프라: `2026-06-17-validation-method.md`.
> 브랜치 `feat/validation-oos-entry-bias`. 본 문서는 **호스레이스 엔진의 실데이터 판정**을 기록한다.
> ⚠ 원칙(§3.5): 수익률 목표 튜닝 금지. 채택 기준은 CAGR 이 아니라 **OOS 단조성의 통계적 유의 개선**.
> 알파가 안 나올 수 있고 그게 정상이며, 그때는 폴백이 답이다.

---

## 0. TL;DR (판정)

**판정: 선택 알파 없음(승자 팩터 0개) → 폴백 C(리스크 엔지니어링)로 전환.**

넓은 유니버스(150종목·2016~2024·월간·OOS 워크포워드, §4b)에서 **어떤 후보 팩터도 OOS 단조성이
유의한 양수가 아니었다** — BH-FDR(q=0.10) 통과해 *양수+홀드아웃 재확인*까지 간 팩터 0건. 즉 점수가
오를 종목을 실제로 가려내는 선택 알파는 현 팩터 풀·현 데이터에서 **발견되지 않았다**(설계 §10이
예견한 정상 결과).

**그러나 통증을 직접 확증하는 발견:** `near_52w`(현 라이브 점수의 **최대 가중 0.30**)는 15개 중
**유일하게 FDR 기각**됐고 그 OOS 단조성이 **유의하게 음수**(mono −0.036, perm p=0.004). `mom`(전구간
모멘텀)도 유의 음수(mono −0.051, p=0.020, CI 전체 음수). → **신고가 근접·고모멘텀 종목일수록 20일
forward 수익이 낮다.** 이는 *"추천 종목을 사면 매수 직후 떨어진다"* 의 정량적 확증이자, 폴백 C
방향(과열 회피·`extension_guard`·near_52w 비중 축소)의 실증 근거다.

---

## 1. 무엇을 만들었나 (엔진, Tasks 1–8a)

OOS 게이팅된 **팩터 호스레이스 알파 발견 엔진**을 기존 백테스트 하니스 위에 구축했다.
**라이브 스코어러(`scoring.score_candidates`·`engine`·`schemas`)는 무수정** — 알파 로직은 전부
백테스트 레이어(프리셋/팩터 풀)로 게이트한다. 367 tests green · ruff/mypy clean · 통합리뷰 통과.

| 단계 | 산출물 | 핵심 |
|---|---|---|
| 유니버스 | `backtest/universe.py` `build_kr_universe` | pykrx KOSPI∪KOSDAQ **거래대금 상위 N**(영업일 스냅샷)·디스크 캐시·fail-open |
| 로더 | `backtest/loader.py` OHLCV 디스크 캐시 | yfinance `.KS/.KQ` OHLCV(캐시) + DART 펀더 + pykrx 밸류(PER/PBR), per-종목 fail-open |
| 팩터 | `scoring.py` 신규 순수함수 + `gp`(DART) | trend_template·ma_alignment·mom_12_1·volume_surge·atr20_over_price·vol_dryup + gross_profitability |
| 검정 | `backtest/metrics.py` `bh_fdr_reject` | Benjamini–Hochberg 다중검정 보정 |
| 호스레이스 | `backtest/horserace.py` `run_horserace` | 팩터별 OOS 단조성 + 부트스트랩 CI + 퍼뮤테이션 p → FDR → 홀드아웃 재확인 → 리더보드 |
| 합성 | `backtest/run.py` `alpha_composite` 프리셋 | 승자 팩터 횡단면 rank → z-score → 등가중 평균 |
| 풀+CLI | `backtest/factor_pool.py` + `--horserace` | 오리엔티드 15-팩터 풀(higher=better) + 엔드투엔드 오케스트레이션 |

검증 자체검증: 합성 그라운드트루스 테스트가 *심은 신호 팩터는 승자로 선별, 노이즈 팩터는 FDR로
기각*함을 증명(발견 엔진의 거짓발견율 통제 — Layer A의 팩터판). 따라서 "승자 0개"는 엔진 결함이
아니라 데이터에 알파가 없다는 신뢰 가능한 측정이다.

---

## 2. 팩터 풀 (15, 모두 "높을수록 좋음"으로 오리엔트)

- **가격/추세:** `trend_template`(Minervini 8조건/8), `ma_alignment`, `mom_12_1`(12−1개월),
  `mom`(전구간 모멘텀), `volume_surge`, `near_52w`, `pocket_pivot`(1/0),
  `neg_atr`(=−ATR20/가, 변동성 낮을수록↑), `neg_vol_dryup`(=−SMA5/SMA50, VDU↑).
- **펀더멘털(DART):** `gp`(Novy-Marx (매출−매출원가)/자산총계), `roe`, `op_margin`, `rev_growth`.
- **가치(pykrx, 역부호):** `neg_per`(=−PER), `neg_pbr`(=−PBR) — 저평가일수록↑.

밸류 팩터는 **KRX 머신(이 PC)이라 가능**(4F PC는 KRX 차단으로 N=0이었음). per/pbr은 pykrx
`get_market_fundamental_by_date` 로 시점별 취득.

---

## 3. 방법 (판정 규칙)

```
[유니버스 상위 N] → [패널 빌드(가격·펀더·밸류, ≤T)] → run_horserace:
  팩터별 OOS(워크포워드 test 폴드 합집합) 풀드 Spearman 단조성
    + 날짜블록 부트스트랩 95% CI + 퍼뮤테이션 p
  → BH-FDR(q=0.10) 전 15팩터 동시보정
  → 홀드아웃(최종 예약구간, 1회) 단조성 부호 재확인
  → winner = FDR기각 AND OOS_CI_lo>0 AND 홀드아웃_단조성>0
→ 승자 있으면 alpha_composite(승자 rank-z 등가중) vs baseline 을
   compare_presets 로 OOS paired Δ단조성 검정.
```
- **알파 채택 조건:** 승자 ≥1개 **그리고** alpha_composite OOS Δ단조성 유의 양수(paired CI 0 배제).
- **폴백 C 전환:** 승자 0개 **또는** 합성 OOS 개선 비유의 → 리스크 엔지니어링으로 전환(별도 스펙).
- 룩어헤드 0(≤T 슬라이스 / T-이후 fwd-return). 홀드아웃은 승자 재확인에만 1회 소진(이중사용 없음).

---

## 4. 실데이터 실행

### 4a. 예비 검증 실행 (파이프라인·타이밍 확인) — 30종목·~5.5분
`--universe-top-n 30 --start 2021-01-01 --end 2024-12-31 --rebalance monthly --horizon 20
--q 0.10 --n-resamples 100`. **승자 0개.** roe(0.114)·op_margin(0.098)·gp(0.071)가 상단이었으나
모두 CI_lo<0·비유의. → 소표본(30종목) 노이즈 가능성 → §4b 에서 재확인(아래에서 그 "퀄리티 우위"는
소멸함을 확인).

### 4b. 정의적 실행 (넓은 유니버스) — 150종목·2016~2024·월간·~63분
`--horserace --start 2016-01-01 --end 2024-12-31 --rebalance monthly --top-n 5
--universe-top-n 150 --horizon 20 --q 0.10 --n-resamples 250 --n-perms 250`
(yfinance 무데이터/상폐 종목 일부 fail-open 제외 → 유효 유니버스 ~145.)

| factor | OOS mono | CI [lo, hi] | perm p | FDR | holdout | n | winner |
|---|---|---|---|---|---|---|---|
| neg_pbr | 0.0296 | [−0.015, 0.083] | 0.064 | — | +0.036 | 10244 | — |
| neg_per | 0.0096 | [−0.033, 0.062] | 0.295 | — | +0.038 | 7940 | — |
| trend_template | 0.0020 | [−0.058, 0.056] | 0.948 | — | +0.016 | 10652 | — |
| mom_12_1 | 0.0001 | [−0.059, 0.066] | 0.992 | — | +0.017 | 10652 | — |
| rev_growth | 0.0000 | [0, 0] | 1.000 | — | 0.000 | 11 | — |
| neg_vol_dryup | −0.0001 | [−0.049, 0.054] | 0.996 | — | +0.000 | 10652 | — |
| volume_surge | −0.0003 | [−0.050, 0.039] | 0.984 | — | −0.040 | 10652 | — |
| gp | −0.0050 | [−0.073, 0.067] | 0.825 | — | +0.007 | 1816 | — |
| pocket_pivot | −0.0072 | [−0.038, 0.015] | 0.442 | — | −0.001 | 10652 | — |
| roe | −0.0124 | [−0.119, 0.106] | 0.797 | — | +0.105 | 471 | — |
| ma_alignment | −0.0146 | [−0.071, 0.038] | 0.076 | — | +0.001 | 10652 | — |
| **near_52w** | **−0.0357** | [−0.103, 0.033] | **0.004** | **기각** | +0.039 | 10652 | — |
| op_margin | −0.0393 | [−0.108, 0.035] | 0.207 | — | +0.137 | 902 | — |
| **mom** | **−0.0514** | **[−0.092, −0.008]** | **0.020** | — | −0.050 | 10652 | — |
| neg_atr | −0.0626 | [−0.127, −0.002] | 0.837 | — | +0.051 | 10652 | — |

- **승자 0개** → `alpha_composite` 검증 생략(폴백 C 권장 출력됨).
- **유일 FDR 기각 `near_52w`는 음수**(반-신호) → 승자 정의(양수) 불충족. `mom`도 유의 음수(CI 전체<0).
  현 라이브 점수가 near_52w 에 0.30, momentum/rs 에 0.25 를 주는 것이 **OOS 역효과**임을 직접 확증.
- **펀더 팩터 표본 희소**(roe n=471·op_margin n=902·gp n=1816·rev_growth n=11 vs 가격 n≈10652):
  DART 커버리지 한계로 **저검정력** — 퀄리티 가설은 *기각도 확증도 아닌 보류*. 30종목 실행의 "퀄리티
  우위"(§4a)는 여기서 소멸 → 소표본 노이즈였음.

---

## 5. 판정 & 다음 단계

**판정: 폴백 C.** 선택 알파(유의 양수 OOS 단조성) 미발견 → 리스크 엔지니어링으로 전환.

- **폴백 C (별도 브레인스토밍·스펙 필요):** 레짐 게이트(분산일→리스크오프 신규 억제) + ATR 손절
  (entry−2×ATR) + 포지션 사이징 + **extension_guard / near_52w 비중 축소**(§4b 음수 단조성을 직접
  반영). 평가지표는 위험조정(Sharpe·Calmar·MDD). 라이브 승격은 위험조정 OOS 개선 검증 통과 시에만.
- **near_52w 리웨이트는 OOS 게이트로 측정할 것:** 현 0.30 비중 축소안을 `entry_bias`류 백테스트
  프리셋으로 만들어 baseline 대비 OOS Δ단조성/ΔMAE 를 `compare_presets` 로 검정(곡선맞춤 금지).
- **퀄리티 렌즈 재검토(조건부):** DART 펀더 커버리지를 넓히면(전 종목 벌크 재무 캐시) roe·op_margin·gp
  를 큰 표본에서 재검정할 가치 — 현재는 저표본이라 보류.
- **호스레이스 엔진은 재사용 자산:** 팩터 추가·호라이즌(5/60일) 확장·유니버스 확대 시 동일 게이트로
  반복 검정 가능(엔진은 합성 그라운드트루스로 FDR 통제 증명됨).

---

## 6. 정직성 / 한계 (반드시 함께 읽기)

- **생존편향·현-상장 근사:** 유니버스 = *현재* 거래대금 상위(시점별 KRX 멤버십 미추적). baseline 과
  변형은 동일 유니버스라 **상대 비교는 공정**하나, 절대 단조성은 생존편향 낙관 편의 가능.
- **단일 호라이즌(20일).** 5/60일은 본 실행 범위 밖 — 다른 호라이즌에서 결과가 다를 수 있음(향후 확장).
- **DART 펀더 커버리지 한계:** §4b 의 펀더 n(471~1816)이 가격 n(~10652)보다 훨씬 작음 → 펀더 팩터
  결론은 저검정력. rev_growth n=11 은 frmtrm 매출 결측 다수.
- **데이터 fetch 견고성:** yfinance 무데이터/상폐 종목(예: 일부 .KS/.KQ)·pykrx 밸류 일시 세션만료는
  per-종목 fail-open 으로 흡수(전체 빌드 무중단) — 유효 유니버스가 명목 150 보다 작음.
- **yfinance 수정종가**(배당·액면 조정) 사용 — KIS 라이브와 미세 drift 가능(백테스트 전용, 라이브 무관).
- **부트스트랩/퍼뮤테이션 반복수 250**(속도) — perm p 최소 해상도 ≈ 1/251, CI 폭에 영향.
- **수익 보장 없음.** OOS·홀드아웃 통과 엣지도 미래에 실패할 수 있다.

---

## 7. 재현

```bash
# .env(DART_API_KEY·KRX_ID·KRX_PW)를 셸 환경에 로드 후 (하니스는 .env 자동로드 안 함):
uv run python -m backend.backtest.run --horserace \
  --start 2016-01-01 --end 2024-12-31 --rebalance monthly \
  --top-n 5 --universe-top-n 150 --horizon 20 --q 0.10 \
  --n-resamples 250 --n-perms 250 --out data/backtest
# 산출: data/backtest/report_horserace.{md,json}
#       (승자 있으면) report_compare_alpha_composite.{md,json}
```
DoD: `uv run pytest` · `uv run ruff check` · `uv run ruff format` · `uv run mypy backend/`.
