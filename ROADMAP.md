# 프로젝트 로드맵 & 작업 현황 (작업 계획서)

> 갱신 2026-06-18. 추세추종 매수추천 스코어 대시보드(국장·미장)의 **매매 로직 검증·재설계** 작업 전체 지도.
> 한 줄: *"점수가 실제로 도움이 되는지 정직하게 측정하고, 검증된 만큼만 채택한다."*
> 상세 산출물은 `docs/superpowers/specs/` · `docs/superpowers/plans/` 의 날짜별 문서 참조(§6 파일맵).

---

## 0. 최종 목표 (정직판)

- **통증:** "추천 종목을 사면 매수 직후 떨어진다" + "조정장에도 STRONG_BUY 발화".
- **목표:** 매수추천이 *신뢰할 수 있는* 근거에 서도록. 단, **수익 보장은 어떤 설계로도 불가**(사용자 동의).
- **지금까지 증거가 말하는 현실(중요):** *능동 전략이 지수를 위험조정으로 이긴다는 증거는 아직 0.* 그래서
  최종 목표는 "시장 이기는 전략 완성"이 아니라 → **① 믿을 수 있는 *측정* 위에서 ② 엣지가 있으면 채택,
  없으면 정직히 인정하고 위험관리·지수추종을 기본값으로** 받아들이는 것. 라이브 승격은 *믿을 수 있는 백테스트
  + 라이브 회귀 + 페이퍼트레이딩* 통과 시에만.

---

## 1. 지금까지 한 것 (DONE)

브랜치 `feat/validation-oos-entry-bias`(main 미병합). 라이브 스코어러 무수정. 397+ tests green.

| # | 작업 | 판정/결과 | 산출물 |
|---|---|---|---|
| A | **검증 하니스** (OOS 워크포워드·날짜블록 부트스트랩 CI·퍼뮤테이션 p·BH-FDR·ablation·합성 그라운드트루스) | **핵심 자산** — 나쁜 아이디어를 정직하게 falsify | `2026-06-17-validation-method.md`, `backtest/{run,metrics,compare}` |
| B | **알파 발견** (팩터 호스레이스: 15팩터 OOS 단조성+FDR+홀드아웃) | **승자 0.** 선택 알파 없음. `near_52w`(라이브 최대가중 0.30)가 유일 FDR기각·**유의 음수**(통증 정량확증) | `2026-06-17-alpha-discovery-*.md`, `backtest/{horserace,factor_pool}` |
| C | **폴백 C** (리스크 엔지니어링: 진입품질 재가중 + 레짐·ATR손절·사이징 오버레이) | **KR 두 게이트 통과** — MAE 유의 축소·MDD 반토막(−63%→−28%) | `2026-06-17-fallback-c-*.md`, `backtest/{portfolio,regime,riskoff?}`, `fallback_c` 프리셋 |
| D | **US 백테스트** (동일 로직, S&P500 상위~150) | **falsify** — 일반화 실패·해로움. 전체 전략 1.6% vs S&P500 12.7% CAGR(Sharpe 0.20 vs 0.92) | `2026-06-18-us-backtest-RESULTS.md`, `--market US` |

---

## 2. 핵심 교훈 (왜 이렇게 됐나)

1. **선택 알파 없음** — 광범위 팩터 탐색에서 OOS 유의 승자 0.
2. **레짐 오버레이는 레짐 *베팅*** — KR(약세·크래시 多) 약 / US(강세장) 독(쉬어서 수익 파괴). 보편적 안전장치 아님.
3. **일반화 안 됨** — KR 두 게이트 통과 로직이 US에서 무너짐 → KR "통과"는 *그 시장·시대 특수*.
4. **지수를 못 이김** — US에선 종목 선별조차 S&P500을 위험조정으로 못 이김(생존편향 유리한데도).
5. **생존편향이 KR 긍정을 부풀림** — 현재-유니버스 백테스트는 살아남은 종목만 봄(아래 §3에서 제거).

---

## 3. 이제 할 것 (NEXT — 진행 중) — **KR 완전 PIT + 현실 비용 (백테스트 신뢰성)**

*로직을 바꾸기 전에 자기기만(생존편향)부터 제거* — "백테스트를 이기도록 고치기"는 곡선맞춤이라 금지.
pykrx 프로브로 **KR 완전 생존편향 제거가 가능 확인됨**(시점별 상장목록 작동·상폐 종목 OHLCV 복원 가능; 2016 KOSPI 887종목 중 99종목이 2024엔 없음).

**설계 합의(1/2):**
- **① PIT 유니버스(시점별·상폐포함):** 각 리밸런스일 `get_market_ticker_list(T)`(KOSPI∪KOSDAQ) → 거래대금 as-of 상위 N. 합집합 fetch. `Panel.pit_universe`로 *그 시점 멤버십* 반영.
- **② PIT 로더:** 종목 OHLCV를 **pykrx**(상폐 포함·정확한 거래대금)로 — yfinance는 상폐 데이터 없음. 지수(^KS11)는 yfinance 유지.
- **③ 현실 비용:** flat bps → **회전율 기반 + 매도세(0.18~0.23%) + 스프레드**. config `sell_tax_pct`·`spread_bps`.

**남은 설계(2/2, 마저 할 것):** 검증·재실행 비교(*생존편향 제거 후 KR 폴백C/baseline 결과가 살아남는가*) · 테스트(합성·단위) · 파일 · 정직성. → 그 다음 **스펙 확정 → writing-plans → subagent-driven 실행**.

> 재개 방법: brainstorming 스킬로 "설계 2/2"부터 이어서 → 스펙(`docs/.../specs/2026-06-1x-kr-pit-realistic-cost-design.md`) → plan → 실행.

---

## 4. 그 다음 (LATER)

1. **PIT 후 *목표 재결정*:** 생존편향·현실비용 제거 후에도 엣지가 있으면 → 라이브 승격 경로. 없으면(가능성 높음)
   → **위험관리·스크리닝 보조·규율 도구**로 재프레이밍(시장 이기기 포기). *이게 가장 가능성 높은 정직한 종착.*
2. **뉴스 리스크오프 필터** (deferred plan #2, `2026-06-18-news-riskoff-filter-design.md`):
   - (a) 객관 트리거(VIX·환율…) **액션 백테스트**(fail-fast) + 큐레이션 *커버리지 갭*(한국고유 사건 놓침률)
   - (b) **수집 파이프라인**(RSS/Telegram → 원시 타임스탬프 아카이브, LLM 0, ₩0; *일찍 시작할수록 전향검증 빨라짐*)
   - (c) **탐지기 + 매매통합**(LLM, (a)+전향검증 통과 시). — 매매 반영은 *예측*이 아니라 *비대칭 방어*만.
3. **US PIT:** 과거 구성종목·상폐 데이터(유료/구축) 확보 시 — 현재 무료로 불가, 보류.
4. **라이브 승격(검증 통과 컴포넌트만, 각 단계 별도 게이트):** 점수/엔진 반영 + 라이브 회귀 + 페이퍼트레이딩.

---

## 5. 최종적으로 (ULTIMATE)

**"이 시스템으로 시장을 이기는가?"에 대한 *믿을 수 있는 답*을 도출**하는 것이 최종 목표.
- 현실적 기대: **못 이길 수 있고, 그게 정상.** 그땐 정직하게 인정하고 → *위험 통제 + 고점추격 방지 + 스크리닝*
  도구로 가치를 낸다(지수추종 + 가드레일).
- 이미 만든 가장 값진 자산은 알파나 오버레이가 아니라 **(a) 정직한 검증 하니스 + (b) 검증된 부정적 발견들**.
  대부분은 자기를 속이지만 이 프로젝트는 안 속였다 — 그게 진짜 성과.
- 원칙(§3.5): 수익률에 맞춰 튜닝 금지. 채택은 위험조정 OOS 개선의 통계적 유의성으로만. 알파가 안 나올 수 있고
  그게 정상이며 그땐 폴백이 답.

---

## 6. 파일 맵

**설계(specs):** `docs/superpowers/specs/`
- `2026-06-16-backtest-validation-harness-design.md`, `2026-06-16-score-reweight-rationale.md`
- `2026-06-17-validation-method.md`, `2026-06-17-alpha-discovery-design.md`
- `2026-06-17-fallback-c-risk-engineering-design.md`
- `2026-06-18-news-riskoff-filter-design.md` (deferred plan #2)

**구현계획(plans):** `docs/superpowers/plans/`
- `2026-06-17-alpha-discovery-plan.md`, `2026-06-17-fallback-c-risk-engineering.md`

**판정(RESULTS):** `docs/superpowers/specs/`
- `2026-06-17-alpha-discovery-RESULTS.md` (알파 0)
- `2026-06-17-fallback-c-RESULTS.md` (KR 두 게이트 통과)
- `2026-06-18-us-backtest-RESULTS.md` (일반화 실패·falsify)

**핵심 모듈:** `backend/backtest/`
- `run.py`(_score_at·프리셋·워크포워드·`--horserace`/`--fallback-c`/`--market` CLI), `compare.py`(ΔMAE 게이트),
  `metrics.py`(부트스트랩 CI·BH-FDR·portfolio_metrics), `horserace.py`, `factor_pool.py`, `portfolio.py`(리스크
  오버레이+토글), `regime.py`(분산일·risk_off), `universe.py`(거래대금 유니버스·US_UNIVERSE), `loader.py`(시장
  인지·OHLCV 캐시), `panel.py`(Panel.market), `ablation.py`. + `backend/scoring.py`(팩터 순수함수·라이브 무수정).

**참고:** 머신 메모 `~/.claude/.../memory/trend-score-alpha-discovery.md`(ella4=KRX 작동·path B). 레포 `HANDOFF.md`(크로스머신).
