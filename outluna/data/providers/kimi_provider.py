"""Kimi Datasource 数据提供商适配器。"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast

import pandas as pd

from outluna.config import settings
from outluna.data.providers import DataProvider


class KimiDataSourceProvider(DataProvider):
    """Kimi Datasource 数据提供商。

    通过调用 kimi-datasource 的脚本工具访问其聚合的多个数据源。
    脚本从 stdin 读取 JSON 参数，stdout 返回文本摘要（含 data_preview 和 CSV 路径）。
    """

    name = "kimi_datasource"

    def __init__(self, home_dir: Path | None = None):
        self.home_dir = home_dir or settings.kimi_datasource_home
        self.script_dir = self.home_dir / "scripts"
        self._desc_cache: dict[str, str] = {}

    def _run_script(self, script_name: str, params: dict[str, Any], timeout: int = 120) -> str:
        """调用 kimi-datasource 脚本，返回 stdout 文本。"""
        script_path = self.script_dir / script_name
        if not script_path.exists():
            raise FileNotFoundError(f"找不到 Kimi Datasource 脚本：{script_path}")

        # Kimi Code CLI 0.12+ 将凭证迁移到 ~/.kimi-code/credentials/kimi-code.json
        # 旧版本在 ~/.kimi/credentials/kimi-code.json。为兼容新旧版本，设置环境变量
        # 让脚本能够找到最新凭证。
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
            raise RuntimeError(f"Kimi Datasource 调用失败：{result.stderr}")
        return result.stdout

    def _call_data_source(self, data_source_name: str, api_name: str, params: dict[str, Any]) -> str:
        """调用通用数据源工具。

        Kimi Code CLI 0.12+ 要求 API 名称为 ``{data_source_name}_{api_name}``
        格式（如 ``stock_finance_data_get_price``）。本方法自动补全前缀，
        向下兼容旧版直接传入 ``api_name`` 的调用方式。
        """
        full_api_name = f"{data_source_name}_{api_name}"
        call_params = {
            "data_source_name": data_source_name,
            "api_name": full_api_name,
            "params": params,
        }
        return self._run_script("call_data_source_tool.py", call_params)

    def _extract_csv_path(self, text: str) -> str | None:
        """从 stdout 文本中提取 CSV 文件路径。"""
        # 匹配 "CSV 数据已写入：/path/to/file.csv" 或类似内容
        patterns = [
            r"CSV\s*数据已写入[：:]\s*(.+?\.csv)",
            r"CSV\s*written\s*to[：:]\s*(.+?\.csv)",
            r"文件路径[：:]\s*(.+?\.csv)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                path = match.group(1).strip()
                # 处理混合 A+港股时拆分的 _a.csv / _hk.csv
                return path
        return None

    def _extract_preview_lines(self, text: str) -> str:
        """从 stdout 文本中提取 data_preview 部分。"""
        # 新版 Kimi Code CLI 返回 JSON，data_preview 是 JSON 字符串字段。
        # 优先尝试 JSON 解析，避免把 JSON 包装符当作 CSV 列名。
        try:
            data = json.loads(text)
            preview = data.get("data_preview")
            if isinstance(preview, str):
                return preview.strip()
        except json.JSONDecodeError:
            pass

        # 兜底：按行扫描旧版文本格式
        lines = text.splitlines()
        preview_lines = []
        for line in lines:
            if "CSV" in line and "写入" in line:
                break
            if "文件路径" in line:
                break
            preview_lines.append(line)
        return "\n".join(preview_lines).strip()

    def _read_csv_result(self, text: str, symbol: str | None = None) -> pd.DataFrame:
        """从返回文本中读取 CSV 结果。"""
        file_path = self._extract_csv_path(text)

        # 处理混合 A+港股拆分的情况
        if file_path and Path(file_path).exists():
            df = pd.read_csv(file_path)
            return df

        if file_path and symbol:
            suffix_map = {".SH": "_a", ".SZ": "_a", ".BJ": "_a", ".HK": "_hk"}
            for suffix, split_suffix in suffix_map.items():
                if symbol.endswith(suffix):
                    split_path = file_path.replace(".csv", f"{split_suffix}.csv")
                    if Path(split_path).exists():
                        return pd.read_csv(split_path)

        # 没有文件路径时，尝试解析文本中的 CSV 预览
        preview = self._extract_preview_lines(text)
        if preview:
            from io import StringIO
            return pd.read_csv(StringIO(preview))

        return pd.DataFrame()

    def _check_error(self, text: str) -> None:
        """检查返回文本是否包含错误信息。"""
        error_markers = ["接口返回失败", "失败", "错误", "未找到", "Missing required"]
        for marker in error_markers:
            if marker in text and "CSV" not in text:
                # 简单启发式，避免误报
                if "is_success" in text or "data_preview" in text:
                    continue
                raise RuntimeError(f"Kimi Datasource 返回错误：{text[:200]}")

    def _get_data_source_desc(self, data_source_name: str) -> str:
        """获取数据源描述文档。"""
        if data_source_name not in self._desc_cache:
            text = self._run_script("get_data_source_desc.py", {"name": data_source_name})
            self._desc_cache[data_source_name] = text
        return self._desc_cache[data_source_name]

    # ==================== 行情数据 ====================

    def get_ohlcv(
        self,
        symbol: str,
        period: str = "1d",
        start_date: str | None = None,
        end_date: str | None = None,
        bars: int = 100,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """获取单只股票历史 K 线数据。"""
        result = self.get_ohlcv_multi(
            symbols=[symbol],
            period=period,
            start_date=start_date,
            end_date=end_date,
            bars=bars,
            adjust=adjust,
        )
        return result.get(symbol, pd.DataFrame())

    def get_ohlcv_multi(
        self,
        symbols: list[str],
        period: str = "1d",
        start_date: str | None = None,
        end_date: str | None = None,
        bars: int = 100,
        adjust: str = "qfq",
    ) -> dict[str, pd.DataFrame]:
        """批量获取多只股票历史 K 线数据。

        Kimi Datasource 的 ``get_price`` 单次最多支持 10 只股票，
        因此本方法将输入 symbols 分批调用，并把返回的聚合 CSV
        按股票代码拆分为每只股票的 DataFrame。

        Args:
            symbols: 股票代码列表，应为内部标准格式（如 ``600519.SH``）。
            period: 周期，支持 1d/1w/1m/1q/1y。
            start_date: 开始日期，格式 ``YYYY-MM-DD``。
            end_date: 结束日期，格式 ``YYYY-MM-DD``。
            bars: 期望 K 线数量。
            adjust: 复权方式，qfq/hfq/none。

        Returns:
            字典，key 为股票代码，value 为对应 K 线 DataFrame。
        """
        if not symbols:
            return {}

        period_map = {"1d": "D", "1w": "W", "1m": "M", "1q": "Q", "1y": "Y"}
        freq = period_map.get(period, "D")
        adjust_map = {"qfq": "front", "hfq": "back", "none": "none"}
        adjust_value = adjust_map.get(adjust, "front")

        result: dict[str, pd.DataFrame] = {}
        batch_size = 10
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i : i + batch_size]
            ticker_str = ",".join(batch)

            params: dict[str, Any] = {
                "ticker": ticker_str,
                "period": freq,
                "adjust": adjust_value,
            }
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date

            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
                params["file_path"] = tmp.name

            try:
                text = self._call_data_source("stock_finance_data", "get_price", params)
                self._check_error(text)
                df = self._read_csv_result(text)
                if not df.empty:
                    per_symbol = self._split_ohlcv_by_symbol(df, batch)
                    for symbol, symbol_df in per_symbol.items():
                        standardized = self._standardize_ohlcv(symbol_df)
                        if not standardized.empty:
                            result[symbol] = standardized
            finally:
                Path(str(params["file_path"])).unlink(missing_ok=True)

        return result

    def _split_ohlcv_by_symbol(
        self, df: pd.DataFrame, expected_symbols: list[str]
    ) -> dict[str, pd.DataFrame]:
        """将批量返回的 K 线数据按股票代码拆分。"""
        # Kimi 返回的多只股票数据通常包含 thscode 或 symbol 列
        symbol_cols = [c for c in df.columns if c.lower() in {"thscode", "symbol", "ticker", "代码"}]
        if symbol_cols:
            col = symbol_cols[0]
            grouped = {}
            for symbol, group in df.groupby(col):
                symbol_key = str(symbol).strip()
                grouped[symbol_key] = group.drop(columns=[col], errors="ignore").reset_index(drop=True)
            return grouped

        # 若无法识别代码列，按预期顺序将整份数据分配给第一只股票（兜底）
        return {expected_symbols[0]: df} if expected_symbols else {}

    def _standardize_ohlcv(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化 Kimi 返回的 K 线列名与格式。"""
        if df.empty:
            return df
        df = df.rename(
            columns={
                "time": "date",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            }
        )
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        cols = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
        return df[cols].copy()

    def get_stock_list(self, market: str = "A") -> list[str]:
        """Kimi Datasource 不直接提供全市场列表，通过通用选股接口获取。"""
        df = self.screen_stocks("全部A股", market=market)
        return df["symbol"].tolist() if "symbol" in df.columns else []

    def get_realtime_price(self, symbols: list[str]) -> pd.DataFrame:
        """获取实时行情，Kimi 限制最多 3 只/次。"""
        if len(symbols) > 3:
            raise ValueError("Kimi Datasource 实时行情每次最多 3 只股票")

        params = {"ticker": ",".join(symbols), "type": "realtime_price"}
        text = self._run_script("query_stock.py", params)
        self._check_error(text)
        return self._read_csv_result(text)

    def get_realtime_tech(self, symbols: list[str], indicator: str = "MA") -> pd.DataFrame:
        """获取实时技术指标，仅 A 股。"""
        if len(symbols) > 3:
            raise ValueError("Kimi Datasource 实时技术指标每次最多 3 只股票")

        params = {"ticker": ",".join(symbols), "type": "realtime_tech"}
        text = self._run_script("query_stock.py", params)
        self._check_error(text)
        return self._read_csv_result(text)

    # ==================== 智能选股 ====================

    def screen_stocks(
        self,
        keyword: str,
        market: str = "stock",
        cross_days: int = 1,
    ) -> pd.DataFrame:
        """使用自然语言条件筛选股票。

        Kimi Code CLI 0.12+ 不再提供 ``stock_finance_data`` 的选股 API，
        因此该方法返回空 DataFrame，由上层网关降级到 akshare 获取股票列表。
        """
        return pd.DataFrame()

    # ==================== 财务与公司信息 ====================

    def get_financials(self, symbol: str) -> dict[str, Any]:
        """获取财务报表。"""
        params = {"ticker": symbol}
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            params["file_path"] = tmp.name
        try:
            text = self._call_data_source("stock_finance_data", "get_financial_statements", params)
            self._check_error(text)
            df = self._read_csv_result(text, symbol)
            return {"balance_sheet": df}
        finally:
            Path(str(params["file_path"])).unlink(missing_ok=True)

    def get_financial_index(self, symbol: str) -> pd.DataFrame:
        """获取财务指标。"""
        # 新版 Kimi Code CLI 要求传入 financial_parameter（报告期）和 category（指标类别）。
        # 由于项目原本未设计这些参数，这里使用最近年报日期和盈利能力类别作为默认值。
        from datetime import datetime

        report_date = f"{datetime.now().year - 1}-12-31"
        params = {
            "ticker": symbol,
            "financial_parameter": report_date,
            "category": "profitability",
        }
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            params["file_path"] = tmp.name
        try:
            text = self._call_data_source("stock_finance_data", "get_stock_financial_index", params)
            self._check_error(text)
            return self._read_csv_result(text, symbol)
        finally:
            Path(str(params["file_path"])).unlink(missing_ok=True)

    def get_company_info(self, symbol: str) -> dict[str, Any]:
        """获取公司基本信息。"""
        params = {"ticker": symbol}
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            params["file_path"] = tmp.name
        try:
            text = self._call_data_source("stock_finance_data", "get_stock_info", params)
            self._check_error(text)
            df = self._read_csv_result(text, symbol)
            if df.empty:
                return {}
            return cast(dict[str, Any], df.iloc[0].to_dict())
        finally:
            Path(str(params["file_path"])).unlink(missing_ok=True)

    def get_holder_info(self, symbol: str) -> pd.DataFrame:
        """获取股东信息。"""
        params = {"ticker": symbol}
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            params["file_path"] = tmp.name
        try:
            text = self._call_data_source("stock_finance_data", "get_holder_info", params)
            self._check_error(text)
            return self._read_csv_result(text, symbol)
        finally:
            Path(str(params["file_path"])).unlink(missing_ok=True)

    def get_announcements(self, symbol: str, days: int = 30) -> pd.DataFrame:
        """获取公司公告。"""
        params = {"ticker": symbol}
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            params["file_path"] = tmp.name
        try:
            text = self._call_data_source("stock_finance_data", "get_stock_announcement", params)
            self._check_error(text)
            return self._read_csv_result(text, symbol)
        finally:
            Path(str(params["file_path"])).unlink(missing_ok=True)

    def get_forecast(self, symbol: str) -> pd.DataFrame:
        """获取业绩预测。"""
        params = {"ticker": symbol}
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            params["file_path"] = tmp.name
        try:
            text = self._call_data_source("stock_finance_data", "get_forecast", params)
            self._check_error(text)
            return self._read_csv_result(text, symbol)
        finally:
            Path(str(params["file_path"])).unlink(missing_ok=True)

    # ==================== 企业风险 ====================

    def get_company_risk(self, company_full_name: str) -> dict[str, Any]:
        """查询企业风险。

        天眼查数据源要求使用企业全称，且 API 名称需通过数据源描述动态发现。
        本方法会先从描述文档中识别可用的 API 名称，再依次执行企业搜索与风险查询。

        Args:
            company_full_name: 企业全称，例如"北京字节跳动科技有限公司"。
                               若传入股票代码或简称，会先尝试搜索获取全称。

        Returns:
            包含企业风险信息的字典；若查询失败，返回包含错误说明的字典。
        """
        if not company_full_name or not company_full_name.strip():
            return {"error": "企业名称不能为空"}

        try:
            apis = self._discover_tianyancha_apis()
            search_api = apis.get("search")
            risk_api = apis.get("risk")

            # 若未识别到搜索 API，则无法通过简称反查全称
            if not search_api and not self._looks_like_full_name(company_full_name):
                return {
                    "error": "未能识别天眼查企业搜索接口，且输入疑似不是企业全称，"
                    "请直接提供企业全称后重试。",
                    "input": company_full_name,
                }

            full_name = company_full_name
            if search_api and not self._looks_like_full_name(company_full_name):
                # 输入不是明显全称时，先搜索获取全称
                found = self._search_company_full_name(search_api, company_full_name)
                if found:
                    full_name = found
                else:
                    return {
                        "error": f"未通过天眼查搜索到企业：{company_full_name}",
                        "input": company_full_name,
                    }

            if not risk_api:
                return {
                    "error": "未能识别天眼查风险查询接口，无法获取企业风险信息",
                    "company_full_name": full_name,
                }

            return self._query_company_risk(risk_api, full_name)
        except Exception as exc:
            logger = __import__("outluna.utils.logger", fromlist=["setup_logging"]).setup_logging()
            logger.warning(f"天眼查风险查询失败：{exc}")
            return {"error": f"天眼查风险查询失败：{exc}", "input": company_full_name}

    def _looks_like_full_name(self, name: str) -> bool:
        """简单判断输入是否像企业全称（包含"公司"、"集团"、"有限"等字样）。"""
        indicators = ["公司", "集团", "有限", "股份", "企业", "厂", "中心", "研究院"]
        return any(indicator in name for indicator in indicators)

    def _discover_tianyancha_apis(self) -> dict[str, str | None]:
        """动态发现天眼查数据源的可用 API 名称。

        通过调用 get_data_source_desc 获取数据源描述文档，
        结合正则提取候选 API 名称，再与常见天眼查 API 别名进行匹配，
        返回搜索类与风险类 API 的名称。
        """
        if hasattr(self, "_tianyancha_api_cache"):
            return self._tianyancha_api_cache

        self._tianyancha_api_cache: dict[str, str | None] = {"search": None, "risk": None}

        try:
            desc = self._get_data_source_desc("tianyancha")
        except Exception:
            desc = ""

        # 从描述文本中提取可能的 API 名称（支持反引号、列表、冒号等格式）
        patterns = [
            r"`([a-zA-Z_][a-zA-Z0-9_]*)`",
            r"-\s*([a-zA-Z_][a-zA-Z0-9_]*)",
            r"([a-zA-Z_][a-zA-Z0-9_]*)\s*[:：]",
            r"api_name[=:]\"?([a-zA-Z_][a-zA-Z0-9_]*)\"?",
        ]
        discovered: set[str] = set()
        for pattern in patterns:
            discovered.update(re.findall(pattern, desc))

        # 搜索类 API 候选，按优先级匹配
        search_candidates = [
            "search_company", "company_search", "enterprise_search", "search",
            "company_name_search", "fuzzy_search", "get_search",
        ]
        for candidate in search_candidates:
            if candidate in discovered:
                self._tianyancha_api_cache["search"] = candidate
                break

        # 风险类 API 候选，按优先级匹配
        risk_candidates = [
            "company_risk", "risk_info", "get_risk_info", "enterprise_risk",
            "judicial_risk", "company_judicial_risk", "risk_detail",
        ]
        for candidate in risk_candidates:
            if candidate in discovered:
                self._tianyancha_api_cache["risk"] = candidate
                break

        return self._tianyancha_api_cache

    def _search_company_full_name(self, search_api: str, keyword: str) -> str | None:
        """通过天眼查搜索接口查找企业全称。

        Args:
            search_api: 识别到的搜索 API 名称。
            keyword: 搜索关键词，可以是企业简称或股票代码关联名称。

        Returns:
            企业全称字符串；若未找到则返回 None。
        """
        params: dict[str, Any] = {"keyword": keyword, "limit": 5}
        text = self._call_data_source("tianyancha", search_api, params)
        self._check_error(text)
        df = self._read_csv_result(text)
        if df.empty:
            return None

        # 优先取包含"公司名称"、"name"或"企业名称"的列
        name_cols = [c for c in df.columns if "公司" in c or "name" in c.lower() or "企业" in c]
        if name_cols:
            first = df[name_cols[0]].dropna().iloc[0]
            return str(first).strip()

        # 退而求其次，取第一行第一列文本值
        first_value = df.iloc[0].dropna().iloc[0]
        return str(first_value).strip()

    def _query_company_risk(self, risk_api: str, company_full_name: str) -> dict[str, Any]:
        """调用天眼查风险 API 获取企业风险信息。

        Args:
            risk_api: 识别到的风险查询 API 名称。
            company_full_name: 企业全称。

        Returns:
            风险信息字典，包含原始 DataFrame 解析后的结果与摘要。
        """
        params: dict[str, Any] = {"company_name": company_full_name, "name": company_full_name}
        text = self._call_data_source("tianyancha", risk_api, params)
        self._check_error(text)
        df = self._read_csv_result(text)

        result: dict[str, Any] = {
            "company_full_name": company_full_name,
            "risk_api": risk_api,
            "has_data": not df.empty,
        }
        if not df.empty:
            result["data"] = df.to_dict(orient="records")
            # 根据常见列名尝试提取风险数量类字段
            risk_count_cols = [c for c in df.columns if any(k in c for k in ["数量", "条数", "count", "total"])]
            if risk_count_cols:
                result["risk_counts"] = {
                    col: int(df[col].iloc[0]) if pd.notna(df[col].iloc[0]) else 0
                    for col in risk_count_cols
                }
        return result
