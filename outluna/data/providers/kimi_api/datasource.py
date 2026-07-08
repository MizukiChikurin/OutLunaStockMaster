"""Kimi Datasource API 调用客户端（同步 httpx 版）。"""

from __future__ import annotations

import base64
import json
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from outluna.data.providers.kimi_api.constants import (
    DEFAULT_DATASOURCE_API_URL,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    KIMI_DATASOURCE_VERSION,
    VALID_STOCK_QUERY_TYPES,
)
from outluna.data.providers.kimi_api.identity import datasource_headers
from outluna.data.providers.kimi_api.models import (
    DatasourceError,
    DatasourceHTTPError,
    OAuthUnauthorizedError,
    ToolInputError,
)
from outluna.data.providers.kimi_api.oauth import KimiOAuthClient
from outluna.data.providers.kimi_api.storage import KimiCredentialStore

logger = __import__("outluna.utils.logger", fromlist=["setup_logging"]).setup_logging()


class KimiDatasourceClient:
    """同步调用 Kimi Datasource API 的客户端。"""

    def __init__(
        self,
        store: KimiCredentialStore,
        oauth: KimiOAuthClient,
        *,
        api_url: str = DEFAULT_DATASOURCE_API_URL,
        timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        proxy: str = "",
        response_parse_mode: str = "official",
        save_response_files: bool = True,
        files_dir: Path | None = None,
        min_request_interval: float = 0.5,
        retry_on_rate_limit: bool = True,
        max_rate_limit_retries: int = 3,
        rate_limit_base_delay: float = 1.0,
    ) -> None:
        self.store = store
        self.oauth = oauth
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds
        self.proxy = proxy.strip() or None
        self.response_parse_mode = response_parse_mode
        self.save_response_files = save_response_files
        self.files_dir = files_dir
        self.min_request_interval = min_request_interval
        self.retry_on_rate_limit = retry_on_rate_limit
        self.max_rate_limit_retries = max_rate_limit_retries
        self.rate_limit_base_delay = rate_limit_base_delay
        self._last_request_time = 0.0
        self._request_lock = threading.Lock()
        self._http_client = httpx.Client(
            timeout=max(1, self.timeout_seconds),
            proxy=self.proxy,
            follow_redirects=True,
        )

    def close(self) -> None:
        """关闭底层 HTTP 客户端。"""
        self._http_client.close()

    def query_stock(
        self,
        *,
        ticker: str,
        query_type: str = "realtime_price",
        query_time: str = "",
        file_path: str = "",
    ) -> str:
        """查询个股行情数据。"""
        params = build_stock_params(ticker, query_type, query_time, file_path)
        result = self.call_kimi_tool("get_stock_realtime_price", params)
        # 服务端可能通过 response.files 返回文件而非写入 params.file_path。
        # 若本地已保存实际文件，优先将其作为 CSV 文件路径返回，避免读取空临时文件。
        saved_path = result.saved_files[0] if result.saved_files else params["file_path"]
        text = f"{result.text}\n\nCSV data written to: {saved_path}".strip()
        if result.saved_files:
            text += "\n\nLocal files saved to:\n" + "\n".join(f"- {path}" for path in result.saved_files)
        return text

    def get_data_source_desc(self, name: str) -> str:
        """获取数据源描述。"""
        result = self.call_kimi_tool("get_data_source_desc", {"name": required_string(name, "name")})
        return result.with_saved_files()

    def call_data_source_tool(
        self,
        *,
        data_source_name: str,
        api_name: str,
        params: dict[str, Any],
    ) -> str:
        """通用数据源工具调用。"""
        if not isinstance(params, dict):
            raise ToolInputError("params must be an object.")
        result = self.call_kimi_tool(
            "call_data_source_tool",
            {
                "data_source_name": required_string(data_source_name, "data_source_name"),
                "api_name": required_string(api_name, "api_name"),
                "params": params,
            },
        )
        return result.with_saved_files()

    def call_kimi_tool(self, method: str, params: dict[str, Any]) -> DatasourceResult:
        """调用 Kimi 工具，失败时按账号轮询、刷新重试，并支持限流退避。"""
        last_exc: Exception | None = None
        for attempt in range(max(1, self.max_rate_limit_retries)):
            try:
                return self._call_kimi_tool_once(method, params)
            except DatasourceError as exc:
                if self.retry_on_rate_limit and self._is_rate_limit_error(exc):
                    delay = self.rate_limit_base_delay * (2 ** attempt)
                    logger.warning(f"Kimi API 触发限流，{delay:.1f} 秒后重试（{attempt + 1}/{self.max_rate_limit_retries}）...")
                    time.sleep(delay)
                    last_exc = exc
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise DatasourceError("Kimi API 限流重试次数耗尽")

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        """判断异常是否为 Kimi 限流错误。"""
        text = str(exc).lower()
        return any(k in text for k in ["too many requests", "rate limit", "too fast"])

    def _call_kimi_tool_once(self, method: str, params: dict[str, Any]) -> DatasourceResult:
        """单次调用 Kimi 工具（账号轮询、凭证刷新）。"""
        account_ids = self.store.list_account_ids(include_revoked=False)
        if not account_ids:
            raise OAuthUnauthorizedError("No Kimi OAuth accounts are available. Ask an administrator to run kimi login.")

        start_id = self.store.next_account_id(account_ids)
        errors: list[str] = []
        for account_id in account_rotation(account_ids, start_id):
            try:
                response = self._post_json(method, params, account_id=account_id, force_refresh=False)
                break
            except OAuthUnauthorizedError as exc:
                errors.append(f"{account_id}: {exc}")
                continue
            except DatasourceHTTPError as exc:
                if exc.status not in {401, 403}:
                    raise
                try:
                    response = self._post_json(method, params, account_id=account_id, force_refresh=True)
                    break
                except DatasourceHTTPError as retry_exc:
                    if retry_exc.status in {401, 403}:
                        self.store.mark_revoked(account_id)
                        errors.append(f"{account_id}: datasource authorization failed")
                        continue
                    raise
                except OAuthUnauthorizedError as retry_exc:
                    errors.append(f"{account_id}: {retry_exc}")
                    continue
        else:
            message = "; ".join(errors) if errors else "all accounts failed"
            raise OAuthUnauthorizedError(f"Kimi datasource authorization failed for every configured account: {message}")

        text = extract_text(response, mode=self.response_parse_mode)
        saved_files = self._save_response_files(response)
        return DatasourceResult(text=text, saved_files=saved_files)

    def _post_json(self, method: str, params: dict[str, Any], *, account_id: str, force_refresh: bool) -> Any:
        """发送 JSON-RPC 风格 POST 请求，控制请求频率。"""
        if self.min_request_interval > 0:
            with self._request_lock:
                elapsed = time.monotonic() - self._last_request_time
                if elapsed < self.min_request_interval:
                    time.sleep(self.min_request_interval - elapsed)
                self._last_request_time = time.monotonic()

        token = self.oauth.ensure_fresh(account_id, force=force_refresh)
        device_id = self.store.get_device_id()
        try:
            response = self._http_client.post(
                self.api_url,
                json={"method": method, "params": params},
                headers=datasource_headers(token, device_id, KIMI_DATASOURCE_VERSION),
            )
            body = response.text
            if not response.is_success:
                raise DatasourceHTTPError(response.status_code, body)
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                return body
        except httpx.TimeoutException:
            raise DatasourceError(f"Request timed out after {self.timeout_seconds} seconds.") from None
        except httpx.HTTPError as exc:
            raise DatasourceError(f"Kimi datasource request failed: {exc}") from exc
        except OAuthUnauthorizedError:
            raise

    def _save_response_files(self, response: Any) -> list[str]:
        """保存响应中的 base64 文件。"""
        if not self.save_response_files or self.files_dir is None or not isinstance(response, dict):
            return []
        files = response.get("files")
        if not isinstance(files, list):
            return []

        self.files_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        for index, item in enumerate(files, start=1):
            if not isinstance(item, dict):
                continue
            name = safe_filename(str(item.get("name") or f"file_{index}"))
            content = item.get("content")
            if not isinstance(content, str):
                continue
            target = unique_path(self.files_dir / name)
            if item.get("encoding") == "base64":
                target.write_bytes(base64.b64decode(content))
            else:
                target.write_text(content, encoding="utf-8")
            saved.append(str(target))
        return saved


class DatasourceResult:
    """API 调用结果包装。"""

    def __init__(self, *, text: str, saved_files: list[str]) -> None:
        self.text = text
        self.saved_files = saved_files

    def with_saved_files(self) -> str:
        """附加本地保存文件列表。"""
        if not self.saved_files:
            return self.text
        return f"{self.text}\n\nLocal files saved to:\n" + "\n".join(f"- {path}" for path in self.saved_files)


def build_stock_params(ticker: str, query_type: str, query_time: str = "", file_path: str = "") -> dict[str, Any]:
    """构造股票查询参数。"""
    ticker = required_string(ticker, "ticker")
    tickers = [item.strip() for item in ticker.split(",") if item.strip()]
    if not tickers:
        raise ToolInputError("Missing required argument: ticker.")
    if len(tickers) > 3:
        raise ToolInputError("ticker accepts at most 3 values separated by commas.")

    query_type = (query_type or "realtime_price").strip()
    if query_type not in VALID_STOCK_QUERY_TYPES:
        raise ToolInputError(f"type must be one of {VALID_STOCK_QUERY_TYPES}; received: {query_type}")

    params: dict[str, Any] = {
        "ticker": ticker,
        "type": query_type,
        "file_path": required_string(file_path, "file_path") if file_path else default_stock_file_path(ticker, query_type),
    }
    if query_time and query_time.strip():
        params["time"] = query_time.strip()
    return params


def default_stock_file_path(ticker: str, query_type: str) -> str:
    """默认 CSV 文件路径。"""
    safe_ticker = ticker.replace(",", "_").replace(".", "_")
    unique = uuid.uuid4().hex[:12]
    return str(Path(tempfile.gettempdir()) / f"stock_{safe_ticker}_{query_type}_{unique}.csv")


def extract_text(response: Any, *, mode: str = "official") -> str:
    """从 API 响应中提取文本。"""
    if isinstance(response, str):
        return response
    if not isinstance(response, dict):
        return str(response)
    if response.get("is_success") is False:
        message = extract_user_text(response.get("error")) or json.dumps(response, ensure_ascii=False)
        raise DatasourceError(f"Tool API returned an error: {message}")

    result = response.get("result")
    if mode == "legacy_zip":
        text = extract_role_text(result, "assistant") or extract_role_text(result, "user")
    else:
        text = extract_role_text(result, "user")
    if text:
        return text

    # 部分接口（如 stock_finance_data）直接返回 data_preview CSV 预览。
    # 有些接口把 data_preview 嵌套在 result 中，因此同时检查顶层和 result 内部。
    data_preview = response.get("data_preview")
    if isinstance(data_preview, str) and data_preview.strip():
        return data_preview.strip()

    result_data = response.get("result")
    if isinstance(result_data, dict):
        data_preview = result_data.get("data_preview")
        if isinstance(data_preview, str) and data_preview.strip():
            return data_preview.strip()

    return f"Tool API succeeded but did not return user text. Raw response: {json.dumps(response, ensure_ascii=False)}"


def extract_user_text(value: Any) -> str | None:
    """提取 user 角色文本。"""
    return extract_role_text(value, "user")


def extract_role_text(value: Any, role: str) -> str | None:
    """提取指定角色文本。"""
    if not isinstance(value, dict):
        return None
    parts = value.get(role)
    if not isinstance(parts, list):
        return None
    text = "\n\n".join(
        item["text"]
        for item in parts
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str) and item["text"]
    )
    return text or None


def required_string(value: str, field: str) -> str:
    """校验非空字符串参数。"""
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(f"Missing required argument: {field}.")
    return value.strip()


def safe_filename(name: str) -> str:
    """生成安全文件名。"""
    base = Path(name).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return cleaned or "file"


def unique_path(path: Path) -> Path:
    """生成不重复文件路径。"""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 10_000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise DatasourceError(f"Unable to choose a unique file path under {path.parent}")


def account_rotation(account_ids: list[str], start_id: str) -> list[str]:
    """从 start_id 开始旋转账号列表。"""
    ids = sorted(dict.fromkeys(account_ids))
    if start_id not in ids:
        return ids
    index = ids.index(start_id)
    return ids[index:] + ids[:index]
