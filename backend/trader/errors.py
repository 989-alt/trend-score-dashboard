"""trend-trader 주문/조회 공용 예외.

``kis_auth``(토큰)·``kis_order``(국내)·``kis_overseas``(해외)가 공유하는 단일 예외 타입.
별도 모듈로 두어 토큰 헬퍼와 주문 클라이언트 간 순환 import 를 피한다.
"""

from __future__ import annotations


class KisOrderError(RuntimeError):
    """KIS 주문/조회 실패 (HTTP·rt_cd≠0·파싱)."""


class KisTokenExpiredError(KisOrderError):
    """KIS 토큰이 서버측에서 만료/무효(EGW00123)된 경우.

    로컬 만료시각은 유효한데 KIS 가 토큰을 무효화한 상황(앱키당 1토큰 정책상 다른 발급으로
    무효화 등). 호출 측이 이 예외를 잡아 ``KisToken.refresh()`` 후 1회 재시도한다.
    """


__all__ = ["KisOrderError", "KisTokenExpiredError"]
