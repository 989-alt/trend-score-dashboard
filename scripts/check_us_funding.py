"""US 모의계좌 자금 진단 (1회용) — 환전/통합증거금으로 미장 매수가 되는지 판정.

서버에서:  uv run python scripts/check_us_funding.py
- 해외 잔고(USD 예수금) + 해외 매수가능금액(통합증거금 반영 buying power)을 raw 로 출력.
- 매수가능금액 > 0  → 통합증거금/원화주문으로 미장 매수 가능(사이징만 그 값 기준으로 수정하면 됨).
- 0/에러            → 모의계좌에 USD·통합증거금이 없음(KIS 앱 환전/설정 또는 KR 단독 운영 필요).
키·토큰은 출력하지 않는다.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

#: 프로젝트 루트를 import 경로에 추가(run_trader.py 와 동일 — 직접 실행 대비).
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.trader.kis_auth import token_from_settings  # noqa: E402

_MOCK = "https://openapivts.koreainvestment.com:29443"


def main() -> None:
    s = get_settings()
    if not (s.kis_appkey and s.kis_appsecret and s.kis_account):
        print("[중단] KIS_APPKEY/KIS_APPSECRET/KIS_ACCOUNT 미설정")
        return
    cano = s.kis_account.strip().split("-")[0]
    prod = (s.kis_account_prod or "01").strip()
    token = token_from_settings(s, _MOCK).get()
    base_headers = {
        "authorization": f"Bearer {token}",
        "appkey": s.kis_appkey,
        "appsecret": s.kis_appsecret,
        "custtype": "P",
    }
    client = httpx.Client(base_url=_MOCK, timeout=10.0)

    # 1) 해외 잔고 — output2(통화별 예수금)에서 USD 외화예수금 확인.
    print("=== 1) 해외 잔고 output2 (VTRP6504R) ===")
    bal = client.get(
        "/uapi/overseas-stock/v1/trading/inquire-balance",
        headers={**base_headers, "tr_id": "VTRP6504R"},
        params={
            "CANO": cano,
            "ACNT_PRDT_CD": prod,
            "OVRS_EXCG_CD": "NASD",
            "TR_CRCY_CD": "USD",
            "WCRC_FRCR_DVSN_CD": "02",
            "NATN_CD": "840",
            "TR_MKET_CD": "01",
            "INQR_DVSN_CD": "00",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": "",
        },
    )
    print("HTTP", bal.status_code)
    try:
        print(json.dumps(bal.json().get("output2"), ensure_ascii=False, indent=2))
    except ValueError:
        print(bal.text[:500])

    # 2) 해외 매수가능금액 — 통합증거금이면 원화로 환산된 매수가능액이 잡힌다.
    # 트레이더가 같은 모의키로 매분 호출 → "초당 거래건수 초과" 가능 → 딜레이+재시도.
    print("\n=== 2) 해외 매수가능금액 (VTTS3007R, AAPL @ $200) ===")
    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": prod,
        "OVRS_EXCG_CD": "NASD",
        "OVRS_ORD_UNPR": "200",
        "ITEM_CD": "AAPL",
    }
    for attempt in range(6):
        time.sleep(1.5)
        ps = client.get(
            "/uapi/overseas-stock/v1/trading/inquire-psamount",
            headers={**base_headers, "tr_id": "VTTS3007R"},
            params=params,
        )
        try:
            body = ps.json()
        except ValueError:
            print("HTTP", ps.status_code, ps.text[:300])
            return
        msg = str(body.get("msg1", ""))
        if "초당" in msg or "거래건수" in msg:
            print(f"  rate limit, 재시도 {attempt + 1}/6...")
            continue
        if str(body.get("rt_cd")) != "0":
            print("rt_cd", body.get("rt_cd"), "msg", msg)
        print(json.dumps(body.get("output"), ensure_ascii=False, indent=2))
        return
    print("  rate limit 지속 — 트레이더 잠시 멈추고 재시도 필요")


if __name__ == "__main__":
    main()
