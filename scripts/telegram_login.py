"""Telethon 세션 생성용 1회 로그인 스크립트 (대화형).

목적: `.env`의 APP_API_ID / APP_API_HASH 로 Telegram 에 최초 인증해
`data/telethon.session` 을 만든다. 한 번 만들면 수집기가 이 세션을 재사용한다.

실행(터미널에서 대화형으로):
    uv run python scripts/telegram_login.py

- 휴대폰 번호(+82...)와 Telegram 이 보내는 인증코드를 직접 입력한다.
- 2단계 인증(비밀번호)을 쓰면 그 비밀번호도 물어본다.
- 성공하면 대상 4채널이 실제로 읽히는지 확인 출력한다.

시크릿: 세션 파일은 `data/`(gitignore) 에 저장되며 절대 커밋하지 않는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

from telethon.sync import TelegramClient

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
SESSION_PATH = ROOT / "data" / "telethon"  # → data/telethon.session

# 수집 대상 채널(공개·가입됨)
CHANNELS = ["FastStockNews", "goodnews_honey", "getfeed", "jusikbiso"]


def read_env(path: Path) -> dict[str, str]:
    """의존성 없이 .env 를 단순 파싱(KEY=VALUE, 따옴표/주석 처리)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        out[key.strip()] = val
    return out


def main() -> int:
    env = read_env(ENV_PATH)
    api_id = env.get("APP_API_ID")
    api_hash = env.get("APP_API_HASH")
    if not api_id or not api_hash:
        print(f"[!] {ENV_PATH} 에 APP_API_ID / APP_API_HASH 가 없습니다.")
        return 1
    try:
        api_id_int = int(api_id)
    except ValueError:
        print(f"[!] APP_API_ID 가 숫자가 아닙니다: {api_id!r}")
        return 1

    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"세션 저장 위치: {SESSION_PATH}.session")
    print("Telegram 인증을 시작합니다. 번호와 인증코드를 입력하세요.\n")

    with TelegramClient(str(SESSION_PATH), api_id_int, api_hash) as client:
        me = client.get_me()
        print(f"\n[OK] 로그인 성공: {getattr(me, 'first_name', '')} "
              f"(@{getattr(me, 'username', '')})\n")
        print("대상 채널 접근 확인:")
        for ch in CHANNELS:
            try:
                entity = client.get_entity(ch)
                msgs = client.get_messages(entity, limit=1)
                last = msgs[0].date.isoformat() if msgs else "(메시지 없음)"
                title = getattr(entity, "title", ch)
                print(f"  ✅ {ch:16s} | {title} | 최신 {last}")
            except Exception as exc:  # noqa: BLE001 — 채널별 진단 출력용
                print(f"  ❌ {ch:16s} | 접근 실패: {exc}")

    print("\n완료. data/telethon.session 이 생성되었습니다(커밋 금지).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
