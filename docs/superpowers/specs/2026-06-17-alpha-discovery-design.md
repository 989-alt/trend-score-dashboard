# 선택 알파 발견 엔진 — 설계 (Alpha Discovery via Factor Horse-Race)

> 작성 2026-06-17. 목표: trend-score 점수가 **실제로 forward-return을 예측(선택 알파)** 하도록,
> 넓은 KR 유니버스에서 후보 팩터를 개별 OOS 검정→다중검정 보정→승자만 합성→OOS 검증한다.
> 전제: §3.5(과최적화 금지) — 수익률 목표에 튜닝하지 않는다. 채택 기준은 CAGR이 아니라
> **OOS 단조성의 유의 개선**. 검증 인프라(Layer A/B/C + compare 게이트)는 이미 구축됨
> (`2026-06-17-validation-method.md`).

## 1. 목표와 성공 기준 (사용자 확정)
- **1차 목표(alpha):** OOS(워크포워드 test/holdout)에서 합성 점수의 **단조성 > 0 이고 통계적으로
  유의**(블록 부트스트랩 95% CI가 0 및 baseline 배제). 즉 점수가 오를 종목을 실제로 가려냄.
- **폴백(risk-adjusted):** 호스레이스에서 FDR+홀드아웃을 통과하는 팩터가 0개이거나 합성이 OOS
  게이트를 실패하면 → 리스크 엔지니어링(별도 스펙)으로 전환, 위험조정(Sharpe·Calmar·MDD)로 평가.
- **불변 원칙:** 수익 보장 없음. 가중치/임계를 수익률에 맞춰 튜닝 금지. 발견은 *측정*이지 *목표*가 아님.

## 2. 아키텍처 (파이프라인)
```
[1] 유니버스(시점별 근사, 상위 500) → 가격 yfinance(.KS/.KQ)·펀더 DART·디스크 캐시
[2] 팩터 라이브러리(순수함수 ~12–15, 가격+펀더) → 각 리밸런스일 T에 ≤T로 횡단면 산출
[3] 호스레이스: 팩터별 OOS 단조성 + 부트스트랩 CI + 퍼뮤테이션 p
      → ★ BH-FDR 다중검정 보정 + 홀드아웃 1회 재확인
[4] 승자 선별(FDR 통과 AND 홀드아웃 부호일치) → rank-z 등가중 합성 = preset `alpha_composite`
[5] 합성 OOS 검증(워크포워드 + paired_diff_ci vs baseline) → 유의개선? YES=알파채택 / NO=[6]
[6] 폴백 C: 리스크 엔지니어링(레짐+ATR손절+사이징+extension_guard) — 별도 스펙
```
기존 하니스 재사용: `backend/backtest/{run(build_event_study·run_walk_forward),metrics(block_bootstrap_ci·permutation_pvalue·paired_diff_ci),compare,report}`. 라이브 스코어러(`scoring.score_candidates`·`engine`·`schemas`)는 **무수정**, 알파 로직은 백테스트 프리셋으로 게이트.

## 3. 유니버스 · 데이터 (KRX 차단 우회)
- **목록 취득(KRX 불필요·자동):** DART `corpCode.xml`(전 상장사 corp↔stock_code, 이 PC 작동) →
  6자리 종목코드 보유분 필터 → yfinance OHLCV로 최근 거래대금(종가×거래량) 산출 → **상위 500 선별.**
  - 폴백: 정적 `data/universe_kr.csv`(KRX 웹 1회 다운로드 또는 FinanceDataReader). corpCode/랭킹이
    부실하면 사용.
- **가격:** yfinance 배치(`yf.download`) + 디스크 캐시(수백 종목 1회 fetch, 재실행 빠르게).
- **펀더:** DART corpCode 벌크맵 + 종목별 재무 캐시. **가격팩터 호스레이스 먼저(DART 불필요), 펀더 2차 패스.**
- **생존편향:** 시점별 멤버십은 과거 KRX 부재로 *현 상장분 근사* → 리포트 명시. baseline vs 변형은
  동일 유니버스라 *상대 비교는 공정*(Layer D 원칙).
- **함정:** 이 PC는 `data.krx.co.kr` SSL 차단 → pykrx 금지. yfinance·DART만 사용.

## 4. 팩터 라이브러리 (신규 순수함수, 라이브 무수정)
각 함수 = (rows≤T 또는 as-of 펀더) → Decimal, 결정론. `scoring.py`에 신규 함수로만 추가.
- **가격/추세:** `trend_template`(Minervini 0–8), `ma_alignment`(P>MA50>MA150>MA200), `mom_12_1`
  (12개월−1개월 모멘텀), `rs_rank`(지수대비 상대강도; 합성 시 횡단면 순위), `vcp_tightness`,
  `volume_surge`(vol/SMA20), 기존 `near_52w`·`pocket_pivot`·`pullback_3pos`, `extension`(가드용).
- **펀더/퀄리티(DART):** `gross_profitability`(Novy-Marx (매출−COGS)/총자산 — 1순위), `roe`,
  `op_margin`, `rev_growth`.
- 매 리밸런스일 후보군 횡단면에서 각 팩터의 (값, forward-return) 쌍을 수집(`build_event_study`의
  `factor_study` 경로 확장: 현재 5개 → 후보 전체).

## 5. 호스레이스 · 다중검정 보정 (엄밀성의 핵심)
- 팩터별 OOS(test 폴드) 단조성 + 날짜블록 부트스트랩 95% CI + 퍼뮤테이션 p(기존 모듈).
- **Benjamini–Hochberg FDR**(신규 `metrics.bh_fdr(pvalues, q=0.10)`): ~15개 동시검정의 가짜 승자
  제어. 보정 없이는 노이즈 1개가 "유의"로 통과(§3.5 함정의 팩터판).
- **홀드아웃 재확인:** FDR 통과 팩터를 최종 홀드아웃 구간에서 1회 평가 → 부호 일치해야 승자 확정.
- **승자 정의:** FDR-adjusted 유의(q<0.10) AND 홀드아웃 단조성 부호 일치 AND CI lo>0(test).
- **산출물:** 팩터 리더보드 리포트(md/json): factor·mono·CI·p·q(FDR)·holdout_mono·승자여부.

## 6. 합성 · 검증
- **합성:** 승자들의 **rank-z 등가중**(가중치 최적화는 과최적화 → 등가중으로 시작). 신규 백테스트
  프리셋 `alpha_composite`(=`_score_at`의 새 분기, 승자 팩터의 횡단면 rank를 z-score 평균).
- **검증:** `compare_presets(variant="alpha_composite", baseline)` — 워크포워드 OOS + `paired_diff_ci`
  로 Δ단조성·ΔMAE 유의성. **채택 = OOS 단조성의 통계적 유의 개선(=선택 알파 달성).** 실패 → 폴백 C.
- **다중검정 2차 가드:** 합성·검증을 여러 번 시도하면 다시 p-hacking. 변종 수 제한 + 홀드아웃은
  최종 1회만.

## 7. 폴백 C (트리거 시 별도 스펙)
승자 0 또는 합성 OOS 실패 시: 레짐 게이트(분산일 카운트→리스크오프 신규억제) + ATR 손절(entry−2×ATR)
+ 포지션 사이징 + extension_guard(검증된 5d MAE↓). 평가지표 위험조정. **본 스펙 범위 밖 — 트리거되면
별도 브레인스토밍·스펙.**

## 8. 테스트 (발견 엔진 자체의 정확성)
- **합성 그라운드트루스:** 패널에 *알려진 예측 팩터 1개 + 노이즈 팩터 다수* 심기 → 호스레이스가
  그 팩터를 승자로 선별 **AND** 노이즈를 FDR로 기각해야 함(거짓발견율 통제 증명). 이는 Layer A
  (계측기 정확성)의 팩터-발견판.
- **BH-FDR 단위테스트:** 알려진 p값 벡터 → 알려진 reject 집합.
- **결정론:** 시드 고정. **라이브 무수정** 회귀(baseline/score_candidates/engine/schemas 불변).
- DoD: `uv run pytest`·`ruff check`·`ruff format`·`mypy backend/` 클린.

## 9. 구현 단계 (writing-plans가 상세화)
1. 유니버스 취득(DART corpCode→yfinance 거래대금 상위500) + 가격/펀더 디스크 캐시 + 로더 확장.
2. 팩터 라이브러리(가격 먼저, 펀더 다음) — 순수함수 + 단위테스트.
3. 호스레이스 엔진(factor_study 확장 + BH-FDR + 홀드아웃) + 리더보드 리포트 + 합성 그라운드트루스 테스트.
4. `alpha_composite` 합성 프리셋 + compare 게이트 검증 + **실데이터 호스레이스 실행·판정**.
5. (조건부) 승자 0/합성 실패 시 폴백 C 별도 스펙.
> 각 단계 OOS 게이트. 어떤 팩터도 유의 알파 없으면 정직하게 "알파 불가" 보고 후 폴백.

## 10. 리스크 / 정직성 메모
- **알파는 안 나올 수 있다**(현 데이터상 현 팩터는 0). 그게 정상이고, 그때 폴백이 답.
- 생존편향·현-상장 근사·yfinance 조정종가·DART 커버리지 한계는 리포트에 명시.
- 수백 종목 fetch는 느림(최초 1회) — 캐시로 완화. DART 레이트리밋 fail-open.
