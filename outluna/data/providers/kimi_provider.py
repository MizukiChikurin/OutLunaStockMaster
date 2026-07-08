import json
import os
import subprocess
from pathlib import Path
from typing import Any

from outluna.config import settings
from outluna.data.providers.kimi_base import KimiAuthError, KimiDataSourceBase


class KimiDataSourceProvider(KimiDataSourceBase):
    """本地调用 Kimi Code CLI 脚本的数据提供商。"""

    name = "kimi_datasource"

    def __init__(self, home_dir: Path | None = None):
        super().__init__()
        self.home_dir = home_dir or settings.kimi_datasource_home
        self.script_dir = self.home_dir / "scripts"

    def _run_script(self, script_name: str, params: dict[str, Any], timeout: int = 120) -> str:
        """调用 kimi-datasource 脚本，返回 stdout 文本。"""
        script_path = self.script_dir / script_name
        if not script_path.exists():
            raise FileNotFoundError(f"找不到 Kimi Datasource 脚本：{script_path}")

        env = dict(os.environ)
        credential_candidates = [
            Path.home() / ".kimi-code" / "credentials" / "kimi-code.json",
            Path.home() / ".kimi" / "credentials" / "kimi-code.json",
        ]
        credential_file = next((p for p in credential_candidates if p.exists()), None)
        if credential_file and credential_file.exists():
            env["KIMI_CREDENTIALS_FILE"] = str(credential_file)

        input_json = json.dumps(params, ensure_ascii=False)
        result = subprocess.run(
            ["python", str(script_path)],
            cwd=str(self.home_dir),
            input=input_json,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        if result.returncode != 0:
            error_text = f"{result.stdout}\n{result.stderr}".strip()
            if self._is_auth_error(error_text):
                raise KimiAuthError(f"Kimi Datasource 凭证错误：{error_text[:300]}")
            raise RuntimeError(f"Kimi Datasource 调用失败：{error_text}")
        if self._is_auth_error(result.stdout):
            raise KimiAuthError(f"Kimi Datasource 凭证错误：{result.stdout[:300]}")
        return result.stdout

    def _call_data_source(self, data_source_name: str, api_name: str, params: dict[str, Any]) -> str:
        """本地调用通用数据源工具。"""
        full_api_name = f"{data_source_name}_{api_name}"
        call_params = {
            "data_source_name": data_source_name,
            "api_name": full_api_name,
            "params": params,
        }
        return self._run_script("call_data_source_tool.py", call_params)

    def _get_data_source_desc(self, data_source_name: str) -> str:
        """本地获取数据源描述文档。"""
        if data_source_name not in self._desc_cache:
            text = self._run_script("get_data_source_desc.py", {"name": data_source_name})
            self._desc_cache[data_source_name] = text
        return self._desc_cache[data_source_name]

    def _query_stock(self, params: dict[str, Any]) -> str:
        """本地调用 query_stock.py。"""
        return self._run_script("query_stock.py", params)
