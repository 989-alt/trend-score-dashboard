# HANDOFF — trend-score-dashboard 매매로직 재설계 + 검증 인프라 + 알파 발견

> 갱신 2026-06-17 (세션 3, 4F 전담실 PC·subagent-driven). **다른 기기 이어작업용 단일 문서.** 레포 루트.
> 레포: github.com/989-alt/trend-score-dashboard · 작업 브랜치 **`feat/validation-oos-entry-bias`**

---

## ▶ RESUME HERE — 다른 기기(KRX 가능 머신)에서 시작

**"클론만 하면 되나?" → 클론 + 3가지:**
| 클론으로 오는 것 ✅ | 클론에 **없는** 것 (추가 필요) |
|---|---|
| 코드·스펙·구현계획·이 `HANDOFF.md` | ① **uv + `uv sync`**(Python 3.13 환경) |
| 검증 인프라·테스트·entry_bias·compare 게이트 | ② **`.env` 시크릿**(gitignore — repo에 없음) |
| 브랜치 `feat/validation-oos-entry-bias` | ③ (선택) F: 연구문서 — 배경용, 실행엔 불필요 |

1. **이 PC에서 `git push` 완료됨**(아래 §8). 다른 기기:
   ```
   git clone https://github.com/989-alt/trend-score-dashboard.git
   cd trend-score-dashboard && git checkout feat/validation-oos-entry-bias
   ```
2. 환경: **uv 설치**(`winget install astral-sh.uv` 또는 `irm https://astral.sh/uv/install.ps1 | iex`) → `uv sync`(Python 3.13). 검증 `uv run pytest -q` → **321 passed, 1 skipped** 이면 정상.
3. **시크릿 `.env` — 사용자가 프로젝트 루트에 직접 배치**(gitignore라 repo에 없음·커밋 안 됨): `DART_API_KEY`(필수), **KRX 머신이면 `KRX_ID`/`KRX_PW`**(이 PC선 네트워크 차단으로 무용, KRX 머신선 가치렌즈 부활에 사용). 백테스트 `main()`은 `DART_API_KEY`를 **OS 환경변수**로 읽음(`.env`도 pydantic 로드).
4. 레포 밖 연구문서(별도 전송, git에 없음): **`F:\바이브코딩\trend-score-research\`** (`STRATEGY_REFERENCE.md`=팩터 메뉴, `PROGRESS_AND_ROADMAP.md`, `matdongsan_report.md`, `quantlab_js/`).
5. **현재 위치 한 줄:** 검증 인프라(Layer A/B/C+게이트) 완성·리뷰 완료. entry_bias 수정안은 **실데이터 엄격게이트 FAIL**(단조성 무개선; extension_guard만 5d MAE 유의 축소). 다음 방향=**선택 알파 발견 엔진**(스펙+계획 작성 완료) → §4.

---

## 1. 지금까지 (DONE)

목표: 대시보드 매수추천 점수 재설계. 통증: *"추천 종목을 사면 매수 직후 떨어진다."*

**세션 1–2 (이전):** 리웨이트 근거 스펙 + 백테스트 하니스 v1(`backend/factors.py`+`backend/backtest/*`) + 실데이터 베이스라인 측정 → **현 점수 단조성≈0(예측력 없음) 진단 확정**. ⚠ ralph "연20% 튜닝" 시도→곡선맞춤이라 중단(§3.5).

**세션 3 (오늘, 이 브랜치 11+커밋):** 각 태스크 구현→스펙리뷰→코드품질리뷰 2단계 + 실버그 수정.
1. **검증 인프라 3계층** (스펙 `docs/superpowers/specs/2026-06-17-validation-method.md`):
   - **T1 계측기 정확성**(`tests/test_backtest_validation.py`): 합성 신호회복 mono≈1·노이즈0·룩어헤드 카나리아.
   - **T2 유의성**(`metrics.py`): 날짜블록 부트스트랩 CI·퍼뮤테이션 p·`paired_diff_ci`.
   - **T3 OOS 워크포워드**(`run.py`): 앵커드 train/test+홀드아웃, `--walk-forward`.
2. **T7 로더 yfinance 전환**(`cab6271`): 이 PC가 `data.krx.co.kr` SSL 차단(학교 방화벽)→pykrx 불가 → KR OHLCV를 yfinance `.KS/.KQ`로. (라이브 KIS 무관.)
3. **T4 프리셋 비교 게이트**(`compare.py`, `--compare`): 동일 OOS 날짜서 두 프리셋 paired Δ단조성·ΔMAE 유의성.
4. **entry_bias 프리셋**(`ea5c2a3`, 백테스트 전용·라이브 무수정): pullback_3pos+extension_guard+near_52w .30→.18.
5. **선택 알파 발견 — 스펙+구현계획 작성**(이 핸드오프의 핵심 다음 단계): §4·§7.

**321 passed, 1 skipped · ruff/mypy clean.**

---

## 2. 핵심 발견 (FINDINGS)

**베이스라인(43종목·2020~24·monthly):** 이벤트스터디 단조성 5d −0.019 / 20d +0.019 / 60d −0.025 ≈ **0 → 현 점수는 forward-return 예측 못 함**. MAE −4%→−14%(매수후 역행). ⚠ 포트폴리오 +50%는 점수실력 아닌 **테마+생존편향**(단조성0이 증거) → **이벤트스터디가 포트폴리오 수익보다 신뢰지표**.

**entry_bias 실데이터 판정(24종목·2019~24·monthly, OOS 46날짜·N=316):**
| 호라이즌 | Δ단조성 (vs base) | ΔMAE (개선=양수) | 게이트 |
|---|---|---|---|
| 5d | +0.005 (CI 0포함) | **+0.0024, CI[+0.0009,+0.0039] 유의** | MAE✅ mono✗ |
| 20d | −0.044 (노이즈) | +0.0027 (비유의) | ✗ |
| 60d | −0.047 (노이즈) | +0.0020 (비유의) | ✗ |
- **결론: 엄격게이트 전체 FAIL.** `extension_guard`(과열회피)만 **5일 매수후 낙폭을 유의하게 축소**(설계대로) — 작지만 검증된 win. 그러나 리웨이트로 **선택 예측력(단조성)은 안 생김**(§3.5 부합). **현 파라미터 entry_bias 채택 안 함**(라이브 무수정 유지).
- top_n=20에선 적격≤15<20이라 두 프리셋 픽이 동일→MAE 구조적 0(테스트 불가)이었음 → top_n=5로 재검해 위 표 확보. **유니버스가 작으면 MAE 검정 불가** → 알파 검증엔 넓은 유니버스 필수.

---

## 3. ⚠ 절대 법칙 — 과최적화 금지 (§3.5, 변함없음)
- **수익률 목표(예: 연 N%)에 맞춰 파라미터 튜닝 = 곡선맞춤.** 신호 없는 팩터는 리웨이트로 신호 안 생김(측정으로 확인됨). 점수는 *이론으로 설계·OOS로 검증*, 채택 기준은 CAGR가 아니라 **OOS 단조성·MAE의 통계적 유의 개선**. 변종 다수 시도 시 다중검정 보정·홀드아웃 1회.

---

## 4. 다음 — 선택 알파 발견 엔진 (NEW DIRECTION, 사용자 확정)

**성공기준(확정): 선택 알파 우선**(OOS 단조성 유의 양수) → **안 되면 위험조정 폴백**(KOSPI 대비 초과+낮은 MDD).

- **스펙:** `docs/superpowers/specs/2026-06-17-alpha-discovery-design.md`
- **구현계획:** `docs/superpowers/plans/2026-06-17-alpha-discovery-plan.md` ← **여기부터 실행**(subagent-driven 권장).
- **요지:** 넓은 KR 유니버스(상위 ~500)에서 후보 팩터 ~12–15개를 **개별 OOS 단조성으로 호스레이스 → BH-FDR 다중검정 보정 + 홀드아웃 재확인 → 승자만 rank-z 등가중 합성(`alpha_composite` 프리셋) → OOS `paired_diff_ci`로 vs baseline 유의 검증.** 라이브 스코어러 무수정.
- **팩터 풀:** 가격(trend_template·ma_alignment·mom_12_1·rs_rank·vcp·volume_surge·near_52w·pocket_pivot) + 펀더(gross_profitability=Novy-Marx 1순위·roe·op_margin·rev_growth) + **(KRX 머신) per·pbr 가치**.

### ★ KRX 머신에서 달라지는 것 (계획 Phase 0 Task 0)
| 항목 | 4F PC(차단) | **KRX 머신(권장)** |
|---|---|---|
| 유니버스 목록 | DART corpCode→yfinance 거래대금 랭킹 | pykrx `get_market_ticker_list` 직접 |
| KR OHLCV | yfinance `.KS/.KQ`(`cab6271`) | pykrx OHLCV(거래대금 정확) 또는 yfinance 유지 |
| **PER/PBR 가치** | **불가** | **pykrx `get_market_fundamental_by_date` → 가치 렌즈 부활, per/pbr 팩터 추가** |
로더 인터페이스(`PanelLoader.build`→`Panel`)는 동일하므로 상위 파이프라인은 데이터소스 무관. KRX 머신이면 `_ohlcv`를 pykrx로 되돌리거나(이전 버전=`cab6271` 직전) 병행. **가치 팩터를 호스레이스 풀에 꼭 포함**시킬 것.

---

## 5. 실행 방법 (HOW TO RUN)
```bash
uv sync
uv run pytest -q                 # 321 passed,1 skipped
uv run ruff check ; uv run ruff format --check ; uv run mypy backend/   # DoD

# 단일 백테스트 (DART 키 OS 환경변수)
DART_API_KEY=<키> uv run python -m backend.backtest.run \
  --start 2019-01-01 --end 2024-12-31 --rebalance monthly --top-n 5 \
  --tickers 005930,000660,... --preset baseline      # baseline|quality_tilt|entry_bias
# 워크포워드 OOS:  --walk-forward [--n-folds 4 --holdout-frac 0.2]
# 프리셋 비교 게이트:  --compare entry_bias  (variant vs baseline, paired Δ CI)
# 리샘플 속도조절:  --n-resamples 200 --n-perms 200
```
> 이 PC: uv가 PATH에 없어 `C:\Users\4F 전담실\.local\bin\uv.exe` full path 사용. 새 기기는 셸 재시작 후 `uv` 정상.

---

## 6. 데이터 지뢰 (CAUTIONS — 이미 밟음)
1. **이 PC `data.krx.co.kr` SSL 차단(SSLEOFError)** — pykrx 전부 실패(로그인까지 가나 연결 끊김). KRX_ID/KRX_PW 있어도 네트워크 문제라 무용. → yfinance/DART로 우회(T7). **KRX 머신엔 해당 없음.**
2. **DART `list.json`은 bsns_year/reprt_code 미제공** → `report_nm "(YYYY.MM)"` 파싱(`dart_client._period_from_report`).
3. **yfinance KR**: `.KS`(KOSPI)/`.KQ`(KOSDAQ), 거래대금 컬럼 없음→`종가×거래량` 프록시(`loader._rows` 폴백). 조정종가 주의(현재 `auto_adjust=False`).
4. **룩어헤드**: 재무 접수일 as-of, 가격 ≤T 슬라이스, forward-return T+1 이후만(구현·테스트됨).
5. **생존편향**: 현-상장 근사 → 포트폴리오 수익 낙관편향. baseline vs 변형 A/B는 동일 유니버스라 상대비교 공정.
6. **품질빚**: entry_bias 테스트 일부가 합성패널 미적격 시 `pytest.skip()`(1 skipped) — 실검증 안 함, 정리 필요. `compare.py`는 신규라 합성테스트만 거침.

---

## 7. 레포 규율 (CONSTRAINTS — CLAUDE.md)
- **Decimal 전면(float 금지).** float 보조연산은 `str()` 경유 즉시 복귀.
- **라이브 무수정**: `scoring.score_candidates`·`engine`·`schemas` 변경 금지(알파 로직은 백테스트 프리셋으로). swing-bot 파일 무수정.
- **시크릿 커밋 금지**(`.env`, KIS/DART/KRX 키). 데이터 산출물 `data/backtest/` 등 커밋 금지(gitignore).
- DoD: `uv run pytest`+`ruff check`+`ruff format`+`mypy backend/`.
- 커밋: Conventional Commits, 한국어 본문 OK.
- **§3.5 과최적화 금지** — 채택은 OOS 유의 개선일 때만, 아니면 롤백.

---

## 8. git 상태
- 브랜치 **`feat/validation-oos-entry-bias`** — `origin/main` 대비 다수 커밋. **이 PC에서 push 함**(다른 기기서 clone+checkout 가능). main 머지는 알파 검증 후 결정.
- 최근 핵심 커밋: `a2551f1`(알파 발견 스펙) · `69f70f0`(compare 게이트) · `cab6271`(yfinance 전환) · `ea5c2a3`(entry_bias) · `c879ee7`(T3 리뷰) · `eec84fb`(T2) · `dccd724`(T1). `git log --oneline origin/main..HEAD`.
- 작업트리: `.claude/`·`data/` 산출물은 미추적(커밋 대상 아님).
