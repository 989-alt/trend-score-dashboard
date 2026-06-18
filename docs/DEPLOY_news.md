# OCI 백엔드 배포 — 시황(뉴스) 탭 라이브화

> 레포에 포함된 배포 절차서(비밀키 없음). `HANDOFF_news.md` §2 의 상세판.
> 목표: main 에 올라간 시황 탭 백엔드를 **OCI 서버에 반영**해 라이브 "시황" 탭을 동작시킨다.

- 서버 IP: **137.131.29.175** · user **ubuntu** · 경로 `/home/ubuntu/trend-score-dashboard` · 서비스 `trend-board.service`
- 비밀키 3종(`APP_API_ID`/`APP_API_HASH`/`GEMINI_API_KEY`)은 **git에 없음**. 보유처에서 옮기거나 재발급:
  `APP_API_ID`/`APP_API_HASH` = my.telegram.org · `GEMINI_API_KEY` = Google AI Studio.

---

## PART 1 — SSH 개인키 찾기 (접속용)

서버 접속엔 OCI **SSH 개인키**가 필요하다. 위치를 모르면 키가 있을 컴퓨터에서 검색:

**Windows (PowerShell):**
```powershell
Get-ChildItem "$HOME\.ssh" -ErrorAction SilentlyContinue | Select-Object Name, Length
Get-ChildItem "$HOME" -Recurse -File -Depth 4 -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match 'id_rsa|id_ed25519|ssh-key|\.pem$|\.ppk$|\.key$' } |
  Select-Object FullName, Length, LastWriteTime
Get-ChildItem "$HOME\Downloads","$HOME\Desktop","$HOME\Documents","$HOME\.ssh" -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Length -lt 15000 } |
  Where-Object { (Get-Content $_.FullName -TotalCount 1 -ErrorAction SilentlyContinue) -match 'PRIVATE KEY' } |
  Select-Object FullName, LastWriteTime
```

**Mac/Linux:**
```bash
ls -la ~/.ssh/
find ~ -maxdepth 4 -type f \( -name 'id_rsa' -o -name 'id_ed25519' -o -name 'ssh-key-*' \
  -o -name '*.pem' -o -name '*.ppk' -o -name '*.key' \) 2>/dev/null
grep -rlE 'BEGIN (OPENSSH|RSA|EC|DSA) ?PRIVATE KEY' ~/.ssh ~/Downloads ~/Desktop ~/Documents 2>/dev/null
```

**식별:** 파일 첫 줄이 `-----BEGIN ... PRIVATE KEY-----` → 개인키(✅). `ssh-rsa …`/`.pub` → 공개키(❌).
`PuTTY-User-Key-File-…` → PuTTY(.ppk): PuTTYgen `Load → Conversions → Export OpenSSH key` 로 변환.
오프라인 PC에 있으면 **USB로 복사**(키는 파일이라 인터넷 불필요). 후보 여러 개면 다 가져와 하나씩 시도.

---

## PART 2 — 접속

```powershell
# (Windows) 키를 .ssh 에 두고 권한 잠금 후 접속
icacls "$HOME\.ssh\oci.key" /inheritance:r /grant:r "$($env:USERNAME):R"
ssh -i "$HOME\.ssh\oci.key" ubuntu@137.131.29.175
```
```bash
# (Mac/Linux)
chmod 600 ~/.ssh/oci.key
ssh -i ~/.ssh/oci.key ubuntu@137.131.29.175
```
프롬프트가 `ubuntu@...$` 면 서버 안.

---

## PART 3 — 서버 안 배포 (순서대로)

```bash
cd /home/ubuntu/trend-score-dashboard
git pull
uv sync                       # 새 deps(telethon·google-genai) — 빠뜨리면 기동 실패
                              # uv 없으면: source $HOME/.bashrc  또는  export PATH="$HOME/.local/bin:$PATH"

# 비밀키 3줄 추가 (값은 본인 보유분으로 채워 붙여넣기)
cat >> .env <<'EOF'
APP_API_ID=<값>
APP_API_HASH=<값>
GEMINI_API_KEY=<값>
EOF
chmod 600 .env
grep -E 'APP_API_ID|APP_API_HASH|GEMINI' .env   # 3줄 확인

# 텔레그램 세션 1회 (대화형): 번호 +8210… / 코드는 텔레그램 앱 안으로 옴
uv run python scripts/telegram_login.py

sudo systemctl restart trend-board.service
```

---

## PART 4 — 검증

```bash
curl -s http://127.0.0.1:8000/api/news/issues | head -c 300 ; echo
systemctl is-active trend-board.service
journalctl -u trend-board.service -n 50 --no-pager   # 문제 시
```
브라우저: `https://board.s-edu.ai.kr/api/news/issues`(JSON) · `https://989-alt.github.io/trend-score-dashboard/` → "시황" 탭.
이후 5분마다 수집 · 토요일 오전 Gemini 주간요약 자동.

## 문제 해결
- `Permission denied (publickey)` → 키 틀림(공개키/.ppk). `BEGIN … PRIVATE KEY` 파일인지 확인.
- `Unprotected private key file` → PART 2의 `icacls`/`chmod 600`.
- `uv: command not found` → `source $HOME/.bashrc` 또는 PATH export.
- 재시작 후 안 뜸 → `journalctl …`(대개 `uv sync` 누락/`.env` 형식 오류).
- 시황 탭 비어 있음 → 정상(첫 수집 전), 5분 후 채워짐.
