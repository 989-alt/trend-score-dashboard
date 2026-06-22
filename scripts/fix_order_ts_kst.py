"""1회용 마이그레이션 — 과거 UTC 로 저장된 주문 시각(ts)을 KST **표기**로 보정.

주문 시각이 한때 ``datetime.now(tz=UTC)`` 로 찍혀(``+00:00``) 저장됐다. 순간(instant)은 옳지만
프론트가 ``ISO=KST`` 로 가정해 문자열을 잘라 표시하므로 9시간 이르게(자정처럼) 보였다. 이제 주문은
KST(``+09:00``)로 찍는다. 이 스크립트는 저장된 ``+00:00`` 행만 같은 순간의 KST 표기
(``astimezone``)로 바꾼다 — 순간 불변, 표기만 ``+09:00``. KST 행은 안 건드려 재실행 안전(멱등).

    uv run python scripts/fix_order_ts_kst.py            # 보정 실행
    uv run python scripts/fix_order_ts_kst.py --dry-run  # 대상만 출력
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402

_KST = timezone(timedelta(hours=9))


def main() -> None:
    parser = argparse.ArgumentParser(description="과거 UTC 주문 시각을 KST 로 보정(멱등)")
    parser.add_argument("--dry-run", action="store_true", help="대상만 출력하고 변경하지 않음")
    dry_run = parser.parse_args().dry_run

    db_path = get_settings().trader_db_path
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT rowid, ts FROM orders WHERE ts LIKE '%+00:00'").fetchall()
    if not rows:
        print("보정 대상 없음(이미 모두 KST).")
        conn.close()
        return

    fixed = 0
    for rowid, ts in rows:
        new_ts = datetime.fromisoformat(ts).astimezone(_KST).isoformat()
        print(f"{ts}  →  {new_ts}")
        if not dry_run:
            conn.execute("UPDATE orders SET ts = ? WHERE rowid = ?", (new_ts, rowid))
            fixed += 1

    if dry_run:
        print(f"\n[dry-run] 대상 {len(rows)}건 — 변경 안 함.")
    else:
        conn.commit()
        print(f"\n보정 완료: {fixed}건.")
    conn.close()


if __name__ == "__main__":
    main()
