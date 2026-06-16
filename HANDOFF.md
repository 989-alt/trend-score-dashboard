# HANDOFF — trend-score-dashboard 매매로직 재설계 + 백테스트 하니스

> 작성 2026-06-17. **다른 기기에서 이어 작업용 단일 문서.** 이 파일은 레포 루트에 있다.
> 레포: github.com/989-alt/trend-score-dashboard · 로컬 `C:\Users\ella4\trend-score-dashboard`

---

## ▶ RESUME HERE — 다른 기기에서 시작하는 법

1. **이 기기에서 먼저 `git push`** (현재 `main`이 `origin/main`보다 **20커밋 ahead·LOCAL only**). 안 하면 다른 기기에 코드가 없다.
   ```
   cd C:\Users\ella4\trend-score-dashboard && git push origin main
   ```
2. 다른 기기: `git clone https://github.com/989-alt/trend-score-dashboard.git` → `cd` → 환경 셋업:
   - **uv 설치**: `winget install astral-sh.uv` (또는 `irm https://astral.sh/uv/install.ps1 | iex`). 그 후 `uv sync` (Python 3.13 + deps 자동).
   - **DART 키**: OpenDART 무료 키 발급(https://opendart.fss.or.kr) → 환경변수 `DART_API_KEY` 설정. (백테스트 `main()`은 `.env`가 아니라 **OS 환경변수**를 읽음.)
   - 검증: `uv run pytest` → **254 passed** 나오면 정상.
3. **별도 전송 필요(로컬)**: `D:\바이브코딩\trend-score-research\` 의 연구 문서들(아래 §2). git에 없음 → USB/클라우드로 옮기거나 새로 받기. `.env`의 DART 키는 시크릿이라 새 기기서 본인 키 발급.
4. **현재 위치 한 줄**: 백테스트 하니스 v1 완성 + main 머지 + 실데이터 베이스라인 측정 완료(→ **현 점수는 수익 예측 못 함, 진단 확정**). 다음 할 일은 §4.

---

## 1. 지금까지 한 것 (DONE)

대시보드(989-alt.github.io/trend-score-dashboard) **매수추천 점수 재설계**가 목표. 통증: *"추천 종목을 사면 매수 직후 떨어진다."*

1. **재설계 방향 정리** → 리웨이트 근거 스펙. (5렌즈 subagent 리서치 + 2단계 리뷰로 도출.)
   - `docs/superpowers/specs/2026-06-16-score-reweight-rationale.md`
   - 핵심: near_52w 0.30→0.18, pullback_3pos·extension_guard 신규, 퀄리티 0.08 틸트, 가치는 별도 렌즈, 레짐·잡주 게이트. **모든 가중치는 prior+범위(최종은 백테스트로 확정).**
2. **백테스트 검증 하니스 v1** 설계→구현→리뷰→수정→재리뷰→**main 머지**.
   - 설계서 `docs/superpowers/specs/2026-06-16-backtest-validation-harness-design.md`
   - 구현계획 `docs/superpowers/plans/2026-06-16-backtest-validation-harness.md`
   - 코드: `backend/factors.py`(공유 스코어러) + `backend/backtest/{metrics,panel,dart_client,loader,run,report}.py`
   - opus 최종리뷰가 실버그 6건 발견(호라이즌 off-by-one, near_52w drift, T-close 룩어헤드, quality_tilt no-op, 거짓 벤치마크 주장) → **전부 수정 + 재리뷰로 룩어헤드-0 재확인(미래데이터 주입 실험)**.
3. **실데이터 통합 디버그** — 최초 실행에서 mock이 못 잡은 실API 3건 수정(§6).
4. **실데이터 베이스라인 측정** — 대시보드 유니버스 43종목, 2020-2024 → **점수 단조성 ≈0, MAE −14%**(§3).

테스트 254 passed · ruff/format/mypy clean. (git log: `da40cfb` ~ `fbeadf4`, 20커밋.)

---

## 2. 산출물 위치 (WHERE)

**레포 안 (git push 하면 따라옴):**
- `HANDOFF.md` (이 파일)
- `docs/superpowers/specs/2026-06-16-score-reweight-rationale.md` — 점수식 리웨이트 근거
- `docs/superpowers/specs/2026-06-16-backtest-validation-harness-design.md` — 하니스 설계
- `docs/superpowers/plans/2026-06-16-backtest-validation-harness.md` — 구현계획
- `backend/factors.py` — `engine._collect_raw`에서 추출한 공유 순수 스코어러
- `backend/backtest/` — 하니스 (metrics·panel·dart_client·loader·run·report)
- `tests/test_backtest_*.py`, `tests/test_factors.py`, `tests/fixtures/backtest_synth.py`
- `CLAUDE.md` — 레포 규율(§7)

**레포 밖 (로컬 `D:\바이브코딩\trend-score-research\`, 별도 전송):**
- `PROGRESS_AND_ROADMAP.md` — 이전 핸드오프(재설계 진단·로드맵 원본)
- `STRATEGY_REFERENCE.md` — 전략 레퍼런스(맛동산·추세추종·가치·종가매매)
- `matdongsan_report.md` — 맛동산 전략 디코딩 원본
- `quantlab_js/` — quantlab 참고 소스(채점 메커닉 차용원)
- `.env` — `DART_API_KEY`(시크릿, 커밋 금지)

---

## 3. 현재 핵심 발견 (FINDINGS)

**베이스라인 실행: 대시보드 43종목 · 2020-01-01~2024-12-31 · monthly · top-5 · 비용 41bp**
(리포트: `data/backtest/report_baseline.{md,json}`)

이벤트스터디 (점수 → forward-return, **N≈505–530, 견고**):
| 호라이즌 | 단조성 | 평균 MAE | 승률 |
|---|---|---|---|
| 5일 | −0.019 | −4.0% | 55.8% |
| 20일 | +0.019 | −8.6% | 47.1% |
| 60일 | −0.025 | −14.1% | 54.7% |

- **단조성 ≈0** → 현 점수는 forward-return을 **예측하지 못함**(노이즈). 진단 확정.
- **MAE가 호라이즌 따라 커짐(−4→−14%)** → 추천 종목이 매수 후 깊이 역행. "매수 직후 하락" 측정.

**⚠ 함정**: 포트폴리오는 +50.7% / ^KS11 대비 +9.0%p로 *좋아 보이나*, 단조성이 ~0이므로 이건 점수 실력이 아니라 **테마 유니버스(반도체·2차전지·AI 등이 2020-24에 상승) + 생존편향**이다. **이벤트스터디(단조성/MAE)가 포트폴리오 수익보다 신뢰 지표** — 양의 포트폴리오가 쓸모없는 점수를 가릴 수 있다.

**미완 데이터**: value 렌즈(PER/PBR) N=0(KRX 인증 필요), rev_growth N=3(DART 전년동기 매출 파싱 미완), 퀄리티 roe/op_margin은 표본 있으나 단조성 약함.

---

## 3.5 ⚠ 절대 금지 — "수익 목표까지 점수 튜닝"(과최적화)

> 2026-06-17 `/oh-my-claudecode:ralph`로 "**연수익률 20% 될 때까지 매수/매도 점수 기준 파인튜닝**" 자율루프 시도 → **중단·재설계**. 아래는 그 교훈(다른 기기서도 반드시 지킬 것).

- **한 표본 백테스트에서 목표 수익률(예: 연 20%)이 나올 때까지 파라미터를 맞추는 것 = 곡선맞춤(overfitting).** 노이즈에 끼워맞춘 모델이라 OOS/라이브에서 무너진다. 스펙·설계서의 "수익 보장 없음 · 곡선맞춤 방지 · OOS 워크포워드"와 정면 충돌.
- **왜 20%가 "건전하게는" 불가능한가(측정 근거):**
  - 베이스라인 이벤트스터디 **단조성 ≈0**(N≈520) → 현 팩터는 forward-return 예측 신호 거의 없음. **신호 없는 팩터는 아무리 리웨이트해도 신호가 안 생긴다** → 20%는 데이터 피팅으로만 가능.
  - 포트폴리오 +50%는 점수 실력이 아니라 **테마 유니버스 + 생존편향**(단조성 0이 증거).
  - 하니스에 **OOS train/test 분리 미구현**(v1 in-sample 전용) → 지금 튜닝 = 100% 곡선맞춤.
  - 룰베이스 KR 롱온리 점수가 **OOS 연 20% 지속** = 세계적 펀드 수준 = 거의 항상 *거짓 백테스트*.
- **건전한 대체(합의된 방향):**
  1. 하니스에 **OOS 워크포워드(train/test 분할)** 추가 — 어떤 파인튜닝도 이게 전제(스펙이 요구했으나 v1이 건너뜀).
  2. **이론 기반 리웨이트**(`near_52w↓` + `pullback_3pos` + `extension_guard`) 구현·**OOS 검증** → **MAE 축소·단조성 회복** 측정(수익 극대화 아님).
  3. **정직한 달성가능 CAGR 보고** — 목표가 아니라 *측정값*(20%일 가능성 낮고, 그게 정상).
- **핵심 원칙: 수익률을 목표로 점수를 맞추지 말 것. 점수는 *이론으로 설계*하고 *OOS로 검증*하며, 채택 기준은 "MAE↓·단조성↑"이지 "CAGR 숫자"가 아니다.**

---

## 4. 앞으로 할 일 (NEXT, 우선순위)

> §3.5 준수: 수익 목표 튜닝 금지. 이론 리웨이트 + OOS 검증.

0. **하니스에 OOS 워크포워드(train/test 분할) 추가** — 어떤 파인튜닝도 이게 전제. 현 v1은 in-sample 전용이라, 이것 없이 가중치 튜닝하면 곡선맞춤.
1. **`quality_tilt` 비교 실행** — 같은 43종목으로 `--preset quality_tilt` 돌려 baseline 대비 MAE↓·단조성↑ 여부 확인. (~5분)
2. **진입편향 수정 구현** (리웨이트 스펙 §3) — `pullback_3pos`(눌림목 가점) + `extension_guard`(과열 곱셈페널티) + near_52w 0.30→0.18. `backend/scoring.py`/`config.py`/`factors.py`에 배선. **하니스로 게이팅**: 베이스라인 대비 MAE 축소·단조성 회복일 때만 채택, 아니면 롤백.
3. **데이터 보강** — (a) value(PER/PBR): pykrx KRX 인증(`KRX_ID`/`KRX_PW`) 또는 DART 기반 대체 산출. (b) rev_growth: `dart_client._ratios_from_accounts`의 매출 전년동기(frmtrm) 파싱 점검(계정명 변형 대응).
4. **유니버스 개선** — 생존편향 완화: 시점별 상장종목 재구성(pykrx 일자별), universe 확대(예: 거래대금 상위 N). 현재는 OHLCV 존재구간 근사.
5. **로드맵(원본 PROGRESS §7)** — Phase1 추세팩터(trend_template·volume_surge·vcp 등) → Phase2 퀄리티 프로덕션 배선 → 레짐 게이트·종가 배지. 각 단계 하니스 게이팅.

---

## 5. 실행 방법 (HOW TO RUN)

```bash
# 환경 (다른 기기, uv 설치 후)
uv sync                                  # Python 3.13 + deps
uv run pytest                            # 254 passed 확인
uv run ruff check ; uv run ruff format --check . ; uv run mypy backend/   # DoD

# 유니버스(43종목) 생성
uv run python -c "from backend.market_data import _universe_from_themes; from backend.config import Settings; print(','.join(_universe_from_themes(Settings().themes_path,'KR')))"

# 백테스트 (DART_API_KEY 환경변수 필요)
DART_API_KEY=<키> uv run python -m backend.backtest.run \
  --start 2020-01-01 --end 2024-12-31 --rebalance monthly --top-n 5 \
  --tickers <위 콤마리스트> --preset baseline      # 또는 quality_tilt
# → data/backtest/report_<preset>.{md,json}
```
> 이 기기 특이사항: uv가 PATH에 없어 full path로 호출했음(`...\WinGet\Packages\astral-sh.uv_...\uv.exe`). 새 기기는 셸 재시작 후 `uv`가 PATH에 정상일 것.

---

## 6. 주의할 점 / 이미 밟은 지뢰 (CAUTIONS)

실데이터 통합에서 mock 테스트가 못 잡은 3건 — **이미 수정**, 유사 작업 시 재발 주의:
1. **DART `list.json`은 `bsns_year`/`reprt_code`를 안 줌** → `report_nm "(YYYY.MM)"` 파싱으로 도출(`dart_client._period_from_report`).
2. **pykrx OHLCV에 `거래대금` 컬럼 없음**(시가/고가/저가/종가/거래량/등락률) → `종가×거래량` 프록시로 유동성 필터 정상화. (이게 첫 실행 N=0의 진범.)
3. **pykrx 밸류(PER/PBR)가 KRX 인증(`KRX_ID`/`KRX_PW`) 요구** → 현재 value 렌즈 **비어 있음**(fail-open). 미해결(§4-3).

원칙적 주의:
- **룩어헤드**: 재무는 **접수일(rcept_dt) as-of** 필수(구현됨). 가격은 ≤T 슬라이스. 평가용 forward-return은 T+1 이후만.
- **생존편향**: v1은 OHLCV 존재구간 + caller 종목리스트 근사 → **포트폴리오 수익은 낙관 편향**. 단조성(횡단면)은 영향 적음.
- **수익 보장 없음.** 검증된 엣지도 OOS 실패 가능.
- 대규모 유니버스: 종목별 pykrx/DART 실패는 fail-open(해당 종목만 비움)으로 처리됨 — 전체 크래시 안 함.

---

## 7. 지켜야 할 조건 (CONSTRAINTS — 레포 CLAUDE.md)

- **Decimal 전면(float 금지).** 금액·가격·팩터는 Decimal. (float 보조연산은 `str()` 경유 즉시 복귀.)
- **swing-bot 프로젝트 파일 무수정**(읽기전용 참고만).
- **scoring.py 무수정** — 하니스는 `factors.build_candidate`(추출)로 라이브 점수 재사용. 점수 로직 변경 시 라이브 회귀(231 테스트) 확인.
- **시크릿(.env, DART_API_KEY, KIS 키) 커밋 금지.**
- 한글 프론트 문자열은 `frontend/scripts/gen-i18n.mjs` 단일출처(백엔드 무관).
- **가중치는 prior+범위** — 최종 단일값은 백테스트 게이팅으로만(MAE↓·단조성↑일 때 채택, 아니면 롤백). **수익률 목표(예: 연 20%)를 좇아 점수를 맞추지 말 것 = 과최적화. OOS 검증 필수.** (§3.5 참조)
- DoD: `uv run pytest` + `ruff check` + `ruff format` + `mypy backend/`.
- 커밋: Conventional Commits(`feat:`/`fix:`/`docs:`/`refactor:`/`style:`), 한국어 본문 OK.
- **데이터 산출물(`data/backtest/`)은 커밋 금지** — 생성물. (`.gitignore`에 추가 권장.)

---

## 8. git 상태

- `main`이 `origin/main`(d8487e2)보다 **20커밋 ahead, LOCAL only** → **push 필요**.
- 작업트리: `data/backtest/`(리포트 산출물·미추적), `.omc/`(툴 상태·미추적) — 둘 다 커밋 대상 아님.
- 최근 커밋: `da40cfb`(lint) ← … ← `6012e12`(factors 추출) ← `fbeadf4`(리웨이트 스펙). `git log --oneline -20`.
