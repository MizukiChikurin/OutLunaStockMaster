"""基于 Kimi Datasource API 的远程数据提供商。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

from outluna.config import settings
from outluna.data.providers.kimi_api.datasource import KimiDatasourceClient
from outluna.data.providers.kimi_api.models import OAuthUnauthorizedError
from outluna.data.providers.kimi_api.oauth import KimiOAuthClient
from outluna.data.providers.kimi_api.storage import KimiCredentialStore
from outluna.data.providers.kimi_base import KimiAuthError, KimiDataSourceBase


class KimiApiDataSourceProvider(KimiDataSourceBase):
    """通过 Kimi Datasource API 直接获取数据。

    与本地 ``KimiDataSourceProvider`` 接口一致，但使用 OAuth 远程调用，
    无需安装本地 ``kimi-datasource`` 脚本。
    """

    name = "kimi_api"

    OHLCV_BATCH_SIZE = 3
    OHLCV_ADJUST_MAP = {"qfq": "forward", "hfq": "backward", "none": "none"}

    def __init__(
        self,
        storage_path: Path | None = None,
        files_dir: Path | None = None,
        proxy: str = "",
        timeout_seconds: int = 30,
    ) -> None:
        super().__init__()
        self.store = KimiCredentialStore(storage_path=storage_path)
        self.oauth = KimiOAuthClient(self.store, proxy=proxy, timeout_seconds=timeout_seconds)
        self.client = KimiDatasourceClient(
            self.store,
            self.oauth,
            files_dir=files_dir or settings.db_path.parent / "kimi_api_files",
            proxy=proxy,
            timeout_seconds=timeout_seconds,
            min_request_interval=2.0,
            retry_on_rate_limit=True,
            max_rate_limit_retries=5,
            rate_limit_base_delay=5.0,
        )

    def _call_data_source(self, data_source_name: str, api_name: str, params: dict[str, Any]) -> str:
        """调用通用数据源工具。"""
        try:
            full_api_name = f"{data_source_name}_{api_name}"
            return self.client.call_data_source_tool(
                data_source_name=data_source_name,
                api_name=full_api_name,
                params=params,
            )
        except OAuthUnauthorizedError as exc:
            raise KimiAuthError(str(exc)) from exc

    def _get_data_source_desc(self, data_source_name: str) -> str:
        """获取数据源描述文档。"""
        try:
            if data_source_name not in self._desc_cache:
                self._desc_cache[data_source_name] = self.client.get_data_source_desc(data_source_name)
            return self._desc_cache[data_source_name]
        except OAuthUnauthorizedError as exc:
            raise KimiAuthError(str(exc)) from exc

    def _query_stock(self, params: dict[str, Any]) -> str:
        """调用 query_stock 工具。"""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            file_path = tmp.name
        try:
            return self.client.query_stock(
                ticker=params["ticker"],
                query_type=params.get("type", "realtime_price"),
                query_time=params.get("time", ""),
                file_path=file_path,
            )
        except OAuthUnauthorizedError as exc:
            raise KimiAuthError(str(exc)) from exc

    def test_realtime_price(
        self, symbols: list[str]
    ) -> tuple[str, pd.DataFrame]:
        """诊断接口：测试 realtime_price 并返回原始响应与解析结果。"""
        params = {"ticker": ",".join(symbols), "type": "realtime_price"}
        text = self._query_stock(params)
        df = self._read_csv_result(text)
        return text, df
