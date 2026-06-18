# 시황 탭 (이슈 휴리스틱 + API + 프런트) Implementation Plan (플랜 B)

> **For agentic workers:** REQUIRED SUB-SKILL: executing-plans. 체크박스로 추적. 플랜 A(수집 백엔드) 완료·실데이터 107건 확보 후 작성.
> **경계 불변:** 점수/매매 미반영(읽기 전용 디스플레이). 라이브 스코어러 무수정.

**Goal:** 플랜 A가 쌓는 아카이브 위에 **대시보드 "시황" 탭** — 긴급도 Top10 이슈 사이드바(클릭→원문 상세) + 주간 매크로(Gemini) 패널. ₩0 휴리스틱(결정론), 점수 무영향.

**Tech:** 백엔드 `backend/news/issues.py`(₩0 휴리스틱) + app.py 라우트 / 프런트 React+Vite+TS(탭 state SPA·CSS Modules·gen-i18n).

## Global Constraints (플랜 A와 동일 + 추가)

- 점수 무반영·라이브 무수정. **Decimal 전면**(긴급도/심각도 점수도 Decimal — float 금지).
- ruff(E,F,I,UP,B,N,SIM,RUF, line 100)·mypy strict(backend). 테스트 `Settings(_env_file=None)`.
- 프런트: **ko.json 직접편집 금지** → `gen-i18n.mjs` 수정 후 `node scripts/gen-i18n.mjs`. 빌드 `npm run build`(tsc -b + vite) + `npx vitest run`.
- 면책: 새 탭 상단 `DisclaimerBanner` 재사용 + 이슈 비검증 고지.

## 실데이터 기반 휴리스틱 설계 (107건 관찰)

- **보일러플레이트 제거 대상:** `✅`, `📝 핵심적 본문 요약`, `#pokara61`(해시태그), `📜원문보기📜`, `✨(In)sight`, `[✨ 리서치]`, 이모지, URL.
- **엔티티 = 종목명 + 심각도어.** 종목명은 **라이브 스냅샷(KR+US `ScoreEntry.name`) 세트**로 substring 매칭(₩0, pykrx 추가호출 없음). 심각도어는 `data/news_severity_lexicon.yml`.
- **이슈 = 같은 엔티티(종목/심각도어) 클러스터.** 한 메시지의 primary key = 매칭된 종목명(최장) 우선, 없으면 심각도어, 둘 다 없으면 미분류(랭킹 제외, 아카이브엔 남음).
- **긴급도(Decimal)** = `W_CHAN·교차채널수 + W_VEL·min(건수,5) + W_REC·최신성감쇠(48h) + W_SEV·최대심각도`. 고정 가중(표시 정렬용, 수익 튜닝 아님).

---

## File Structure

| 파일 | 책임 |
|---|---|
| `data/news_severity_lexicon.yml` (생성) | 심각도 키워드·가중(편입기준 주석) |
| `backend/news/issues.py` (생성) | clean_text·엔티티 태깅·클러스터·긴급도(순수·Decimal·₩0) |
| `backend/news/api_models.py` (생성) | NewsMessage·NewsIssue·NewsIssuesResponse·WeeklyResponse(pydantic) |
| `backend/app.py` (수정) | `/api/news/issues`·`/api/news/weekly` 라우트 + lifespan 심각도 로드 |
| `frontend/src/types.ts` (수정) | News 타입 |
| `frontend/src/api.ts` (수정) | fetchNewsIssues·fetchNewsWeekly |
| `frontend/src/components/MarketTabs.tsx` (수정) | "news" 탭 |
| `frontend/src/App.tsx` (수정) | news 탭 렌더·폴링 |
| `frontend/src/components/NewsView.tsx (+.module.css)` (생성) | Top10 사이드바·상세·주간패널 |
| `frontend/scripts/gen-i18n.mjs` (수정) | 한국어 문자열 |
| `tests/test_news_issues.py`·`test_news_api.py` (생성) | 휴리스틱·라우트·점수무영향 |

---

### Task B1: 심각도 사전 + issues.py (휴리스틱)

**Interfaces (Produces):**
- `load_severity(path: Path) -> dict[str, Decimal]`
- `clean_text(text: str) -> str`
- `Issue`(frozen dataclass): `key:str, title:str, urgency:Decimal, channels:tuple[str,...], severity:Decimal, count:int, last_ts:datetime, item_keys:tuple[tuple[str,int],...]`
- `build_issues(items: list[RawNewsItem], stock_names: set[str], severity: dict[str,Decimal], *, now: datetime, top_n: int = 10) -> list[Issue]`

**구현 요지:**
- `clean_text`: 정규식으로 URL·이모지·해시태그·정의된 마커 문자열 제거, 공백 정리.
- `primary_key(clean, stock_names, severity)`: 최장일치 종목명 → 없으면 등장 심각도어(최고가중) → 없으면 None.
- `build_issues`: items별 primary_key로 그룹 → 그룹별 urgency(Decimal) 계산 → top_n 정렬. tie-break은 last_ts 내림차순(결정론).
- 가중 상수: `W_CHAN=Decimal("2.0")`, `W_VEL=Decimal("1.0")`(count cap 5), `W_REC=Decimal("1.5")`(48h 선형감쇠), `W_SEV=Decimal("2.0")`.

**테스트(합성 RawNewsItem):** 종목 클러스터·교차채널 가중·심각도 가중·최신성·top_n·결정론 재현. clean_text가 마커/URL 제거. 종목 없는 메시지 미분류.

DoD: pytest·ruff·mypy + commit.

### Task B2: API 모델 + 라우트 (점수 무영향)

**Interfaces (Produces):**
- `backend/news/api_models.py`: `NewsMessage(channel,ts_kst,text,urls)`, `NewsIssue(key,title,urgency,channels,severity,count,last_ts,messages)`, `NewsIssuesResponse(generated_at,disclaimer,issues)`, `WeeklyResponse(week_start,kr_markdown,generated_at,disclaimer)`.
- app.py: `GET /api/news/issues`(48h 윈도우·스냅샷 종목명·build_issues→매핑), `GET /api/news/weekly`(latest_weekly). 둘 다 `DISCLAIMER` 포함. lifespan에서 `app.state.severity = load_severity(...)`.

**테스트(TestClient):** `/api/news/issues`·`/api/news/weekly` 200·disclaimer 존재·이슈 정렬. **점수 무영향 회귀:** `backend/scoring.py`·`engine.py` 가 news 모듈을 import 안 함(정적 확인) + 기존 snapshot 테스트 불변.

DoD: pytest·ruff·mypy + commit.

### Task B3: 프런트 "시황" 탭

- `types.ts`: Raw/정규화 News 타입(api.ts 파서).
- `api.ts`: `fetchNewsIssues`·`fetchNewsWeekly`(+URL, API_BASE/정적데모 분기).
- `MarketTabs.tsx`: `TabKey += "news"`, TABS += `{key:"news",labelKey:"tab.news"}`.
- `App.tsx`: `tab==="news"` 시 `NewsView` 렌더 + `usePolling(fetchNewsIssues, enabled: tab==="news")`(+weekly 1회/동일 폴).
- `NewsView.tsx`+css: 좌측 Top10 사이드바(긴급도순·클릭 select) → 우측 상세(선택 이슈의 원문 메시지·채널·KST·링크) + 하단/우측 주간 매크로 패널(Gemini md 렌더). 상단 비검증 고지 배지.
- `gen-i18n.mjs`: `tab.news`,`news.*` 문자열 추가 → `node scripts/gen-i18n.mjs`.

**검증:** `npm run build`(tsc+vite) 통과 + `npx vitest run` + 로컬 백엔드 띄워 `/api/news/*` 응답 확인(수동).

DoD: 빌드 통과 + commit.

### Task B4: 통합 검증

- 백엔드 `uv run pytest`(전체)·ruff·mypy 전부 green(회귀 0).
- 프런트 빌드 green.
- 로컬 end-to-end: uvicorn + 프런트 dev 로 "시황" 탭에 실제 이슈/주간요약 노출 수동 확인.
- commit + (선택) 데모 데이터 `public/data/news-*.json`(정적 폴백).

---

## 범위 밖 (플랜 B 이후)
- 뉴스→점수 반영(전향검증 게이트 뒤), LLM 실시간 클러스터링, 한국어 형태소 분석, 이슈 상세 on-demand 요약.
