"""KIS OAuth 토큰 발급·디스크캐시 공용 헬퍼 (trend-trader P8).

국내(``KisOrderClient``)·해외(``KisOverseasOrderClient``) 주문 클라이언트가 **같은 모의 앱키**를
쓰므로 KIS 정책상 토큰은 앱키당 1개 → 둘이 토큰을 공유해야 한다. 디스크캐시(``token_path``)를
공유 저장소로 삼아 한쪽이 발급하면 다른 쪽은 디스크에서 읽는다(토큰 thrash 방지).

토큰 발급 로직(메모리+디스크 2단 캐시·스레드락·tenacity 재시도)을 ``KisOrderClient`` 에서 분리해
양쪽이 ``KisToken`` 인스턴스를 보유한다. 발급 응답 파싱 실패는 ``KisOrderError`` 로 던진다.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.config import Settings
from backend.trader.errors import KisOrderError

logger = logging.getLogger(__name__)

_RETRY = retry(
    retry=retry_if_exception_type(httpx.HTTPError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    reraise=True,
)


class KisToken:
    """KIS OAuth 토큰 공급자 — 메모리+디스크 2단 캐시(스레드세이프).

    같은 ``token_path`` 를 가리키는 여러 인스턴스가 디스크캐시를 공유해 토큰을 재사용한다.
    ``get()`` 으로 유효 토큰을 얻는다(만료 임박/미보유 시 재발급 또는 디스크 로드).
    """

    def __init__(self, appkey: str, appsecret: str, base_url: str, token_path: Any) -> None:
        self._appkey = appkey
        self._appsecret = appsecret
        self._client = httpx.Client(base_url=base_url, timeout=10.0)
        self._token_path = token_path
        self._token: str | None = None
        self._token_exp: datetime | None = None
        self._lock = threading.Lock()

    def get(self) -> str:
        """유효한 access_token. 메모리 캐시 → 디스크 캐시 → KIS 발급 순으로 확보."""
        now = datetime.now(tz=UTC)
        if self._token and self._token_exp and now < self._token_exp:
            return self._token
        if not (self._appkey and self._appsecret):
            raise KisOrderError("모의 앱키 미설정 (KIS_APPKEY/KIS_APPSECRET)")
        with self._lock:
            now = datetime.now(tz=UTC)
            if self._token and self._token_exp and now < self._token_exp:
                return self._token
            disk = self._load(now)
            if disk is not None:
                self._token, self._token_exp = disk
                return self._token
            return self._issue(now)

    def refresh(self) -> str:
        """강제 재발급 — 캐시(메모리·디스크) 무시하고 KIS 에서 새 토큰을 받는다.

        서버측 토큰 무효화(EGW00123) 감지 시 호출. 디스크의 stale 토큰도 새 토큰으로 덮어써
        다음 ``get()`` 및 다른 인스턴스가 유효 토큰을 읽게 한다.
        """
        if not (self._appkey and self._appsecret):
            raise KisOrderError("모의 앱키 미설정 (KIS_APPKEY/KIS_APPSECRET)")
        with self._lock:
            return self._issue(datetime.now(tz=UTC))

    def _issue(self, now: datetime) -> str:
        """KIS 토큰 발급 + 메모리·디스크 저장. **``_lock`` 보유 상태에서만 호출**."""
        try:
            payload = self._request()
        except httpx.HTTPError as exc:
            raise KisOrderError("KIS 토큰 발급 실패") from exc
        token = payload.get("access_token")
        if not token:
            raise KisOrderError("KIS 토큰 응답에 access_token 없음")
        ttl = int(payload.get("expires_in", 86400))
        self._token = str(token)
        self._token_exp = now + timedelta(seconds=max(ttl - 60, 60))
        self._save(self._token, self._token_exp)
        return self._token

    @_RETRY
    def _request(self) -> dict[str, Any]:
        resp = self._client.post(
            "/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self._appkey,
                "appsecret": self._appsecret,
            },
        )
        resp.raise_for_status()
        try:
            payload: dict[str, Any] = resp.json()
        except ValueError as exc:
            raise KisOrderError("KIS 토큰 응답 JSON 파싱 실패") from exc
        return payload

    def _load(self, now: datetime) -> tuple[str, datetime] | None:
        try:
            data = json.loads(self._token_path.read_text(encoding="utf-8"))
            token = data["access_token"]
            expires_at = datetime.fromisoformat(data["expires_at"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if not token or expires_at.tzinfo is None or now >= expires_at - timedelta(seconds=60):
            return None
        return str(token), expires_at

    def _save(self, token: str, expires_at: datetime) -> None:
        path = self._token_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"access_token": token, "expires_at": expires_at.isoformat()}),
                encoding="utf-8",
            )
            with contextlib.suppress(OSError):
                os.chmod(path, 0o600)
        except OSError:
            logger.warning("매매봇 토큰 디스크 저장 실패 — 메모리 캐시로 계속", exc_info=True)


def token_from_settings(settings: Settings, base_url: str) -> KisToken:
    """``Settings`` 의 모의 앱키/토큰경로로 ``KisToken`` 1개 생성(공유 토큰 진입점)."""
    return KisToken(
        settings.kis_appkey,
        settings.kis_appsecret,
        base_url,
        settings.trader_token_path,
    )


__all__ = ["KisToken", "token_from_settings"]
