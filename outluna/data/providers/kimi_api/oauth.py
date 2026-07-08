"""Kimi OAuth 设备流与 token 刷新（同步 httpx 版）。"""

from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from outluna.data.providers.kimi_api.constants import (
    DEFAULT_CLIENT_ID,
    DEFAULT_OAUTH_HOST,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    KIMI_DATASOURCE_VERSION,
)
from outluna.data.providers.kimi_api.identity import oauth_device_headers
from outluna.data.providers.kimi_api.models import (
    DeviceAuthorization,
    DevicePollResult,
    OAuthError,
    OAuthUnauthorizedError,
    TokenInfo,
    token_from_credentials,
    token_from_oauth_payload,
)
from outluna.data.providers.kimi_api.storage import KimiCredentialStore

RETRYABLE_REFRESH_STATUSES = {429, 500, 502, 503, 504}
MIN_REFRESH_THRESHOLD_SECONDS = 300
REFRESH_THRESHOLD_RATIO = 0.5


class KimiOAuthClient:
    """同步 OAuth 客户端：设备流登录 + token 刷新。"""

    def __init__(
        self,
        store: KimiCredentialStore,
        *,
        oauth_host: str = DEFAULT_OAUTH_HOST,
        client_id: str = DEFAULT_CLIENT_ID,
        version: str = KIMI_DATASOURCE_VERSION,
        timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        proxy: str = "",
        max_refresh_retries: int = 3,
    ) -> None:
        self.store = store
        self.oauth_host = oauth_host.rstrip("/")
        self.client_id = client_id
        self.version = version
        self.timeout_seconds = timeout_seconds
        self.proxy = proxy.strip() or None
        self.max_refresh_retries = max(1, max_refresh_retries)
        self._refresh_lock = threading.Lock()
        self._http_client = httpx.Client(
            timeout=max(1, self.timeout_seconds),
            proxy=self.proxy,
            follow_redirects=True,
        )

    def close(self) -> None:
        """关闭底层 HTTP 客户端。"""
        self._http_client.close()

    def request_device_authorization(self) -> DeviceAuthorization:
        """请求设备授权码。"""
        status, data = self._post_form(
            "/api/oauth/device_authorization",
            {"client_id": self.client_id},
        )
        if status != 200:
            raise OAuthError(f"Device authorization failed (HTTP {status}): {pick_error_detail(data)}")

        user_code = data.get("user_code")
        device_code = data.get("device_code")
        verification_uri = data.get("verification_uri")
        if not isinstance(user_code, str) or not user_code:
            raise OAuthError("Device authorization response missing user_code")
        if not isinstance(device_code, str) or not device_code:
            raise OAuthError("Device authorization response missing device_code")
        if not isinstance(verification_uri, str) or not verification_uri:
            raise OAuthError("Device authorization response missing verification_uri")

        verification_uri_complete = data.get("verification_uri_complete")
        if not isinstance(verification_uri_complete, str):
            verification_uri_complete = ""
        expires_in = to_optional_int(data.get("expires_in"))
        interval = to_optional_int(data.get("interval")) or DEFAULT_POLL_INTERVAL_SECONDS
        return DeviceAuthorization(
            user_code=user_code,
            device_code=device_code,
            verification_uri=verification_uri,
            verification_uri_complete=verification_uri_complete,
            expires_in=expires_in,
            interval=interval,
        )

    def poll_device_token(self, device_code: str) -> DevicePollResult:
        """轮询设备 token。"""
        status, data = self._post_form(
            "/api/oauth/token",
            {
                "client_id": self.client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        if status == 200 and isinstance(data.get("access_token"), str):
            return DevicePollResult("success", token=token_from_oauth_payload(data, now_seconds()))
        if status >= 500:
            raise OAuthError(f"Device token polling server error (HTTP {status}): {pick_error_detail(data)}")

        error_code = data.get("error")
        error_code = error_code if isinstance(error_code, str) else "unknown_error"
        description = data.get("error_description")
        description = description if isinstance(description, str) else pick_error_detail(data)
        if error_code in {"authorization_pending", "slow_down"}:
            return DevicePollResult("pending", error_code=error_code, description=description)
        if error_code == "expired_token":
            return DevicePollResult("expired")
        if error_code == "access_denied":
            return DevicePollResult("denied", description=description)
        raise OAuthError(f"Device token polling failed (HTTP {status}): {error_code} {description}".strip())

    def refresh_access_token(self, refresh_token: str) -> TokenInfo:
        """使用 refresh_token 刷新 access_token。"""
        last_error: Exception | None = None
        for attempt in range(self.max_refresh_retries):
            try:
                status, data = self._post_form(
                    "/api/oauth/token",
                    {
                        "client_id": self.client_id,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                )
            except OAuthError as exc:
                last_error = exc
                if attempt < self.max_refresh_retries - 1:
                    time.sleep(2**attempt)
                    continue
                raise
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_error = OAuthError(f"OAuth refresh request failed: {exc}")
                if attempt < self.max_refresh_retries - 1:
                    time.sleep(2**attempt)
                    continue
                raise last_error from exc

            if status == 200 and isinstance(data.get("access_token"), str):
                return token_from_oauth_payload(data, now_seconds())

            error_code = data.get("error")
            if status in {401, 403} or error_code == "invalid_grant":
                raise OAuthUnauthorizedError(pick_error_detail(data) or "Token refresh unauthorized.")
            if status in RETRYABLE_REFRESH_STATUSES and attempt < self.max_refresh_retries - 1:
                last_error = OAuthError(pick_error_detail(data) or f"Token refresh failed (HTTP {status}).")
                time.sleep(2**attempt)
                continue
            raise OAuthError(pick_error_detail(data) or f"Token refresh failed (HTTP {status}).")

        raise OAuthError(str(last_error or "Token refresh failed."))

    def ensure_fresh(self, account_id: str | None = None, *, force: bool = False) -> str:
        """确保返回可用的 access_token；必要时刷新。"""
        account_id, token = self._load_account_token(account_id)
        if token is None:
            credentials = self.store.load_credentials(account_id)
            if credentials and credentials.get("status") == "revoked":
                raise OAuthUnauthorizedError(f"Kimi account {account_id} was rejected; re-login required.")
            raise OAuthError(f"No Kimi token stored for account {account_id}. Ask an administrator to run kimi login.")
        if not self._should_refresh(token, force):
            return token.access_token

        with self._refresh_lock:
            account_id, token = self._load_account_token(account_id)
            if token is None:
                raise OAuthUnauthorizedError(f"Kimi account {account_id} is missing or revoked; re-login required.")
            if not self._should_refresh(token, force):
                return token.access_token
            if not token.refresh_token:
                raise OAuthError(f"Kimi account {account_id} has no refresh_token; re-login required.")

            try:
                refreshed = self.refresh_access_token(token.refresh_token)
                device_id = self.store.get_device_id()
                self.store.save_refreshed_token(account_id, refreshed, device_id=device_id)
                return refreshed.access_token
            except OAuthUnauthorizedError:
                recovery = self.store.load_token(account_id)
                if recovery and recovery.refresh_token != token.refresh_token:
                    return recovery.access_token
                self.store.mark_revoked(account_id)
                raise

    def _load_account_token(self, account_id: str | None) -> tuple[str, TokenInfo | None]:
        """加载账号及 token。"""
        credentials = self.store.load_credentials(account_id)
        if not credentials:
            if account_id:
                return account_id, None
            ids = self.store.list_account_ids(include_revoked=False)
            return (ids[0] if ids else "default"), None
        selected_id = str(credentials.get("account_id") or account_id or "")
        return selected_id, token_from_credentials(credentials)

    def _should_refresh(self, token: TokenInfo, force: bool) -> bool:
        """判断 token 是否需要刷新。"""
        if force:
            return True
        if token.expires_at == 0:
            return False
        threshold = max(MIN_REFRESH_THRESHOLD_SECONDS, token.expires_in * REFRESH_THRESHOLD_RATIO)
        return token.expires_at - now_seconds() < threshold

    def _post_form(self, path: str, params: dict[str, str]) -> tuple[int, dict[str, Any]]:
        """发送 x-www-form-urlencoded POST 请求。"""
        device_id = self.store.get_device_id()
        headers = {
            **oauth_device_headers(device_id, self.version),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        body = urlencode(params)
        try:
            response = self._http_client.post(
                f"{self.oauth_host}{path}",
                content=body,
                headers=headers,
            )
            try:
                payload = response.json()
            except Exception:
                payload = {}
            return response.status_code, payload if isinstance(payload, dict) else {}
        except httpx.TimeoutException:
            raise OAuthError(f"OAuth request timed out after {self.timeout_seconds} seconds.") from None
        except httpx.HTTPError as exc:
            raise OAuthError(f"OAuth request failed: {exc}") from exc


def pick_error_detail(data: dict[str, Any]) -> str:
    """从错误响应中提取可读信息。"""
    for key in ("message", "error_description", "error"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    detail = data.get("detail")
    if isinstance(detail, str) and detail:
        return detail
    return "unknown"


def to_optional_int(value: Any) -> int | None:
    """将值转为可选整数。"""
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def now_seconds() -> int:
    """返回当前时间戳。"""
    return int(time.time())
