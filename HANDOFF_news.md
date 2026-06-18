# HANDOFF (뉴스) — 뉴스 수집 + 시황 탭

> 작성 2026-06-18. **다른 기기에서 이어 작업용 단일 문서.** 레포 루트에 있다.
> 레포: github.com/989-alt/trend-score-dashboard · main 에 병합·푸시 완료.
> (백테스트/매매로직 트랙은 별도 `HANDOFF.md` 참고 — 이 문서는 *뉴스 기능* 전용.)

---

## ▶ RESUME HERE — 한 줄 상태 + 다음 행동

- **상태:** 뉴스 수집 + 대시보드 "시황" 탭 기능 **100% 구현·테스트·실검증·main 병합·프런트(Pages) 배포 완료.**
  **남은 작업은 단 1건 — OCI 백엔드 배포**(서버에 새 코드 반영). 그것만 하면 라이브 시황 탭이 동작한다.
- **왜 아직 안 됐나:** OCI 서버 SSH 키가 *인터넷 안 되는 다른 컴퓨터*에 있어 접속을 못 했다. → 키를 USB로 옮기면 됨.
- **배포 방법(상세, copy-paste):** `C:\Users\Public\OCI_deploy_guide.md` (이 기기 로컬, git 아님). 아래 §2에 요약.
- **다른 기기에서 시작:** `git clone …/trend-score-dashboard` → `uv sync` → (로컬 개발 시) `.env`에 키 + 세션(§4).

---

## 1. 지금까지 한 것 (DONE)

대시보드(989-alt.github.io/trend-score-dashboard)에 **뉴스 수집 + 시황 탭** 추가. 텔레그램·FactSet을 수집해
긴급도순 이슈를 보여주고 토요일 Gemini 주간요약을 제공. **경계: 점수/매매에 반영 안 함(검증 전).**

**플랜 A — 수집 백엔드** (`backend/news/`):
- `models.py`(RawNewsItem·FactsetArticle·WeeklySummary) · `store.py`(SQLite 4테이블·dedup·커서)
- `collector.py`(텔레그램 Telethon catch-up 폴링, 첫 폴링 시드 분리) · `factset.py`(**공식 RSS `insight.factset.com/rss.xml`** 파싱)
- `summary.py`(Gemini 주간 한국어 요약, fail-open) · 스케줄러 5분 폴링 + 토요일 잡(news_store 게이트) · app lifespan 배선
- **286 backend tests green** · ruff/mypy clean

**플랜 B — 시황 탭**:
- `issues.py`(clean_text·종목명[스냅샷 세트]+심각도사전[`data/news_severity_lexicon.yml`] 클러스터·긴급도 Decimal·₩0)
- `api_models.py` + app.py `/api/news/issues`·`/api/news/weekly`(면책 포함) · **점수 무영향 회귀(scoring/engine news 미import 정적검증)**
- 프런트 "시황" 탭: `NewsView.tsx`(+css)·`MarketTabs`·`App.tsx`·`api.ts`·`types.ts`·`gen-i18n.mjs`. **67 vitest + tsc 빌드 green**

**실 e2e 검증:** 텔레그램 4채널 실수집 107건 · FactSet RSS 10건 · Gemini 주간요약 실생성(국제유가·중동·FOMC 출처인용) ·
로컬 uvicorn+빌드SPA로 `/api/news/issues` 실데이터(SK하이닉스 3채널 최상위) · Playwright 스샷으로 탭 렌더 확인.

**배포 상태:** main 병합·푸시(`9985846`) 완료 → **GitHub Pages 워크플로 성공 → 프런트 라이브.**
(단 OCI 백엔드 미반영이라 현재 라이브 시황 탭은 `/api/news/*` 404 — §2가 그걸 닫는다.)

---

## 2. 남은 작업 — OCI 백엔드 배포 (유일한 미완)

- **서버:** IP `137.131.29.175` · user `ubuntu` · 경로 `/home/ubuntu/trend-score-dashboard` · 서비스 `trend-board.service`
- **선행:** OCI **SSH 개인키**가 있어야 SSH 접속 가능(오프라인 다른 PC에 있음 → USB로 이동). 키 찾는 법·접속법은 가이드 PART 1·2.
- **서버 안 5단계:**
  ```bash
  cd /home/ubuntu/trend-score-dashboard
  git pull
  uv sync                                  # 새 deps(telethon·google-genai) — 빠뜨리면 기동 실패
  # .env 에 3줄 추가(APP_API_ID/APP_API_HASH/GEMINI_API_KEY) — 값은 이 기기 repo .env 에 있음
  uv run python scripts/telegram_login.py  # 번호+코드 1회 → 서버에 telethon.session 생성
  sudo systemctl restart trend-board.service
  ```
- **검증:** 서버 `curl -s localhost:8000/api/news/issues` → 이슈 JSON. 브라우저 시황 탭 동작 확인.
- **그 뒤 자동:** 5분마다 실수집 · 토요일 오전 Gemini 주간요약.
- **상세 가이드:** `C:\Users\Public\OCI_deploy_guide.md`(키 검색·.ppk 변환·문제해결 포함). ⚠ 비밀키 값은 *이 기기* `trend-score-dashboard/.env`(git 아님)에만 있음 — 다른 기기로 갈 땐 본인이 옮기거나 재발급.

---

## 3. 그 다음 (LATER — 검증 게이트 뒤, 지금 범위 밖)

1. **뉴스→매매 반영은 안 함(현재).** 정직성 경계: *검증된 리스크오프만*, 전향(forward)검증 통과 후에만 매매에 닿음.
   지금 쌓는 아카이브가 그 전향검증의 연료(시점별 과거 뉴스는 살 수 없으므로 일찍 쌓을수록 빨리 검증).
   설계 출처: `docs/superpowers/specs/2026-06-18-news-riskoff-filter-design.md`.
2. **이슈 휴리스틱 정교화**(옵션): 한국어 형태소 분석 또는 LLM 클러스터링, 이슈 클릭 시 on-demand 요약.
3. **정적 데모 폴백**(옵션): `frontend/public/data/news-issues.json`·`news-weekly.json` 두면 `VITE_STATIC` 데모에서도 시황 탭 노출.

---

## 4. 다른 기기에서 로컬 개발·재현

```bash
git clone https://github.com/989-alt/trend-score-dashboard.git
cd trend-score-dashboard
uv sync                       # 백엔드(telethon·google-genai 포함)
uv run pytest -q              # 286 passed 면 정상
cd frontend && npm install && npm run build && npx vitest run   # 67 passed
```
- **DoD:** `uv run ruff check` · `uv run ruff format` · `uv run mypy backend/` (백엔드) / `tsc -b`(빌드가 게이트, lint 스크립트 없음).
- **로컬에서 실제 수집을 돌리려면:** repo `.env`에 `APP_API_ID`/`APP_API_HASH`(my.telegram.org) + `GEMINI_API_KEY` + `data/telethon.session`(최초 `uv run python scripts/telegram_login.py` 로 생성, 번호+코드 1회). 없으면 **fail-open으로 조용히 스킵**(테스트는 키 없이 green).

---

## 5. 파일 맵

- **백엔드:** `backend/news/{models,store,collector,factset,summary,issues,api_models}.py` + 수정 `backend/{config,scheduler,app}.py` + `data/news_severity_lexicon.yml` + `scripts/telegram_login.py`
- **프런트:** `frontend/src/components/NewsView.tsx`(+`.module.css`) + 수정 `MarketTabs.tsx`·`App.tsx`·`api.ts`·`types.ts`·`scripts/gen-i18n.mjs`(→`ko.json`)
- **문서:** specs `2026-06-18-news-collection-situation-tab-design.md` · plans `2026-06-18-news-collection-backend.md`·`2026-06-18-news-situation-tab.md` (모두 `docs/superpowers/`)

---

## 6. 주의·함정 (재발 방지)

- **시크릿 커밋 금지:** `.env`·`data/telethon.session`·`data/news.db` 전부 gitignore(확인됨). 다른 기기엔 본인이 옮김.
- **`uv sync` 필수**(서버 배포 시) — telethon·google-genai 안 깔리면 기동 실패.
- **라이브 스코어러 무수정** 규율: `scoring.py`·`engine.py`·`schemas.py` 점수 산출 불변. 뉴스는 점수에 안 닿음.
- **FactSet = 공식 RSS**(`rss.xml`) 사용 — 목록 HTML 스크래핑은 폐기(마크업 취약). RSS가 견고.
- **Telethon 첫 폴링:** cursor=0이면 최신 SEED_LIMIT(30)만 시드(과거 전체 역주입 방지).
- **Windows 콘솔 cp949 크래시:** 한글/이모지 출력 시 `PYTHONIOENCODING=utf-8`.
- **한글 홈 경로(`4F 전담실`) 인코딩 버그:** 파일 생성 시 한글 경로가 깨질 수 있음 → 로컬 산출물은 ASCII 경로(`C:\Users\Public\`) 권장.
- **테스트 `.env` 누출:** repo `.env`에 실키가 있어 테스트는 `Settings(_env_file=None)` 로 격리(아니면 "키 없음" 테스트가 깨짐).
- **asyncio_mode=auto / ruff select(E,F,I,UP,B,N,SIM,RUF)·line100 / mypy strict** — 미선택 규칙 noqa 금지, `getattr(x,"const")` 금지(B009), `from collections.abc import Callable`(UP035).
