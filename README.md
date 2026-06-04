# 추세추종 스코어 대시보드 (trend-score-dashboard)

KR(국장)·US(미장) 종목의 **매수 추천도를 0~100 점수·순위**로 보여주고, **테마별 주도주**를 모아
보여주는 공개 대시보드. 모든 종목의 등락을 **30분 단위로 자동 반영**한다. **손절가 이하로 내려간
종목은 "매도 요구"** 를 표시한다.

> **본 대시보드는 투자 자문에 해당하지 않으며, 투자의 판단과 결정은 철저히 개인에게 있습니다.**

## 추세추종 방법론 출처

점수·손절 로직은 swing-bot(`주식 매매 판별기`)의 **추세추종(맛동산/Sperandeo)** 규칙을 **독립
재구현**한 것이다(원본 파일은 읽기 전용 참고, 본 프로젝트는 의존성 0):

- 점수: `near_52w·0.30 + pocket_pivot·0.20 + momentum·0.25 + turnover·0.15 + vol_fit·0.10`
- 하드필터: 거래대금 ≥ 100억 · 모멘텀 ≥ 0 · 200일선 위 · 변동성 밴드 0.20~0.60
- 손절(매도요구): 트레일링 `peak×(1−8%)` 또는 200일선 이탈

## 데이터 소스

| 시장 | 시세/일봉 | 투자자별 매매(외국인/기관/개인) |
|---|---|---|
| KR | KIS OpenAPI | KIS 투자자별 매매동향 ✓ |
| US | yfinance | ✗ (yfinance 미제공) |

## 구성

- `backend/` — FastAPI(API + 정적 서빙) + APScheduler(일일 prep + 30분 intraday 잡)
- `frontend/` — React + Vite + TS (테마/국장/미장 3탭)
- `data/themes.yml` — 테마 매핑(편집형)

## 실행 (개발)

```bash
uv sync                                   # 백엔드 의존성
# .env 없이도 sample 모드로 구동 (DATA_MODE=sample 기본)
uv run uvicorn backend.app:app --reload   # http://localhost:8000
cd frontend && npm install && npm run dev  # http://localhost:5173
```

실 데이터: `.env` 에 `KIS_APP_KEY/SECRET` 입력 후 `DATA_MODE=live`.

## 검증 (DoD)

```bash
uv run ruff check backend tests       # 0 에러
uv run ruff format --check backend tests
uv run mypy backend                   # 0 에러
uv run pytest -q                      # 209 passed
```

프론트: `cd frontend && npm run build && npm run test`.

## 운영 노트 · 라이브 모드 한계

`DATA_MODE=sample`(기본)은 키 없이 전 기능을 구동·검증한다(결정론 합성데이터). 실거래 데이터는
`DATA_MODE=live` + KIS 키에서만 나오며, 아래는 **운영자가 KIS 키로 한 번 검증**해야 한다:

- **종목 유니버스**: 라이브는 KR=pykrx 전 상장종목, US=NASDAQ Trader 심볼덤프로 "조회 가능한 종목 전체"를
  스캔한다. 전 종목(수천 개) 30분 갱신은 **일봉 캐시(일 1회 prep) + 시세만 30분 갱신** 최적화가 필요하다
  (현재 `scheduler`가 일일/30분 잡을 분리; 대규모 일봉 캐시는 후속 과제). 소스 조회 실패 시 themes.yml 큐레이션으로 graceful fallback.
- **투자자별 매매(금액)**: KIS의 매수/매도 **거래대금** 필드로 외국인/기관/개인 매수·매도·순매수(원)를 채운다.
  실제 TR_ID·엔드포인트·필드명은 KIS Developers 문서로 1회 실호출 확인이 필요하다(코드에 TODO 명시, 실패 시 해당 종목 스킵).
- **시가총액 단위**: KIS `hts_avls`(억원 가정)→원 환산은 실응답으로 단위 확인 권장.
- **면책**: 본 대시보드는 투자 자문이 아니며, 모든 투자 판단·책임은 이용자 본인에게 있다.

## 배포 (OCI 단일 프로세스)

`uvicorn`이 API + 빌드된 프론트(`frontend/dist`)를 단일 포트로 서빙한다. OCI에서 systemd 서비스 +
Caddy(자동 HTTPS)로 서브도메인 공개. 자세한 절차는 `deploy/`(systemd 유닛·Caddyfile) 참조.
