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
