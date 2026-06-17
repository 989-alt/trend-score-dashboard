# 폴백 C — 결과 & 판정 (Fallback C: Risk Engineering RESULTS)

> 작성 2026-06-17 (세션4). 설계: `2026-06-17-fallback-c-risk-engineering-design.md` · 계획:
> `../plans/2026-06-17-fallback-c-risk-engineering.md`. 브랜치 `feat/validation-oos-entry-bias`.
> ⚠ 원칙(§3.5): 수익률 목표 튜닝 금지. 채택은 *위험조정 OOS 개선의 통계적/실증적 유의성*으로만.
> 알파(선택 우위)는 0개로 닫혔고(`2026-06-17-alpha-discovery-RESULTS.md`), 폴백 C는 *덜 잃기*를 시도한다.

---

## 0. TL;DR (판정)

**판정: 두 게이트 모두 통과 → 폴백 C 채택(검증된 컴포넌트 한정). 라이브 승격은 별도 게이트.**

150종목·2016~2024·월간·OOS 워크포워드(~50분):
- **레이어1(진입품질·ΔMAE 1차 게이트) 통과:** `fallback_c`(extension_guard + pullback + near_52w 축소)가
  baseline 대비 **20일 매수후 MAE를 유의하게 축소**(ΔMAE>0 **그리고** CI_lo>0, 세 후보 모두). → *"사면 떨어진다"
  통증을 실제로 완화.*
- **레이어2(리스크 오버레이·MDD 2차 게이트) 통과:** 오버레이가 **MDD를 −63%→−28%로 반토막**, Sharpe 0.30→0.70.
  ablation: **레짐 게이트가 최대 기여**, ATR 손절 유의, 사이징은 미미.
- **채택 컴포넌트:** extension_guard + pullback(near_52w≈0.20) · 레짐 게이트 · ATR 손절. (사이징은 한계효용 미미 → 보류.)

이는 알파-발견(승자 0)과 대조적인 **실증적 양의 결과** — 단, *수익 보장이 아니라 위험 축소*이고 경로지표는
신뢰도가 약함(§4 한계).

---

## 1. 방법 (요약)

`fallback_c` = 2 레이어 · 2 게이트(설계 §3). **라이브 스코어러 무수정** — 전부 백테스트 프리셋/시뮬.
- **레이어1(점수 재가중):** `_score_at` 의 `fallback_c` 분기 — near_52w 가중치 `w`(후보), pullback `0.30−w`,
  × extension_guard. 검증 = `compare_presets(fallback_c vs baseline)` OOS paired **ΔMAE + CI**(개선=양수, CI 0 배제).
- **레이어2(리스크 오버레이):** `simulate_risk_overlay` — 고정 baseline 점수에 레짐 보류·ATR 손절·사이징을
  누적 토글. 워크포워드 **연속 구간**(fold test + holdout) NAV → `portfolio_metrics`(MDD/Sharpe/Calmar).
- 합성 그라운드트루스 테스트로 두 메커니즘(레짐→MDD↓·재가중→MAE↓)을 *심은 신호로* 증명(385 tests green).

명령(재현, §5):
`--fallback-c --start 2016-01-01 --end 2024-12-31 --rebalance monthly --top-n 5 --universe-top-n 150 --n-resamples 250 --n-perms 250`

---

## 2. 레이어1 — 진입품질 (ΔMAE 20일) · **통과**

near_52w 가중치 후보별 `fallback_c` vs baseline OOS paired Δ(20일 MAE). **개선 = 양수, 채택 = ΔMAE>0 AND CI_lo>0.**

| near_52w(w) | ΔMAE(20d) | ΔMAE CI_lo | 판정 |
|---|---|---|---|
| 0.30 | +0.00628 | +0.00118 | 유의 개선 ✅ |
| **0.20** | **+0.00657** | **+0.00187** | **유의 개선(최적) ✅** |
| 0.12 | +0.00596 | +0.00025 | 유의 개선 ✅ |

- **세 후보 모두 유의 MAE 축소** — CI_lo>0 으로 0 배제. w=0.30(near_52w 가중치 불변)에서도 개선이 나오므로
  **주 효과는 extension_guard + pullback**(과열 회피·눌림목 보상)이고, near_52w 축소(0.20)는 추가 소폭 개선.
- ΔMAE 절대값은 작음(+0.006) — 세션2 entry_bias 평가(extension_guard ΔMAE +0.0024)와 정합·확대.
- **채택:** near_52w≈0.20 + extension_guard + pullback.

## 3. 레이어2 — 리스크 오버레이 (위험조정) · **통과**

고정 baseline 점수에 오버레이 컴포넌트를 누적 토글(연속 OOS 구간 NAV).

| config | MDD | Sharpe | Calmar |
|---|---|---|---|
| no_overlay (점수만) | −0.632 | 0.299 | 0.074 |
| +regime | −0.361 | 0.587 | 0.315 |
| +regime+atr | −0.285 | 0.682 | 0.432 |
| +regime+atr+sizing | −0.282 | 0.698 | 0.418 |

- **오버레이가 MDD를 −63%→−28%(반토막)**, Sharpe 0.30→0.70, Calmar 0.074→0.42. 위험조정 대폭 개선.
- **ablation 귀속:** **레짐 게이트가 최대 기여**(MDD −63%→−36%, Sharpe 0.30→0.59 — 조정장 미진입 효과) ·
  **ATR 손절 유의**(−36%→−28%, 꼬리손실 절단) · **사이징 미미**(−28.5%→−28.2% — 한계효용 거의 0).
- **채택:** 레짐 게이트 + ATR 손절. **사이징은 보류**(효과 미미, 복잡도 대비 가치 낮음).

## 4. 정직성 / 한계 (반드시 함께 읽기)

- **경로지표 신뢰도 약함(설계 §11 예고대로):** MDD/Sharpe 는 단일 경로·짧은 연속구간. 연속 구간 NAV 는
  fold 경계 1기간을 누락한 *근사 곡선*(통합리뷰 Minor) — **절대 MDD 수치는 근사**, 신뢰할 신호는 *상대 비교*
  (오버레이 on vs off, 동일 모델링이라 공정)다. 레이어1(MAE+부트스트랩 CI)이 더 엄밀한 게이트.
- **ATR 손절 동일봉 스캔(보수적):** 진입봉 자체의 저가도 손절 스캔에 포함(비관적). 4개 config 동일 적용이라
  ablation 공정성엔 무영향(통합리뷰 Minor).
- **생존편향·현-상장 근사·단일 호라이즌(20d)·월간 Sharpe(ppy=12).** yfinance 무데이터 종목 fail-open 제외(유효
  ~145). pykrx 밸류 일시오류는 fallback_c 가 밸류/펀더를 안 써서 무관.
- **수익 보장 아님.** *덜 잃기* 검증이지 수익 알파가 아니다(알파는 0). OOS 통과도 미래 실패 가능.

## 5. 다음 단계 — 라이브 승격 (별도 게이트, 본 작업 밖)

검증 통과 컴포넌트만 라이브에 반영하는 **별도 단계**(라이브 회귀 필수):
1. **진입품질(레이어1):** `scoring.score_candidates`/`config` 에 extension_guard + pullback 반영 + near_52w 가중치
   0.30→≈0.20. 라이브 회귀 테스트(점수 드리프트 허용범위) 통과 필수.
2. **레짐 게이트(레이어2 최대 기여):** 라이브 엔진에 분산일→risk_off 산정 + STRONG_BUY 상한/배지 + 프런트
   레짐 배너. `Snapshot.market_regime` 추가.
3. **ATR 손절:** 정보용 손절가/사이징 노출(기존 트레일링 손절과 병행).
4. **사이징:** 보류(효과 미미).
> 라이브 승격 각 단계는 라이브 회귀 + (가능하면) 운영 모니터링으로 재확인. 본 백테스트 판정은 *채택 후보*를
> 가린 것이지 라이브 성과 보장이 아니다.

## 6. 재현
```bash
# .env(KRX_ID·KRX_PW·DART_API_KEY) 셸 로드 후(하니스 자동로드 안 함):
uv run python -m backend.backtest.run --fallback-c \
  --start 2016-01-01 --end 2024-12-31 --rebalance monthly \
  --top-n 5 --universe-top-n 150 --n-resamples 250 --n-perms 250 --out data/backtest
# 산출: data/backtest/report_fallback_c.{md,json}
```
DoD: `uv run pytest`(385 passed, 1 skipped) · `ruff check` · `ruff format` · `mypy backend/`.
