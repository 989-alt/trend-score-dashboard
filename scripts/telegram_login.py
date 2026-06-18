"""텔레그램 MTProto 최초 1회 로그인 — ``data/.telegram.session`` 생성.

실행: ``uv run python scripts/telegram_login.py``

``.env`` 의 ``TELEGRAM_API_ID`` / ``TELEGRAM_API_HASH`` 를 읽어 대화형 로그인(전화번호 +
인증코드, 2FA 시 비밀번호)을 수행하고 세션 파일을 만든다. 한 번 만들면 앱/수집 잡이 그
세션으로 자동 인증된다(재로그인 불필요). 세션 파일은 시크릿이라 .gitignore 대상이다.

서버(OCI)에서도 동일하게 1회 실행하거나, 로컬에서 만든 세션 파일을 서버의 같은 경로로
복사하면 된다.
"""

from __future__ import annotations

import asyncio

from backend.config import get_settings


async def main() -> None:
    """대화형 로그인으로 텔레그램 세션 파일을 생성한다."""
    from telethon import TelegramClient

    settings = get_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise SystemExit(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH 가 .env 에 없습니다. "
            "my.telegram.org 에서 발급 후 .env 에 넣고 다시 실행하세요."
        )

    client = TelegramClient(
        str(settings.telegram_session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.start()  # 전화번호·인증코드(·2FA) 대화형 프롬프트
    me = await client.get_me()
    handle = getattr(me, "username", None) or getattr(me, "id", "?")
    print(f"로그인 성공: {handle}")
    print(f"세션 저장: {settings.telegram_session_path}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
