#!/usr/bin/env bash
# 스모크 테스트 강제매수 예약 — 2026-06-22(월) 장 시작 시 KR 하이닉스 + US 알파벳A 각 1주.
# LLM 의사결정과 무관하게 실제 체결이 일어나는지 확인한다.
# 실행:  sudo bash deploy/schedule_force_buy.sh
#   - systemd-run 일회성 타이머를 ubuntu 권한으로 생성(개장 직후 20초 마진).
#   - KR 09:00:20 KST / US 22:30:20 KST(NYSE 09:30 ET, EDT 여름).
set -euo pipefail

DIR=/home/ubuntu/trend-score-dashboard
PY="$DIR/.venv/bin/python"

systemd-run --uid=ubuntu --gid=ubuntu --collect \
  --working-directory="$DIR" --setenv=PYTHONUTF8=1 \
  --on-calendar="2026-06-22 09:00:20 Asia/Seoul" --unit=force-buy-kr \
  "$PY" scripts/force_buy.py --market KR

systemd-run --uid=ubuntu --gid=ubuntu --collect \
  --working-directory="$DIR" --setenv=PYTHONUTF8=1 \
  --on-calendar="2026-06-22 22:30:20 Asia/Seoul" --unit=force-buy-us \
  "$PY" scripts/force_buy.py --market US

echo "예약 완료:"
systemctl list-timers 'force-buy-*' --all || true
echo "체결 후 로그 확인: journalctl -u force-buy-kr ; journalctl -u force-buy-us"
