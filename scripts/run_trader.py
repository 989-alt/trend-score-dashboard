"""모의 매매봇 엔트리포인트 — 로깅 설정 후 ``runner.run`` 구동(블로킹 데몬).

실행:
    uv run python scripts/run_trader.py

systemd ``trend-trader.service`` 가 이 스크립트를 ExecStart 로 띄운다. 로깅 설정(INFO,
stdout, UTF-8)은 여기서만 하고 모듈에서는 하지 않는다(라이브러리 오염 방지).
"""

from __future__ import annotations

import contextlib
import logging
import sys
from pathlib import Path

#: 프로젝트 루트를 import 경로에 추가(uv 외 직접 실행 대비).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.trader.runner import run  # noqa: E402


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Windows 콘솔 한글 깨짐 방지(서버는 PYTHONUTF8=1; 로컬 직접 실행 안전망).
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    run(get_settings())


if __name__ == "__main__":
    main()
