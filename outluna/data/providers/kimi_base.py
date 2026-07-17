"""Kimi Datasource 数据提供商基类。"""

from __future__ import annotations

import json
import re
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, cast

import pandas as pd

from outluna.data.providers import DataProvider


class KimiAuthError(RuntimeError):
    """Kimi Datasource 凭证过期或无效。"""

    def __init__(self, message: str = "Kimi 凭证已过期或无效"):
        super().__init__(message)


class KimiDataSourceBase(DataProvider, ABC):
    """Kimi Datasource 数据提供商基类。

    子类只需实现 ``_call_data_source`` 和 ``_get_data_source_desc``
    即可复用历史 K 线、实时行情、财务、公司信息等通用方法。
    """

    name = "kimi_datasource_base"

    OHLCV_BATCH_SIZE = 10
    OHLCV_ADJUST_MAP = {"qfq": "front", "hfq": "back", "none": "none"}

    def __init__(self) -> None:
        self._desc_cache: dict[str, str] = {}
        self._tianyancha_api_cache: dict[str, str | None] | None = None

    @abstractmethod
    def _call_data_source(self, data_source_name: str, api_name: str, params: dict[str, Any]) -> str:
        """调用通用数据源工具，返回 stdout 文本。"""

    @abstractmethod
    def _get_data_source_desc(self, data_source_name: str) -> str:
        """获取数据源描述文档。"""

    @abstractmethod
    def _query_stock(self, params: dict[str, Any]) -> str:
        """调用 query_stock 工具，返回 stdout 文本。"""

    def _is_auth_error(self, text: str) -> bool:
        """判断返回文本是否包含 401/凭证过期等认证错误。"""
        if not text:
            return False
        indicators = ["401", "unauthorized", "invalid token", "token expired", "凭证", "认证"]
        lower = text.lower()
        return any(indicator in lower for indicator in indicators)

    def _extract_csv_path(self, text: str) -> str | None:
        """从 stdout 文本中提取 CSV 文件路径。

        兼容两种来源：
        1. 工具直接写入的 CSV 文件路径（如 ``CSV data written to``）。
        2. 服务端通过 ``files`` 字段返回并由本地保存的文件路径（如 ``Local files saved to``）。
        """
        patterns = [
            r"CSV\s*数据已写入[：:]\s*(.+?\.csv)",
            r"CSV\s*written\s*to[：:]\s*(.+?\.csv)",
            r"CSV\s*data\s*written\s*to[：:]\s*(.+?\.csv)",
            r"文件路径[：:]\s*(.+?\.csv)",
            r"Local\s*files\s*saved\s*to:\s*\n?\s*[-•]\s*(.+?\.csv)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _extract_preview_lines(self, text: str) -> str:
        """从 stdout 文本中提取 data_preview 或 CSV 预览部分。"""
        # 1. 尝试将整个文本当作 JSON 解析
        try:
            data = json.loads(text)
            preview = data.get("data_preview")
            if isinstance(preview, str) and preview.strip():
                return preview.strip()
        except json.JSONDecodeError:
            pass

        # 2. 从 fallback 消息 "Raw response: {...}" 中提取 JSON 并解析 data_preview
        marker = "Raw response:"
        idx = text.find(marker)
        if idx != -1:
            json_text = text[idx + len(marker) :].strip()
            try:
                data = json.loads(json_text)
                preview = data.get("data_preview")
                if isinstance(preview, str) and preview.strip():
                    return preview.strip()
            except json.JSONDecodeError:
                pass

        # 3. 逐行提取文本，遇到 CSV 文件路径说明时停止
        lines = text.splitlines()
        preview_lines: list[str] = []
        for line in lines:
            if "CSV" in line and "写入" in line:
                break
            if "CSV" in line and "written" in line.lower():
                break
            if "文件路径" in line:
                break
            preview_lines.append(line)
        return "\n".join(preview_lines).strip()

    def _read_csv_result(self, text: str, symbol: str | None = None) -> pd.DataFrame:
        """从返回文本中读取 CSV 结果。

        优先从文本中的文件路径读取，若文件为空或不存在，则回退到 ``data_preview`` 中的 CSV 文本。
        """
        file_path = self._extract_csv_path(text)

        if file_path and Path(file_path).exists():
            try:
                df = pd.read_csv(file_path)
                if not df.empty:
                    return df
            except pd.errors.EmptyDataError:
                pass

        if file_path and symbol:
            suffix_map = {".SH": "_a", ".SZ": "_a", ".BJ": "_a", ".HK": "_hk"}
            for suffix, split_suffix in suffix_map.items():
                if symbol.endswith(suffix):
                    split_path = file_path.replace(".csv", f"{split_suffix}.csv")
                    if Path(split_path).exists():
                        try:
                            df = pd.read_csv(split_path)
                            if not df.empty:
                                return df
                        except pd.errors.EmptyDataError:
                            pass

        preview = self._extract_preview_lines(text)
        if preview:
            from io import StringIO

            try:
                return pd.read_csv(StringIO(preview))
            except pd.errors.EmptyDataError:
                return pd.DataFrame()

        return pd.DataFrame()

    def _check_error(self, text: str) -> None:
        """检查返回文本是否包含错误信息。"""
        error_markers = ["接口返回失败", "失败", "错误", "未找到", "Missing required"]
        for marker in error_markers:
            if marker in text and "CSV" not in text:
                if "is_success" in text or "data_preview" in text:
                    continue
                raise RuntimeError(f"Kimi Datasource 返回错误：{text[:200]}")

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
        """批量获取多只股票历史 K 线数据。"""
        if not symbols:
            return {}

        period_map = {"1d": "D", "1w": "W", "1m": "M", "1q": "Q", "1y": "Y"}
        freq = period_map.get(period, "D")
        adjust_value = self.OHLCV_ADJUST_MAP.get(adjust, "front")

        result: dict[str, pd.DataFrame] = {}
        batch_size = self.OHLCV_BATCH_SIZE
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
        symbol_cols = [c for c in df.columns if c.lower() in {"thscode", "symbol", "ticker", "代码"}]
        if symbol_cols:
            col = symbol_cols[0]
            grouped: dict[str, pd.DataFrame] = {}
            for symbol, group in df.groupby(col):
                symbol_key = str(symbol).strip()
                grouped[symbol_key] = group.drop(columns=[col], errors="ignore").reset_index(drop=True)
            return grouped

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
            df["date"] = self._parse_dates(df["date"])
            # 按日期升序排列，确保 tail(days) 取到最近数据
            df = df.sort_values("date").reset_index(drop=True)
        cols = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
        return df[cols].copy()

    def _parse_dates(self, series: pd.Series) -> pd.Series:
        """将日期列统一解析为 datetime，兼容多种时间戳与字符串格式。

        数据源可能返回 ISO 字符串、秒级/毫秒级 Unix 时间戳、YYYYMMDD 整数等。
        本方法依次尝试多种解析方式，并选择第一个结果落在合理年份范围（1971-2099）
        的解析结果；若均不合理，则返回原序列。
        """
        if pd.api.types.is_datetime64_any_dtype(series):
            return series

        def _is_reasonable(parsed: pd.Series) -> bool:
            """判断解析后的日期是否落在合理年份范围。"""
            if parsed.empty:
                return True
            try:
                year = int(parsed.max().year)
                return 1971 <= year <= 2099
            except Exception:
                return False

        candidates: list[pd.Series] = []

        # 1. 默认解析（ISO 字符串、普通日期字符串等）
        try:
            candidates.append(pd.to_datetime(series))
        except Exception:
            pass

        # 2. 秒级 Unix 时间戳
        try:
            candidates.append(pd.to_datetime(series, unit="s"))
        except Exception:
            pass

        # 3. 毫秒级 Unix 时间戳
        try:
            candidates.append(pd.to_datetime(series, unit="ms"))
        except Exception:
            pass

        # 4. YYYYMMDD 整数/字符串格式
        try:
            candidates.append(pd.to_datetime(series.astype(str), format="%Y%m%d"))
        except Exception:
            pass

        # 选择第一个合理的解析结果
        for parsed in candidates:
            if _is_reasonable(parsed):
                return parsed

        # 没有合理结果时返回首个候选，最差情况返回原序列
        if candidates:
            return candidates[0]
        return series

    def get_stock_list(self, market: str = "A") -> list[str]:
        """Kimi Datasource 不直接提供全市场列表，通过通用选股接口获取。"""
        df = self.screen_stocks("全部A股", market=market)
        return df["symbol"].tolist() if "symbol" in df.columns else []

    def get_realtime_price(self, symbols: list[str]) -> pd.DataFrame:
        """获取实时行情，Kimi 限制最多 3 只/次。"""
        if len(symbols) > 3:
            raise ValueError("Kimi Datasource 实时行情每次最多 3 只股票")

        params = {"ticker": ",".join(symbols), "type": "realtime_price"}
        text = self._query_stock(params)
        self._check_error(text)
        return self._read_csv_result(text)

    def get_realtime_tech(self, symbols: list[str], indicator: str = "MA") -> pd.DataFrame:
        """获取实时技术指标，仅 A 股。"""
        if len(symbols) > 3:
            raise ValueError("Kimi Datasource 实时技术指标每次最多 3 只股票")

        params = {"ticker": ",".join(symbols), "type": "realtime_tech"}
        text = self._query_stock(params)
        self._check_error(text)
        return self._read_csv_result(text)

    def get_close_summary(self, symbols: list[str]) -> pd.DataFrame:
        """获取日线收盘/开盘汇总数据，用于获取今日开盘价等日线汇总信息。"""
        if len(symbols) > 3:
            raise ValueError("Kimi Datasource close_summary 每次最多 3 只股票")

        params = {"ticker": ",".join(symbols), "type": "close_summary"}
        text = self._query_stock(params)
        self._check_error(text)
        return self._read_csv_result(text)

    # ==================== 智能选股 ====================

    def screen_stocks(
        self,
        keyword: str,
        market: str = "stock",
        cross_days: int = 1,
    ) -> pd.DataFrame:
        """Kimi Datasource 选股接口已弃用，返回空 DataFrame 由上层降级。"""
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
        """查询企业风险。"""
        if not company_full_name or not company_full_name.strip():
            return {"error": "企业名称不能为空"}

        try:
            apis = self._discover_tianyancha_apis()
            search_api = apis.get("search")
            risk_api = apis.get("risk")

            if not search_api and not self._looks_like_full_name(company_full_name):
                return {
                    "error": "未能识别天眼查企业搜索接口，且输入疑似不是企业全称，请直接提供企业全称后重试。",
                    "input": company_full_name,
                }

            full_name = company_full_name
            if search_api and not self._looks_like_full_name(company_full_name):
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
        """简单判断输入是否像企业全称。"""
        indicators = ["公司", "集团", "有限", "股份", "企业", "厂", "中心", "研究院"]
        return any(indicator in name for indicator in indicators)

    def _discover_tianyancha_apis(self) -> dict[str, str | None]:
        """动态发现天眼查数据源的可用 API 名称。"""
        if self._tianyancha_api_cache is not None:
            return self._tianyancha_api_cache

        self._tianyancha_api_cache = {"search": None, "risk": None}

        try:
            desc = self._get_data_source_desc("tianyancha")
        except Exception:
            desc = ""

        patterns = [
            r"`([a-zA-Z_][a-zA-Z0-9_]*)`",
            r"-\s*([a-zA-Z_][a-zA-Z0-9_]*)",
            r"([a-zA-Z_][a-zA-Z0-9_]*)\s*[:：]",
            r"api_name[=:]\"?([a-zA-Z_][a-zA-Z0-9_]*)\"?",
        ]
        discovered: set[str] = set()
        for pattern in patterns:
            discovered.update(re.findall(pattern, desc))

        search_candidates = [
            "search_company",
            "company_search",
            "enterprise_search",
            "search",
            "company_name_search",
            "fuzzy_search",
            "get_search",
        ]
        for candidate in search_candidates:
            if candidate in discovered:
                self._tianyancha_api_cache["search"] = candidate
                break

        risk_candidates = [
            "company_risk",
            "risk_info",
            "get_risk_info",
            "enterprise_risk",
            "judicial_risk",
            "company_judicial_risk",
            "risk_detail",
        ]
        for candidate in risk_candidates:
            if candidate in discovered:
                self._tianyancha_api_cache["risk"] = candidate
                break

        return self._tianyancha_api_cache

    def _search_company_full_name(self, search_api: str, keyword: str) -> str | None:
        """通过天眼查搜索接口查找企业全称。"""
        params: dict[str, Any] = {"keyword": keyword, "limit": 5}
        text = self._call_data_source("tianyancha", search_api, params)
        self._check_error(text)
        df = self._read_csv_result(text)
        if df.empty:
            return None

        name_cols = [c for c in df.columns if "公司" in c or "name" in c.lower() or "企业" in c]
        if name_cols:
            first = df[name_cols[0]].dropna().iloc[0]
            return str(first).strip()

        first_value = df.iloc[0].dropna().iloc[0]
        return str(first_value).strip()

    def _query_company_risk(self, risk_api: str, company_full_name: str) -> dict[str, Any]:
        """调用天眼查风险 API 获取企业风险信息。"""
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
            risk_count_cols = [c for c in df.columns if any(k in c for k in ["数量", "条数", "count", "total"])]
            if risk_count_cols:
                result["risk_counts"] = {
                    col: int(df[col].iloc[0]) if pd.notna(df[col].iloc[0]) else 0
                    for col in risk_count_cols
                }
        return result
