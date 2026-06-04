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

> 라이브 전 종목(KR pykrx + US 심볼덤프) 30분 갱신 성능은 일봉 캐시 최적화가 필요할 수 있다(README 운영 노트 참조).
