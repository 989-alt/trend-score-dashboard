"""trend-trader 주문/조회 공용 예외.

``kis_auth``(토큰)·``kis_order``(국내)·``kis_overseas``(해외)가 공유하는 단일 예외 타입.
별도 모듈로 두어 토큰 헬퍼와 주문 클라이언트 간 순환 import 를 피한다.
"""

from __future__ import annotations


class KisOrderError(RuntimeError):
    """KIS 주문/조회 실패 (HTTP·rt_cd≠0·파싱)."""


__all__ = ["KisOrderError"]
