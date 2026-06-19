# 배포 (OCI Always Free · 단일 프로세스)

`uvicorn` 한 프로세스가 API(`/api/*`, `/healthz`)와 빌드된 프론트(`frontend/dist` SPA)를 함께 서빙하고,
APScheduler가 일일 prep + 30분 갱신 잡을 구동한다. Caddy가 자동 HTTPS로 서브도메인을 공개한다.

## 1. 초기 셋업 (운영자 1회)

```bash
ssh ubuntu@<OCI_PUBLIC_IP>
sudo timedatectl set-timezone Asia/Seoul
sudo apt update && sudo apt -y install git curl
# uv (Python 매니저)
curl -LsSf https://astral.sh/uv/install.sh | sh
# Node (프론트 빌드용)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt -y install nodejs

git clone <REPO_URL> /home/ubuntu/trend-score-dashboard
cd /home/ubuntu/trend-score-dashboard
uv sync                                   # 백엔드 의존성
cd frontend && npm install && npm run build && cd ..   # frontend/dist 생성
mkdir -p logs

cp .env.example .env
# nano .env — KIS_APP_KEY/KIS_APP_SECRET 입력(시세+투자자매매 권한, 봇과 별도 키 권장),
#            DATA_MODE=live, KIS_MODE=real(또는 mock). 절대 커밋 금지.
chmod 600 .env
```

## 2. systemd 서비스

```bash
sudo cp deploy/systemd/trend-board.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trend-board.service
systemctl status trend-board.service
curl -s localhost:8000/healthz
```

## 3. Caddy (자동 HTTPS)

```bash
sudo apt -y install caddy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile   # board.example.com → 실제 서브도메인
sudo systemctl restart caddy
```

- DNS: 서브도메인 A 레코드 → OCI 공인 IP.
- OCI 보안목록 + `ufw`: 80·443 인바운드 허용(그 외 차단), SSH(22) 유지.

## 4. 검증

- `https://<도메인>/` 접속 → 3탭(테마/국장/미장)·면책 배너·랭킹·매도요구 렌더, 콘솔 에러 0.
- 장중(KST 09:00–15:30 / ET 09:30–16:00) 30분마다 점수·순위 자동 갱신 확인.

---

## 5. GitHub Pages 프론트 + OCI 백엔드 (라이브 공개 링크)

**github.io 링크는 그대로 두고 실데이터**를 보여주는 구성. Pages(정적 SPA)가 OCI 백엔드 API를 fetch한다.

### 백엔드(OCI) — CORS + HTTPS 필수
GitHub Pages(https)가 OCI API를 부르려면 **OCI도 HTTPS**여야 하고(혼합콘텐츠 차단), **CORS 허용**이 필요하다.

`.env` 에 추가:
```bash
DATA_MODE=live
KIS_MODE=real            # 모의 도메인은 시세 부정확 → 반드시 real(조회 전용, 주문 안 함)
CORS_ORIGINS=https://989-alt.github.io
LIVE_UNIVERSE_TOP_N=300       # 국장 거래대금 상위
LIVE_UNIVERSE_TOP_N_US=30     # 미장은 yfinance(Yahoo) 429 회피 위해 소수만
```
Caddy로 서브도메인(예 `board.s-edu.ai.kr`)에 자동 HTTPS(위 2~3절). 그러면 API가
`https://board.s-edu.ai.kr/api/snapshot?market=kr` 등으로 공개된다.

### 프론트(GitHub Pages) — API_BASE 변수만 설정
저장소에 **Actions Variable** 하나만 추가하면 Pages 빌드가 그 OCI URL로 라이브 fetch한다:
```bash
gh variable set API_BASE --body "https://board.s-edu.ai.kr"
gh workflow run deploy-pages.yml        # 재빌드·재배포
```
- `API_BASE` 미설정 → Pages는 **샘플 데모**(번들 JSON).
- `API_BASE` = OCI URL → Pages가 **실데이터** fetch(상단 배너도 "30분 자동 갱신 실데이터"로 전환).
- 누구나 `https://989-alt.github.io/trend-score-dashboard/` 로 라이브 확인.

### 주의
- **혼합콘텐츠**: OCI가 `http://IP` 면 https Pages에서 차단됨 → 반드시 도메인+Caddy HTTPS.
- **KIS 해외 IP 정책**: 일부 계정은 해외 IP 차단 → OCI 공인 IP를 KIS에 화이트리스트. (봇이 이미 이 OCI에서 동작하면 통과 가능.)
- **미장 yfinance**: 데이터센터 IP는 Yahoo 429가 잦다 → `LIVE_UNIVERSE_TOP_N_US`를 작게(30) 유지. 실패는 per-ticker 흡수(빈 칸).
- **장외**: 장 마감/개장 전엔 당일 거래대금 0 → 하드필터 통과 0(빈 보드). 장중에 실데이터가 채워진다.

> 봇과 **별도 KIS 시세키(조회 전용)** 권장. `.env`·`data/.kis_token.json` 절대 커밋 금지(이미 gitignore).

---

## 6. 모의 매매봇 (trend-trader) — P7 전진검증

대시보드(`trend-board.service`)와 **별도 프로세스**로 KIS 모의 매매 루프를 1분(기본) 주기로 돈다.
대시보드가 쓴 점수 스냅샷(`data/dashboard.db`)을 읽어 진입/청산을 결정하고, 결과를
`data/trading.db`(TradeStore)에 기록한다. 대시보드 "매매 현황" 탭이 그 DB 를 읽어 보여준다.

### 6-1. 서버 준비

```bash
ssh ubuntu@<OCI_PUBLIC_IP>
cd /home/ubuntu/trend-score-dashboard
git pull
uv sync                                   # 의존성 갱신
mkdir -p logs
# .env 확인 — 매매봇은 KIS 키 + 모의계좌가 필요(대시보드 .env 에 함께 둔다):
#   KIS_APP_KEY=...        # 모의 도메인 공용 키
#   KIS_APP_SECRET=...
#   KIS_ACCOUNT=50190719   # 모의계좌번호(상품코드 기본 01 = KIS_ACCOUNT_PROD)
#   TRADER_LOOP_SEC=60     # (선택) 루프 주기 초, 기본 60·최소 10
# 인라인 주석 금지(systemd 가 주석을 값으로 읽음). chmod 600 .env.
```

### 6-2. systemd 유닛 설치/기동

```bash
sudo cp deploy/systemd/trend-trader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trend-trader.service
journalctl -u trend-trader -f            # "매매봇 시작 — markets=('KR',) …" + 사이클 로그 확인
```

- 유닛은 `.venv/bin/python scripts/run_trader.py` 로 실행(trend-board 와 동일 방식).
  `uv` 로 띄우려면 ExecStart 를 `uv run python scripts/run_trader.py` 로 바꾼다.
- `Restart=on-failure`, `RestartSec=10` — 사이클 1회 실패는 봇이 자체 흡수(로그만), 프로세스
  크래시 시에만 재시작.

### 6-3. 국장 가동 전 점검 (월요일 09:00 개장 전)

- **swing-bot 잔고 청산**: 같은 모의계좌(50190719)를 swing-bot 이 쓰고 있었다면, 보유 종목이
  남아 있으면 매매봇이 그 보유를 자기 포지션으로 동기화한다. 개장 전 swing-bot 쪽에서 전량
  청산해 **깨끗한 시드(5억)**에서 시작한다.
- 봇은 **장중에만 주문**(`market_hours` 게이트), 장 마감엔 NAV 스냅샷만 기록한다.
- 킬스위치: 운영 중 매수만 멈추려면 `touch data/.trader_halt`(매도·손절은 계속), 해제는
  `rm data/.trader_halt`. 재배포 불필요.

### 6-4. 검증

- `journalctl -u trend-trader -f` 에서 매 주기 사이클 요약 로그(경고 없이) 확인.
- 대시보드 **"매매 현황" 탭**에서 NAV 추이·보유 종목·최근 주문이 채워지는지 확인(개장 후).
