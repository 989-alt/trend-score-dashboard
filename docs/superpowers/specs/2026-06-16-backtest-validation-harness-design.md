# 백테스트 검증 하니스 설계서 (Backtest Validation Harness — v1)

> 작성 2026-06-16 · 브랜치 `docs/score-reweight-rationale` · 레포 989-alt/trend-score-dashboard
> 선행/자매 산출물: `2026-06-16-score-reweight-rationale.md`(검증할 가중치 prior의 출처).
> 방법: brainstorming(컨텍스트→명확화→접근법→섹션 승인). 결정 로그는 부록.
> 다음 단계: 이 설계서 승인 후 `writing-plans`로 구현계획.

---

## 0. 목적 · 성공 기준

**목적:** 결정론 점수를 **과거에 replay**해 *"점수 추천대로 샀으면 성과·예측력은?"* 을 룩어헤드 없이 측정한다. 첫 임무는 **현 점수 베이스라인의 정량화** — 리웨이트 스펙 §1의 진단(점수가 forward-return을 단조 예측하지 못함 / 고점추격으로 MAE 큼)을 **수치로 확인**하고, 이후 리웨이트·후보팩터(퀄리티)가 베이스라인 대비 개선되는지 게이팅한다.

**하니스 자체의 성공 기준(이 설계서가 "완료"되는 조건):**
1. 시점 T의 점수를 **라이브와 동일 로직**(공유 스코어러)으로 재현하고, **룩어헤드 0**을 테스트로 증명.
2. 5년+ KR에서 **현 점수 베이스라인**의 이벤트스터디(버킷 단조성·MAE·승률) + 포트폴리오(누적·CAGR·MDD vs ^KS11) 리포트 생성.
3. **후보 팩터(퀄리티 ROE/마진/성장, 가치 PER/PBR)** 의 예측력을 동일 이벤트스터디 틀로 평가(프로덕션 배선 전).
4. 무결성 가드(룩어헤드·생존편향·OOS·비용)가 **테스트로 검증**됨.

> ⚠ **수익 보장 불가.** 검증된 엣지도 OOS 실패 가능. 룩어헤드·생존편향이 있으면 백테스트는 거짓말한다 — 가드가 본 설계의 본질이다.

---

## 1. 스코프 경계

**v1 포함**
- KR 단독 · 5년+ (기본 예: 2020-01-01 ~ 직전월말, CLI 설정가능).
- `engine._collect_raw`의 팩터 계산을 **공유 순수 스코어러로 추출**(엔진 행동 변경 0).
- pykrx + DART **시점별(as-of) 오프라인 데이터층**(가격·밸류·일자별 상장종목·재무).
- **이벤트스터디**: 현 점수 + 후보 팩터(퀄리티/가치)의 예측력.
- **간이 포트폴리오 시뮬**: 현 점수 베이스라인 + 퀄리티 틸트 실험 프리셋.
- 무결성 가드 · 결정론 합성 패널 테스트.

**핵심 설계 결정 — 퀄리티는 "검증 먼저, 배선 나중":** 퀄리티(ROE/마진/성장)를 프로덕션 `scoring.py`에 **영구 배선하지 않고**, 하니스가 as-of 데이터로 **후보 신호로서 이벤트스터디·틸트 실험**으로 평가한다. 검증 통과 후에야 별도 플랜으로 프로덕션 배선. → `scoring.py` 무수정, 변경 면적 최소.

**v1 제외(후속 플랜 — 이 하니스로 게이팅):** trend_template · pullback_3pos · extension_guard · volume_surge · vcp_tightness · 레짐 게이트 · 가치 렌즈 UI · 종가 배지 · **US 시장**.

> 로드맵 기본 순서(추세 리웨이트 먼저)와 달리 퀄리티 데이터층을 v1로 당김 — 사용자 우선순위 반영(의식적 선택). 추세 신규팩터는 후속 플랜에서 본 하니스로 검증.

---

## 2. 아키텍처 (접근 2 — 공유 순수 스코어러 추출)

라이브가 보는 **바로 그 점수 경로**를 백테스트가 공유하도록, 팩터 계산을 순수 함수로 1회 추출한다(라이브-백테스트 drift 0).

| 단위 (신규/리팩터) | 책임 (단일) | 입력 → 출력 | 의존 |
|---|---|---|---|
| `backend/factors.py` **(신규·추출)** | **공유 순수 스코어러** | `build_candidate(rows≤T, fundamentals_asof, index_momentum, market, settings) → scoring.Candidate` | `scoring.py`(무수정) |
| `backend/engine.py` **(리팩터)** | `_collect_raw`가 provider I/O 후 `factors.build_candidate` 위임 — **행동 변경 0**(기존 테스트 그대로 통과) | — | factors.py |
| `backend/backtest/dart_client.py` **(신규)** | OpenDART **as-of 재무** | corpCode 매핑 · 공시목록(접수일 `rcept_dt`) · 전체재무제표 → 당기순이익·자본·영업이익·매출 | httpx(기존) |
| `backend/backtest/loader.py` **(신규)** | **시점별 오프라인 패널** + SQLite 캐시 | pykrx OHLCV·밸류·일자별상장·거래대금 + DART 재무 + ^KS11 → in-memory 패널 | pykrx·yfinance·dart_client |
| `backend/backtest/metrics.py` **(신규)** | **순수 메트릭** | 버킷 단조성(Spearman)·MAE·승률·CAGR·MDD·vol | (없음) |
| `backend/backtest/run.py` **(신규·CLI)** | 리밸런스 루프·포트폴리오 시뮬·리포트 | 위 전부 → md+json | 위 전부 |
| `backend/backtest/__init__.py` | 패키지 | — | — |

**`build_candidate` 추출 경계:** 현 `_collect_raw`(engine.py:106-181)는 ① provider 호출(OHLCV·quote·fundamentals·flow) ② 팩터 계산(momentum·rs·volatility·near_52w·pocket_pivot·ma200·turnover·eligibility) ③ `Candidate` 조립을 섞는다. **②③만** `factors.build_candidate`로 추출 — provider I/O는 `_collect_raw`(라이브)·`loader`(백테스트) 각자 담당하고, 동일한 순수 함수에 rows/fundamentals/index_momentum을 넘긴다.

---

## 3. 시점별(as-of) 데이터층

| 데이터 | 소스 | API (시점별 보장) | 캐시 |
|---|---|---|---|
| 일봉 OHLCV | pykrx | `get_market_ohlcv_by_date(from,to,ticker)` → ≤T 슬라이스 | SQLite (ticker+range) |
| 밸류 PER/PBR/EPS/BPS/DIV | pykrx | `get_market_fundamental_by_date(from,to,ticker)` — **일자별 시계열**(룩어헤드 0) | SQLite |
| 유니버스(상장종목) | pykrx | `get_market_ticker_list(date, market=KOSPI/KOSDAQ)` — **그 날짜 상장분만** | 날짜별 |
| 거래대금(유동성 필터) | pykrx | `get_market_ohlcv_by_ticker(date)` 거래대금 컬럼 | 날짜별 |
| 재무(ROE/마진/성장) | **OpenDART** | corpCode.xml(zip)→corp_code · `list.json`(접수일 `rcept_dt`) · `fnlttSinglAcntAll.json`(당기순이익·자본총계·영업이익·매출액) | corp+연도+보고서 |
| 지수(벤치마크·RS·레짐) | yfinance | `^KS11` (pykrx 지수는 datacenter 403; **로컬은 동작 가능하나 라이브 일관성 위해 yfinance 기본**) | 날짜별 |

**파생 퀄리티/가치(as-of, 결정론):**
- ROE(실제, **기본**) = 당기순이익 / 자본총계 (DART, 접수일 기준). 커버리지 결손 시 **폴백** = EPS / BPS (pykrx 프록시, 시점별).
- 영업이익률 = 영업이익 / 매출액 (DART). 매출/EPS 성장 = 당기 / 전년동기 (DART 또는 pykrx EPS 시계열).
- 가치 = PER·PBR·(PSR는 매출 필요 시 DART) — 후보 팩터로 이벤트스터디 평가.

**DART as-of 규칙(룩어헤드 차단의 핵심):** 시점 T에 쓸 재무는 **`rcept_dt`(접수일) ≤ T 인 보고서 중 최신**을 선택한다(사업연도 종료일이 아니라 *공시된 날* 기준). 분기↔연간 보고서코드(`reprt_code`) 매핑·연결/별도 우선순위는 구현계획에서 확정.

---

## 4. 데이터 흐름 (리밸런스일 T마다)

1. `universe(T)` = pykrx 일자별 상장종목 ∩ 거래대금 ≥ 임계 → **그 시점 실재 종목만**(생존편향 차단).
2. 종목별: OHLCV **≤T 슬라이스** · 밸류 as-of T · 재무 **접수일≤T 최신** · 지수모멘텀(T) → `factors.build_candidate` → `Candidate`.
3. `scoring.passes_hard_filter` + `scoring.score_candidates` (**무수정**) → 점수·등급·랭킹.
4. 상위 N 매수 · **등가중** · 비용 차감 → 포트폴리오 NAV 갱신, 다음 T까지 보유.
5. **forward-return은 T+1 이후 가격으로만** 별도 계산(점수 산정엔 절대 미사용 — 평가 전용).

---

## 5. 무결성 가드 (필수)

- **룩어헤드 0:** 모든 입력 ≤T 슬라이스 강제. DART는 **접수일** 기준. forward-return은 평가 전용 분리. (테스트: 스코어러가 >T 봉을 받으면 실패.)
- **생존편향:** 유니버스를 매 T 재구성. 보유 중 **상폐 시 마지막 거래가/정리매매가로 청산**(−100% 가정 회피), 리포트에 상폐 처리·편입제외 수 표기.
- **OOS 워크포워드:** 가중치 튜닝 시 train/test 폴드 분리, **OOS 메트릭 별도 보고**(곡선맞춤 방지). 베이스라인 측정은 튜닝 없음(전구간 in-sample 측정이되 forward-return 자체가 OOS 성격).
- **비용·사이징 명시:** 기본 **KR ~0.41%/회전**(수수료+세금+슬리피지, CLI 설정가능) · 등가중 · 무레버리지. 리포트 상단에 가정 출력. 회전율도 리포트.

---

## 6. 메트릭 / 리포트

**이벤트스터디 (증상 직격 — 1차 게이트):**
- 점수 **십분위 버킷**별 forward-return(호라이즌 **5 / 20 / 60 거래일**) → **단조성**(Spearman rank corr, 점수↑→수익↑ 이어야 함) · **MAE**(매수후 최대역행, 고점추격 직격) · **승률**(fwd>0).
- 동일 틀로 **후보 팩터**(ROE·영업이익률·성장·PER·PBR) 단독 예측력 평가 → "퀄리티/가치가 KR에서 forward-return을 단조 예측하나"를 배선 전 검증.

**포트폴리오 시뮬 (현실성 보조):**
- 누적수익 · CAGR · MDD · 변동성, **^KS11 벤치마크 대비** 초과CAGR.

**프리셋(실험 단위):**
- `baseline` = 현 `config.py` 가중치(불변).
- `quality_tilt` = 현 추세팩터 + quality 0.08(하니스가 as-of 데이터로 합성, 재정규화). 베이스라인 대비 단조성·MAE 개선 측정.

**출력:** 마크다운(사람) + **JSON**(프리셋 간 diff용 기계판독). CLI:
```
python -m backend.backtest.run \
  --start 2020-01-01 --end 2025-12-31 \
  --rebalance weekly --top-n 20 --cost-bps 41 \
  --preset baseline            # 또는 quality_tilt
```
기본값: 리밸런스 주간(주 마지막 거래일 종가) · 상위 20 · 등가중 · 비용 41bp/회전.

---

## 7. 에러처리 / 관측성

- 종목별 데이터 결손(OHLCV/펀더 부재 at T) → **fail-open**(유니버스 제외 또는 해당 팩터 미적용 — 라이브 정책 계승) + **카운트 로깅**(scanned/eligible/scored/excluded — `SnapshotCounts` 정신).
- DART 레이트리밋/공시부재 → 캐시 + 스킵 카운트(무음 절단 금지, 리포트에 커버리지율).
- pykrx 실패 → 재시도/캐시, 날짜 단위 graceful skip + 경고.

---

## 8. 테스트 (레포 패턴 계승)

- `tests/test_factors.py` — 추출된 `build_candidate`가 기존 `_collect_raw` 결과와 동치(회귀). 기존 `test_engine.py` 그대로 통과.
- `tests/test_backtest_loader.py` · `test_dart_client.py` · `test_backtest_metrics.py` · `test_backtest_run.py`.
- **결정론 합성 패널 Provider**(기존 `SampleProvider` 방식) — 외부 API 없이 가드 단위검증:
  - 룩어헤드(스코어러가 >T 미관측) · 생존편향(미상장 제외, 상폐 청산) · **as-of**(접수일≤T 최신 선택) · 메트릭 산술(단조성/MAE/MDD 알려진 시계열) · 비용 적용 · **Decimal 규율**.
- DoD: `uv run pytest` + `ruff check` + `ruff format` + `mypy backend/`.

---

## 9. 의존성 / 선결

- **신규 패키지 0** — httpx·pandas(pykrx 경유)·pykrx·yfinance 모두 기존 dep.
- **OpenDART API 키** → `.env` `DART_API_KEY`(무료 발급, `.env.example`에 템플릿). 시크릿 커밋 금지.
- **Decimal 전면**(float 금지) · **swing-bot 무수정** · **scoring.py 무수정**(factors.py 추출만) · 한글은 코드 주석/리포트만(i18n 무관).
- 실행 환경: 오프라인 CLI(사용자 로컬). 라이브 파이프라인·스케줄러 무수정.

---

## 10. 후속(deferred) · 검증 게이트 연결

본 하니스가 게이팅할 후속 플랜(각 단계는 **베이스라인 대비 MAE 축소·단조성 회복 시에만 채택, 아니면 롤백**):
1. **퀄리티 프로덕션 배선** — 이벤트스터디가 ROE/마진/성장의 예측력을 확인하면 `scoring.py`/`config.py`에 quality 0.08 배선.
2. **추세 진입편향 수정** — trend_template·pullback_3pos·extension_guard(리웨이트 스펙 §3) 구현 후 하니스로 검증.
3. **레짐 게이트·가치 렌즈·종가 배지·US 시장**.

---

## 부록: 결정 로그

| 질문 | 결정 | 근거 |
|---|---|---|
| v1 검증 범위 | **퀄리티 포함** | 사용자. 단 "검증 먼저, 배선 나중"으로 재구성(§1) |
| 대상 시장 | **KR 우선(US Phase 2)** | KR=pykrx 시점별 펀더 무료, US=무료 시점별 펀더 사실상 부재(룩어헤드 위험) |
| KR 퀄리티 소스 | **DART(정식 재무) + pykrx 밸류** | 사용자. ROE 실제/영업이익률/성장까지 |
| 아키텍처 | **접근 2 — 공유 순수 스코어러 추출** | 사용자. 라이브-백테스트 drift 0, scoring.py 무수정 |
| 리밸런스 기본 | 주간 · 상위20 · 등가중 · 41bp | 로드맵 §6 + KR 비용 관행(설정가능) |
