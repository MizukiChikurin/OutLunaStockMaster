"""Kimi Datasource API 凭证存储（同步 JSON 文件版）。"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from outluna.config import settings
from outluna.data.providers.kimi_api.identity import new_device_id
from outluna.data.providers.kimi_api.models import TokenInfo, token_from_credentials

DEVICE_ID_KEY = "kimi_code.device_id"
ACCOUNTS_KEY = "kimi_code.accounts"
ROTATION_CURSOR_KEY = "kimi_code.rotation_cursor"
ACCOUNT_ID_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


class KimiCredentialStore:
    """OutLuna 私有的 Kimi OAuth 凭证存储，使用本地 JSON 文件。"""

    def __init__(self, storage_path: Path | None = None) -> None:
        self.storage_path = storage_path or self._default_storage_path()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _default_storage_path() -> Path:
        """默认凭证文件路径：data/kimi_api_credentials.json。"""
        return settings.db_path.parent / "kimi_api_credentials.json"

    def _load_data(self) -> dict[str, Any]:
        """加载完整存储数据。"""
        if not self.storage_path.exists():
            return {}
        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            from outluna.utils.logger import setup_logging

            logger = setup_logging()
            logger.warning(f"读取 Kimi 凭证文件失败：{exc}")
        return {}

    def _save_data(self, data: dict[str, Any]) -> None:
        """保存完整存储数据。"""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(self.storage_path, 0o600)
        except OSError:
            pass

    def get_device_id(self) -> str:
        """获取或创建设备 ID。"""
        data = self._load_data()
        existing = data.get(DEVICE_ID_KEY)
        if isinstance(existing, str) and existing.strip():
            return existing.strip()
        device_id = new_device_id()
        data[DEVICE_ID_KEY] = device_id
        self._save_data(data)
        return device_id

    def save_device_id(self, device_id: str) -> None:
        """保存设备 ID。"""
        cleaned = str(device_id).strip()
        if cleaned:
            data = self._load_data()
            data[DEVICE_ID_KEY] = cleaned
            self._save_data(data)

    def list_accounts(self) -> dict[str, dict[str, Any]]:
        """返回所有账号的凭证字典。"""
        data = self._load_data()
        accounts = data.get(ACCOUNTS_KEY, {})
        normalized = normalize_accounts(accounts)
        if normalized != accounts:
            data[ACCOUNTS_KEY] = normalized
            self._save_data(data)
        return normalized

    def list_account_ids(self, *, include_revoked: bool = True) -> list[str]:
        """返回所有账号 ID。"""
        accounts = self.list_accounts()
        ids = []
        for account_id, credentials in accounts.items():
            if include_revoked or credentials.get("status") != "revoked":
                ids.append(account_id)
        return ids

    def load_credentials(self, account_id: str | None = None) -> dict[str, Any] | None:
        """加载指定账号或轮询到的可用账号凭证。"""
        accounts = self.list_accounts()
        if account_id is not None:
            credentials = accounts.get(normalize_account_id(account_id))
            return credentials.copy() if isinstance(credentials, dict) else None

        account_ids = [key for key, value in accounts.items() if value.get("status") != "revoked"]
        if not account_ids:
            return None
        selected = self.next_account_id(account_ids)
        credentials = accounts.get(selected)
        if isinstance(credentials, dict):
            credentials = credentials.copy()
            credentials["account_id"] = selected
            return credentials
        return None

    def load_token(self, account_id: str) -> TokenInfo | None:
        """加载指定账号的 token。"""
        credentials = self.load_credentials(account_id)
        if not credentials:
            return None
        return token_from_credentials(credentials)

    def save_login_token(
        self,
        token: TokenInfo,
        *,
        account_id: str,
        device_id: str,
        session_id: str,
    ) -> str:
        """保存登录获取的 token。"""
        normalized_id = self.allocate_account_id(account_id)
        data = self._load_data()
        accounts = data.get(ACCOUNTS_KEY, {})
        accounts[normalized_id] = self._token_payload(
            token,
            device_id=device_id,
            last_login_session=session_id,
            last_refresh_at=None,
        )
        data[ACCOUNTS_KEY] = accounts
        self._save_data(data)
        return normalized_id

    def save_refreshed_token(self, account_id: str, token: TokenInfo, *, device_id: str) -> None:
        """保存刷新后的 token。"""
        account_id = normalize_account_id(account_id)
        data = self._load_data()
        accounts = data.get(ACCOUNTS_KEY, {})
        previous = accounts.get(account_id, {})
        accounts[account_id] = self._token_payload(
            token,
            device_id=device_id,
            last_login_session=str(previous.get("last_login_session") or ""),
            last_refresh_at=utc_now_iso(),
        )
        data[ACCOUNTS_KEY] = accounts
        self._save_data(data)

    def mark_revoked(self, account_id: str) -> None:
        """标记账号为已撤销。"""
        account_id = normalize_account_id(account_id)
        data = self._load_data()
        accounts = data.get(ACCOUNTS_KEY, {})
        credentials = accounts.get(account_id)
        if not credentials:
            return
        credentials["status"] = "revoked"
        credentials["updated_at"] = utc_now_iso()
        data[ACCOUNTS_KEY] = accounts
        self._save_data(data)

    def delete_account(self, account_id: str) -> bool:
        """删除单个账号。"""
        account_id = normalize_account_id(account_id)
        data = self._load_data()
        accounts = data.get(ACCOUNTS_KEY, {})
        if account_id not in accounts:
            return False
        accounts.pop(account_id, None)
        data[ACCOUNTS_KEY] = accounts
        self._save_data(data)
        return True

    def delete_credentials(self) -> None:
        """删除全部凭证。"""
        data = self._load_data()
        data.pop(ACCOUNTS_KEY, None)
        data.pop(ROTATION_CURSOR_KEY, None)
        self._save_data(data)

    def next_account_id(self, account_ids: list[str] | None = None) -> str:
        """轮询选择下一个账号。"""
        if account_ids is None:
            account_ids = self.list_account_ids(include_revoked=False)
        account_ids = sorted(dict.fromkeys(normalize_account_id(item) for item in account_ids if item))
        if not account_ids:
            raise ValueError("没有可用的 Kimi OAuth 账号。")

        data = self._load_data()
        cursor = data.get(ROTATION_CURSOR_KEY, 0)
        if not isinstance(cursor, int):
            cursor = 0
        selected = account_ids[cursor % len(account_ids)]
        data[ROTATION_CURSOR_KEY] = (cursor + 1) % len(account_ids)
        self._save_data(data)
        return selected

    def allocate_account_id(self, requested: str = "") -> str:
        """分配一个账号 ID。"""
        requested = normalize_account_id(requested or "")
        accounts = self.list_accounts()
        if requested and requested not in accounts:
            return requested
        if requested:
            return requested

        index = 1
        while True:
            candidate = f"account-{index}"
            if candidate not in accounts:
                return candidate
            index += 1

    def _token_payload(
        self,
        token: TokenInfo,
        *,
        device_id: str,
        last_login_session: str,
        last_refresh_at: str | None,
    ) -> dict[str, Any]:
        """构建凭证条目。"""
        now = utc_now_iso()
        return {
            "access_token": token.access_token,
            "refresh_token": token.refresh_token,
            "expires_at": token.expires_at,
            "expires_in": token.expires_in,
            "token_type": token.token_type,
            "scope": token.scope,
            "status": "valid",
            "device_id": device_id,
            "updated_at": now,
            "last_refresh_at": last_refresh_at,
            "last_login_session": last_login_session,
        }


def normalize_accounts(accounts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """规范化账号 ID。"""
    normalized: dict[str, dict[str, Any]] = {}
    for raw_id, credentials in accounts.items():
        account_id = normalize_account_id(str(raw_id))
        if not account_id or not isinstance(credentials, dict):
            continue
        normalized[account_id] = credentials
    return normalized


def normalize_account_id(value: str) -> str:
    """清理账号 ID。"""
    cleaned = ACCOUNT_ID_PATTERN.sub("-", str(value).strip())
    cleaned = cleaned.strip(".-_")
    return cleaned[:64]


def normalize_account_id_list(values: list[Any]) -> list[str]:
    """规范化账号 ID 列表。"""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        account_id = normalize_account_id(value)
        if account_id and account_id not in seen:
            result.append(account_id)
            seen.add(account_id)
    return result


def utc_now_iso() -> str:
    """返回当前 UTC 时间 ISO 字符串。"""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def mask_token(token: str) -> str:
    """脱敏显示 token。"""
    if not token:
        return "none"
    if len(token) <= 12:
        return f"{token[:2]}...{token[-2:]}"
    return f"{token[:6]}...{token[-4:]}"
