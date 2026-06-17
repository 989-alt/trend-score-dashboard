# 폴백 C — 리스크 엔지니어링 설계 (Fallback C: Risk Engineering)

> 작성 2026-06-17 (세션4). 트리거: 알파 발견 호스레이스가 **선택 알파 0개**로 판정
> (`2026-06-17-alpha-discovery-RESULTS.md`) → 설계 §7·RESULTS §5의 폴백 C 발동.
> ⚠ 원칙(§3.5): 수익률 목표 튜닝 금지. 채택은 *위험조정 OOS 개선의 통계적 유의성*으로만.
> **폴백 C도 OOS 개선이 없을 수 있고, 그게 정상이며, 그땐 정직하게 그렇게 보고한다.**

---

## 1. 목표 & 근거

- **진짜 통증:** ① "추천 종목을 사면 매수 직후 떨어진다" ② "조정장에도 STRONG_BUY 발화".
- **신규 실증 근거(RESULTS §4b):** `near_52w`(라이브 점수 최대 가중 0.30)는 150종목 OOS에서
  **유일 FDR 기각 · 단조성 유의 음수**(mono −0.036, p=0.004), `mom`도 음수 → *신고가 근접·고모멘텀
  종목일수록 20일 후 수익이 낮다*. 통증 ①의 정량 확증.
- **레짐 게이트 부재 확인:** 라이브 `engine`·`schemas`·`market_data`에 분산일/risk_off 전무 → 통증 ②
  방어 수단 없음.
- **목표:** 수익 알파가 아니라 **"덜 잃기"** — 진입 품질(MAE↓)과 경로 리스크(MDD↓)를 OOS로 검증된
  만큼만 개선. **수익 보장 아님.**

## 2. 성공 기준 (사용자 확정)

- **1차 게이트 = MAE 축소** (매수 후 최대역행). `compare_presets`의 기존 paired **ΔMAE + CI** 규약
  (개선=양수, CI가 0 배제). 진입 품질 레이어의 *공식 채택 게이트*.
- **2차 게이트 = MDD 축소** (+ Calmar·Sharpe·레짐 구간 손실 보조). 포트폴리오 경로 레이어 판정.
- **위계:** MAE(진입 품질)는 *어떤·언제 사는가*가 좌우, MDD(경로)는 *손절·사이징·레짐*이 좌우 →
  1차=MAE, 2차=MDD 로 분리 검증. 둘 다 실패 가능.

## 3. 아키텍처 — 2 레이어 · 2 게이트

```
fallback_c =
 [레이어1 진입품질 재가중]  _score_at 새 분기  → top-N 픽 변화 → compare_presets ΔMAE (1차)
 [레이어2 리스크 오버레이]  포트폴리오 시뮬 확장 → NAV 경로 변화 → OOS MDD/Calmar/Sharpe (2차)
 [Ablation]                 각 컴포넌트 증분 기여 분리 측정(묶음이라도 개별 판정)
```
라이브 스코어러(`scoring.score_candidates`·`engine`·`schemas`) **무수정** — 폴백 C 로직은 전부
백테스트 레이어. 통과한 컴포넌트만 *별도 단계*에서 라이브 승격.

## 4. 레이어 1 — 진입품질 재가중 (점수 레벨)

`run.py` `_score_at` 의 새 `fallback_c` 분기(기존 `entry_bias` 분기의 강화판; 동일 패턴으로 `base` 후처리).
- **near_52w 비중 축소:** 현 0.30 → 후보 **소수만 측정**(예: 0.30·0.20·0.12), 자유분배는 pullback·기존
  가중으로(합=1.0 유지). *값은 OOS로 결정, 사전 고정/튜닝 금지.* near_52w 음수 단조성이 직접 근거.
- **extension_guard 승수:** `compute_extension_guard(rows, settings)`(기존) — 과열(이격 과대) 종목 하방
  조정. entry_bias 평가에서 5일 MAE 유의 축소가 이미 검증됨.
- **pullback_3pos 가산:** `compute_pullback_3pos(rows, settings)`(기존) — 눌림목 후 지지 재상승 보상.
- **검증:** `compare_presets(variant="fallback_c", baseline)` OOS paired ΔMAE + CI(1차 게이트). MAE는
  top-N 픽 기준이므로 재가중이 *덜 떨어지는 종목*을 상위로 올리면 ΔMAE 가 움직인다.

## 5. 레이어 2 — 리스크 오버레이 (포트폴리오 시뮬)

신규 `backtest/portfolio.py` + `run_backtest` 시뮬 확장. 진입 MAE 가 아니라 NAV 경로(MDD)에 작용.
- **레짐 게이트:** risk_off 일엔 신규 진입 보류(현금 유지). 조정장 미진입 → MDD↓. (정의 §6)
- **ATR 손절:** 진입가 − 2×ATR20 를 손절선으로, 리밸런스 구간 [T, T+1] 내 **일중 저가**가 손절을
  터치하면 손절가로 조기 청산(다음 리밸런스까지 현금). → 큰 낙폭 절단 → MDD↓.
- **포지션 사이징:** 동일가중 대신 `weight_pct = R% / (2·ATR20/price)`, 상한(예 10%). 변동성 큰 종목
  비중↓ → NAV 경로 평활화. 합이 1 초과면 정규화.
- **검증:** baseline vs fallback_c 포트폴리오 시뮬을 **워크포워드 연속 구간**에서 돌려 MDD·Calmar·Sharpe
  + 레짐 구간 손실 비교. ※ NAV 경로는 연속 날짜 필요 → 이벤트스터디 OOS(=test 폴드 *합집합*, 비연속)와
  달리 **fold별 연속 test 구간 + 최종 홀드아웃**을 각각 path 로 산정(§7).

## 6. 레짐 게이트 정의 (결정론)

- **분산일(distribution day):** 지수(^KS11) 당일 종가 ≤ 전일 종가 × 0.998 **AND** 당일 거래량 > 전일 거래량.
- **risk_off 판정:** 직전 **25 거래일** 중 분산일 **≥ 5회** → 해당일 risk_off.
- 백테스트 산정: `panel.index_rows_asof(t)` 로 ≤T 지수 일봉만 사용(룩어헤드 0). 임계(0.998·25·5)는
  `config.py` 파라미터, 후보 소수만 측정.

## 7. 검증 · Ablation

- **1차(레이어1) — 엄밀:** `compare_presets` OOS paired ΔMAE+CI. 곡선맞춤 방지 = near_52w 후보 소수 +
  홀드아웃 1회 확정 + 변종 다수 시 BH-FDR(`bh_fdr_reject`) 재사용.
- **2차(레이어2) — 경로지표:** 포트폴리오 MDD/Calmar/Sharpe. **연속 구간 필수** → fold별 연속 test 구간
  + 홀드아웃에서 각각 NAV 산정(이벤트스터디의 test-폴드 합집합과 달리 path 는 연속이어야 함). 단일 경로·
  짧은 구간이라 부트스트랩이 까다로움 → **각 구간의 리밸런스-구간 수익 시퀀스를 블록 부트스트랩**
  (`block_bootstrap_ci` 재사용)해 Sharpe/MDD CI 산출. *솔직한 한계: 짧은 연속구간·단일경로라 경로지표
  유의성은 MAE 게이트보다 약함(§11).*
- **Ablation(귀속):**
  - 레이어1: baseline → +extension → +near_52w↓ → +pullback, 각 단계 ΔMAE.
  - 레이어2: sim-baseline → +regime → +ATR손절 → +sizing, 각 단계 ΔMDD.
  - **효과 없거나 악화하는 컴포넌트는 채택하지 않는다**(통합 프리셋이라도 개별 판정).

## 8. 채택 규칙 & 라이브 승격 경계

- **레이어1 채택:** OOS ΔMAE 유의 양수(CI 0 배제) → 채택. ablation 으로 기여 컴포넌트만 남김.
- **레이어2 채택:** OOS MDD 유의(또는 명확) 축소 **그리고** MAE 무회귀 → 채택.
- **둘 다 실패:** 정직하게 "폴백 C도 OOS 개선 없음" 보고 → RESULTS 문서화(추가 폴백/중단은 사용자 결정).
- **라이브 승격(통과 시에만, 별도 단계):** 검증된 컴포넌트를 라이브 점수/엔진에 반영 + 라이브 회귀
  통과 필수(`engine`·`scoring`·`schemas` 변경 시). 프런트에 레짐 배너·과열 배지 노출.

## 9. 테스트 (엔진 정확성)

- **합성 그라운드트루스:** (a) risk_off 구간에 *수익 악화*를 심은 패널 → 레짐 게이트가 그 구간 진입을
  보류해 MDD 를 줄임을 증명. (b) *과열 종목에 MAE 악화*를 심은 패널 → 재가중이 그 종목을 top-N 에서
  밀어내 ΔMAE 를 줄임을 증명. (Layer A 의 리스크판.)
- **단위:** 분산일 카운트(심은 패턴 정확 검출)·ATR 손절 청산가(일중 저가 터치 시 손절가 청산)·사이징
  공식(R%/(2·ATR/price)·상한·정규화)·재가중 합=1.0.
- **결정론**(시드 고정)·**Decimal 전면**(경로지표 sqrt 등 float 접점은 즉시 Decimal 복귀)·**라이브 무수정
  회귀**(score_candidates/engine/schemas 불변). DoD: `pytest`·`ruff check`·`ruff format`·`mypy backend/`.

## 10. 파일 · 인터페이스

| 파일 | 변경 |
|---|---|
| `backtest/regime.py` (신규) | `distribution_days`·`is_risk_off(index_rows≤T, cfg)` 결정론 |
| `backtest/portfolio.py` (신규, **최대 작업**) | 리스크 오버레이 시뮬(레짐 보류·ATR 손절·사이징) + MDD/Calmar/Sharpe + 블록부트스트랩 CI + OOS 구간 실행 |
| `backtest/run.py` | `fallback_c` `_score_at` 분기(재가중) + `--fallback-c` CLI(검증+ablation 오케스트레이션) |
| `scoring.py` | ATR20·`atr_stop_price`·`suggested_weight` 순수 헬퍼(없으면 추가; **신규 함수만**, 라이브 무수정) |
| `config.py` | fallback_c 파라미터(near_52w 후보가중·ATR배수·R%·상한·분산일 0.998/25/5) |
| `report.py`/`compare.py` | ablation 리더보드 + 포트폴리오 위험조정 리포트(md/json) |
| 테스트 | `test_regime.py`·`test_portfolio_risk.py`·`test_fallback_c.py` |

## 11. 리스크 / 정직성

- **알파 없음 확정 위에서 출발** — 폴백 C는 *덜 잃기* 시도지 수익 보장 아님. OOS 통과도 미래 실패 가능.
- **경로지표 약한 유의성** — MDD/Sharpe 는 단일 경로. 블록부트스트랩으로 완화하나 MAE 게이트보다 신뢰↓.
- **생존편향·현-상장 근사·단일 호라이즌·DART 커버리지** 한계는 RESULTS §6 동일하게 승계·명시.
- **레짐 게이트는 미진입(기회손실) 동반** — 상승장에선 baseline 대비 수익 낮을 수 있음(MDD↔수익 트레이드오프).
  그래서 위험조정(Calmar/Sharpe)으로 평가.

## 12. 구현 단계 (writing-plans 가 상세화)

1. **레짐 모듈**(`regime.py`) — 분산일·risk_off 순수함수 + 단위테스트.
2. **레이어1 `fallback_c` 분기**(재가중) + `compare_presets` ΔMAE 검증 + 레이어1 ablation.
3. **리스크 오버레이 시뮬**(`portfolio.py`) — 레짐 보류·ATR 손절·사이징 + MDD/Calmar/Sharpe + 블록부트스트랩.
4. **`--fallback-c` CLI 오케스트레이션** + 레이어2 ablation + 합성 그라운드트루스 테스트.
5. **실데이터 실행·판정** → RESULTS 추가(레이어1/2 채택 여부·기여 컴포넌트·정직성).
> 각 단계 OOS 게이트. 개선 없으면 채택 안 함(롤백). 라이브 승격은 별도 결정.
