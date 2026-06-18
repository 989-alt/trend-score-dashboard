# 뉴스 수집 + 대시보드 시황 탭 — 설계 (News Collection & Situation Tab)

> 작성 2026-06-18 (세션5). 출발: "텔레그램·뉴스사이트(FactSet)를 연결해 뉴스 수집, 대시보드에 긴급도순
> 이슈 사이드바, 종목 평가에도 반영". 사용자 우선순위 = **신뢰성·정직성**.
> 이 스펙은 기존 `2026-06-18-news-riskoff-filter-design.md` §8의 **하위 프로젝트 #2(수집 파이프라인)** 를
> 착수하는 것 + 그 위에 **사람이 읽는 시황 뷰**를 얹는 것이다. **매매 무영향**(점수 미반영)이 핵심 경계.
> ⚠ 원칙(§3.5): 수익률 튜닝 금지. 뉴스→매매 반영은 *전향검증 게이트 뒤*에서만(본 스펙 범위 밖).

---

## 0. TL;DR

- **만드는 것:** ① 텔레그램 4채널 + FactSet Insight를 수집해 원문 아카이브(SQLite) ② 대시보드 **"시황" 탭** —
  긴급도순 **Top10 이슈 사이드바**(클릭 → 상세 원문 패널) + **주간 매크로(FactSet) 패널** ③ **주간(토요일)
  한국어 요약**은 **Gemini API**로.
- **안 만드는 것(경계, 사용자 확정):** 뉴스를 **매수 점수/엔진에 반영하지 않는다.** 매매 반영(검증된
  리스크오프)은 *아카이브를 전향으로 쌓아 검증 통과한 뒤*의 별도 프로젝트. 본 스펙은 **데이터 인프라 +
  읽기 전용 디스플레이**까지.
- **비용:** 실시간 사이드바 = **₩0 휴리스틱**(LLM 0, 5분 폴링). 주간 요약 = **주 1회 소액 Gemini**.
  (프로젝트가 "LLM 미사용·₩0"에서 *주 1회 소액 LLM·읽기전용*으로 변경됨 — 매매 무관.)

---

## 1. 신뢰성·정직성 프레이밍 (왜 이 경계인가)

이 프로젝트의 출발 통증은 *"추천 종목을 사면 매수 직후 떨어진다"* 이고, 근본원인은 **검증 안 된 신호를
점수에 그냥 넣은 것**이었다. 뉴스는 *과거 백테스트가 불가능*(시점별 아카이브 부재 → 룩어헤드)하므로,
검증 전에 점수에 꽂으면 동일 실수를 반복한다. 그래서:

- **수집·디스플레이는 매매와 분리.** 이슈 사이드바는 *인지/스크리닝 보조*일 뿐 점수 숫자를 바꾸지 않는다.
- **매매 반영은 게이트 뒤.** 뉴스→리스크오프→매매는 (a)액션 백테스트 + (b)탐지기 전향검증을 통과한 뒤
  (`2026-06-18-news-riskoff-filter-design.md` (3)). 본 스펙이 쌓는 아카이브가 그 전향검증의 *연료*다.
- **고지 필수.** 시황 탭에 기존 면책 + *"본 이슈는 가공 없는 집계 신호이며 검증되지 않았고 매수 점수에
  반영되지 않습니다."*

---

## 2. 아키텍처 (3개 부품, 매매와 분리)

```
[텔레그램 4채널]──5분 폴링(Telethon, catch-up)──┐
                                               ├──> news_raw (SQLite·원문·LLM0·₩0)
[FactSet Insight]──토요일 폴링(HTML scrape)─────┘            │
                                                            ├─> 이슈 클러스터+긴급도(휴리스틱·₩0, 읽을 때 계산)
                                                            │        │  /api/news/issues
                                                            │   ┌────┴─────────────────────┐
[Gemini API]──토요일 1회─────> weekly_summary(KR) ─────────┘   [대시보드 "시황" 탭]
                                                              Top10 사이드바 → 클릭 → 상세(원문·출처·링크)
                                                              + 주간 매크로(FactSet·KR요약) 패널
```

- **호스팅:** OCI 공유서버(대시보드 백엔드 FastAPI+APScheduler). 기존 30분 intraday 스케줄러에 **새 5분 잡** +
  **토요일 잡** 추가. 별도 서버·비용 없음.
- **데이터소스 무관 인터페이스:** 수집기는 `RawNewsItem`을 반환, 저장·이슈·요약은 소스(텔레그램/FactSet)를
  몰라도 됨.

---

## 3. 텔레그램 연결 (Telethon · MTProto 유저계정)

봇 API는 *자기가 admin인 채널만* 읽으므로 부적합. 기존 공개 뉴스 채널을 읽으려면 **본인 계정(MTProto)** 필요.

**대상 채널(사용자 제공·가입됨):** `FastStockNews`, `goodnews_honey`, `getfeed`, `jusikbiso` (증시속보·시황·뉴스).

**일회성 셋업(사용자 직접 1회):**
1. `my.telegram.org` → API development tools → `api_id`·`api_hash` 발급.
2. `.env`: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (gitignore — 절대 커밋 금지).
3. 최초 1회 번호+인증코드 로그인 → `data/telethon.session` 생성(이후 자동·재로그인 불필요).
   로컬 로그인 후 `.session`을 서버로 복사해도 됨. ⚠ 인증코드 입력은 번호 소유자(사용자)가 직접.

**수집 방식(catch-up 폴링, 5분):** 채널별 `last_seen_id` 보관 → `get_messages(channel, min_id=last_seen)` 로
*새 메시지만* fetch. 텔레그램이 채널 히스토리를 보존하므로 프로세스가 잠시 죽어도 **누락 0**(다음 폴링에서
따라잡음). 상시 접속 불필요 → 호스팅 단순.

**중복제거:** `(channel, msg_id)` 유일. 같은 뉴스의 교차채널 중복은 이슈 클러스터링에서 다룸(§5).

## 4. 저장 스키마 (SQLite · repo 규율: gitignore `data/`, Decimal where numeric)

- **`news_raw`** — id, source(`telegram`), channel, msg_id, ts_utc, ts_kst, text, urls(json), dedup_hash, fetched_at.
  PK/UNIQUE(source, channel, msg_id).
- **`news_factset`** — id, url(unique), title, published_at, excerpt, body, fetched_at.
- **`news_state`** — key(channel), last_seen_id, updated_at. (폴링 커서.)
- **`weekly_summary`** — id, week_start(date), kr_markdown, model, generated_at. (Gemini 산출, 주 1회.)
- **이슈는 테이블 없음** — `/api/news/issues` 요청 시 최근 24~48h 윈도우로 *읽을 때 계산*(캐시 짧게).

## 5. 긴급도 Top10 이슈 (₩0 휴리스틱 · 결정론 · 읽을 때 계산)

LLM 없이 근사. *v1 = "쓸 만한 스크리닝"* 수준(정교함보다 ₩0·결정성·디버그용이성 우선).

- **엔티티 태깅:** 메시지를 ① **KR 종목명 사전**(pykrx `get_market_ticker_name`, 대시보드가 이미 보유) ②
  **심각도 사전**(`data/news_severity_lexicon.yml` — 급락·서킷·사이드카·하한가·전쟁·디폴트·금리·환율급등·
  실적쇼크 등, 가중치 포함)에 매칭.
- **이슈 묶기:** 공유 키워드(종목명·핵심명사·심각도어)로 메시지 버킷팅(단순 토큰 자카드 유사도 임계).
  한국어 형태소분석 미사용(사전·n-gram 매칭) — 근사적임을 명시.
- **긴급도 점수:** `urgency = w1·교차채널_언급수 + w2·최신성(시간감쇠) + w3·속도(분당빈도) + w4·심각도가중`
  → 상위 10. 가중치는 **고정 표준값**(수익률 튜닝 아님 — 디스플레이 정렬용).
- **출력:** 이슈마다 {제목(대표 메시지/키워드), urgency, 구성 메시지 ids, 대표 종목, 첫/막 타임스탬프}.

## 6. FactSet 주간 패널 + Gemini 주간 요약

- **수집(토요일 잡):** `insight.factset.com` 목록 HTML scrape → 지난 7일 글(제목·링크·발췌·본문) →
  `news_factset`. 공식 RSS 없음 확인 → 목록 파싱. fail-open(스크랩 실패해도 탭 정상).
- **Gemini 주간 요약(토요일 1회):** 지난 7일 ① FactSet 글 + ② 텔레그램 아카이브 핵심 → **한국어 "이번 주
  시황" 마크다운** 생성 → `weekly_summary`. 모델 `GEMINI_MODEL`(기본 Flash급), 키 `GEMINI_API_KEY`(.env).
  프롬프트는 *사실 요약·출처 보존*에 한정(예측·매매조언 금지 — 면책과 일관).
- **표시:** 시황 탭 "주간 매크로" 패널 = Gemini 한국어 요약 + 원문 글 링크 리스트(영어 원문 보존).

## 7. API + 프런트 ("시황" 탭)

- **API(읽기):** `GET /api/news/issues`(Top10 + 각 구성 원문), `GET /api/news/issue/{id}`(상세),
  `GET /api/news/weekly`(최신 주간 요약). 모두 면책 meta 포함.
- **프런트:** 새 라우트/탭 "시황". 좌측 **Top10 사이드바**(긴급도순) → 클릭 시 **상세 패널**(묶인 텔레그램
  원문 + 채널·KST 타임스탬프 + 링크). 우측/하단 **주간 매크로 패널**(Gemini KR 요약 + FactSet 링크).
  5분 백엔드 갱신 + 프런트 폴링 = 24/7 근실시간. 한글 문자열은 `gen-i18n.mjs` 단일출처(`ko.json` 직접편집 금지).
- **면책 배지:** 탭 상단에 §1 고지문 상시.

## 8. 파일 · 인터페이스

| 파일 | 변경 |
|---|---|
| 신규 `backend/news/collector.py` | Telethon 클라이언트·5분 catch-up 폴링·`RawNewsItem` 산출·세션관리 |
| 신규 `backend/news/factset.py` | insight.factset.com 목록 scrape → `news_factset`(fail-open) |
| 신규 `backend/news/issues.py` | 엔티티 태깅·이슈 클러스터·긴급도 점수(₩0 휴리스틱·결정론) |
| 신규 `backend/news/summary.py` | Gemini 주간 한국어 요약(토요일·`GEMINI_API_KEY`·fail-open) |
| 신규 `backend/news/store.py` | SQLite 스키마·CRUD(news_raw/factset/state/weekly_summary) |
| 신규 `data/news_severity_lexicon.yml` | 심각도 키워드·가중치(편입기준 주석) |
| `backend/` 스케줄러·라우터 | APScheduler 5분/토요일 잡 등록 + `/api/news/*` 라우트(면책 meta) |
| 프런트 | "시황" 탭(사이드바·상세·주간패널·면책배지) + i18n 키 |
| `.env.example` | `TELEGRAM_API_ID/HASH`, `GEMINI_API_KEY/MODEL` 템플릿 |
| 테스트 | `tests/test_news_*.py` |

재사용: `DailyCache`/SQLite 패턴, pykrx 종목명, 면책 meta 규약, gen-i18n.

## 9. 테스트 (DoD: pytest·ruff·ruff format·mypy / 프런트 tsc·vitest)

- **collector:** catch-up 커서(min_id 이후만)·중복제거·세션 부재 시 fail-open(앱 기동 유지). Telethon은
  목(mock)으로 — 실네트워크 없이 결정론 테스트.
- **issues:** 합성 메시지셋 → 엔티티 태깅·클러스터·긴급도 순위가 결정론적(시드/고정 윈도우)으로 재현.
- **factset/summary:** scrape 파서 단위(고정 HTML 픽스처)·Gemini 호출 목(요약 텍스트 주입)·fail-open.
- **api:** 면책 meta 존재·점수 무영향(스코어러 회귀: 뉴스 모듈 추가가 기존 점수 산출 불변).
- **결정론·라이브 스코어러 무수정 회귀**. 시크릿 없이 `DATA_MODE=sample`에서 그린.

## 10. 범위 밖 (지금 안 함) · 후속

- **뉴스→점수/매매 반영** — 전향검증 게이트 뒤(별도 스펙·`riskoff` (a)+(3)).
- **LLM 실시간 클러스터링·번역** — 5분 사이드바는 ₩0 유지. 옵션 업그레이드로 보류.
- **US 뉴스 채널 / 텔레그램 외 소스 확장** — 후속.
- **이슈 클릭 상세의 on-demand Gemini 요약** — 후속 옵션(주간만 우선).

## 11. 리스크 · 정직성

- **점수 미반영 불변** — 디스플레이가 매매에 새는지 회귀 테스트로 차단.
- **휴리스틱 근사** — 한국어 형태소 미사용 → 이슈 묶기 거칠 수 있음. v1 한계 명시, 옵션 LLM 여지.
- **Gemini 비결정·비용·환각** — 주간·읽기전용·fail-open으로 한정. 요약 프롬프트는 사실·출처보존(예측 금지).
- **스크랩 취약성(FactSet)** — 목록 구조 변경 시 fail-open(탭은 텔레그램만으로도 동작).
- **시크릿** — `.env`(텔레그램·Gemini 키)·`data/telethon.session`·`data/*.db` 절대 커밋 금지(gitignore).
- **수익 보장 없음.** 본 스펙은 *인지 보조 + 전향검증용 아카이브*이지 매매 엣지 약속이 아니다.
