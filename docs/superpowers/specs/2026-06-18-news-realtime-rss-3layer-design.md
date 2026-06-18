# 실시간 뉴스 강화 — 1분 수집 · 국내외 RSS · 3-레이어 랭킹 · 가독성 (설계)

> 작성 2026-06-18 (세션6, /brainstorming). 기존 "시황 탭"(`2026-06-18-news-collection-situation-tab-design.md`)
> 위에 4가지 개선을 얹는다. **전부 ₩0·LLM 0·매매 무영향(읽기전용)** 경계 유지.

---

## 0. TL;DR

| | 개선 | 핵심 |
|---|---|---|
| A | **수집 주기 단축** | 텔레그램 **1분** / RSS **3분** (config) |
| B | **가독성** | `clean_text` 표시 + 휴리스틱 **대표 한 줄** + 종목명·%·심각도어 **하이라이트** |
| C | **국내외 RSS 크롤링(신규)** | 피드 → `news_raw` 합류 → 기존 클러스터링 그대로 |
| D | **3-레이어 랭킹** | **국내(KR)/미국(US)/종합(거시)** `issue.market` 기준 각자 순위 |

- **Gemini 요약 미채택**(사용자 확정) — 실시간 경로 ₩0·LLM 0 유지. 주간 매크로(FactSet+Gemini)는 기존 그대로.
- 경계: 뉴스는 **매수 점수/엔진에 미반영**(검증 전). 본 설계는 데이터 인프라 + 읽기전용 디스플레이.

---

## 1. A — 수집 주기

- `config.py`: `news_poll_interval_min: int = 1`(텔레그램), `news_rss_interval_min: int = 3`(RSS).
- `scheduler.py`: 기존 하드코딩 `IntervalTrigger(minutes=5)` → `news_poll_interval_min`. 신규 `news-rss` 잡(`news_rss_interval_min`).
- 텔레그램은 커서 catch-up(`collect_once`)이라 1분 = 채널당 분당 1호출 → **flood 무관·₩0**. RSS는 사이트 예의상 3분(피드는 자주 안 바뀜).

## 2. C — 국내외 RSS 크롤링

- 신규 `backend/news/rss.py`: `httpx` GET(타임아웃·UA) → `feedparser` 파싱. **피드별 fail-open**.
- `data/news_sources.yml`(운영자 편집형): `{name, url}` 목록.
- `RawNewsItem` 재사용으로 `news_raw`에 합류 — `source="rss"`, `channel=피드명`, `msg_id=crc32(article_url)`(중복제거 PK), `ts_utc=발행시각`(없으면 now), `text=제목`(+요약 일부), `urls=[기사url]`.
- `collect_rss_once(store, settings)`: 피드 순회 → `store.insert_raw`. 신규 건수 반환.
- **클러스터링 자동**: `recent_raw`에 포함 → `build_issues`가 KR+US 스냅샷 종목명으로 묶음. **국내 기사=KR 종목, 영문 기사=US 종목명** 매칭.
- **추천 시작셋**(config 교체 가능): 국내 = 매경 증권·한국경제·연합 경제·이데일리 증시(이전 실호출 확인). 해외 = MarketWatch·CNBC·Investing.com. FactSet Insight는 기존 주간 매크로 유지(중복 회피).
- 빌드 시 피드 도달성 실호출 확인 후 확정.

## 3. B — 가독성 (₩0, LLM 0)

- `clean_for_display(text)`: 기존 `clean_text`(URL·해시태그·보일러플레이트 마커·노이즈 이모지 제거) 재사용/경량화 — **문장은 보존**.
- **이슈 대표 한 줄(headline)**: 이슈 구성 메시지 중 대표(최신 또는 가장 정보량 큰 정리본) 1줄을 추출.
- `api_models.NewsIssue` += `headline`(정리된 대표 한 줄). `NewsMessage.text`는 정리본 노출.
- **프런트 하이라이트**: 종목명·`%`변동·심각도어를 `<mark>`로 강조(클라이언트 토큰 매칭, i18n 무관).

## 4. D — 3-레이어 랭킹

- **레이어 = `issue.market`**: `KR`→국내, `US`→미국, `None`(심각도/거시 키)→종합.
- `build_issues`는 이미 market 부착(통합 PR#3). `/api/news/issues`에서 이슈를 market으로 그룹핑 → `{domestic, us, macro}` 각 **urgency 순 Top N**(`news_top_n_per_layer`, 기본 10).
- `api_models.NewsIssuesResponse`: 3 리스트(`domestic`/`us`/`macro`)로 반환(프런트 그룹핑보다 명확·each 충분한 N 확보).
- **프런트**:
  - **시황 탭**: 국내/미국/종합 **3 섹션**, 각 순위 + 대표 한 줄·하이라이트.
  - **플로팅 레일(타 탭)**: **3-탭 토글**(국내/미국/종합), **종합 기본 활성**, 선택 레이어 Top N. 클릭→시황 탭 해당 레이어·이슈로 점프.

## 5. 데이터 흐름

```
[텔레그램 1분] + [RSS 3분]
        └─> news_raw (원문·dedup·₩0)
                └─(read-time)─> build_issues(스냅샷 KR+US명 + 심각도사전)
                        └─> market 그룹핑 → 3 레이어(국내/미국/종합) 각 Top N + 대표 한 줄
                                └─> /api/news/issues (3 리스트)
                                        └─> 시황 탭 3섹션 / 레일 3-탭(종합 기본)
```

## 6. 한계 / 정직

- **해외 영문기사의 한국 종목 언급**(예: 영문 "Samsung")은 영문→한글 별칭사전이 없으면 국내 레이어에 미매칭 → **별칭 매핑은 선택적 후속**(YAGNI).
- 한국어 심각도어는 영문 기사에 미적용(영문은 종목 매칭만).
- RSS 사이트 예의: 3분·UA·fail-open. 죽은 피드는 조용히 skip.
- 매매 무영향·실시간 ₩0·LLM 0 불변.

## 7. 파일

- **신규**: `backend/news/rss.py`, `data/news_sources.yml`, `tests/test_news_rss.py`, (프런트) 시황 3섹션·레일 3-탭.
- **수정**: `config.py`(간격·rss경로·layer N), `scheduler.py`(poll 간격·rss 잡), `backend/news/issues.py`/`app.py`(레이어 그룹핑+대표 한 줄), `api_models.py`, `NewsView.tsx`, `IssueRail.tsx`, `types.ts`, `api.ts`, `gen-i18n.mjs`(레이어 라벨).
- **DoD**: `uv run pytest`·ruff·mypy + 프런트 build·vitest. Playwright로 3섹션·레일 토글 육안 검증.
