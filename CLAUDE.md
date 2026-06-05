# CLAUDE.md — Trend Score Dashboard (프로젝트 메모리)

> 글로벌 `~/.claude/CLAUDE.md` 와 병합되어 세션 시작 시 로드됨. 비밀키·서버 IP 등 시크릿은
> 여기 적지 않는다(`.env`/토큰은 gitignore). 이 repo 는 **public**.

## 무엇 / 왜

- 추세추종(맛동산/Sperandeo) **매수추천 스코어 대시보드**. swing-bot 의 매매 로직만 **읽기전용
  참고**해 독립 재구현(봇 파일 무수정, 의존성 0).
- KR(국장)·US(미장) 종목 매수추천도 **0~100 점수 + 등급** + **테마별 주도주**. 30분 자동 갱신.
  손절 이탈 시 **'매도 요구'**(매도판단은 매수 점수와 독립 축).
- 공개: <https://989-alt.github.io/trend-score-dashboard/> — GitHub Pages(정적 SPA)가 OCI 백엔드를
  라이브 fetch.
- **면책 필수 문구**(헤더·푸터·드로어·API meta 상시): "본 대시보드는 투자 자문에 해당하지
  않으며, 투자의 판단과 결정은 철저히 개인에게 있습니다."

## 스택 / 구조

- 백엔드: Python 3.13, **FastAPI + uvicorn**(단일 프로세스 = `/api` + 빌드된 SPA 서빙),
  **APScheduler**(일일 prep + 30분 intraday), pydantic v2, SQLite, **Decimal 전면**(float 금지).
  패키지=`uv`.
- 프론트: **React + Vite + TS**, CSS Modules, i18n. 한글 문자열은 `frontend/scripts/gen-i18n.mjs`
  **단일 출처**에서 `src/i18n/ko.json`(`\uXXXX`)로 생성 — **ko.json 직접편집 금지**(재생성 시
  덮어씀). 변경 후 `node scripts/gen-i18n.mjs`.
- 배포 2형태: (a) OCI 단일프로세스(API+SPA 동일 출처), (b) **GitHub Pages(정적) + OCI 백엔드
  라이브 fetch** ← **현재 운영**.

## 점수 모델 (`backend/scoring.py`, `config.py`, `engine.py`)

- `score(0~1) = near_52w·0.30 + pocket_pivot·0.20 + momentum_norm·0.13 + rs_norm·0.12
  + turnover_norm·0.15 + vol_fit·0.10` (합 1.0) → ×100.
- **하드필터**: 거래대금 ≥ (KR 100억 / US 3천만$) · 모멘텀 ≥ 0 · 200일선 위 · 변동성 밴드
  [0.20,0.60] (Gap B: 52주 신고가 근접 ≥ 0.90 이면 변동성 **상한** 면제).
- 정규화는 **적격(통과) 종목만** cross-sectional min-max (부적격이 min/max 오염 방지).
- 등급: ≥75 적극매수 / ≥60 매수 / ≥45 관망 / 그 외 회피. **손절 발동 시 SELL 오버라이드**.
- **RS(지수대비 상대강도)** = 종목 모멘텀 − 시장 지수 모멘텀(같은 lookback). momentum 과
  collinear → 기존 momentum 0.25 를 **0.13 + rs 0.12** 로 분할(이중계상 방지). 지수 모멘텀은
  스캔당 1회 산정해 공유 주입.
- **무상태 트레일링 손절**: `stop = 최근 trail_window_days(60) 고점 ×(1−8%)`. 200일선 위 종목만
  '매도요구' 게이트(상태/DB 없음 = staleness 원천차단).
- **부적격 진단**: 부적격 종목의 momentum/rs/turnover 를 0 으로 두지 않고 **통과 종목 밴드 대비
  위치**(`scoring.linear_position`, 클램프 없음 — 미달=음수·초과=1↑; `FactorBounds`/`factor_bounds`)
  로 매겨 *어느 항목이 막혔는지* 표시. 점수는 0 불변. near52w·vol_fit·pocket_pivot 은 원시 기반.

## 데이터 소스 + 함정 (`backend/market_data.py`)

- **KIS_MODE=real 필수**(모의 도메인 시세 부정확). 토큰 1분당 1회 → 싱글턴 + 디스크 영속
  (`data/.kis_token.json`).
- **KR 일봉**: KIS `inquire-daily-itemchartprice`(FHKST03010100) — **호출당 ~100봉 cap → 윈도우
  페이지네이션**으로 280봉 누적. (단일 호출이면 MA200/1년수익률 None → 전종목 '회피' 오판별.)
- **KR 종목명**: KIS 가 `hts_kor_isnm` 미제공(업종 `bstp_kor_isnm` 만) → **pykrx
  `get_market_ticker_name`**(인스턴스 캐시).
- **KR 투자자 매매금**: `*_tr_pbmn` 은 **백만원 단위 → ×1e6**. `output[0]`(최신일)은 미정산 빈값
  → 첫 정산행 사용.
- **US 시세/펀더/일봉 + 지수**: **yfinance + curl_cffi `Session(impersonate="chrome")`** —
  데이터센터(OCI) IP 의 Yahoo 429 회피(필수; plain `requests` 면 US 전종목 실패). 일봉 일1회 캐시
  + `yf.download` 배치 + tenacity 백오프.
- **지수(RS 분모)**: KR=KOSPI `^KS11`, US=S&P500 `^GSPC` (yfinance). **pykrx 지수는 데이터센터 IP
  에서 KRX 403** → 지수는 yfinance 로 KR·US 일원화. (pykrx 종목 유니버스/일봉은 동작.)
- US 유니버스: 정적 화이트리스트 상위 N(기본 30 — yfinance 429 회피).
- 캐시: `DailyCache`(SQLite, key=market+ticker+오늘+kind). kind = `ohlcv`/`fundamentals`/`index`.

## 배포 (OCI Always Free + Caddy + Pages)

- 서버: OCI Always Free ARM, **swing-bot 과 동일 인스턴스 공유**. `/home/ubuntu/trend-score-dashboard`,
  systemd `trend-board.service`(uvicorn `127.0.0.1:8000`).
- 공개 HTTPS: `board.s-edu.ai.kr`(yesnic DNS A레코드) + **Caddy 자동 인증서**.
- **OCI 우분투는 22 외 인바운드 REJECT 기본** → ① 보안목록 80·443 + ② 호스트
  `sudo iptables -I INPUT -p tcp --dport 80/443 -j ACCEPT` + `netfilter-persistent save` **둘 다** 필요.
- Caddyfile: 파일 로그는 공식 caddy.service 샌드박스가 `/var/log/caddy` 쓰기 차단 → 기동 실패.
  **journald 사용**(파일 로그 제거). `journalctl -u caddy`.
- `.env`(gitignore, 템플릿=`.env.example`): `DATA_MODE=live`, `KIS_MODE=real`,
  `CORS_ORIGINS=https://989-alt.github.io`(**트레일링 슬래시·인라인 주석 금지** — systemd 가 주석을
  값으로 읽음), `LIVE_UNIVERSE_TOP_N=300`, `LIVE_UNIVERSE_TOP_N_US=30`, KIS 키.
- 프론트(Pages, `.github/workflows/deploy-pages.yml`): repo Variable **`API_BASE=https://board.s-edu.ai.kr`**
  → 라이브 fetch. **변수만 바꾸면 재빌드 안 됨** → `frontend/**` push 또는 `gh workflow run
  deploy-pages.yml`. 미설정이면 번들 샘플 데모.
- 배포 절차: **백엔드** `git pull && sudo systemctl restart trend-board.service`(재시작=재스캔).
  캐시 강제초기화 필요 시 `rm data/dashboard.db*`(KIS 토큰은 별도라 유지). **프론트** `frontend/**`
  push → Pages 자동 재빌드.

## 개발 워크플로 (DoD)

- 백엔드: `uv run pytest` + `uv run ruff check` + `uv run ruff format` + `uv run mypy backend/`.
- 프론트: `npm run build`(tsc -b) + `npx vitest run`. (lint 스크립트 없음 — tsc 가 게이트.)
- 모드: `DATA_MODE=sample`(기본, 키 불필요 결정론 합성데이터=검증용) / `live`.
- 라이브 검증: 공개 API `curl` + Playwright(콘솔 에러 0 확인).

## 비용

- **≈ ₩0/월**. OCI Always Free(공유) + 보유 도메인 무료 서브도메인 + GitHub Pages/Actions +
  KIS/yfinance 무료. **LLM 미사용**(순수 데이터+계산). 도메인 갱신비(연 ~₩2~3만)는 보유 도메인
  공유라 신규 비용 아님.

## 규칙

- **Decimal 전면**(float 금지). 한글 문자열은 `gen-i18n.mjs` 단일 출처(`\uXXXX`).
- 시크릿(`.env`, `data/.kis_token.json`) **절대 커밋 금지**(gitignore). 채팅에 KIS 키 붙여넣지
  않음 — 서버 터미널에서만 입력.
- **swing-bot 프로젝트 파일 무수정**(읽기전용 참고만).
- 커밋: Conventional Commits(`feat:`/`fix:`/`docs:` …), 한국어 본문 OK.
